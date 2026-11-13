/* Minimal SVG chart kit: line (with forecast band), bar (grouped / stacked / horizontal), heatmap.
   One y-axis per chart, colour assigned by role in a fixed order, hover tooltips on every mark. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const SLOTS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];
  const ACTUAL = ["--s1", "--s3", "--s7", "--s5"];          // forecast always takes --s2, so actual series skip it
  const registry = [];

  function S(tag, attrs, parent, style) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (style) for (const k in style) e.style.setProperty(k, style[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function fmt(v, t) {
    if (v == null || Number.isNaN(v)) return "–";
    if (typeof v !== "number") return String(v);
    const a = Math.abs(v), sg = v < 0 ? "-" : "";
    const loc = (x, d) => x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
    switch (t) {
      case "usd": return sg + "$" + (a >= 1e9 ? loc(a / 1e9, 2) + "B" : a >= 1e6 ? loc(a / 1e6, 2) + "M" : a >= 1e4 ? loc(a / 1e3, 0) + "K" : loc(a, 0));
      case "usd0": return sg + "$" + loc(a, 0);
      case "usd1": return sg + "$" + loc(a, 1);
      case "pct": return loc(v * 100, 1) + "%";
      case "pct2": return loc(v * 100, 2) + "%";
      case "signed_pct": case "signed_pct_bad": return (v > 0 ? "+" : "") + loc(v * 100, 1) + "%";
      case "dec0": return loc(v, 0);
      case "dec1": return loc(v, 1);
      case "dec2": return loc(v, 2);
      case "dec1x": return loc(v, 1) + "x";
      case "num": return a >= 1e6 ? loc(v / 1e6, 2) + "M" : loc(v, 0);
      default: return loc(v, a < 10 && a % 1 ? 1 : 0);
    }
  }
  function axisFmt(v, t) {
    const a = Math.abs(v);
    if (t.startsWith("usd")) return (v < 0 ? "-" : "") + "$" + (a >= 1e6 ? +(a / 1e6).toFixed(1) + "M" : a >= 1e3 ? +(a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "K" : +a.toFixed(0));
    if (t.includes("pct")) return +(v * 100).toFixed(a < 0.1 ? 1 : 0) + "%";
    return a >= 1e6 ? +(v / 1e6).toFixed(1) + "M" : a >= 1e4 ? +(v / 1e3).toFixed(0) + "K" : +v.toFixed(a < 10 ? 1 : 0) + "";
  }
  function ticks(lo, hi, n) {
    if (hi === lo) { hi = lo + 1; }
    const raw = (hi - lo) / (n || 4), mag = Math.pow(10, Math.floor(Math.log10(raw))), r = raw / mag;
    const step = (r > 5 ? 10 : r > 2 ? 5 : r > 1 ? 2 : 1) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toPrecision(12));
    return out;
  }
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function dateLabel(s, grain, long) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (!m) return String(s);
    if (grain === "month") return MON[+m[2] - 1] + " " + m[1].slice(2);
    return +m[3] + " " + MON[+m[2] - 1] + (long ? " " + m[1] : "");
  }

  const tip = {
    el: null,
    show(html, ev) {
      this.el = this.el || document.getElementById("tooltip");
      this.el.innerHTML = html; this.el.hidden = false;
      const r = this.el.getBoundingClientRect();
      let x = ev.clientX + 14, y = ev.clientY + 14;
      if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
      if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14;
      this.el.style.left = Math.max(8, x) + "px"; this.el.style.top = Math.max(8, y) + "px";
    },
    hide() { if (this.el) this.el.hidden = true; },
  };
  const row = (color, name, val) => `<div class="tt-r"><span>${color ? `<i style="background:var(${color})"></i>` : ""}${esc(name)}</span><b>${val}</b></div>`;

  function styleFor(series) {
    let ai = 0;
    return series.map((s) => {
      if (s.kind === "forecast") return { color: "--s2", dash: "6 3", cls: "dash", w: 2 };
      if (s.kind === "budget") return { color: "--ink-2", dash: "5 4", cls: "dash", w: 1.5 };
      if (s.kind === "capacity") return { color: "--muted", dash: "2 3", cls: "dot", w: 1.5 };
      if (s.kind === "compare") return { color: "--neutral-mark", dash: "", cls: "", w: 2 };
      return { color: ACTUAL[ai++ % ACTUAL.length], dash: "", cls: "", w: 2 };
    });
  }
  function legend(el, items) {
    if (items.length < 2) return;
    const d = document.createElement("div");
    d.className = "legend";
    d.innerHTML = items.map((i) => `<span><i class="${i.cls}" style="${i.cls === "box" ? "background" : "border-color"}:var(${i.color})"></i>${esc(i.name)}</span>`).join("");
    el.appendChild(d);
  }
  function holder(el, h) {
    const d = document.createElement("div");
    d.className = "chart";
    el.appendChild(d);
    const W = Math.max(d.clientWidth || el.clientWidth || 600, 240);
    return { d, W, svg: S("svg", { viewBox: `0 0 ${W} ${h}`, height: h, role: "img" }, d) };
  }

  function line(el, p) {
    el.innerHTML = "";
    const series = (p.series || []).filter((s) => s.data.some((v) => v != null));
    if (!p.x || !p.x.length || !series.length) { el.innerHTML = '<div class="empty">No data for this selection</div>'; return; }
    const st = styleFor(series), t = p.fmt || "num";
    legend(el, series.map((s, i) => ({ name: s.name, color: st[i].color, cls: st[i].cls })));
    const H = p.height || 232, { svg, W } = holder(el, H), m = { l: 48, r: 14, t: 8, b: 24 }, iw = W - m.l - m.r, ih = H - m.t - m.b, n = p.x.length;
    if (p.label) svg.setAttribute("aria-label", p.label);
    let vals = [];
    series.forEach((s) => { vals = vals.concat(s.data, s.lo || [], s.hi || []); });
    vals = vals.filter((v) => v != null);
    let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (lo >= 0 && (hi === 0 || lo / hi < 0.6)) lo = 0; else { const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1; lo -= pad; }
    hi += (hi - lo) * 0.06 || 1;
    const tk = ticks(lo, hi, 4);
    lo = Math.min(lo, tk[0]); hi = Math.max(hi, tk[tk.length - 1]);
    const X = (i) => m.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1)), Y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;
    tk.forEach((v) => {
      S("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, svg, { stroke: v === 0 ? "var(--axis)" : "var(--grid)", "stroke-width": 1 });
      S("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = axisFmt(v, t);
    });
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 78))));
    for (let i = 0; i < n; i += every) {
      if (n - 1 - i < every * 0.6 && i !== n - 1 && i + every >= n) continue;
      S("text", { x: X(i), y: H - 6, "text-anchor": i === 0 ? "start" : "middle" }, svg).textContent = dateLabel(p.x[i], p.grain);
    }
    series.forEach((s, k) => {
      if (s.lo && s.hi) {
        let top = "", bot = "";
        s.data.forEach((v, i) => { if (v != null && s.lo[i] != null) { top += (top ? "L" : "M") + X(i) + " " + Y(s.hi[i]); bot = "L" + X(i) + " " + Y(s.lo[i]) + bot; } });
        if (top) S("path", { d: top + bot + "Z" }, svg, { fill: `var(${st[k].color})`, opacity: 0.14 });
      }
    });
    series.forEach((s, k) => {
      let d = "", pen = false, pts = 0, last = null;
      s.data.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1); pen = true; pts++; last = i; });
      S("path", { d, fill: "none", "stroke-linejoin": "round", "stroke-linecap": "round" }, svg, { stroke: `var(${st[k].color})`, "stroke-width": st[k].w, "stroke-dasharray": st[k].dash || "none" });
      if (pts === 1) S("circle", { cx: X(last), cy: Y(s.data[last]), r: 4 }, svg, { fill: `var(${st[k].color})` });
    });
    const cross = S("line", { y1: m.t, y2: m.t + ih, x1: 0, x2: 0 }, svg, { stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
    const dots = series.map((s, k) => S("circle", { r: 4, cx: 0, cy: 0 }, svg, { fill: `var(${st[k].color})`, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }));
    const hit = S("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (ev) => {
      const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.style.visibility = "visible";
      let html = `<div class="tt-h">${dateLabel(p.x[i], p.grain, true)}${p.grain === "week" ? " (week)" : ""}</div>`;
      series.forEach((s, k) => {
        const v = s.data[i];
        dots[k].style.visibility = v == null ? "hidden" : "visible";
        if (v == null) return;
        dots[k].setAttribute("cx", X(i)); dots[k].setAttribute("cy", Y(v));
        html += row(st[k].color, s.name, fmt(v, t));
        if (s.lo && s.lo[i] != null) html += row(null, "80% interval", fmt(s.lo[i], t) + " – " + fmt(s.hi[i], t));
      });
      tip.show(html, ev);
    });
    hit.addEventListener("mouseleave", () => { cross.style.visibility = "hidden"; dots.forEach((d) => (d.style.visibility = "hidden")); tip.hide(); });
  }

  function barColor(s, k, stacked, nActual) {
    if (s.kind === "compare") return "--neutral-mark";
    if (s.kind === "budget") return "--ink-2";
    return stacked || nActual > 1 ? SLOTS[k % SLOTS.length] : "--s1";
  }
  function bar(el, p) {
    el.innerHTML = "";
    const cats = p.categories || [], series = p.series || [];
    if (!cats.length || !series.length) { el.innerHTML = '<div class="empty">No data for this selection</div>'; return; }
    const t = p.fmt || "num", nAct = series.filter((s) => !s.kind || s.kind === "actual").length;
    const cols = series.map((s, k) => barColor(s, k, p.stacked, nAct));
    legend(el, series.map((s, k) => ({ name: s.name, color: cols[k], cls: "box" })));
    const tot = cats.map((_, i) => series.reduce((a, s) => a + (s.data[i] || 0), 0));
    let hi = p.stacked ? Math.max.apply(null, tot) : Math.max.apply(null, series.map((s) => Math.max.apply(null, s.data.map((v) => v || 0))));
    let lo = Math.min(0, Math.min.apply(null, series.map((s) => Math.min.apply(null, s.data.map((v) => v || 0)))));
    hi = Math.max(hi, 0);
    const tk = ticks(lo, hi * 1.04 || 1, 4);
    lo = Math.min(lo, tk[0]); hi = Math.max(hi * 1.04, tk[tk.length - 1]);
    const tipFor = (i, k) => (ev) => {
      let html = `<div class="tt-h">${esc(cats[i])}</div>`;
      (p.stacked ? series.map((_, j) => j) : [k]).forEach((j) => { html += row(cols[j], series[j].name, fmt(series[j].data[i], t)); });
      if (p.stacked) html += row(null, "Total", fmt(tot[i], t));
      tip.show(html, ev);
    };
    const mark = (svg, x, y, w, h, k, i) => {
      if (w < 0.5 || h < 0.5) return;
      const r = S("rect", { x, y, width: w, height: h, rx: Math.min(3, w / 2, h / 2) }, svg, { fill: `var(${cols[k]})`, opacity: series[k].kind === "budget" ? 0.55 : 1 });
      r.addEventListener("mousemove", tipFor(i, k)); r.addEventListener("mouseleave", () => tip.hide());
    };
    if (p.horizontal) {
      const per = p.stacked ? 1 : series.length, bh = per > 1 ? 9 : 14, rowH = per * (bh + 2) + 12, lw = Math.min(170, Math.max(90, (el.clientWidth || 500) * 0.3));
      const H = cats.length * rowH + 26, { svg, W } = holder(el, H), m = { l: lw, r: per === 1 && !p.stacked ? 62 : 16, t: 4, b: 22 }, iw = W - m.l - m.r;
      const X = (v) => m.l + ((v - lo) / (hi - lo)) * iw;
      tk.forEach((v) => {
        S("line", { x1: X(v), x2: X(v), y1: m.t, y2: H - m.b }, svg, { stroke: v === 0 ? "var(--axis)" : "var(--grid)", "stroke-width": 1 });
        S("text", { x: X(v), y: H - 6, "text-anchor": "middle" }, svg).textContent = axisFmt(v, t);
      });
      cats.forEach((c, i) => {
        const y0 = m.t + i * rowH + 6, maxc = Math.floor(lw / 6.4), txt = S("text", { x: m.l - 8, y: y0 + (per * (bh + 2)) / 2 + 3, "text-anchor": "end", class: "dl" }, svg);
        txt.textContent = String(c).length > maxc ? String(c).slice(0, maxc - 1) + "…" : c;
        let acc = 0;
        series.forEach((s, k) => {
          const v = s.data[i] || 0;
          if (p.stacked) { mark(svg, X(acc) + 1, y0, Math.max(X(acc + v) - X(acc) - 2, 0), bh, k, i); acc += v; }
          else {
            mark(svg, Math.min(X(0), X(v)), y0 + k * (bh + 2), Math.abs(X(v) - X(0)), bh, k, i);
            if (per === 1) S("text", { x: Math.max(X(v), X(0)) + 6, y: y0 + bh - 3, class: "dl" }, svg).textContent = fmt(s.data[i], t);
          }
        });
      });
      return;
    }
    const H = p.height || 232, { svg, W } = holder(el, H), m = { l: 48, r: 10, t: 8, b: 36 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
    const Y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih, band = iw / cats.length, per = p.stacked ? 1 : series.length;
    const bw = Math.min(28, Math.max(4, (band * 0.72) / per - 2));
    tk.forEach((v) => {
      S("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, svg, { stroke: v === 0 ? "var(--axis)" : "var(--grid)", "stroke-width": 1 });
      S("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = axisFmt(v, t);
    });
    cats.forEach((c, i) => {
      const cx = m.l + band * (i + 0.5), x0 = cx - (per * (bw + 2) - 2) / 2, maxc = Math.max(4, Math.floor(band / 6.2));
      const words = String(c).split(" "), l1 = [], l2 = [];
      words.forEach((w) => (!l1.length || ((l1.join(" ") + " " + w).length <= maxc && !l2.length) ? l1 : l2).push(w));
      const clip = (s) => (s.length > maxc ? s.slice(0, Math.max(1, maxc - 1)) + "…" : s);
      S("text", { x: cx, y: H - 20, "text-anchor": "middle", class: "dl" }, svg).textContent = clip(l1.join(" ") || words[0]);
      if (l2.length) S("text", { x: cx, y: H - 7, "text-anchor": "middle", class: "dl" }, svg).textContent = clip(l2.join(" "));
      let acc = 0;
      series.forEach((s, k) => {
        const v = s.data[i] || 0;
        if (p.stacked) { mark(svg, x0, Y(acc + v) + 1, bw, Math.max(Y(acc) - Y(acc + v) - 2, 0), k, i); acc += v; }
        else mark(svg, x0 + k * (bw + 2), Math.min(Y(v), Y(0)), bw, Math.abs(Y(v) - Y(0)), k, i);
      });
    });
  }

  function heatmap(el, p) {
    el.innerHTML = "";
    if (!p.x || !p.x.length || !p.y.length) { el.innerHTML = '<div class="empty">No data for this selection</div>'; return; }
    const t = p.fmt || "num", flat = [].concat.apply([], p.values).filter((v) => v != null), max = Math.max.apply(null, flat) || 1;
    const lw = Math.min(160, Math.max(44, Math.max.apply(null, p.y.map((s) => s.length)) * 6.6)), rowH = 26, H = p.y.length * rowH + 24;
    const { svg, W } = holder(el, H), cw = (W - lw - 4) / p.x.length;
    p.x.forEach((c, j) => { S("text", { x: lw + cw * (j + 0.5), y: 12, "text-anchor": "middle" }, svg).textContent = cw < 38 ? String(c).slice(0, 2) : c; });
    p.y.forEach((r, i) => {
      const maxc = Math.floor(lw / 6.4);
      S("text", { x: lw - 8, y: 20 + i * rowH + 16, "text-anchor": "end", class: "dl" }, svg).textContent = r.length > maxc ? r.slice(0, maxc - 1) + "…" : r;
      p.x.forEach((c, j) => {
        const v = p.values[i][j], step = v == null ? 0 : Math.min(6, Math.round((v / max) * 6));
        const cell = S("rect", { x: lw + cw * j + 1, y: 20 + i * rowH + 1, width: Math.max(cw - 2, 1), height: rowH - 2, rx: 3 }, svg, { fill: `var(--seq-${step})` });
        if (cw >= 36 && v != null) S("text", { x: lw + cw * (j + 0.5), y: 20 + i * rowH + 17, "text-anchor": "middle", "pointer-events": "none" }, svg, { fill: step >= 4 ? "var(--surface)" : "var(--ink)" }).textContent = fmt(v, t);
        cell.addEventListener("mousemove", (ev) => tip.show(`<div class="tt-h">${esc(r)} · ${esc(c)}</div>` + row(null, p.title || "Value", fmt(v, t)), ev));
        cell.addEventListener("mouseleave", () => tip.hide());
      });
    });
  }

  function draw(el, type, spec) {
    registry.push({ el, type, spec });
    ({ line, bar, heatmap })[type](el, spec);
  }
  let timer = null;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => registry.forEach((r) => r.el.isConnected && ({ line, bar, heatmap })[r.type](r.el, r.spec)), 150);
  });
  window.Charts = { draw, fmt, tip, esc, dateLabel, reset() { registry.length = 0; tip.hide(); } };
})();
