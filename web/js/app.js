/* Aurelian Collection - Hospitality Intelligence web client.
   One generic renderer draws every dashboard from the page spec the API returns; the AI Control Tower
   and governance pages are purpose-built. All data is already scoped to the signed-in persona by the API. */
(function () {
  const { fmt, esc, tip } = Charts;
  const $ = (s, r) => (r || document).querySelector(s);
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const k in attrs || {}) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") e.className = v; else if (k === "html") e.innerHTML = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
    }
    kids.flat(9).forEach((c) => c != null && c !== false && e.appendChild(typeof c === "object" ? c : document.createTextNode(String(c))));
    return e;
  }
  const store = { get(k, d) { try { return localStorage.getItem("hip." + k) || d; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem("hip." + k, v); } catch (e) { /* private mode */ } } };
  const state = { persona: store.get("persona", "ceo"), props: [], preset: "30d", start: null, end: null, compare: "pp", meta: null };

  async function api(path, opts) {
    const r = await fetch("/api" + path, Object.assign({ headers: { "X-Persona": state.persona, "Content-Type": "application/json" } }, opts));
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
      throw Object.assign(new Error(msg), { status: r.status });
    }
    return r.json();
  }
  function toast(msg, err) { const t = $("#toast"); t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 4200); }

  // ---------------------------------------------------------------- dates and filters
  const iso = (d) => d.toISOString().slice(0, 10);
  const D = (s) => new Date(s + "T00:00:00Z");
  const addDays = (s, n) => iso(new Date(D(s).getTime() + n * 864e5));
  const longDate = (s) => (s ? Charts.dateLabel(String(s), "day", true) : "");
  const PRESETS = [["7d", "Last 7 days"], ["30d", "Last 30 days"], ["90d", "Last 90 days"], ["mtd", "Month to date"], ["qtd", "Quarter to date"], ["ytd", "Year to date"], ["12m", "Last 12 months"], ["custom", "Custom range"]];
  function range() {
    const a = state.meta.as_of, d = D(a);
    switch (state.preset) {
      case "7d": return [addDays(a, -6), a];
      case "90d": return [addDays(a, -89), a];
      case "mtd": return [a.slice(0, 8) + "01", a];
      case "qtd": return [iso(new Date(Date.UTC(d.getUTCFullYear(), Math.floor(d.getUTCMonth() / 3) * 3, 1))), a];
      case "ytd": return [a.slice(0, 4) + "-01-01", a];
      case "12m": return [addDays(a, -363), a];
      case "custom": return [state.start || addDays(a, -29), state.end || a];
      default: return [addDays(a, -29), a];
    }
  }
  function qs(extra) {
    const [s, e] = range(), p = new URLSearchParams(Object.assign({ start: s, end: e, compare: state.compare }, extra || {}));
    if (state.props.length) p.set("properties", state.props.join(","));
    return "?" + p.toString();
  }
  const propQs = () => (state.props.length ? "?properties=" + state.props.join(",") : "");
  const propName = (id) => (id === "ALL" ? "Group" : (state.meta.properties.find((p) => p.property_id === id) || {}).short_name || id);

  function buildFilters(showPeriod) {
    const f = $("#filters");
    f.innerHTML = "";
    const props = state.meta.properties, label = !state.props.length ? `All properties (${props.length})` : state.props.length === 1 ? propName(state.props[0]) : `${state.props.length} properties`;
    const menu = h("div", { class: "dd-menu", hidden: true },
      props.map((p) => h("label", null, h("input", { type: "checkbox", value: p.property_id, checked: state.props.includes(p.property_id), onchange: (ev) => {
        state.props = ev.target.checked ? state.props.concat(p.property_id) : state.props.filter((x) => x !== p.property_id);
        if (state.props.length === props.length) state.props = [];
      } }), p.short_name, h("span", { class: "sub" }, p.region))),
      h("div", { class: "row", style: "padding:6px 4px 2px" }, h("button", { class: "link-btn", onclick: () => { state.props = []; render(); } }, "Clear (all properties)"), h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: () => render() }, "Apply")));
    f.appendChild(h("div", { class: "ctl dd-wrap" }, "Property", h("button", { class: "dd", "aria-haspopup": "true", onclick: (ev) => { ev.stopPropagation(); menu.hidden = !menu.hidden; } }, label), menu));
    menu.addEventListener("click", (ev) => ev.stopPropagation());
    if (!showPeriod) return;
    f.appendChild(h("label", { class: "ctl" }, "Period", h("select", { onchange: (ev) => { state.preset = ev.target.value; render(); } }, PRESETS.map(([v, l]) => h("option", { value: v, selected: v === state.preset }, l)))));
    if (state.preset === "custom") {
      const [s, e] = range();
      f.appendChild(h("label", { class: "ctl" }, "From", h("input", { type: "date", value: s, min: state.meta.history_start, max: state.meta.as_of, onchange: (ev) => { state.start = ev.target.value; render(); } })));
      f.appendChild(h("label", { class: "ctl" }, "To", h("input", { type: "date", value: e, min: state.meta.history_start, max: state.meta.as_of, onchange: (ev) => { state.end = ev.target.value; render(); } })));
    }
    f.appendChild(h("label", { class: "ctl" }, "Compare with", h("select", { onchange: (ev) => { state.compare = ev.target.value; render(); } },
      h("option", { value: "pp", selected: state.compare === "pp" }, "Previous period"), h("option", { value: "ly", selected: state.compare === "ly" }, "Same period last year"))));
  }
  document.addEventListener("click", () => document.querySelectorAll(".dd-menu").forEach((m) => (m.hidden = true)));

  // ---------------------------------------------------------------- shared widgets
  const TAGS = { good: ["Within limit", "Normal", "Monitor", "Reconciled", "Passed", "Star", "Healthy", "Stable", "Deployed", "Succeeded", "Approved", "Implemented", "Resolved"],
    warn: ["Ends within 60 days", "Ends within 90 days", "Ends within 120 days", "Inspect at next room check", "Inspect before expiry", "Plan replacement", "Warning", "Warnings", "Plowhorse", "Puzzle", "Slow-moving", "Watch", "Monitored", "Medium", "Acknowledged", "Pending approval", "High", "Skipped"],
    crit: ["Over limit", "Anomaly", "Leak suspected", "Expired", "Ends within 30 days", "Service within 7 days", "Raise warranty claim and service now", "Inspect and claim before expiry", "Break", "Failed", "Failed checks", "Dog", "Stock-out risk", "At risk", "Critical", "Drift - retrain", "Rejected", "Escalated", "Open"], info: ["Info", "Low", "Archived"] };
  const tagClass = (t) => Object.keys(TAGS).find((k) => TAGS[k].includes(t)) || "plain";
  const tag = (t) => h("span", { class: "tag " + tagClass(t) }, t);
  function cell(v, f) {
    if (f === "tag") return v == null ? "" : tag(v);
    if (f === "text" || f == null) return v == null ? "–" : String(v);
    if (f === "date") return longDate(v);
    if (f === "datetime") return v ? String(v).replace("T", " ").slice(0, 16) : "–";
    if ((f === "signed_pct" || f === "signed_pct_bad") && v != null && Math.abs(v) >= 0.0005) return h("span", { class: "chg " + ((v > 0) === (f === "signed_pct") ? "good" : "bad") }, fmt(v, f));
    return fmt(v, f);
  }
  function table(cols, rows, opts) {
    opts = opts || {};
    let sortK = null, dir = 1;
    const wrap = h("div", { class: "tbl-wrap", style: opts.maxHeight ? `max-height:${opts.maxHeight}px;overflow-y:auto` : null });
    const draw = () => {
      const rs = sortK == null ? rows : rows.slice().sort((a, b) => { const x = a[sortK], y = b[sortK]; return (x == null) - (y == null) || (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir; });
      wrap.innerHTML = "";
      if (!rows.length) { wrap.appendChild(h("div", { class: "empty" }, opts.empty || "Nothing to show for this selection")); return; }
      const num = (c) => !["text", "tag", "date", "datetime", undefined, null].includes(c.fmt);
      wrap.appendChild(h("table", { class: "tbl" },
        h("thead", null, h("tr", null, cols.map((c) => h("th", { class: num(c) ? "n" : null, title: "Sort by " + c.label, onclick: () => { dir = sortK === c.key ? -dir : 1; sortK = c.key; draw(); } }, c.label + (sortK === c.key ? (dir > 0 ? " ▲" : " ▼") : ""))))),
        h("tbody", null, rs.map((r) => h("tr", { class: opts.onRow ? "click" : null, tabindex: opts.onRow ? 0 : null, onclick: opts.onRow ? () => opts.onRow(r) : null, onkeydown: opts.onRow ? (ev) => ev.key === "Enter" && opts.onRow(r) : null },
          cols.map((c) => h("td", { class: num(c) ? "n" : null }, c.render ? c.render(r) : cell(r[c.key], c.fmt))))))));
    };
    draw();
    return wrap;
  }
  function delta(k) {
    if (k.value == null || k.prev == null) return null;
    const pts = k.fmt.includes("pct"), d = pts ? k.value - k.prev : k.prev ? k.value / k.prev - 1 : null;
    if (d == null) return null;
    if (Math.abs(d) < 0.0005) return { txt: "No change", cls: "", sym: "–" };
    return { txt: (pts ? Math.abs(d * 100).toFixed(1) + " pts" : Math.abs(d * 100).toFixed(1) + "%"), cls: k.good === "neutral" ? "" : (d > 0) === (k.good === "up") ? "good" : "bad", sym: d > 0 ? "▲" : "▼" };
  }
  function kpiTile(k, prevLabel) {
    const d = delta(k);
    return h("div", { class: "kpi" },
      h("div", { class: "lbl" }, h("span", null, k.label), k.definition ? h("button", { class: "info", "aria-label": "Definition of " + k.label, onmousemove: (ev) => tip.show(`<div class="tt-h">${esc(k.label)}</div>${esc(k.definition)}`, ev), onmouseleave: () => tip.hide(),
        onclick: (ev) => tip.show(`<div class="tt-h">${esc(k.label)}</div>${esc(k.definition)}`, ev), onblur: () => tip.hide() }, "ⓘ") : null),
      h("div", { class: "val" }, fmt(k.value, k.fmt)),
      h("div", { class: "delta" }, d ? [h("span", { class: "chg " + d.cls }, d.sym + " " + d.txt), " vs " + (k.prev_label || prevLabel || "comparison")] : k.prev == null ? " " : ""));
  }
  function panel(title, span, body, opts) {
    opts = opts || {};
    const p = h("section", { class: "panel span-" + (span || 6) }, h("div", { class: "panel-head" }, h("h3", null, title), opts.action || null), body, opts.note ? h("div", { class: "note" }, opts.note) : null);
    return p;
  }
  const pending = [];
  function chartEl(type, spec) { const d = h("div"); pending.push([d, type, spec]); return d; }
  function flushCharts() { pending.splice(0).forEach(([d, type, spec]) => d.isConnected && Charts.draw(d, type, spec)); }
  const priTag = (p) => h("span", { class: "tag " + ({ Critical: "crit", High: "warn", Medium: "info", Low: "plain" }[p] || "plain") }, p);
  const sevTag = (s) => h("span", { class: "tag " + ({ Critical: "crit", Warning: "warn", Info: "info" }[s]) }, s);

  // ---------------------------------------------------------------- drawer
  function openDrawer(node) { const d = $("#drawer"); d.innerHTML = ""; d.appendChild(h("div", { class: "row", style: "justify-content:flex-end" }, h("button", { class: "btn", onclick: closeDrawer }, "Close"))); d.appendChild(node); d.hidden = false; $("#drawer-backdrop").hidden = false; d.scrollTop = 0; }
  function closeDrawer() { $("#drawer").hidden = true; $("#drawer-backdrop").hidden = true; }
  $("#drawer-backdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (ev) => ev.key === "Escape" && closeDrawer());
  const timeline = (rows) => (rows.length ? h("ul", { class: "timeline" }, rows.map((r) => h("li", null, h("b", null, r.action.charAt(0).toUpperCase() + r.action.slice(1)), r.to_status && r.to_status !== r.from_status ? ` → ${r.to_status}` : "",
    h("div", { class: "who" }, `${r.persona} · ${String(r.at).replace("T", " ")}`), r.comment ? h("div", null, r.comment) : null))) : h("div", { class: "empty", style: "padding:8px 0;text-align:left" }, "No actions recorded yet."));

  // ---------------------------------------------------------------- dashboards
  async function exportPanel(name, idx) {
    try {
      const r = await fetch(`/api/dashboard/${name}/export` + qs({ panel: idx }), { headers: { "X-Persona": state.persona } });
      if (!r.ok) throw new Error((await r.json()).detail);
      const a = h("a", { href: URL.createObjectURL(await r.blob()), download: (/filename="(.+?)"/.exec(r.headers.get("Content-Disposition") || "") || [])[1] || name + ".csv" });
      document.body.appendChild(a); a.click(); a.remove();
      toast("Export downloaded and recorded in the audit trail");
    } catch (e) { toast(e.message, true); }
  }
  async function dashboard(name, root) {
    const spec = await api(`/dashboard/${name}` + qs());
    setTitle(spec.title, spec.subtitle);
    const canExport = state.meta.persona.can_export, pr = spec.period;
    if (name === "executive") root.appendChild(h("div", { class: "hero", style: "background-image:url(/img/hero.jpg)" }, h("div", { class: "hero-body" }, h("div", { class: "eyebrow" }, "Enterprise control tower"),
      h("h2", null, state.meta.chain), h("p", null, `${state.meta.properties.length} properties in view · business date ${longDate(state.meta.as_of)} · reporting currency ${state.meta.reporting_currency}`))));
    root.appendChild(h("div", { class: "ctx" }, h("span", null, h("b", null, `${longDate(pr.start)} – ${longDate(pr.end)}`)), h("span", null, `compared with ${pr.compare_label} (${longDate(pr.compare_start)} – ${longDate(pr.compare_end)})`),
      h("span", null, spec.properties.length === state.meta.properties.length ? "All properties in your scope" : spec.properties.map(propName).join(", ")),
      !spec.panels.some((x) => x.type === "line") ? null : h("span", { class: "legend", style: "margin:0" }, h("span", null, h("i", { style: "border-color:var(--s1)" }), "Actual"), h("span", null, h("i", { class: "dash", style: "border-color:var(--ink-2)" }), "Budget"), h("span", null, h("i", { class: "dash", style: "border-color:var(--s2)" }), "Forecast")),
      h("span", { class: "spacer" }), canExport && spec.kpis.length ? h("button", { class: "link-btn", onclick: () => exportPanel(name, -1) }, "Export KPIs") : null));
    if (spec.kpis.length) root.appendChild(h("div", { class: "kpis" }, spec.kpis.map((k) => kpiTile(k, pr.compare_label))));
    if (spec.insights.length || spec.recommendations.length || spec.actions) {
      root.appendChild(h("div", { class: "insights" },
        h("div", { class: "ins" }, h("h3", null, "AI-generated insights"), h("ul", null, spec.insights.map((i) => h("li", null, h("span", { class: "sym " + i.tone, "aria-hidden": "true" }, i.tone === "good" ? "▲" : i.tone === "bad" ? "▼" : "●"), h("span", null, i.text),
          i.measure ? h("span", { class: "src" }, "Measure: " + i.measure) : null)))),
        spec.actions ? h("div", { class: "ins", style: "max-height:330px;overflow-y:auto" }, h("h3", null, `${spec.actions_title} (${spec.actions.length})`), spec.actions.length ? h("ul", null, spec.actions.map((a) => h("li", null, priTag(a.priority),
          h("button", { class: "link-btn", style: "color:inherit;text-align:left;white-space:normal", onclick: () => findingDrawer(a) }, a.title), a.value ? h("span", { class: "src" }, fmt(a.value, "usd") + " / yr") : null))) : h("div", { class: "empty", style: "padding:6px 0;text-align:left" }, "Nothing needs attention for this view.")) :
        h("div", { class: "ins" }, h("h3", null, "Recommended actions"), spec.recommendations.length ? h("ul", null, spec.recommendations.map((r) => h("li", null, priTag(r.priority),
          h("a", { href: "#/recommendations?id=" + r.recommendation_id, style: "color:inherit" }, r.title), h("span", { class: "src" }, fmt(r.estimated_impact_usd, "usd"))))) : h("div", { class: "empty", style: "padding:6px 0;text-align:left" }, "No open recommendations for this view."))));
    }
    const grid = h("div", { class: "grid" });
    spec.panels.forEach((p, i) => {
      if (p.type === "section") { grid.appendChild(h("div", { class: "span-12", style: "grid-column:span 12;margin-top:10px" }, h("h2", { style: "font-size:17px" }, p.title), h("div", { style: "color:var(--ink-2);font-size:12.5px" }, p.note))); return; }
      const action = canExport ? h("button", { class: "link-btn", onclick: () => exportPanel(name, i) }, "Export") : null;
      if (p.type === "questions") { grid.appendChild(panel(p.title, 12, questions(p.items, spec.counts, pr.compare_label), { note: p.note, action })); return; }
      let body;
      if (p.type === "table") body = table(p.columns, p.rows, { maxHeight: 420, onRow: p.drill ? (r) => { if (r.property_id) { state.props = [r.property_id]; location.hash = "#/" + p.drill; if (p.drill === name) render(); } } : null });
      else body = chartEl(p.type, Object.assign({ label: p.title }, p));
      grid.appendChild(panel(p.title, p.span, body, { note: p.note, action }));
    });
    root.appendChild(grid);
  }

  // CxO morning report: one card per management question, with its KPIs, the verdict, the answer and the action
  function questions(items, counts, cmpLabel) {
    const cls = { good: "good", watch: "warn", bad: "crit" }, label = { good: "On track", watch: "Watch", bad: "Act now" };
    const kpiBox = (k) => {
      const d = delta(k);
      return h("div", { class: "mq-kpi" },
        h("div", { class: "l", title: k.definition || "" }, k.label),
        h("div", { class: "v" }, fmt(k.value, k.fmt)),
        h("div", { class: "d" }, d ? [h("span", { class: "chg " + d.cls }, d.sym + " " + d.txt), " vs " + (k.prev_label || cmpLabel)] : k.prev == null ? "" : "No change"),
        k.target ? h("div", { class: "t " + (k.target.met === false ? "miss" : "met") }, `${k.target.met === false ? "Outside" : "Within"} target ${k.target.side === "max" ? "\u2264" : "\u2265"} ${fmt(k.target.value, k.fmt)}`) : null);
    };
    return h("div", null,
      counts ? h("div", { class: "mq-sum" }, ["bad", "watch", "good"].map((v) => h("span", { class: "tag " + cls[v] }, `${counts[v]} ${label[v].toLowerCase()}`))) : null,
      h("div", { class: "mq" }, items.map((i) => h("article", { class: "mq-card " + i.verdict, "aria-label": `${i.question} ${i.verdict_label}` },
        h("div", { class: "mq-head" }, h("span", { class: "mq-n", "aria-hidden": "true" }, i.n), h("h4", null, i.question), h("span", { class: "tag " + cls[i.verdict] }, i.verdict_label)),
        h("div", { class: "mq-kpis" }, i.kpis.map(kpiBox)),
        h("p", { class: "mq-read" }, i.read),
        h("div", { class: "mq-act" }, h("b", null, "What to do  "), i.action),
        h("a", { class: "mq-link", href: i.link }, "Open the detail \u203A")))));
  }

  function findingDrawer(a) {
    openDrawer(h("div", null, h("div", { class: "row" }, priTag(a.priority), h("span", { class: "tag plain" }, a.category)), h("h2", { style: "margin-top:8px" }, a.title), h("p", { style: "margin-top:10px" }, a.detail),
      h("dl", { class: "kv", style: "margin-top:12px" }, h("dt", null, "Property"), h("dd", null, a.property), h("dt", null, "Category"), h("dd", null, a.category),
        a.value ? [h("dt", null, "Estimated annual value"), h("dd", null, fmt(a.value, "usd"))] : null, a.due_date ? [h("dt", null, "Date"), h("dd", null, longDate(a.due_date))] : null),
      h("div", { class: "note", style: "margin-top:14px" }, "Produced by the engineering and energy models from synthetic data. Values are estimates based on stated assumptions.")));
  }

  // ---------------------------------------------------------------- AI Control Tower: overview
  async function towerOverview(root) {
    setTitle("Enterprise Intelligence Overview", "Forecast outlook, exceptions, opportunities and risk across the estate");
    const o = await api("/tower/overview" + propQs());
    const cls = { Healthy: "good", Watch: "warn", "At risk": "crit" }[o.health];
    root.appendChild(h("div", { class: "banner " + cls }, tag(o.health), h("div", null, h("b", null, "Enterprise performance health: " + o.health), h("div", { style: "color:var(--ink-2);font-size:12.5px" },
      `${o.alert_counts.Critical} critical and ${o.alert_counts.Warning} warning exceptions open · ${o.top_recommendations.length ? "recommendations are waiting for a decision" : "no recommendations waiting"} · business date ${longDate(o.as_of)}`))));
    root.appendChild(h("div", { class: "kpis" }, o.tiles.filter((t) => t.value != null).map((t) => kpiTile(t))));
    const grid = h("div", { class: "grid" });
    grid.appendChild(panel("Revenue: last 60 days and 90-day forecast", 8, chartEl("line", { x: o.revenue_chart.x, fmt: "usd", series: [{ name: "Actual", kind: "actual", data: o.revenue_chart.actual }, { name: "Forecast", kind: "forecast", data: o.revenue_chart.forecast, lo: o.revenue_chart.lo, hi: o.revenue_chart.hi }] }),
      { note: "Shaded area is the 80% forecast interval." }));
    grid.appendChild(panel("Business opportunities awaiting approval", 4, chartEl("bar", { categories: o.opportunity_by_vertical.map((x) => x.vertical), series: [{ name: "Estimated impact", data: o.opportunity_by_vertical.map((x) => x.impact) }], fmt: "usd", horizontal: true })));
    grid.appendChild(panel("High-priority recommendations", 6, h("div", { class: "cards" }, o.top_recommendations.length ? o.top_recommendations.map(recCard) : h("div", { class: "empty" }, "Nothing awaiting approval.")),
      { action: h("a", { class: "link-btn", href: "#/recommendations" }, "All recommendations") }));
    grid.appendChild(panel("Critical business exceptions", 6, h("div", { class: "cards" }, o.critical_alerts.length ? o.critical_alerts.map(alertCard) : h("div", { class: "empty" }, "No critical exceptions open.")),
      { action: h("a", { class: "link-btn", href: "#/alerts" }, "All alerts") }));
    const cols = [{ key: "property", label: "Property", fmt: "text" }, { key: "forecast_occupancy", label: "Forecast occupancy (30 days)", fmt: "pct" }, { key: "occupancy_last_year", label: "Last year", fmt: "pct" },
      { key: "forecast_revenue", label: "Forecast revenue", fmt: "usd" }, { key: "revenue_vs_last_year", label: "vs last year", fmt: "signed_pct" }];
    if (o.risk_indicators.length && "forecast_gop_margin" in o.risk_indicators[0]) cols.push({ key: "forecast_gop_margin", label: "Forecast GOP margin", fmt: "pct" });
    cols.push({ key: "open_critical_alerts", label: "Critical alerts", fmt: "num" }, { key: "status", label: "Risk status", fmt: "tag" });
    grid.appendChild(panel("Risk indicators by property", 8, table(cols, o.risk_indicators, { onRow: (r) => { state.props = [r.property_id]; location.hash = "#/forecasting"; } }), { note: "Select a property to open its forecasts." }));
    grid.appendChild(panel("Model health", 4, h("div", null, h("dl", { class: "kv" }, Object.keys(o.model_health).map((k) => [h("dt", null, tag(k)), h("dd", null, o.model_health[k] + " model(s)")])),
      h("div", { class: "note" }, "Drift is measured with the population stability index on recent prediction errors.")), { action: h("a", { class: "link-btn", href: "#/models" }, "Model monitoring") }));
    root.appendChild(grid);
  }

  // ---------------------------------------------------------------- forecasting workspace
  const fc = { metric: null, key: null, horizon: 30, adj: 0 };
  async function forecasting(root) {
    setTitle("Forecasting Workspace", "Actual versus forecast, confidence intervals, accuracy and scenarios");
    const opts = await api("/tower/forecast/options" + propQs());
    if (!opts.length) { root.appendChild(h("div", { class: "empty" }, "No forecasts are available for your properties and business verticals.")); return; }
    const m = opts.find((o) => o.metric === fc.metric) || opts.find((o) => o.metric === "hotel_rooms_sold") || opts[0];
    fc.metric = m.metric;
    const keyOf = (s) => s.property_id + "|" + s.entity_id;
    const s = m.series.find((x) => keyOf(x) === fc.key) || m.series.find((x) => x.entity_id === "ALL") || m.series[0];
    fc.key = keyOf(s);
    const d = await api(`/tower/forecast?metric=${m.metric}&property_id=${s.property_id}&entity_id=${encodeURIComponent(s.entity_id)}&horizon=${fc.horizon}`);
    const u = d.unit === "USD" ? "usd" : "num";
    root.appendChild(h("div", { class: "row", style: "margin-bottom:14px" },
      h("label", { class: "ctl" }, "Forecast", h("select", { onchange: (ev) => { fc.metric = ev.target.value; fc.key = null; render(); } }, opts.map((o) => h("option", { value: o.metric, selected: o.metric === m.metric }, `${o.vertical}: ${o.name}`)))),
      h("label", { class: "ctl" }, "Property and series", h("select", { onchange: (ev) => { fc.key = ev.target.value; render(); } }, m.series.map((x) => h("option", { value: keyOf(x), selected: keyOf(x) === fc.key }, `${x.property} – ${x.entity_name}`)))),
      h("div", { class: "ctl" }, "Horizon", h("div", { class: "seg" }, [30, 60, 90].map((n) => h("button", { class: fc.horizon === n ? "on" : "", onclick: () => { fc.horizon = n; render(); } }, n + " days")))),
      h("label", { class: "ctl" }, `Scenario: demand ${fc.adj > 0 ? "+" : ""}${fc.adj}%`, h("input", { type: "range", min: -20, max: 20, step: 5, value: fc.adj, style: "width:170px;padding:0", onchange: (ev) => { fc.adj = +ev.target.value; render(); } }))));
    const hist = {}; d.history.x.forEach((x, i) => (hist[x] = d.history.y[i]));
    const bt = {}; d.backtest.x.forEach((x, i) => (bt[x] = d.backtest.forecast[i]));
    const hx = d.history.x.slice(-100), xs = hx.concat(d.future.x), pad = hx.map(() => null), k = 1 + fc.adj / 100;
    const cap = d.future.capacity.some((v) => v != null) ? d.future.capacity : null;
    const series = [{ name: "Actual", kind: "actual", data: hx.map((x) => hist[x]).concat(d.future.x.map(() => null)) },
      { name: "Earlier forecast (backtest)", kind: "compare", data: hx.map((x) => (x in bt ? bt[x] : null)).concat(d.future.x.map(() => null)) },
      { name: "Forecast", kind: "forecast", data: pad.concat(d.future.forecast), lo: pad.concat(d.future.lo), hi: pad.concat(d.future.hi) },
      { name: "Same days last year", kind: "budget", data: pad.concat(d.future.last_year) }];
    if (cap) series.push({ name: "Capacity", kind: "capacity", data: pad.concat(cap) });
    if (fc.adj) series.push({ name: `Scenario (${fc.adj > 0 ? "+" : ""}${fc.adj}% demand)`, kind: "actual", data: pad.concat(d.future.forecast.map((v, i) => (cap && cap[i] != null ? Math.min(v * k, cap[i]) : v * k))) });
    const t = d.totals, a = d.accuracy, avg = d.unit === "USD" && d.metric === "restaurant_spend_per_guest";
    const tot = (v) => (v == null ? null : avg ? v / d.future.x.length : v), vsLy = (v) => (t.last_year ? v / t.last_year - 1 : null);
    root.appendChild(h("div", { class: "kpis" },
      kpiTile({ label: `Forecast, next ${d.horizon} days${avg ? " (daily average)" : ""}`, value: tot(t.forecast), prev: tot(t.last_year), prev_label: "same days last year", fmt: avg ? "usd1" : u, good: m.metric === "finance_opex" ? "down" : "up" }),
      kpiTile({ label: "80% interval, low", value: tot(t.lo), fmt: avg ? "usd1" : u, good: "neutral" }), kpiTile({ label: "80% interval, high", value: tot(t.hi), fmt: avg ? "usd1" : u, good: "neutral" }),
      t.capacity ? kpiTile({ label: "Forecast share of capacity", value: t.forecast / t.capacity, fmt: "pct", good: "neutral" }) : null,
      kpiTile({ label: "Forecast error (WAPE, 90-day backtest)", value: a.wape, prev: a.wape_naive, prev_label: "seasonal-naive baseline", fmt: "pct", good: "down", definition: "Weighted absolute percentage error: sum of absolute errors divided by sum of actuals over the last 90 days, forecast made 90 days ago." }),
      kpiTile({ label: "Interval coverage in backtest", value: a.interval_coverage, fmt: "pct", good: "neutral", definition: "Share of backtest days whose actual value fell inside the 80% interval. Close to 80% means the interval is well calibrated." })));
    const grid = h("div", { class: "grid" });
    grid.appendChild(panel(`${d.entity_name}: actual versus forecast (${d.unit})`, 12, chartEl("line", { x: xs, series, fmt: avg ? "usd1" : u, height: 300 }),
      { note: `Model: ${d.model.model_name} v${d.model.version} · champion for this series: ${d.champion} · generated ${String(d.generated_at).replace("T", " ").slice(0, 16)}. The grey line is the forecast made 90 days ago, shown against what then happened.` }));
    grid.appendChild(panel("Historical forecast accuracy by week", 6, chartEl("bar", { categories: d.weekly_accuracy.map((w) => Charts.dateLabel(String(w.week).slice(0, 10))), series: [{ name: "Weekly error (WAPE)", data: d.weekly_accuracy.map((w) => w.wape) }], fmt: "pct" }),
      { note: "Each bar is the backtest error for one week; later weeks are further from the forecast date." }));
    grid.appendChild(panel("Accuracy by forecast horizon", 6, table([{ key: "h", label: "Horizon", fmt: "text" }, { key: "wape", label: "Error (WAPE)", fmt: "pct" }, { key: "note", label: "", fmt: "text" }],
      [{ h: "Days 1-30", wape: a.wape_h30, note: "" }, { h: "Days 31-60", wape: a.wape_h60, note: "" }, { h: "Days 61-90", wape: a.wape_h90, note: "" }, { h: "All 90 days", wape: a.wape, note: `Bias ${fmt(a.bias, "signed_pct")} (positive = over-forecast)` },
        { h: "Gradient boosting", wape: a.wape_gbm, note: a.champion === "Gradient boosting" ? "Champion" : "" }, { h: "Seasonal naive", wape: a.wape_naive, note: a.champion === "Seasonal naive" ? "Champion" : "Challenger" }])));
    const sc = [["Base forecast", t.forecast], ["Optimistic (upper 80% bound)", t.hi], ["Pessimistic (lower 80% bound)", t.lo]];
    if (fc.adj) sc.push([`Custom: demand ${fc.adj > 0 ? "+" : ""}${fc.adj}%`, series.find((x) => x.name.startsWith("Scenario")).data.reduce((p, v) => p + (v || 0), 0)]);
    grid.appendChild(panel("Scenario comparison", 6, table([{ key: "s", label: "Scenario", fmt: "text" }, { key: "v", label: avg ? "Daily average" : `Total, ${d.horizon} days`, fmt: avg ? "usd1" : u }, { key: "ly", label: "vs last year", fmt: "signed_pct" }, { key: "cap", label: "Share of capacity", fmt: "pct" }],
      sc.map(([s2, v]) => ({ s: s2, v: tot(v), ly: vsLy(v), cap: t.capacity ? v / t.capacity : null }))), { note: "Move the demand slider to test a custom scenario; it is capped at capacity where one applies." }));
    grid.appendChild(panel("Model and related recommendations", 6, h("div", null, h("dl", { class: "kv" }, h("dt", null, "Objective"), h("dd", null, d.model.business_objective), h("dt", null, "Algorithm"), h("dd", null, d.model.algorithm),
      h("dt", null, "Features"), h("dd", null, d.model.features.replace(/_/g, " ")), h("dt", null, "Drift status"), h("dd", null, tag(d.model.drift_status))),
      d.related_recommendations.length ? h("div", { class: "cards", style: "margin-top:10px" }, d.related_recommendations.map(recCard)) : h("div", { class: "note" }, "No open recommendations draw on this forecast."))));
    root.appendChild(grid);
  }

  // ---------------------------------------------------------------- recommendations
  const rf = { status: "Pending approval", vertical: "", priority: "" };
  function recCard(r) {
    return h("button", { class: "card", onclick: () => recDrawer(r.recommendation_id) }, h("div", { class: "t" }, r.title),
      h("div", { class: "m" }, priTag(r.priority), tag(r.approval_status), h("span", null, r.business_vertical + " · " + r.property), h("span", null, "Impact " + fmt(r.estimated_impact_usd, "usd")), h("span", null, "Confidence " + fmt(r.confidence, "pct"))));
  }
  async function recommendations(root, params) {
    setTitle("Recommendation Engine", "Prioritised, explainable actions that need a business decision");
    const all = await api("/tower/recommendations" + propQs());
    const sum = (s) => all.filter((r) => r.approval_status === s);
    root.appendChild(h("div", { class: "kpis" },
      kpiTile({ label: "Awaiting approval", value: sum("Pending approval").length, fmt: "num", good: "neutral" }), kpiTile({ label: "Opportunity awaiting approval", value: sum("Pending approval").reduce((a, r) => a + r.estimated_impact_usd, 0), fmt: "usd", good: "neutral" }),
      kpiTile({ label: "Approved", value: sum("Approved").length, fmt: "num", good: "neutral" }), kpiTile({ label: "Implemented", value: sum("Implemented").length, fmt: "num", good: "neutral" }),
      kpiTile({ label: "Rejected", value: sum("Rejected").length, fmt: "num", good: "neutral" }), kpiTile({ label: "Realised impact recorded", value: all.reduce((a, r) => a + (r.realised_impact_usd || 0), 0), fmt: "usd", good: "neutral" })));
    const sel = (label, key, values) => h("label", { class: "ctl" }, label, h("select", { onchange: (ev) => { rf[key] = ev.target.value; render(); } }, h("option", { value: "" }, "All"), values.map((v) => h("option", { value: v, selected: rf[key] === v }, v))));
    root.appendChild(h("div", { class: "row", style: "margin-bottom:12px" }, sel("Status", "status", ["Pending approval", "Approved", "Implemented", "Rejected"]), sel("Business domain", "vertical", [...new Set(all.map((r) => r.business_vertical))]),
      sel("Priority", "priority", ["Critical", "High", "Medium", "Low"])));
    const rows = all.filter((r) => (!rf.status || r.approval_status === rf.status) && (!rf.vertical || r.business_vertical === rf.vertical) && (!rf.priority || r.priority === rf.priority));
    root.appendChild(panel(`${rows.length} recommendation(s)`, 12, table([
      { key: "recommendation_id", label: "ID", fmt: "text" }, { key: "priority", label: "Priority", render: (r) => priTag(r.priority) }, { key: "title", label: "Recommendation", fmt: "text" }, { key: "business_vertical", label: "Domain", fmt: "text" },
      { key: "property", label: "Property", fmt: "text" }, { key: "estimated_impact_usd", label: "Est. impact", fmt: "usd" }, { key: "confidence", label: "Confidence", fmt: "pct" }, { key: "recommendation_date", label: "Date", fmt: "date" },
      { key: "approval_status", label: "Status", fmt: "tag" }, { key: "owner", label: "Owner", fmt: "text" }], rows, { onRow: (r) => recDrawer(r.recommendation_id), empty: "No recommendations match these filters." }),
      { note: "Priority combines estimated impact and model confidence. Nothing is executed automatically: every recommendation needs an authorised approval." }));
    if (params.get("id")) recDrawer(params.get("id"));
  }
  async function recDrawer(id) {
    let all, hist;
    try { all = await api("/tower/recommendations"); hist = await api(`/tower/recommendations/${id}/history`); } catch (e) { toast(e.message, true); return; }
    const r = all.find((x) => x.recommendation_id === id);
    if (!r) { toast("That recommendation is outside your access scope", true); return; }
    const can = state.meta.persona.can_approve, comment = h("textarea", { placeholder: "Comment or reason (required to reject)", "aria-label": "Comment" });
    const owner = h("input", { class: "field", value: r.owner, "aria-label": "Owner" }), realised = h("input", { class: "field", type: "number", placeholder: "Realised impact in USD (optional)", "aria-label": "Realised impact" });
    const act = async (action) => {
      try { await api(`/tower/recommendations/${id}/action`, { method: "POST", body: JSON.stringify({ action, comment: comment.value, owner: owner.value, realised_impact_usd: realised.value }) }); toast("Recorded: " + action); closeDrawer(); await render(); recDrawer(id); }
      catch (e) { toast(e.message, true); }
    };
    const st = r.approval_status, btn = (label, action, cls, on) => h("button", { class: "btn " + (cls || ""), disabled: !on, onclick: () => act(action) }, label);
    openDrawer(h("div", null,
      h("div", { class: "row" }, priTag(r.priority), tag(st), h("span", { style: "color:var(--muted)" }, r.recommendation_id)), h("h2", { style: "margin-top:8px" }, r.title),
      h("dl", { class: "kv", style: "margin-top:10px" }, h("dt", null, "Business domain"), h("dd", null, r.business_vertical), h("dt", null, "Property"), h("dd", null, r.property), h("dt", null, "Scenario"), h("dd", null, r.scenario),
        h("dt", null, "Recommendation date"), h("dd", null, longDate(r.recommendation_date)), h("dt", null, "Assigned owner"), h("dd", null, r.owner),
        h("dt", null, "Estimated impact"), h("dd", null, h("b", null, fmt(r.estimated_impact_usd, "usd")), h("div", { style: "color:var(--ink-2);font-size:12px" }, r.impact_basis)),
        h("dt", null, "Confidence"), h("dd", null, h("div", { class: "row" }, h("div", { class: "meter", style: "width:120px" }, h("i", { style: `width:${Math.round(r.confidence * 100)}%` })), fmt(r.confidence, "pct"))),
        r.decided_by && st !== "Pending approval" ? [h("dt", null, "Decision"), h("dd", null, `${st} by ${r.decided_by} on ${String(r.decided_at).replace("T", " ")}`)] : null,
        r.realised_impact_usd != null ? [h("dt", null, "Realised impact"), h("dd", null, fmt(r.realised_impact_usd, "usd"))] : null),
      h("h4", null, "Problem or opportunity"), h("p", null, r.problem), h("h4", null, "Recommended action"), h("p", null, r.recommended_action),
      h("h4", null, "Supporting data and model output"), table([{ key: "label", label: "Measure", fmt: "text" }, { key: "value", label: "Value", fmt: "text" }], r.supporting_data),
      r.forecast_link ? h("p", { style: "margin-top:8px" }, h("a", { class: "link-btn", href: "#/forecasting", onclick: () => { fc.metric = r.forecast_link.metric; fc.key = r.forecast_link.property_id + "|" + r.forecast_link.entity_id; closeDrawer(); } }, "Open the underlying forecast")) : null,
      h("h4", null, "Decision"), comment, h("div", { class: "row", style: "margin-top:8px" }, btn("Approve", "approve", "primary", can && st === "Pending approval"), btn("Reject", "reject", "danger", can && st === "Pending approval"),
        btn("Mark implemented", "implement", "", can && st === "Approved"), btn("Reopen", "reopen", "", can && (st === "Approved" || st === "Rejected")), btn("Add comment", "comment", "", true)),
      st === "Approved" ? h("div", { style: "margin-top:8px" }, realised) : null,
      !can ? h("div", { class: "note" }, "Your role can comment but cannot approve or reject recommendations.") : null,
      h("h4", null, "Owner"), h("div", { class: "row" }, h("div", { style: "flex:1" }, owner), btn("Reassign", "assign", "", true)),
      h("h4", null, "Action history"), timeline(hist)));
  }

  // ---------------------------------------------------------------- alerts
  const af = { severity: "", status: "active" };
  function alertCard(a) {
    return h("button", { class: "card", onclick: () => alertDrawer(a.alert_id) }, h("div", { class: "t" }, a.title), h("div", { class: "m" }, sevTag(a.severity), tag(a.status), h("span", null, a.category + " · " + a.property), h("span", null, a.assignee)));
  }
  async function alerts(root) {
    setTitle("Alert Management", "Exceptions raised by data quality rules, anomaly detectors and forecasts");
    const all = await api("/tower/alerts" + propQs()), active = all.filter((a) => a.status !== "Resolved");
    root.appendChild(h("div", { class: "kpis" }, ["Critical", "Warning", "Info"].map((s) => kpiTile({ label: s + " alerts open", value: active.filter((a) => a.severity === s).length, fmt: "num", good: "neutral" })),
      kpiTile({ label: "Unacknowledged", value: all.filter((a) => a.status === "Open").length, fmt: "num", good: "neutral" }), kpiTile({ label: "Escalated", value: all.filter((a) => a.status === "Escalated").length, fmt: "num", good: "neutral" }),
      kpiTile({ label: "Resolved", value: all.filter((a) => a.status === "Resolved").length, fmt: "num", good: "neutral" })));
    root.appendChild(h("div", { class: "row", style: "margin-bottom:12px" },
      h("div", { class: "seg" }, [["", "All severities"], ["Critical", "Critical"], ["Warning", "Warning"], ["Info", "Informational"]].map(([v, l]) => h("button", { class: af.severity === v ? "on" : "", onclick: () => { af.severity = v; render(); } }, l))),
      h("div", { class: "seg" }, [["active", "Active"], ["Resolved", "Resolved"], ["", "All"]].map(([v, l]) => h("button", { class: af.status === v ? "on" : "", onclick: () => { af.status = v; render(); } }, l)))));
    const rows = all.filter((a) => (!af.severity || a.severity === af.severity) && (af.status === "" || (af.status === "active" ? a.status !== "Resolved" : a.status === af.status)));
    root.appendChild(panel(`${rows.length} alert(s)`, 12, table([{ key: "severity", label: "Severity", render: (a) => sevTag(a.severity) }, { key: "title", label: "Alert", fmt: "text" }, { key: "category", label: "Category", fmt: "text" },
      { key: "property", label: "Property", fmt: "text" }, { key: "detected_at", label: "Detected", fmt: "datetime" }, { key: "status", label: "Status", fmt: "tag" }, { key: "assignee", label: "Assigned to", fmt: "text" }, { key: "source", label: "Source", fmt: "text" }],
      rows, { onRow: (a) => alertDrawer(a.alert_id), empty: "No alerts match these filters." })));
  }
  async function alertDrawer(id) {
    let all, hist;
    try { all = await api("/tower/alerts"); hist = await api(`/tower/alerts/${id}/history`); } catch (e) { toast(e.message, true); return; }
    const a = all.find((x) => x.alert_id === id);
    if (!a) return;
    const comment = h("textarea", { placeholder: "Comment (required to resolve)", "aria-label": "Comment" }), who = h("input", { class: "field", value: a.assignee, "aria-label": "Assignee" });
    const act = async (action) => {
      try { await api(`/tower/alerts/${id}/action`, { method: "POST", body: JSON.stringify({ action, comment: comment.value, assignee: who.value }) }); toast("Recorded: " + action); closeDrawer(); await render(); alertDrawer(id); }
      catch (e) { toast(e.message, true); }
    };
    const s = a.status, btn = (label, action, cls, on) => h("button", { class: "btn " + (cls || ""), disabled: !on, onclick: () => act(action) }, label);
    openDrawer(h("div", null, h("div", { class: "row" }, sevTag(a.severity), tag(s), h("span", { style: "color:var(--muted)" }, a.alert_id)), h("h2", { style: "margin-top:8px" }, a.title), h("p", { style: "margin-top:8px" }, a.description),
      h("dl", { class: "kv", style: "margin-top:10px" }, h("dt", null, "Category"), h("dd", null, a.category), h("dt", null, "Business domain"), h("dd", null, a.business_vertical), h("dt", null, "Property"), h("dd", null, a.property),
        h("dt", null, "Detected"), h("dd", null, String(a.detected_at).replace("T", " ")), h("dt", null, "Source"), h("dd", null, a.source), h("dt", null, "Assigned to"), h("dd", null, a.assignee),
        a.acknowledged_by ? [h("dt", null, "Acknowledged"), h("dd", null, `${a.acknowledged_by}, ${String(a.acknowledged_at).replace("T", " ")}`)] : null, a.escalated_to ? [h("dt", null, "Escalated to"), h("dd", null, a.escalated_to)] : null,
        a.resolved_by ? [h("dt", null, "Resolved"), h("dd", null, `${a.resolved_by}, ${String(a.resolved_at).replace("T", " ")}`), h("dt", null, "Resolution"), h("dd", null, a.resolution_comment)] : null),
      h("h4", null, "Action"), comment, h("div", { class: "row", style: "margin-top:8px" }, btn("Acknowledge", "acknowledge", "primary", s === "Open" || s === "Escalated"), btn("Escalate", "escalate", "", s === "Open" || s === "Acknowledged"),
        btn("Resolve", "resolve", "", s !== "Resolved"), btn("Reopen", "reopen", "", s === "Resolved"), btn("Add comment", "comment", "", true)),
      h("h4", null, "Assignment"), h("div", { class: "row" }, h("div", { style: "flex:1" }, who), btn("Reassign", "assign", "", true)), h("h4", null, "Action history"), timeline(hist)));
  }

  // ---------------------------------------------------------------- model monitoring
  async function models(root) {
    setTitle("AI Model Monitoring", "Versions, accuracy, drift, data quality and deployment status");
    const d = await api("/tower/models");
    const metric = (m) => (/WAPE|share/i.test(m.metric_name) ? fmt(m.metric_value, "pct") : fmt(m.metric_value, "dec2"));
    const detail = (m) => openDrawer(h("div", null, h("div", { class: "row" }, tag(m.deployment_status), tag(m.drift_status), h("span", { style: "color:var(--muted)" }, `${m.model_id} v${m.version}`)), h("h2", { style: "margin-top:8px" }, m.model_name),
      h("p", { style: "margin-top:6px" }, m.business_objective),
      h("dl", { class: "kv", style: "margin-top:10px" }, h("dt", null, "Business domain"), h("dd", null, m.business_vertical), h("dt", null, "Type"), h("dd", null, m.model_type), h("dt", null, "Algorithm"), h("dd", null, m.algorithm),
        h("dt", null, "Features"), h("dd", null, m.features.replace(/_/g, " ")), h("dt", null, "Training data"), h("dd", null, `${fmt(m.training_rows, "num")} rows, ${longDate(m.training_from)} to ${longDate(m.training_to)}`),
        h("dt", null, m.metric_name), h("dd", null, metric(m)), h("dt", null, m.baseline_name), h("dd", null, /WAPE|share/i.test(m.metric_name) ? fmt(m.baseline_value, "pct") : fmt(m.baseline_value, "dec2")),
        h("dt", null, "Drift score (PSI)"), h("dd", null, fmt(m.drift_score, "dec2") + " – " + m.drift_status), h("dt", null, "Data quality"), h("dd", null, tag(m.data_quality_status)),
        h("dt", null, "Last training"), h("dd", null, String(m.trained_at).replace("T", " ").slice(0, 16)), h("dt", null, "Last inference"), h("dd", null, String(m.last_inference_at).replace("T", " ").slice(0, 16)),
        h("dt", null, "Approved by"), h("dd", null, m.approved_by), h("dt", null, "Pipeline run"), h("dd", null, m.run_id)),
      h("h4", null, "Explainability"), h("p", null, m.explainability),
      h("h4", null, "Version history"), table([{ key: "version", label: "Version", fmt: "text" }, { key: "trained_at", label: "Trained", fmt: "datetime" }, { key: "metric_value", label: "Metric", fmt: "dec2" }, { key: "drift_score", label: "Drift", fmt: "dec2" },
        { key: "deployment_status", label: "Status", fmt: "tag" }], d.history.filter((x) => x.model_id === m.model_id)),
      d.series_accuracy.some((x) => x.model_id === m.model_id) ? [h("h4", null, "Accuracy by series (your properties)"), table([{ key: "property", label: "Property", fmt: "text" }, { key: "entity_name", label: "Series", fmt: "text" }, { key: "wape", label: "WAPE", fmt: "pct" },
        { key: "wape_naive", label: "Naive", fmt: "pct" }, { key: "bias", label: "Bias", fmt: "signed_pct" }, { key: "champion", label: "Champion", fmt: "text" }], d.series_accuracy.filter((x) => x.model_id === m.model_id), { maxHeight: 320 })] : null));
    const dep = d.deployed, n = (s) => dep.filter((m) => m.drift_status === s).length;
    root.appendChild(h("div", { class: "kpis" }, kpiTile({ label: "Deployed models", value: dep.length, fmt: "num", good: "neutral" }), kpiTile({ label: "Stable", value: n("Stable"), fmt: "num", good: "neutral" }),
      kpiTile({ label: "On watch", value: n("Watch"), fmt: "num", good: "neutral" }), kpiTile({ label: "Drift: retraining needed", value: n("Drift - retrain"), fmt: "num", good: "neutral" }),
      kpiTile({ label: "With data quality warnings", value: dep.filter((m) => m.data_quality_status !== "Passed").length, fmt: "num", good: "neutral" })));
    root.appendChild(panel("Deployed models", 12, table([{ key: "model_name", label: "Model", fmt: "text" }, { key: "version", label: "Version", fmt: "text" }, { key: "business_vertical", label: "Domain", fmt: "text" }, { key: "model_type", label: "Type", fmt: "text" },
      { key: "metric_value", label: "Accuracy metric", render: (m) => `${metric(m)} ${m.metric_name.split(" (")[0]}` }, { key: "forecast_error", label: "Forecast error", render: (m) => (m.model_type === "Forecast" ? fmt(m.forecast_error, "pct") : fmt(m.forecast_error, "dec2")) },
      { key: "drift_status", label: "Model drift", fmt: "tag" }, { key: "data_quality_status", label: "Data quality", fmt: "tag" }, { key: "trained_at", label: "Last training", fmt: "datetime" }, { key: "last_inference_at", label: "Last inference", fmt: "datetime" },
      { key: "deployment_status", label: "Deployment", fmt: "tag" }], dep, { onRow: detail }), { note: "Select a model for its objective, features, training data, explainability and version history. A drift status of 'Drift - retrain' raises an alert and queues retraining for review." }));
  }

  // ---------------------------------------------------------------- data platform and governance
  async function platform(root) {
    setTitle("Data Platform & Governance", "Ingestion, medallion layers, data quality, lineage, catalogue and privacy");
    const d = await api("/platform"), L = {}; d.layers.forEach((l) => (L[l.layer] = l));
    const ok = d.ingestion.filter((s) => s.status === "Succeeded").length, pass = d.dq.filter((r) => r.status === "Passed").length;
    root.appendChild(h("div", { class: "flow" },
      h("div", { class: "step" }, h("b", null, "Sources"), h("div", { class: "big" }, d.ingestion.length), h("small", null, `feeds · ${ok} succeeded in the last run`)),
      ["bronze", "silver", "gold", "ai"].map((k) => h("div", { class: "step" }, h("b", null, { bronze: "Bronze: raw", silver: "Silver: standardised", gold: "Gold: data products", ai: "AI: scores and registry" }[k]), h("div", { class: "big" }, L[k] ? fmt(L[k].row_count, "num") : "0"),
        h("small", null, `rows in ${L[k] ? L[k].tables : 0} tables`)))));
    const grid = h("div", { class: "grid" });
    grid.appendChild(panel("Source ingestion monitor (latest run per feed)", 12, table([{ key: "source_system", label: "Source system", fmt: "text" }, { key: "source_id", label: "Feed", fmt: "text" }, { key: "domain", label: "Domain", fmt: "text" }, { key: "connector", label: "Connector", fmt: "text" },
      { key: "mode", label: "Load mode", fmt: "text" }, { key: "frequency_minutes", label: "Target frequency", render: (r) => (r.frequency_minutes >= 1440 ? "Daily" : r.frequency_minutes >= 60 ? "Hourly" : r.frequency_minutes + " min") },
      { key: "status", label: "Status", fmt: "tag" }, { key: "attempts", label: "Attempts", fmt: "num" }, { key: "rows_loaded", label: "Rows loaded", fmt: "num" }, { key: "rows_quarantined", label: "Quarantined", fmt: "num" },
      { key: "watermark_to", label: "Watermark", fmt: "text" }, { key: "finished_at", label: "Finished", fmt: "datetime" }], d.ingestion, { maxHeight: 380 }),
      { note: "Incremental feeds load only rows past the stored watermark; snapshot feeds are fully refreshed. Rows without a business key go to quarantine." }));
    grid.appendChild(panel(`Data quality rules: ${pass} of ${d.dq.length} passed`, 7, table([{ key: "rule_id", label: "Rule", fmt: "text" }, { key: "description", label: "Check", fmt: "text" }, { key: "severity", label: "Severity", fmt: "text" }, { key: "failed", label: "Failed", fmt: "num" },
      { key: "pass_rate", label: "Pass rate", fmt: "pct2" }, { key: "status", label: "Result", fmt: "tag" }], d.dq, { maxHeight: 380 })));
    grid.appendChild(panel("Revenue reconciliation breaks (operations to ledger)", 5, table([{ key: "fiscal_period", label: "Period", fmt: "text" }, { key: "property", label: "Property", fmt: "text" }, { key: "business_vertical", label: "Vertical", fmt: "text" },
      { key: "operational_local", label: "Operational", fmt: "num" }, { key: "ledger_local", label: "Ledger", fmt: "num" }, { key: "difference_pct", label: "Difference", fmt: "signed_pct" }], d.reconciliation, { empty: "All periods reconcile within tolerance." }),
      { note: "Local currency, 0.5% tolerance per property, vertical and month." }));
    const search = h("input", { class: "field", placeholder: "Filter tables, domains or owners", style: "width:260px", "aria-label": "Filter catalogue" }), catBody = h("div");
    const drawCat = () => { const q = search.value.toLowerCase(); catBody.innerHTML = ""; catBody.appendChild(table([{ key: "full_name", label: "Table", fmt: "text" }, { key: "layer", label: "Layer", fmt: "text" }, { key: "domain", label: "Domain", fmt: "text" },
      { key: "data_owner", label: "Data owner", fmt: "text" }, { key: "data_steward", label: "Steward", fmt: "text" }, { key: "row_count", label: "Rows", fmt: "num" }, { key: "retention", label: "Retention", fmt: "text" }],
      d.catalog.filter((t) => !q || (t.full_name + t.domain + t.data_owner + t.data_steward).toLowerCase().includes(q)), { maxHeight: 380, onRow: (t) => {
        const up = d.lineage.filter((l) => l.target === t.full_name), down = d.lineage.filter((l) => l.source === t.full_name);
        openDrawer(h("div", null, h("h2", null, t.full_name), h("dl", { class: "kv", style: "margin-top:10px" }, h("dt", null, "Layer"), h("dd", null, t.layer), h("dt", null, "Domain"), h("dd", null, t.domain), h("dt", null, "Data owner"), h("dd", null, t.data_owner),
          h("dt", null, "Data steward"), h("dd", null, t.data_steward), h("dt", null, "Rows"), h("dd", null, fmt(t.row_count, "num")), h("dt", null, "Retention"), h("dd", null, t.retention)),
          h("h4", null, "Upstream lineage (built from)"), up.length ? h("ul", null, up.map((l) => h("li", null, l.source))) : h("p", null, t.layer === "bronze" ? "Source feed in the landing zone." : "Configuration."),
          h("h4", null, "Downstream lineage (feeds)"), down.length ? h("ul", null, down.map((l) => h("li", null, l.target))) : h("p", null, "No downstream tables."),
          h("h4", null, "Sensitive columns"), table([{ key: "column_name", label: "Column", fmt: "text" }, { key: "classification", label: "Classification", fmt: "text" }], d.sensitive_columns.filter((c) => c.layer + "." + c.table_name === t.full_name), { empty: "No personal data columns." })));
      } })); };
    search.addEventListener("input", drawCat); drawCat();
    grid.appendChild(panel("Data catalogue, ownership and lineage", 12, catBody, { action: search, note: "Select a table to see its upstream and downstream lineage and its sensitive columns. Lineage is captured automatically from each transformation." }));
    grid.appendChild(panel("Data classification", 4, table([{ key: "classification", label: "Classification", fmt: "text" }, { key: "columns", label: "Columns", fmt: "num" }, { key: "tables", label: "Tables", fmt: "num" }], d.classification)));
    const pv = d.privacy;
    grid.appendChild(panel("Guest identity and privacy", 4, h("dl", { class: "kv" }, h("dt", null, "Unified guest profiles"), h("dd", null, fmt(pv.guests, "num")), h("dt", null, "Source records resolved"), h("dd", null, fmt(pv.source_records, "num")),
      h("dt", null, "Profiles spanning 2+ systems"), h("dd", null, fmt(pv.multi_system_share, "pct")), h("dt", null, "Loyalty members"), h("dd", null, fmt(pv.member_share, "pct")), h("dt", null, "Members with marketing consent"), h("dd", null, fmt(pv.consent_rate, "pct"))),
      { note: "Gold holds pseudonymised guest keys, hashed e-mail and masked names only. Models never train on names, e-mail or nationality, and outreach recommendations exclude guests without consent." }));
    grid.appendChild(panel("Quarantined source rows", 4, h("div", null, table([{ key: "source_id", label: "Feed", fmt: "text" }, { key: "reason", label: "Reason", fmt: "text" }, { key: "row_count", label: "Rows", fmt: "num" }], d.quarantine, { empty: "Nothing in quarantine." }),
      d.quarantine_sample ? h("div", { class: "note" }, "Sample payload (administrators only): " + (d.quarantine_sample[0] ? d.quarantine_sample[0].payload.slice(0, 160) + "…" : "none")) : null)));
    grid.appendChild(panel("Enterprise KPI glossary", 12, table([{ key: "kpi", label: "KPI", fmt: "text" }, { key: "domain", label: "Domain", fmt: "text" }, { key: "definition", label: "Business definition", fmt: "text" }, { key: "formula", label: "Calculation", fmt: "text" },
      { key: "exclusions", label: "Rules and exclusions", fmt: "text" }, { key: "owner", label: "Owner", fmt: "text" }], d.glossary)));
    grid.appendChild(panel("Recent ingestion batches", 12, table([{ key: "batch_id", label: "Batch", fmt: "text" }, { key: "started_at", label: "Started", fmt: "datetime" }, { key: "sources", label: "Feeds", fmt: "num" }, { key: "rows_loaded", label: "Rows loaded", fmt: "num" },
      { key: "rows_quarantined", label: "Quarantined", fmt: "num" }, { key: "failed", label: "Failed feeds", fmt: "num" }, { key: "seconds", label: "Seconds", fmt: "dec1" }], d.batches)));
    root.appendChild(grid);
  }
  async function audit(root) {
    setTitle("Audit Trail", "Approvals, alert actions and data exports, with who and when");
    const rows = await api("/audit");
    root.appendChild(panel(`${rows.length} audit record(s)`, 12, table([{ key: "at", label: "When", fmt: "datetime" }, { key: "persona", label: "Who", fmt: "text" }, { key: "entity_type", label: "Item type", fmt: "text" }, { key: "entity_id", label: "Item", fmt: "text" },
      { key: "action", label: "Action", fmt: "text" }, { key: "from_status", label: "From", fmt: "text" }, { key: "to_status", label: "To", fmt: "text" }, { key: "comment", label: "Comment", fmt: "text" }], rows,
      { empty: "No actions recorded yet. Approve a recommendation, act on an alert or export a panel to see it here." }), { note: "Enterprise and administrator roles see all records; other roles see their own." }));
  }

  // ---------------------------------------------------------------- splash screen
  const BLURB = { executive: "The whole group on one page", morning: "Thirteen questions, each with an answer and an action", hotels: "Occupancy, rate and pickup", restaurants: "Covers, spend and menu profit", waterpark: "Attendance, capacity and queues",
    retail: "Sales, margin and stock", spa: "Appointments and utilisation", marketing: "Campaigns, loyalty and value", finance: "Revenue, cost and profit",
    tower: "Outlook, exceptions and risk", energy: "Energy, devices and contracts", forecasting: "Forecasts with confidence ranges", recommendations: "Actions awaiting a decision",
    alerts: "Exceptions to resolve", models: "Accuracy, drift and versions", platform: "Sources, quality and lineage", audit: "Who decided what, and when" };
  async function splash(root) {
    document.title = state.meta.chain + " | Hospitality Intelligence";
    const p = state.meta.persona, groups = NAV.map(([g, items]) => [g, items.filter(([id]) => state.meta.pages.includes(id) || PAGES[id])]).filter(([, items]) => items.length);
    const first = state.meta.pages.includes("executive") ? "executive" : state.meta.pages[0];
    // four live headline figures; the splash still works if they cannot be loaded
    let stats = [];
    try {
      const [ex, ov] = await Promise.all([api("/dashboard/executive"), api("/tower/overview")]);
      const k = (label) => ex.kpis.find((x) => x.label === label), tile = (label) => ov.tiles.find((x) => x.label === label);
      stats = [["Revenue, last 30 days", fmt(k("Total revenue").value, "usd")], ["Occupancy", fmt(k("Occupancy").value, "pct")],
        ["Critical alerts open", fmt(tile("Critical exceptions open").value, "num")], ["Actions awaiting approval", fmt(tile("Recommendations awaiting approval").value, "num")]];
    } catch (e) { stats = []; }
    root.appendChild(h("div", { class: "splash", style: "background-image:url(/img/hero.jpg)" },
      h("aside", { class: "splash-rail" },
        h("div", { class: "splash-brand" }, h("div", { class: "brand-mark", "aria-hidden": "true" }, "A"), h("div", null, h("div", { class: "brand-name" }, state.meta.chain), h("div", { class: "brand-sub" }, "Hospitality Intelligence"))),
        h("nav", { "aria-label": "All pages" }, groups.map(([g, items]) => h("div", { class: "splash-group" }, h("h2", null, g),
          items.map(([id, label]) => h("a", { href: "#/" + id, title: BLURB[id] || "" }, h("span", null, label),
            counts && counts[id] ? h("span", { class: "badge", title: counts[id] + " need attention" }, counts[id]) : h("span", { class: "go", "aria-hidden": "true" }, "\u203A")))))),
        h("label", { class: "splash-role" }, "Signed in as (simulated)", h("select", { onchange: async (ev) => { state.persona = ev.target.value; store.set("persona", state.persona); state.props = []; state.meta = null; await render(); } },
          state.meta.personas.map((x) => h("option", { value: x.id, selected: x.id === state.persona }, `${x.name} - ${x.title}`))))),
      h("section", { class: "splash-stage" }, h("div", { class: "splash-panel" },
        h("div", { class: "splash-copy" },
          h("div", { class: "eyebrow" }, "Enterprise hospitality intelligence"),
          h("h1", null, "One view of every property, forecast and decision"),
          h("p", null, `Welcome, ${p.name.split(" ")[0]}. ${state.meta.properties.length} propert${state.meta.properties.length === 1 ? "y" : "ies"} in your view, with data through ${longDate(state.meta.as_of)}.`),
          h("div", { class: "splash-actions" }, h("a", { class: "splash-btn primary", href: "#/" + first }, "Open " + (first === "executive" ? "the executive view" : "your dashboard")),
            h("a", { class: "splash-btn", href: "#/tower" }, "AI Control Tower"))),
        stats.length ? h("div", { class: "splash-stats" }, stats.map(([label, value]) => h("div", { class: "splash-stat" }, h("div", { class: "v" }, value), h("div", { class: "l" }, label)))) : null,
        h("div", { class: "splash-note" }, "Working prototype on synthetic data")))));
  }

  // ---------------------------------------------------------------- shell, navigation and routing
  const NAV = [["Performance", [["executive", "Executive"], ["morning", "CxO Morning Report"], ["hotels", "Hotels"], ["restaurants", "Restaurants"], ["waterpark", "Waterpark"], ["retail", "Retail"], ["spa", "Spa & Leisure"], ["marketing", "Marketing & Loyalty"], ["finance", "Finance"]]],
    ["AI Control Tower", [["tower", "Intelligence Overview"], ["energy", "Energy & Assets"], ["forecasting", "Forecasting Workspace"], ["recommendations", "Recommendations"], ["alerts", "Alert Management"], ["models", "Model Monitoring"]]],
    ["Platform", [["platform", "Data & Governance"], ["audit", "Audit Trail"]]]];
  const PAGES = { tower: towerOverview, forecasting, recommendations, alerts, models, platform, audit };
  const HOME = "home";
  function setTitle(t, s) { $("#page-title").textContent = t; $("#page-sub").textContent = s || ""; document.title = t + " | " + state.meta.chain; }
  function buildNav(current, counts) {
    const nav = $("#nav");
    nav.innerHTML = "";
    NAV.forEach(([group, items]) => {
      const vis = items.filter(([id]) => state.meta.pages.includes(id) || PAGES[id]);
      if (!vis.length) return;
      nav.appendChild(h("div", { class: "nav-group" }, group));
      vis.forEach(([id, label]) => nav.appendChild(h("a", { href: "#/" + id, class: id === current ? "active" : "", "aria-current": id === current ? "page" : null, onclick: () => $("#sidebar").classList.remove("open") }, label,
        counts && counts[id] ? h("span", { class: "badge", title: counts[id] + " need attention" }, counts[id]) : null)));
    });
    const p = state.meta.persona;
    $("#side-foot").innerHTML = `Signed in as <b style="color:#e9e6df">${esc(p.name)}</b><br>${esc(p.title)}<br>Scope: ${esc(p.scope)} · ${p.properties.length} propert${p.properties.length === 1 ? "y" : "ies"}<br>Data through ${longDate(state.meta.as_of)}`;
  }
  function buildPersona() {
    const box = $("#persona-box");
    box.innerHTML = "";
    box.appendChild(h("label", { class: "ctl" }, "Signed in as (simulated)", h("select", { onchange: async (ev) => { state.persona = ev.target.value; store.set("persona", state.persona); state.props = []; state.meta = null; closeDrawer(); await render(); } },
      state.meta.personas.map((p) => h("option", { value: p.id, selected: p.id === state.persona }, `${p.name} – ${p.title}`)))));
  }
  let counts = null, seq = 0;
  async function render() {
    const my = ++seq, content = $("#content");
    try {
      if (!state.meta) {
        try { state.meta = await api("/meta"); } catch (e) { if (e.status === 401) { state.persona = "ceo"; state.meta = await api("/meta"); } else throw e; }
        state.props = state.props.filter((p) => state.meta.properties.some((x) => x.property_id === p));
        counts = null;
        Promise.all([api("/tower/recommendations"), api("/tower/alerts")]).then(([r, a]) => {
          counts = { recommendations: r.filter((x) => x.approval_status === "Pending approval" && ["Critical", "High"].includes(x.priority)).length, alerts: a.filter((x) => x.severity === "Critical" && x.status !== "Resolved").length };
          if (((location.hash.slice(2).split("?")[0]) || HOME) === HOME) render(); else buildNav(location.hash.slice(2).split("?")[0], counts);
        }).catch(() => {});
      }
      const [path, query] = (location.hash.slice(2) || HOME).split("?"), params = new URLSearchParams(query || "");
      const isDash = state.meta.pages.includes(path), page = isDash || PAGES[path] ? path : HOME;
      document.body.classList.toggle("splash-on", page === HOME);
      buildNav(page, counts); buildPersona(); buildFilters(state.meta.pages.includes(page));
      Charts.reset();
      pending.length = 0;
      const root = h("div");
      content.setAttribute("aria-busy", "true");
      if (page === HOME) await splash(root); else if (state.meta.pages.includes(page)) await dashboard(page, root); else await PAGES[page](root, params);
      if (my !== seq) return;
      content.innerHTML = ""; content.appendChild(root); content.removeAttribute("aria-busy"); flushCharts();
    } catch (e) {
      if (my !== seq) return;
      document.body.classList.remove("splash-on");
      content.innerHTML = "";
      content.appendChild(h("div", { class: "banner crit" }, h("div", null, h("b", null, e.status === 403 ? "You do not have access to this page" : "This page could not be loaded"), h("div", { style: "color:var(--ink-2)" }, e.message))));
      if (state.meta) { setTitle(e.status === 403 ? "Access restricted" : "Something went wrong", ""); }
    }
  }
  window.addEventListener("hashchange", () => { closeDrawer(); window.scrollTo(0, 0); render(); });
  $("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  render();
})();
