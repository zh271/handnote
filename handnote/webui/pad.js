// Writing pad: a square canvas that records strokes as cell-normalised points.
//
//   const pad = createPad(el, { size: 420, ink: "#1e1f2c", onStroke(points) {} });
//   pad.paint(strokes, width);   pad.guide(drawFn);   pad.width = 0.05;
//
// Points are [u, v, pressure] with u, v in [0, 1], origin top-left, v down;
// the same frame the server expects for captures. Needs pen.js (pressure).
"use strict";

function createPad(host, options) {
  const size = options.size || 420;
  const ink = options.ink || "#1e1f2c";
  const dpr = window.devicePixelRatio || 1;

  const layer = (z) => {
    const canvas = document.createElement("canvas");
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    canvas.style.width = `${size}px`;
    canvas.style.height = `${size}px`;
    canvas.style.zIndex = String(z);
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    host.appendChild(canvas);
    return { canvas, ctx };
  };

  host.classList.add("pad");
  host.style.width = `${size}px`;
  host.style.height = `${size}px`;
  const back = layer(1);
  const front = layer(2);
  const pad = { size, width: options.width || 0.05, drawing: null };

  const dot = (ctx, p) => {
    ctx.beginPath();
    const radius = (pad.width * size * pen.scale(p[2])) / 2;
    ctx.arc(p[0] * size, p[1] * size, Math.max(1, radius), 0, Math.PI * 2);
    ctx.fill();
  };
  const segment = (ctx, a, b) => {
    ctx.lineWidth = Math.max(1, pad.width * size * pen.scale((a[2] + b[2]) / 2));
    ctx.beginPath();
    ctx.moveTo(a[0] * size, a[1] * size);
    ctx.lineTo(b[0] * size, b[1] * size);
    ctx.stroke();
  };

  pad.paint = (strokes) => {
    const { ctx } = front;
    ctx.clearRect(0, 0, size, size);
    ctx.strokeStyle = ink;
    ctx.fillStyle = ink;
    for (const stroke of strokes) {
      const points = stroke.points;
      if (!points.length) continue;
      dot(ctx, points[0]);
      for (let i = 1; i < points.length; i += 1) segment(ctx, points[i - 1], points[i]);
    }
  };

  /** Redraw the background layer with ``draw(ctx, size)``. */
  pad.guide = (draw) => {
    back.ctx.clearRect(0, 0, size, size);
    if (draw) draw(back.ctx, size);
  };

  const pointOf = (event, stroke = null) => {
    const rect = front.canvas.getBoundingClientRect();
    const clamp = (v) => Math.min(1, Math.max(0, v));
    const u = clamp((event.clientX - rect.left) / (rect.width || 1));
    const v = clamp((event.clientY - rect.top) / (rect.height || 1));
    return [u, v, pen.pressure(event, u, v, stroke)];
  };
  const finish = (event) => {
    if (!pad.drawing) return;
    pad.drawing.points.push(pointOf(event, pad.drawing));
    const points = pad.drawing.points;
    pad.drawing = null;
    options.onStroke(points);
  };
  front.canvas.addEventListener("pointerdown", (event) => {
    if (event.button > 0) return;
    event.preventDefault();
    front.canvas.setPointerCapture(event.pointerId);
    const p = pointOf(event);
    pad.drawing = { points: [p], t: event.timeStamp };
    front.ctx.fillStyle = ink;
    dot(front.ctx, p);
  });
  front.canvas.addEventListener("pointermove", (event) => {
    if (!pad.drawing) return;
    event.preventDefault();
    const p = pointOf(event, pad.drawing);
    const points = pad.drawing.points;
    const last = points[points.length - 1];
    if (Math.hypot(p[0] - last[0], p[1] - last[1]) < 0.003) return;
    points.push(p);
    pad.drawing.t = event.timeStamp;
    front.ctx.strokeStyle = ink;
    segment(front.ctx, last, p);
  });
  front.canvas.addEventListener("pointerup", finish);
  front.canvas.addEventListener("pointercancel", finish);
  return pad;
}
