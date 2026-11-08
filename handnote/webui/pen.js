// Pen pressure shared by the capture pages.
//
// A stylus reports real pressure. A mouse or finger does not, so pressure is
// simulated from writing speed (slow = heavy, fast = light) when
// ``pen.simulate`` is on. Width scale mirrors the server's
// ``capture.pressure_scale``: 1 + gain * 0.6 * (p - 0.5).
"use strict";

const pen = {
  KEY: "handnote:pen",
  gain: 1,
  simulate: true,

  load() {
    try {
      const saved = JSON.parse(localStorage.getItem(pen.KEY) || "null");
      if (saved) {
        pen.gain = Math.min(2, Math.max(0, Number(saved.gain ?? 1)));
        pen.simulate = saved.simulate !== false;
      }
    } catch (err) {
      // Defaults are fine.
    }
  },

  save() {
    try {
      localStorage.setItem(pen.KEY, JSON.stringify({ gain: pen.gain, simulate: pen.simulate }));
    } catch (err) {
      // Not worth interrupting the writer.
    }
  },

  /** Width multiplier for a pressure value. */
  scale(p) {
    return Math.max(0.3, 1 + pen.gain * 0.6 * ((p ?? 0.5) - 0.5));
  },

  /**
   * Pressure for a new point at (u, v). ``prev`` is the stroke so far as
   * ``{points, t}`` (``t`` = timestamp of the last point), or null at pen-down.
   */
  pressure(event, u, v, prev) {
    if (event.pointerType === "pen" && event.pressure > 0) {
      return Math.min(1, Math.max(0, event.pressure));
    }
    if (!pen.simulate) return 0.5;
    if (!prev || !prev.points.length) return 0.6;
    const last = prev.points[prev.points.length - 1];
    const dt = Math.max(4, event.timeStamp - prev.t);
    const speed = Math.hypot(u - last[0], v - last[1]) / dt; // cell widths per ms
    const target = Math.min(0.8, Math.max(0.2, 0.8 - speed * 120));
    return last[2] * 0.7 + target * 0.3;
  },

  /** Wire a gain slider, its read-out and the simulate checkbox. */
  bind(gainInput, gainOut, simulateInput, onChange) {
    pen.load();
    gainInput.value = String(pen.gain);
    gainOut.textContent = pen.gain.toFixed(1);
    simulateInput.checked = pen.simulate;
    gainInput.addEventListener("input", () => {
      pen.gain = Number(gainInput.value);
      gainOut.textContent = pen.gain.toFixed(1);
      pen.save();
      if (onChange) onChange();
    });
    simulateInput.addEventListener("change", () => {
      pen.simulate = simulateInput.checked;
      pen.save();
    });
  },
};
