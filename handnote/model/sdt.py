# Vendored from SDT (https://github.com/dailenson/SDT), "Disentangling Writer and
# Character Styles for Handwriting Generation", CVPR 2023.
#
# MIT License
#
# Copyright (c) 2023 Gang Dai
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Changes for handnote: merged models/{model,encoder,transformer,gmm,loss}.py and
# utils/util.normalize_xys into one module; dropped the contrastive-loss path from
# the training forward (``decode``), the unused Transformer/Content_Cls classes and
# every hard-coded ``.cuda()``; ResNet stems are built without ImageNet weights
# (the checkpoint replaces them); inference runs the decoders on the generated
# prefix only, which the causal mask makes equivalent to the full-length pass.
"""SDT network: style encoder, content encoder and the two style decoders."""

from __future__ import annotations

import copy
import math

import numpy as np
import torch
import torch.nn.functional as F
from einops import rearrange
from torch import Tensor, nn
from torchvision import models

#: Network shape of the released Chinese checkpoint (configs/CHINESE_USER.yml).
D_MODEL = 512
ENCODER_LAYERS = 2
HEAD_LAYERS = 1
WRI_DEC_LAYERS = 2
GLY_DEC_LAYERS = 2
GMM_OUT = 123  # 3 pen logits + 20 mixtures x (pi, mu1, mu2, sigma1, sigma2, corr)


# -- transformer ---------------------------------------------------------------
def _clones(module: nn.Module, n: int) -> nn.ModuleList:
    return nn.ModuleList([copy.deepcopy(module) for _ in range(n)])


class TransformerEncoder(nn.Module):
    def __init__(self, layer: nn.Module, num_layers: int, norm: nn.Module | None = None):
        super().__init__()
        self.layers = _clones(layer, num_layers)
        self.norm = norm

    def forward(self, src: Tensor) -> Tensor:
        out = src
        for layer in self.layers:
            out = layer(out)
        return self.norm(out) if self.norm is not None else out


class TransformerDecoder(nn.Module):
    """Returns the normalised output of every layer, stacked."""

    def __init__(self, layer: nn.Module, num_layers: int, norm: nn.Module):
        super().__init__()
        self.layers = _clones(layer, num_layers)
        self.norm = norm

    def forward(self, tgt: Tensor, memory: Tensor, tgt_mask: Tensor | None = None) -> Tensor:
        out = tgt
        intermediate = []
        for layer in self.layers:
            out = layer(out, memory, tgt_mask=tgt_mask)
            intermediate.append(self.norm(out))
        return torch.stack(intermediate)


class TransformerEncoderLayer(nn.Module):
    """Pre-norm encoder layer."""

    def __init__(self, d_model: int, nhead: int, dim_feedforward: int = 2048,
                 dropout: float = 0.1):
        super().__init__()
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(dim_feedforward, d_model)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)

    def forward(self, src: Tensor) -> Tensor:
        src2 = self.norm1(src)
        src2 = self.self_attn(src2, src2, value=src2)[0]
        src = src + self.dropout1(src2)
        src2 = self.norm2(src)
        src2 = self.linear2(self.dropout(F.relu(self.linear1(src2))))
        return src + self.dropout2(src2)


class TransformerDecoderLayer(nn.Module):
    """Pre-norm decoder layer."""

    def __init__(self, d_model: int, nhead: int, dim_feedforward: int = 2048,
                 dropout: float = 0.1):
        super().__init__()
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.multihead_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(dim_feedforward, d_model)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.norm3 = nn.LayerNorm(d_model)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)
        self.dropout3 = nn.Dropout(dropout)

    def forward(self, tgt: Tensor, memory: Tensor, tgt_mask: Tensor | None = None) -> Tensor:
        tgt2 = self.norm1(tgt)
        tgt2 = self.self_attn(tgt2, tgt2, value=tgt2, attn_mask=tgt_mask)[0]
        tgt = tgt + self.dropout1(tgt2)
        tgt2 = self.norm2(tgt)
        tgt2 = self.multihead_attn(query=tgt2, key=memory, value=memory)[0]
        tgt = tgt + self.dropout2(tgt2)
        tgt2 = self.norm3(tgt)
        tgt2 = self.linear2(self.dropout(F.relu(self.linear1(tgt2))))
        return tgt + self.dropout3(tgt2)


class PositionalEncoding(nn.Module):
    """Sinusoidal positional encoding; also scales the input by sqrt(dim)."""

    def __init__(self, dropout: float, dim: int, max_len: int = 500):
        super().__init__()
        pe = torch.zeros(max_len, dim)
        position = torch.arange(0, max_len).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, dim, 2, dtype=torch.float)
                             * -(math.log(10000.0) / dim))
        pe[:, 0::2] = torch.sin(position.float() * div_term)
        pe[:, 1::2] = torch.cos(position.float() * div_term)
        self.register_buffer("pe", pe.unsqueeze(1))
        self.dropout = nn.Dropout(p=dropout)
        self.dim = dim

    def forward(self, emb: Tensor, step: int | None = None) -> Tensor:
        emb = emb * math.sqrt(self.dim)
        emb = emb + (self.pe[:emb.size(0)] if step is None else self.pe[step])
        return self.dropout(emb)


def causal_mask(size: int) -> Tensor:
    """0 on and below the diagonal, -inf above."""
    mask = (torch.triu(torch.ones(size, size)) == 1).transpose(0, 1)
    return mask.float().masked_fill(~mask, float("-inf")).masked_fill(mask, 0.0)


def _resnet_stem() -> nn.Sequential:
    """ResNet-18 up to layer4 with a one-channel first convolution."""
    resnet = models.resnet18(weights=None)
    return nn.Sequential(nn.Conv2d(1, 64, kernel_size=7, stride=2, padding=3, bias=False),
                         *list(resnet.children())[1:-2])


# -- encoders and generator ----------------------------------------------------
class ContentEncoder(nn.Module):
    """Upstream ``Content_TR``; the checkpoint was built with nhead=2, layers=3."""

    def __init__(self, d_model: int = D_MODEL, nhead: int = 2, num_encoder_layers: int = 3,
                 dropout: float = 0.1):
        super().__init__()
        self.Feat_Encoder = _resnet_stem()
        layer = TransformerEncoderLayer(d_model, nhead, 2048, dropout)
        self.add_position = PositionalEncoding(dropout=0.1, dim=d_model)
        self.encoder = TransformerEncoder(layer, num_encoder_layers, norm=nn.LayerNorm(d_model))

    def forward(self, x: Tensor) -> Tensor:
        x = self.Feat_Encoder(x)
        x = rearrange(x, "n c h w -> (h w) n c")
        return self.encoder(self.add_position(x))


class SeqtoEmb(nn.Module):
    def __init__(self, hid_dim: int, dropout: float = 0.1):
        super().__init__()
        self.fc_1 = nn.Linear(5, 256)
        self.fc_2 = nn.Linear(256, hid_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, seq: Tensor) -> Tensor:
        return self.fc_2(self.dropout(torch.relu(self.fc_1(seq))))


class EmbtoSeq(nn.Module):
    def __init__(self, hid_dim: int, dropout: float = 0.1):
        super().__init__()
        self.fc_1 = nn.Linear(hid_dim, 256)
        self.fc_2 = nn.Linear(256, GMM_OUT)
        self.dropout = nn.Dropout(dropout)

    def forward(self, seq: Tensor) -> Tensor:
        return self.fc_2(self.dropout(torch.relu(self.fc_1(seq))))


class SDTGenerator(nn.Module):
    """Upstream ``SDT_Generator`` with the released Chinese configuration."""

    def __init__(self, d_model: int = D_MODEL, nhead: int = 8, dropout: float = 0.1):
        super().__init__()
        self.Feat_Encoder = _resnet_stem()
        enc = TransformerEncoderLayer(d_model, nhead, 2048, dropout)
        self.base_encoder = TransformerEncoder(enc, ENCODER_LAYERS, None)
        self.writer_head = TransformerEncoder(enc, HEAD_LAYERS, nn.LayerNorm(d_model))
        self.glyph_head = TransformerEncoder(enc, HEAD_LAYERS, nn.LayerNorm(d_model))
        self.content_encoder = ContentEncoder(d_model, ENCODER_LAYERS)
        dec = TransformerDecoderLayer(d_model, nhead, 2048, dropout)
        self.wri_decoder = TransformerDecoder(dec, WRI_DEC_LAYERS, nn.LayerNorm(d_model))
        self.gly_decoder = TransformerDecoder(dec, GLY_DEC_LAYERS, nn.LayerNorm(d_model))
        # Projection heads for the contrastive losses; unused here but part of
        # the checkpoint.
        self.pro_mlp_writer = nn.Sequential(nn.Linear(512, 4096), nn.GELU(),
                                            nn.Linear(4096, 256))
        self.pro_mlp_character = nn.Sequential(nn.Linear(512, 4096), nn.GELU(),
                                               nn.Linear(4096, 256))
        self.SeqtoEmb = SeqtoEmb(hid_dim=d_model)
        self.EmbtoSeq = EmbtoSeq(hid_dim=d_model)
        self.add_position = PositionalEncoding(dropout=0.1, dim=d_model)
        for p in self.parameters():
            if p.dim() > 1:
                nn.init.xavier_uniform_(p)

    def _styles(self, style_imgs: Tensor) -> tuple[Tensor, Tensor]:
        """[B, N, 1, 64, 64] -> writer and glyph memories, each [4*N, B, C]."""
        batch, num, planes, h, w = style_imgs.shape
        feat = self.Feat_Encoder(style_imgs.reshape(-1, planes, h, w))
        feat = feat.reshape(batch * num, D_MODEL, -1).permute(2, 0, 1)
        memory = self.base_encoder(self.add_position(feat))
        writer = rearrange(self.writer_head(memory), "t (b n) c -> (t n) b c", b=batch)
        glyph = rearrange(self.glyph_head(memory), "t (b n) c -> (t n) b c", b=batch)
        return writer, glyph

    def _content(self, char_img: Tensor) -> Tensor:
        return torch.mean(self.content_encoder(char_img), 0)  # [B, C]

    def decode(self, style_imgs: Tensor, seq: Tensor, char_img: Tensor) -> Tensor:
        """Teacher-forced pass for training: [B, T, 5] -> GMM outputs [B, T+1, 123]."""
        writer, glyph = self._styles(style_imgs)
        seq_emb = self.SeqtoEmb(seq).permute(1, 0, 2)
        tgt = torch.cat((self._content(char_img).unsqueeze(0), seq_emb), 0)
        tgt = self.add_position(tgt)
        mask = causal_mask(tgt.shape[0]).to(tgt)
        wri = self.wri_decoder(tgt, writer, tgt_mask=mask)
        hs = self.gly_decoder(wri[-1], glyph, tgt_mask=mask)
        return self.EmbtoSeq(hs.transpose(1, 2)[-1])

    @torch.no_grad()
    def inference(self, style_imgs: Tensor, char_img: Tensor, max_len: int = 120) -> Tensor:
        """Greedy generation: [B, T, 5] rows of (dx, dy, down, up, end)."""
        writer, glyph = self._styles(style_imgs)
        char_emb = self._content(char_img)
        batch = char_img.shape[0]
        src = torch.zeros(max_len + 1, batch, D_MODEL).to(char_emb)
        pred = torch.zeros(max_len, batch, 5).to(char_emb)
        src[0] = char_emb
        mask = causal_mask(max_len + 1).to(char_emb)
        finished = torch.zeros(batch, dtype=torch.bool, device=char_emb.device)
        for i in range(max_len):
            src[i] = self.add_position(src[i], step=i)
            n = i + 1
            wri = self.wri_decoder(src[:n], writer, tgt_mask=mask[:n, :n])
            hs = self.gly_decoder(wri[-1], glyph, tgt_mask=mask[:n, :n])
            pred[i] = seq_from_gmm(self.EmbtoSeq(hs[-1][i]))
            src[i + 1] = self.SeqtoEmb(pred[i])
            # Upstream stops only when every sample ends on the same step; each
            # sample's sequence is read up to its own first end, so stopping once
            # all have ended gives the same characters.
            finished |= pred[i, :, -1] == 1
            if bool(finished.all()):
                pred = pred[:n]
                break
        return pred.transpose(0, 1)


# -- mixture density output ------------------------------------------------------
def mixture_coef(output: Tensor) -> list[Tensor]:
    """Split [N, 123] into (pi, mu1, mu2, sigma1, sigma2, corr, pen_logits)."""
    pen_logits = output[:, 0:3]
    pi, mu1, mu2, s1, s2, corr = torch.split(output[:, 3:], 20, 1)
    pi = torch.softmax(pi, -1)
    s1 = torch.clamp(torch.exp(s1), max=500.0)
    s2 = torch.clamp(torch.exp(s2), max=500.0)
    return [pi, mu1, mu2, s1, s2, torch.tanh(corr), pen_logits]


def seq_from_gmm(gmm_pred: Tensor) -> Tensor:
    """Most likely mixture's mean and the arg-max pen state: [N, 5]."""
    gmm_pred = gmm_pred.reshape(-1, GMM_OUT)
    pi, mu1, mu2, _s1, _s2, _corr, _pen = mixture_coef(gmm_pred)
    rows = torch.arange(pi.shape[0], device=pi.device)
    best = torch.argmax(pi, 1)
    pen = F.one_hot(torch.argmax(gmm_pred[:, :3], dim=-1), num_classes=3).to(gmm_pred)
    return torch.cat([mu1[rows, best].unsqueeze(1), mu2[rows, best].unsqueeze(1), pen], -1)


def _normal_2d(x1, x2, mu1, mu2, s1, s2, rho):
    s1 = torch.clip(s1, 1e-6, 500.0)
    s2 = torch.clip(s2, 1e-6, 500.0)
    n1, n2 = x1 - mu1, x2 - mu2
    s1s2 = s1 * s2
    z = (n1 / s1) ** 2 + (n2 / s2) ** 2 - 2 * rho * n1 * n2 / s1s2
    neg_rho = torch.clip(1 - rho ** 2, 1e-6, 1.0)
    return torch.exp(-z / (2 * neg_rho)) / (2 * np.pi * s1s2 * torch.sqrt(neg_rho))


def pen_loss(preds: Tensor, gt: Tensor, lengths: Tensor) -> Tensor:
    """Upstream pen loss: GMM offset NLL (per real point) + 2 x pen-state CE."""
    pi, mu1, mu2, s1, s2, corr, pen_logits = mixture_coef(preds.reshape(-1, GMM_OUT))
    gt = gt.reshape(-1, 5)
    density = torch.sum(_normal_2d(gt[:, 0:1], gt[:, 1:2], mu1, mu2, s1, s2, corr) * pi,
                        1, keepdim=True)
    offset = -torch.log(density + 1e-10) * (1.0 - gt[:, 4]).reshape(-1, 1)
    state = F.cross_entropy(pen_logits, torch.argmax(gt[:, 2:], -1))
    return torch.sum(offset) / torch.sum(lengths) + 2 * state


# -- coordinates -----------------------------------------------------------------
def normalize_xys(xys: np.ndarray) -> np.ndarray:
    """Centre on the ink's length-weighted mean and scale by its spread.

    ``xys`` rows are ``(x, y, down, up, end)``; segments that cross a pen-up
    are ignored. Upstream ``utils.util.normalize_xys``.
    """
    xys = np.array(xys, dtype=np.float64)
    stroke = np.cumsum(np.concatenate(([0], xys[:, -2]))[:-1])
    px = py = total = 0.0
    segments = []
    for i in range(xys.shape[0] - 2):
        if stroke[i] == stroke[i + 1]:
            a, b = xys[i, :2], xys[i + 1, :2]
            length = float(np.hypot(*(b - a)))
            segments.append((a, b, length))
            mx, my = length * (a + b) / 2
            px, py, total = px + mx, py + my, total + length
    if total == 0:
        raise ValueError("character has no ink")
    mux, muy = px / total, py / total
    dx = dy = 0.0
    for a, b, length in segments:
        dx += length * ((a[0] - mux) ** 2 + (b[0] - mux) ** 2 + (a[0] - mux) * (b[0] - mux)) / 3
        dy += length * ((a[1] - muy) ** 2 + (b[1] - muy) ** 2 + (a[1] - muy) * (b[1] - muy)) / 3
    sigma = math.sqrt(dx / total) or math.sqrt(dy / total)
    xys[:, 0] = (xys[:, 0] - mux) / sigma
    xys[:, 1] = (xys[:, 1] - muy) / sigma
    return xys
