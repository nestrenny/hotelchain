"""Build docs/Leadership Briefing.pptx - a 15-slide deck for the leadership team.

Figures are read live from the lakehouse and the running app, so the deck always matches the
platform. Also writes a rough preview image per slide and reports any text that would not fit.

    python tools/capture_screens.py     # once, with the app running
    python tools/build_deck.py
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path

import duckdb
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "data" / "docs_build" / "shots"
OPT = ROOT / "data" / "docs_build" / "opt"
PREVIEW = ROOT / "data" / "docs_build" / "render" / "slides"
OUT = ROOT / "docs" / "Leadership Briefing.pptx"
W, H = 13.333, 7.5
NAVY, NAVY2, GOLD, INK, MUTED, LIGHT, WHITE, GREY = "0F2233", "183247", "B8894A", "1E1E1E", "5F6B76", "F1F4F7", "FFFFFF", "B4B2A9"
GOOD, BAD = "0A7A0A", "B02A2A"
HEAD, BODY = "Cambria", "Calibri"
FONTS = {("Calibri", False): "calibri.ttf", ("Calibri", True): "calibrib.ttf", ("Cambria", False): "cambria.ttc", ("Cambria", True): "cambriab.ttf"}
FOOT = "Aurelian Collection  |  Hospitality Intelligence Platform  |  Prototype on synthetic data"


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


def money(v: float) -> str:
    return f"${v / 1e6:.1f}M" if abs(v) >= 1e6 else f"${v / 1e3:.0f}K"


def prep(name: str, photo: bool = False) -> Path:
    OPT.mkdir(parents=True, exist_ok=True)
    src, dst = SHOTS / f"{name}.png", OPT / f"deck_{name}.{'jpg' if photo else 'png'}"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        im = Image.open(src).convert("RGB")
        if im.width > 2200:
            im = im.resize((2200, round(im.height * 2200 / im.width)), Image.LANCZOS)
        im.save(dst, quality=88, optimize=True) if photo else im.quantize(colors=160, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(dst, optimize=True)
    return dst


class Deck:
    def __init__(self):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.ops: list[list] = []          # per-slide drawing log for the preview and the fit check
        self.warnings: list[str] = []

    # ------------------------------------------------------------ primitives
    def slide(self, title: str, dark: bool = False, notes: str = "", footer: bool = True):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[5])      # "Title Only": keeps a real title placeholder
        self.s, self.cur = s, []
        self.ops.append(self.cur)
        self.rect(0, 0, W, H, NAVY if dark else WHITE)
        bg = s.background.fill
        bg.solid(); bg.fore_color.rgb = rgb(NAVY if dark else WHITE)
        t = s.shapes.title
        t.left, t.top, t.width, t.height = Inches(0.6), Inches(0.42), Inches(12.1), Inches(1.0)
        self._fill_text(t.text_frame, [(title, 32, True, WHITE if dark else NAVY, HEAD)], PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE)
        self.cur.append(("text", 0.6, 0.42, 12.1, 1.0, [(title, 32, True, WHITE if dark else NAVY, HEAD)], "l", "m"))
        if footer:
            self.text(0.6, 7.0, 9.5, 0.3, FOOT, 10, color="9AA5AE" if dark else MUTED)
            self.text(11.9, 7.0, 0.85, 0.3, str(len(self.prs.slides)), 10, color="9AA5AE" if dark else MUTED, align="r")
        if notes:
            s.notes_slide.notes_text_frame.text = notes
        return s

    def _fill_text(self, tf, runs, align, anchor):
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Inches(0.04)
        tf.vertical_anchor = anchor
        first = True
        for text, size, bold, color, font in runs:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            p.alignment = align
            r = p.add_run()
            r.text = text
            r.font.size, r.font.bold, r.font.name, r.font.color.rgb = Pt(size), bold, font, rgb(color)
            p.space_after = Pt(size * 0.35)

    def text(self, x, y, w, h, text, size=16, bold=False, color=INK, font=BODY, align="l", anchor="t"):
        runs = text if isinstance(text, list) else [(text, size, bold, color, font)]
        tb = self.s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        self._fill_text(tb.text_frame, runs, {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[align], {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor])
        self.cur.append(("text", x, y, w, h, runs, align, anchor))
        return tb

    def rect(self, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE, name=None):
        if not hasattr(self, "s") or (x, y, w, h) == (0, 0, W, H):
            self.cur.append(("rect", x, y, w, h, fill, "rect"))
            return None
        sh = self.s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(fill)
        sh.line.fill.background()
        sh.shadow.inherit = False
        if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
            sh.adjustments[0] = min(0.12, 0.14 / min(w, h))
        if name:
            sh.name = name
        self.cur.append(("rect", x, y, w, h, fill, "round" if shape == MSO_SHAPE.ROUNDED_RECTANGLE else "oval" if shape == MSO_SHAPE.OVAL else "rect"))
        return sh

    def card(self, x, y, w, h, fill=LIGHT):
        return self.rect(x, y, w, h, fill, MSO_SHAPE.ROUNDED_RECTANGLE)

    def pic(self, name, x, y, w, h, photo=False, alt="", cover=False, top=False):
        path = Path(name) if Path(str(name)).is_absolute() else prep(name, photo)
        iw, ih = Image.open(path).size
        if top and ih / iw > h / w:                # fill the width and keep the top of a tall capture
            p = self.s.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
            p.crop_bottom = 1 - (h / w) / (ih / iw)
            p.line.color.rgb = rgb("D9DEE3"); p.line.width = Pt(0.75)
            p._element.nvPicPr.cNvPr.set("descr", alt or str(name))
            self.cur.append(("pic", x, y, w, h, str(path), "top"))
            return
        if cover:                                  # fill the box, cropping the overflow
            p = self.s.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
            box, img = w / h, iw / ih
            if img > box:
                crop = (1 - box / img) / 2; p.crop_left = p.crop_right = crop
            else:
                crop = (1 - img / box) / 2; p.crop_top = p.crop_bottom = crop
            dx, dy, dw, dh = x, y, w, h
        else:                                      # fit inside the box, keeping the aspect ratio
            sc = min(w / iw, h / ih)
            dw, dh = iw * sc, ih * sc
            dx, dy = x + (w - dw) / 2, y
            p = self.s.shapes.add_picture(str(path), Inches(dx), Inches(dy), Inches(dw), Inches(dh))
            p.line.color.rgb = rgb("D9DEE3"); p.line.width = Pt(0.75)
        p._element.nvPicPr.cNvPr.set("descr", alt or name)
        self.cur.append(("pic", dx, dy, dw, dh, str(path), cover))

    def chart(self, kind, x, y, w, h, cats, series, colors, num_fmt, legend=False, title=None, gap=60):
        cd = CategoryChartData()
        cd.categories = cats
        for n, vals in series:
            cd.add_series(n, vals)
        gf = self.s.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), cd)
        ch = gf.chart
        ch.font.size, ch.font.name, ch.font.color.rgb = Pt(12), BODY, rgb(INK)
        ch.has_legend = legend
        if legend:
            ch.legend.position, ch.legend.include_in_layout = XL_LEGEND_POSITION.TOP, False
            ch.legend.font.size = Pt(12)
        ch.has_title = bool(title)
        if title:
            ch.chart_title.text_frame.text = title
            ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(13)
            ch.chart_title.text_frame.paragraphs[0].runs[0].font.bold = True
        plot = ch.plots[0]
        plot.gap_width, plot.overlap = gap, -10
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.number_format, dl.number_format_is_linked, dl.position = num_fmt, False, XL_LABEL_POSITION.OUTSIDE_END
        dl.font.size, dl.font.color.rgb = Pt(11), rgb(INK)
        for s, c in zip(plot.series, colors):
            s.format.fill.solid(); s.format.fill.fore_color.rgb = rgb(c)
        va, ca = ch.value_axis, ch.category_axis
        va.visible = False
        va.has_major_gridlines = False
        ca.format.line.color.rgb = rgb("C9CED3")
        ca.tick_labels.font.size = Pt(12)
        if kind == XL_CHART_TYPE.BAR_CLUSTERED:
            ca.reverse_order = True
        self.cur.append(("chart", x, y, w, h, title or "chart", cats))
        return ch

    # ------------------------------------------------------------ preview and fit check
    @staticmethod
    def _font(name, bold, size_px):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + FONTS[(name, bold)], size_px)
        except OSError:
            return ImageFont.load_default()

    def _wrap(self, runs, width_in, scale):
        lines = []
        for text, size, bold, color, font in runs:
            f = self._font(font, bold, round(size / 72 * scale))
            maxw, cur = (width_in - 0.08) * scale, ""
            for word in text.split(" "):
                trial = (cur + " " + word).strip()
                if f.getlength(trial) <= maxw or not cur:
                    cur = trial
                else:
                    lines.append((cur, f, color, size)); cur = word
            lines.append((cur, f, color, size))
            lines.append(None)                     # paragraph gap
        return lines[:-1]

    def render(self):
        PREVIEW.mkdir(parents=True, exist_ok=True)
        for old in PREVIEW.glob("slide-*.png"):
            old.unlink()
        sc = 120
        for i, ops in enumerate(self.ops, 1):
            im = Image.new("RGB", (round(W * sc), round(H * sc)), "white")
            d = ImageDraw.Draw(im)
            for op in ops:
                kind, x, y, w, h = op[:5]
                box = [x * sc, y * sc, (x + w) * sc, (y + h) * sc]
                if kind == "rect":
                    if op[6] == "round":
                        d.rounded_rectangle(box, radius=0.12 * sc, fill="#" + op[5])
                    elif op[6] == "oval":
                        d.ellipse(box, fill="#" + op[5])
                    else:
                        d.rectangle(box, fill="#" + op[5])
                elif kind == "pic":
                    src = Image.open(op[5]).convert("RGB")
                    if op[6] == "top":
                        bw, bh = round(w * sc), round(h * sc)
                        src = src.resize((bw, round(src.height * bw / src.width))).crop((0, 0, bw, bh))
                    elif op[6]:
                        bw, bh = round(w * sc), round(h * sc)
                        r = max(bw / src.width, bh / src.height)
                        src = src.resize((round(src.width * r) + 1, round(src.height * r) + 1))
                        l, t = (src.width - bw) // 2, (src.height - bh) // 2
                        src = src.crop((l, t, l + bw, t + bh))
                    else:
                        src = src.resize((max(round(w * sc), 1), max(round(h * sc), 1)))
                    im.paste(src, (round(x * sc), round(y * sc)))
                elif kind == "chart":
                    d.rectangle(box, outline="#9AA5AE", width=2)
                    d.text((box[0] + 12, box[1] + 10), f"[native chart] {op[5]}: {', '.join(map(str, op[6]))}"[:150], fill="#5F6B76", font=self._font("Calibri", False, 16))
                elif kind == "text":
                    lines = self._wrap(op[5], w, sc)
                    heights = [(ln[3] / 72 * sc * 1.2) if ln else (lines[k - 1][3] / 72 * sc * 0.35 if k else 0) for k, ln in enumerate(lines)]
                    total = sum(heights)
                    if total > (h - 0.04) * sc + 2:
                        self.warnings.append(f"slide {i}: text needs {total / sc:.2f}in but box is {h:.2f}in: {op[5][0][0][:50]!r}")
                    widest = max((ln[1].getlength(ln[0]) for ln in lines if ln), default=0)
                    if widest > w * sc:
                        self.warnings.append(f"slide {i}: a word is wider than its box: {op[5][0][0][:50]!r}")
                    cy = y * sc + 0.04 * sc + ({"t": 0, "m": ((h - 0.08) * sc - total) / 2, "b": (h - 0.08) * sc - total}[op[7]])
                    for ln, hh in zip(lines, heights):
                        if ln:
                            tw = ln[1].getlength(ln[0])
                            tx = x * sc + 0.04 * sc if op[6] == "l" else x * sc + (w * sc - tw) / 2 if op[6] == "c" else (x + w) * sc - tw - 0.04 * sc
                            d.text((tx, cy), ln[0], fill="#" + ln[2], font=ln[1])
                        cy += hh
            im.save(PREVIEW / f"slide-{i:02d}.png")


def facts() -> dict:
    c = duckdb.connect(str(ROOT / "data" / "lakehouse.duckdb"), read_only=True)
    q = lambda s: c.execute(s).fetchall()  # noqa: E731
    api = lambda p: json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:8000/api" + p, headers={"X-Persona": "ceo"})))  # noqa: E731
    ex = api("/dashboard/executive")
    k = {x["label"]: x for x in ex["kpis"]}
    f = {
        "k": k, "period": ex["period"],
        "feeds": q("SELECT count(DISTINCT source_id) FROM ops.ingestion_log")[0][0],
        "layers": {r[0]: (r[1], r[2]) for r in q("SELECT layer, count(*), sum(row_count) FROM ops.catalog_tables GROUP BY 1")},
        "dq": q("SELECT count(*), count(*) FILTER (WHERE status = 'Passed') FROM ops.dq_results WHERE run_at = (SELECT max(run_at) FROM ops.dq_results)")[0],
        "models": q("SELECT count(*) FROM ai.model_registry WHERE deployment_status = 'Deployed'")[0][0],
        "fc": q("SELECT model_name, metric_value, baseline_value FROM ai.model_registry WHERE deployment_status = 'Deployed' AND model_type = 'Forecast' ORDER BY metric_value"),
        "auc": dict(q("SELECT model_id, metric_value FROM ai.model_registry WHERE deployment_status = 'Deployed' AND model_type = 'Classification'")),
        "recs": q("SELECT count(*), sum(estimated_impact_usd), count(*) FILTER (WHERE priority IN ('Critical', 'High')) FROM gold.fact_ai_recommendations")[0],
        "rec_v": q("SELECT business_vertical, sum(estimated_impact_usd), count(*) FROM gold.fact_ai_recommendations GROUP BY 1 ORDER BY 2 DESC"),
        "rec_top": q("SELECT title, estimated_impact_usd, priority FROM gold.fact_ai_recommendations ORDER BY priority_score DESC LIMIT 3"),
        "alerts": dict(q("SELECT severity, count(*) FROM ai.alerts GROUP BY 1")),
        "props": q(f"""SELECT p.short_name, sum(total_revenue_usd) / 1e6 FROM gold.mart_executive_daily e JOIN gold.dim_property p USING (property_id)
                       WHERE business_date BETWEEN DATE '{ex['period']['start']}' AND DATE '{ex['period']['end']}' GROUP BY 1 ORDER BY 2 DESC"""),
        "kpis": q("SELECT count(*) FROM ops.kpi_glossary")[0][0],
        "energy": api("/dashboard/energy"),
        "morning": {**api("/dashboard/morning"), "items": api("/dashboard/morning")["panels"][0]["items"]},
        "sup": q("SELECT estimated_impact_usd FROM gold.fact_ai_recommendations WHERE scenario = 'Supplier spend optimisation' ORDER BY 1 DESC LIMIT 1")[0][0],
    }
    c.close()
    return f


def build() -> None:
    F, D = facts(), Deck()
    k = F["k"]
    chg = lambda x, pts=False: (f"{(x['value'] - x['prev']) * 100:+.1f} pts" if pts else f"{(x['value'] / x['prev'] - 1):+.1%}")  # noqa: E731

    # 1 ---------------------------------------------------------------- title
    D.slide("From fragmented reporting to predictive hospitality", dark=True, footer=False,
            notes="Opening. This is a review of the working prototype of the Enterprise Hospitality Intelligence Platform. Everything shown runs today on synthetic data for a fictional six-property chain. "
                  "The aim of the session is a decision on moving to the production build.")
    D.s.shapes.title.width, D.s.shapes.title.top, D.s.shapes.title.height = Inches(6.3), Inches(2.0), Inches(1.9)
    D.cur[-1] = ("text", 0.6, 2.0, 6.3, 1.9, [("From fragmented reporting to predictive hospitality", 32, True, WHITE, HEAD)], "l", "m")
    D.pic(str(ROOT / "img" / "hero.png"), 7.3, 0, 6.033, 7.5, alt="Aurelian Collection resort at sunset", cover=True)
    D.text(0.6, 1.35, 6.3, 0.4, "AURELIAN COLLECTION", 14, True, GOLD)
    D.text(0.6, 4.05, 6.3, 0.9, "Enterprise Hospitality Intelligence Platform: prototype review for the leadership team", 18, color="D7DDE3")
    D.text(0.6, 6.6, 6.3, 0.4, f"{date.today():%d %B %Y}  |  Working prototype on synthetic data", 12, color="9AA5AE")

    # 2 ---------------------------------------------------------------- summary
    D.slide("One platform shows the business and what to do next",
            notes=f"Three points. One: all seven business domains now report from one governed data foundation. Two: {F['models']} predictive models are running and every forecast beats a simple baseline. "
                  f"Three: the platform has turned those forecasts into {F['recs'][0]} recommended actions with an estimated value of {money(F['recs'][1])}. That value is an estimate on synthetic data, not a business case. "
                  "The decision we are asking for is at the end.")
    stats = [("7", "business domains in one governed view", "Hotels, restaurants, waterpark, retail, spa, marketing and finance"),
             (str(F["models"]), "predictive models running", "Every forecast beats a simple seasonal baseline"),
             (str(F["recs"][0]), "recommended actions", f"Estimated value {money(F['recs'][1])}, each awaiting human approval")]
    for i, (big, label, sub) in enumerate(stats):
        x = 0.6 + i * 4.12
        D.card(x, 1.75, 3.86, 3.2)
        D.text(x + 0.3, 1.95, 3.3, 1.25, big, 66, True, NAVY, HEAD)
        D.text(x + 0.3, 3.2, 3.3, 0.75, label, 20, True, INK)
        D.text(x + 0.3, 3.98, 3.3, 0.85, sub, 15, color=MUTED)
    D.card(0.6, 5.25, 12.1, 1.35, NAVY)
    D.text(0.9, 5.37, 11.5, 1.1, [("What we are asking for", 14, True, GOLD, BODY),
                                   ("Confirm the source systems, adopt the KPI definitions and fund the first two phases of the Azure build.", 18, False, WHITE, BODY)], anchor="m")

    # 3 ---------------------------------------------------------------- how it works
    L = F["layers"]
    D.slide("How it works: from source systems to one trusted view",
            notes="Left to right. Data arrives from every operational system. Bronze keeps it exactly as received, for audit and replay. Silver cleans it and brings the two property management systems, "
                  "currencies and guest identities into one standard. Gold is the business-ready layer the dashboards and models read. Quality rules and reconciliation to the ledger run on every load. "
                  "Each local component maps to a named Azure or Fabric service.")
    steps = [(str(F["feeds"]), "Source feeds", "PMS, POS, ticketing, CRM, ERP, buildings"), (f"{L['bronze'][1] / 1e6:.1f}M", "Bronze: raw", "Kept as received for audit"),
             (f"{L['silver'][0]}", "Silver tables", "Cleaned and standardised"), (f"{L['gold'][0]}", "Gold tables", "Business-ready data products"),
             ("8 + 6", "Dashboards and AI pages", "One design, role-based access")]
    for i, (big, label, sub) in enumerate(steps):
        x = 0.6 + i * 2.5
        D.card(x, 1.85, 2.1, 2.75, NAVY if i == 4 else LIGHT)
        D.text(x + 0.15, 2.0, 1.8, 0.95, big, 40, True, GOLD if i == 4 else NAVY, HEAD, align="c", anchor="m")
        D.text(x + 0.15, 2.98, 1.8, 0.7, label, 16, True, WHITE if i == 4 else INK, align="c")
        D.text(x + 0.15, 3.68, 1.8, 0.8, sub, 14, color="D7DDE3" if i == 4 else MUTED, align="c")
        if i < 4:
            D.rect(x + 2.16, 3.02, 0.28, 0.36, GOLD, MSO_SHAPE.RIGHT_ARROW)
    for i, (head, body) in enumerate([("Checked on every load", f"{F['dq'][0]} data-quality rules, including revenue reconciled to the ledger"),
                                      ("One guest, one identity", "Guests matched across hotels, dining, retail and spa, with consent respected"),
                                      ("Built to move to Azure", "Each component maps to a named Azure or Fabric service")]):
        x = 0.6 + i * 4.12
        D.rect(x, 5.08, 0.42, 0.42, GOLD, MSO_SHAPE.OVAL)
        D.text(x, 5.08, 0.42, 0.42, str(i + 1), 15, True, WHITE, align="c", anchor="m")
        D.text(x + 0.58, 5.02, 3.3, 0.45, head, 16, True, NAVY)
        D.text(x + 0.58, 5.47, 3.3, 1.0, body, 14, color=MUTED)

    # 4 ---------------------------------------------------------------- one view
    D.slide("Every leader sees the same numbers, defined once",
            notes="This is the Executive dashboard as it runs today. The point is consistency: occupancy, ADR, RevPAR and GOP are defined once and used everywhere. "
                  "The same page works for the group, a region or a single property, and each role sees only what it is entitled to.")
    D.pic("executive_top", 0.6, 1.7, 7.6, 5.1, photo=True, alt="Executive dashboard")
    for i, (head, body) in enumerate([(f"{F['kpis']} governed KPIs", "One definition each, shown beside every figure"),
                                      ("Actual, budget and forecast", "Always drawn the same way, so they are never confused"),
                                      ("From group to property", "Filter by property and period, then drill into the detail"),
                                      ("Access by role", "Eleven roles tested; each sees only its own properties and domains")]):
        y = 1.8 + i * 1.25
        D.card(8.55, y, 4.15, 1.08)
        D.text(8.75, y + 0.08, 3.8, 0.42, head, 17, True, NAVY)
        D.text(8.75, y + 0.5, 3.8, 0.52, body, 14, color=MUTED)

    # 5 ---------------------------------------------------------------- morning report
    mr = F["morning"]
    cnt = mr["counts"]
    D.slide("Every morning, thirteen questions answered on one page",
            notes=f"This is the CxO Morning Report. It takes the thirteen questions the executive team asks every morning, from how full the hotels were last night to whether next month "
                  f"will be better or worse, and answers each one with the KPIs that measure it, a verdict and a suggested action. For the last 30 days it shows {cnt['good']} questions on track, "
                  f"{cnt['watch']} to watch and {cnt['bad']} needing action. The verdicts come from simple, written rules rather than a model, so each one can be explained. Targets shown are illustrative.")
    D.pic("morning_q9", 0.6, 1.65, 5.6, 3.3, alt="Morning report card: Are guests satisfied?", top=True)
    D.text(0.6, 5.1, 5.6, 0.36, "Each card: the question, its KPIs, a verdict, the answer and what to do", 13, True, NAVY, align="c")
    for i, (key, label, colour) in enumerate([("good", "On track", GOOD), ("watch", "Watch", "B7791F"), ("bad", "Act now", BAD)]):
        x = 6.6 + i * 2.06
        D.card(x, 1.7, 1.9, 1.45)
        D.text(x + 0.1, 1.75, 1.7, 0.85, str(cnt[key]), 40, True, colour, HEAD, align="c", anchor="m")
        D.text(x + 0.1, 2.6, 1.7, 0.45, label, 15, True, INK, align="c")
    acts = [i for i in mr["items"] if i["verdict"] == "bad"] + [i for i in mr["items"] if i["verdict"] == "watch"]
    D.text(6.6, 3.35, 6.1, 0.4, "What the report says today", 16, True, NAVY)
    for j, i in enumerate(acts[:3]):
        y = 3.85 + j * 0.86
        D.card(6.6, y, 6.1, 0.76, "FBE4E4" if i["verdict"] == "bad" else LIGHT)
        D.text(6.75, y + 0.04, 5.85, 0.68, [(i["question"], 12, True, NAVY, BODY), (i["action"], 11, False, INK, BODY)], anchor="m")
    D.text(0.6, 6.45, 12.1, 0.4, "Synthetic data, last 30 days. Verdicts follow written rules; targets are illustrative.", 12, color=MUTED)

    # 6 ---------------------------------------------------------------- 30 days
    per = F["period"]
    D.slide("The last 30 days at a glance",
            notes="These are the figures the platform reports for the most recent 30 days, compared with the 30 days before. They come from synthetic data, so read them as an illustration of what leadership will see, not as results. "
                  "The chart shows revenue by property.")
    tiles = [("Total revenue", money(k["Total revenue"]["value"]), chg(k["Total revenue"])), ("Occupancy", f"{k['Occupancy']['value']:.1%}", chg(k["Occupancy"], True)),
             ("RevPAR", f"${k['RevPAR']['value']:.0f}", chg(k["RevPAR"])), ("Gross operating profit", money(k["GOP"]["value"]), chg(k["GOP"]))]
    for i, (label, val, delta) in enumerate(tiles):
        x = 0.6 + (i % 2) * 2.72
        y = 1.75 + (i // 2) * 2.42
        D.card(x, y, 2.52, 2.2)
        D.text(x + 0.2, y + 0.15, 2.15, 0.4, label, 14, color=MUTED)
        D.text(x + 0.2, y + 0.58, 2.15, 0.85, val, 36, True, NAVY, HEAD)
        D.text(x + 0.2, y + 1.5, 2.15, 0.55, f"{delta} vs prior 30 days", 14, True, GOOD if not delta.startswith("-") else BAD)
    D.chart(XL_CHART_TYPE.COLUMN_CLUSTERED, 6.3, 1.65, 6.4, 4.75, [p[0] for p in F["props"]], [("Revenue ($M)", [round(p[1], 2) for p in F["props"]])], [NAVY],
            '"$"0.0"M"', title="Revenue by property, last 30 days ($M)")
    D.text(0.6, 6.5, 12.1, 0.4, f"Synthetic data, {per['start']} to {per['end']}. Illustrates the view; not actual results.", 12, color=MUTED)

    # 7 ---------------------------------------------------------------- domains
    D.slide("Each business domain gets its own working view",
            notes="Beyond the executive page there is a dashboard for each of the seven domains. Four examples: forward bookings against the occupancy forecast for revenue managers; queue times by attraction and hour for park operations; "
                  "menu items ranked by profit for food and beverage; and cost centres against budget for finance.")
    shots = [("hotels_p2", "Hotels: bookings already made against the forecast, next 60 days"), ("waterpark_p2", "Waterpark: queue time by attraction and hour"),
             ("restaurants_p4", "Restaurants: menu items ranked by contribution"), ("finance_p2", "Finance: cost centres against budget")]
    for i, (shot, cap) in enumerate(shots):
        x, y = 0.6 + (i % 2) * 6.15, 1.62 + (i // 2) * 2.72
        D.pic(shot, x, y, 5.95, 2.22, alt=cap, top=True)
        D.text(x, y + 2.25, 5.95, 0.36, cap, 13, True, NAVY, align="c")

    # 8 ---------------------------------------------------------------- forecasts
    occ = next(r for r in F["fc"] if r[0] == "Hotel Occupancy Forecast")
    D.slide("Forecasts beat the simple baseline in every domain",
            notes=f"Each forecast was tested by hiding the last 90 days, forecasting them, and comparing with what happened. The chart shows the error for each model against a simple method that repeats last year's pattern. "
                  f"Lower is better. Hotel occupancy error is {occ[1]:.1%} against {occ[2]:.1%}. Retail is the hardest to forecast because product-level demand is small and volatile. "
                  "These are results on synthetic data; accuracy on real data must be re-established in the build.")
    D.chart(XL_CHART_TYPE.BAR_CLUSTERED, 0.6, 1.6, 8.3, 5.25, [r[0].replace(" Forecast", "").replace(" Prediction", "") for r in F["fc"]],
            [("Model error", [r[1] for r in F["fc"]]), ("Simple baseline error", [r[2] for r in F["fc"]])], [NAVY, GREY], "0.0%", legend=True, gap=45)
    D.card(9.2, 1.75, 3.5, 2.25)
    D.text(9.4, 1.85, 3.1, 0.4, "Hotel occupancy", 14, color=MUTED)
    D.text(9.4, 2.25, 3.1, 0.85, f"{occ[1]:.1%}", 40, True, NAVY, HEAD)
    D.text(9.4, 3.12, 3.1, 0.75, f"forecast error, against {occ[2]:.1%} for the baseline", 14, color=INK)
    D.card(9.2, 4.2, 3.5, 2.55)
    D.text(9.4, 4.32, 3.1, 2.35, [("How to read this", 14, True, NAVY, BODY), ("Error is the average miss over a 90-day test. Shorter bars are better.", 14, False, INK, BODY),
                                   ("Measured on synthetic data.", 14, False, MUTED, BODY)])

    # 9 ---------------------------------------------------------------- recommendations
    n, total, urgent = F["recs"]
    D.slide("Forecasts become prioritised, costed actions",
            notes=f"The platform does not stop at prediction. It has produced {n} recommendations with a combined estimated value of {money(total)}; {urgent} are rated critical or high. "
                  "Each one states the problem, the action, the figures behind it, the estimated impact with its assumption, and a confidence score. The estimates rest on stated assumptions and synthetic data, so treat the total as indicative.")
    D.chart(XL_CHART_TYPE.BAR_CLUSTERED, 0.6, 1.6, 6.3, 5.2, [r[0] for r in F["rec_v"]], [("Estimated impact ($K)", [round(r[1] / 1e3) for r in F["rec_v"]])], [NAVY],
            '"$"#,##0"K"', title="Estimated impact of open recommendations by domain ($K)")
    D.text(7.2, 1.65, 5.5, 0.4, "Largest three recommendations", 16, True, NAVY)
    for i, (title, impact, pri) in enumerate(F["rec_top"]):
        y = 2.15 + i * 1.27
        D.card(7.2, y, 5.5, 1.12)
        D.text(7.4, y + 0.1, 1.5, 0.92, money(impact), 24, True, NAVY, HEAD, anchor="m")
        D.text(8.95, y + 0.1, 3.6, 0.92, title, 14, color=INK, anchor="m")
    D.text(7.2, 6.05, 5.5, 0.75, f"{n} recommendations, {money(total)} estimated in total. Estimates use stated assumptions on synthetic data.", 13, color=MUTED)

    # 10 ---------------------------------------------------------------- energy and assets
    E = F["energy"]
    ek = {x["label"]: x for x in E["kpis"]}
    D.slide("It also predicts failures, energy waste and contract gaps",
            notes=f"The same approach now covers the buildings. The Energy and Assets page reads the building management system, sub-meters, device telemetry, the maintenance log and the contract register. "
                  f"A baseline model works out how much energy each property should use for its occupancy and weather; the period ran {ek['Energy vs expected']['value']:+.1%} against that. "
                  f"A failure model scores every device: {ek['Devices at high failure risk']['value']:.0f} are at high risk in the next 30 days. "
                  f"{ek['Contracts lapsed or ending in 90 days']['value']:.0f} contracts have lapsed or end within 90 days. The value shown is an estimate on synthetic data.")
    tiles = [("Energy against expected", f"{ek['Energy vs expected']['value']:+.1%}", "for the occupancy and weather"),
             ("Devices at high failure risk", f"{ek['Devices at high failure risk']['value']:.0f}", "likely to fail within 30 days"),
             ("Contracts needing action", f"{ek['Contracts lapsed or ending in 90 days']['value']:.0f}", "lapsed or ending in 90 days"),
             ("Savings and avoidable cost", money(ek["Annual savings and avoidable cost identified"]["value"]), "a year, estimated")]
    for i, (label, val, sub) in enumerate(tiles):
        x = 0.6 + (i % 2) * 2.72
        y = 1.75 + (i // 2) * 2.42
        D.card(x, y, 2.52, 2.2)
        D.text(x + 0.2, y + 0.15, 2.15, 0.4, label, 14, color=MUTED)
        D.text(x + 0.2, y + 0.58, 2.15, 0.85, val, 36, True, NAVY, HEAD)
        D.text(x + 0.2, y + 1.5, 2.15, 0.55, sub, 14, color=INK)
    D.pic("energy_p0", 6.3, 1.72, 6.4, 2.35, alt="Energy use: actual, expected for occupancy and weather, and forecast", top=True)
    D.text(6.3, 4.1, 6.4, 0.34, "Energy use: actual, expected for occupancy and weather, and forecast", 13, True, NAVY, align="c")
    found, seen = [], set()
    for a in E["actions"]:
        if a["category"] not in seen:
            seen.add(a["category"]); found.append(a)
    for i, a in enumerate(found[:3]):
        y = 4.55 + i * 0.64
        D.card(6.3, y, 6.4, 0.56)
        D.text(6.45, y + 0.03, 1.75, 0.5, a["category"], 12, True, NAVY, anchor="m")
        D.text(8.2, y + 0.03, 4.4, 0.5, a["title"].split(": ", 1)[-1][:1].upper() + a["title"].split(": ", 1)[-1][1:] + f" ({a['property']})", 12, color=INK, anchor="m")
    D.text(0.6, 6.55, 12.1, 0.35, "Synthetic data. Savings are estimates based on stated assumptions; findings are reminders for the engineering team.", 12, color=MUTED)

    # 11 --------------------------------------------------------------- planted problems
    D.slide("It found every problem we planted in the data",
            notes="To test whether the platform notices what matters, we deliberately hid four problems in the synthetic data. It found all four without being told where to look, and the automated tests assert this on every build. "
                  "This is the strongest evidence in the prototype that the detection logic works.")
    D.text(0.6, 1.6, 5.6, 0.4, "What we planted", 15, True, MUTED)
    D.text(6.75, 1.6, 5.9, 0.4, "What the platform did", 15, True, MUTED)
    rows = [("Two days of restaurant revenue missing from the Doha ledger", "Flagged a reconciliation break between operations and the ledger"),
            ("A utilities cost spike at Dubai Palm", "Raised a critical alert: energy and water 35% above normal"),
            ("A supplier quietly raising prices at Phuket", f"Flagged the supplier and recommended renegotiation, worth an estimated {money(F['sup'])}"),
            ("Unapproved discounting at a Phuket boutique", "Raised a critical alert for revenue leakage")]
    for i, (a, b) in enumerate(rows):
        y = 2.05 + i * 1.18
        D.card(0.6, y, 5.6, 1.02)
        D.text(0.8, y + 0.06, 5.2, 0.9, a, 15, color=INK, anchor="m")
        D.rect(6.3, y + 0.33, 0.34, 0.36, GOLD, MSO_SHAPE.RIGHT_ARROW)
        D.card(6.75, y, 5.95, 1.02, NAVY)
        D.text(6.95, y + 0.06, 5.55, 0.9, b, 15, color=WHITE, anchor="m")

    # 12 --------------------------------------------------------------- control
    D.slide("People stay in control of every decision",
            notes="Nothing is executed automatically. A recommendation is reviewed with its evidence, then approved or rejected by an authorised person, and a rejection needs a reason. Once implemented, the realised impact is recorded, "
                  "so over time we can measure whether decisions paid off. Every step is written to an audit trail, and guest data is pseudonymised with marketing consent respected.")
    flow = [("Recommend", "Model and rules propose an action"), ("Review", "Evidence, impact and confidence shown"), ("Decide", "Authorised role approves or rejects"),
            ("Act", "Owner carries it out"), ("Measure", "Realised impact recorded")]
    for i, (head, body) in enumerate(flow):
        y = 1.72 + i * 0.98
        D.rect(0.6, y + 0.1, 0.56, 0.56, NAVY, MSO_SHAPE.OVAL)
        D.text(0.6, y + 0.1, 0.56, 0.56, str(i + 1), 18, True, WHITE, align="c", anchor="m")
        D.text(1.4, y, 2.0, 0.76, head, 19, True, NAVY, anchor="m")
        D.text(3.3, y, 3.6, 0.76, body, 15, color=INK, anchor="m")
    D.pic("drawer_recommendation", 7.3, 1.62, 5.4, 4.5, alt="Recommendation detail with evidence and approval controls", top=True)
    D.text(7.3, 6.2, 5.4, 0.62, "Every approval, rejection and export is recorded with who and when", 14, True, NAVY, align="c")

    # 13 --------------------------------------------------------------- limits
    D.slide("What this prototype does not yet prove",
            notes="To be clear about the limits. The data is synthetic, so model accuracy must be re-established on real data. Nothing has been deployed to Azure, Fabric or Power BI yet. Sign-in is simulated. "
                  "And data loads in batches, not every few minutes. None of these is a design obstacle, but each is work in the production build.")
    lim = [("Synthetic data", "Accuracy and impact figures must be re-established on our real data"), ("Not yet on Azure", "Runs on one machine; nothing is deployed to Azure, Fabric or Power BI"),
           ("Simulated sign-in", "Access rules are enforced, but there is no corporate sign-in yet"), ("Batch refresh", "Data loads on demand; 5 to 15 minute refresh is designed, not running")]
    for i, (head, body) in enumerate(lim):
        x, y = 0.6 + (i % 2) * 6.15, 1.75 + (i // 2) * 2.45
        D.card(x, y, 5.95, 2.2)
        D.rect(x + 0.3, y + 0.32, 0.5, 0.5, GOLD, MSO_SHAPE.OVAL)
        D.text(x + 0.3, y + 0.32, 0.5, 0.5, "!", 20, True, WHITE, align="c", anchor="m")
        D.text(x + 1.0, y + 0.27, 4.7, 0.6, head, 22, True, NAVY, anchor="m")
        D.text(x + 1.0, y + 0.98, 4.7, 1.1, body, 16, color=INK)

    # 14 --------------------------------------------------------------- roadmap
    D.slide("The path from prototype to production",
            notes="These seven phases and durations come from the requirements specification. They are planning estimates, not a committed schedule, and phases can overlap. "
                  "The prototype shortens discovery and design because the data model, KPI definitions, dashboards and model approach already exist and have been tested.")
    phases = [("Discovery", "3-4"), ("Foundation", "4-6"), ("Integration", "6-10"), ("Data platform", "5-8"), ("Dashboards", "4-6"), ("AI tower", "6-10"), ("Test and go-live", "3-4")]
    for i, (name, wk) in enumerate(phases):
        x = 0.6 + i * 1.74
        D.card(x, 2.0, 1.6, 1.75, NAVY if i < 2 else LIGHT)
        D.text(x + 0.08, 2.1, 1.44, 0.5, f"Phase {i + 1}", 13, True, GOLD if i < 2 else MUTED, align="c")
        D.text(x + 0.08, 2.55, 1.44, 0.7, name, 15, True, WHITE if i < 2 else NAVY, align="c", anchor="m")
        D.text(x + 0.08, 3.22, 1.44, 0.42, f"{wk} weeks", 14, color="D7DDE3" if i < 2 else INK, align="c")
    D.text(0.6, 3.95, 3.34, 0.4, "Funding requested now", 14, True, NAVY, align="c")
    for i, (head, body) in enumerate([("The prototype already gives us", "A tested data model, governed KPI definitions, all dashboards and a working model approach"),
                                      ("What the build adds", "Real source connections, Azure and Fabric deployment, Power BI, corporate sign-in and live refresh"),
                                      ("Order of delivery", "Hotels, restaurants and finance first; then the other domains; then forecasting and recommendations")]):
        x = 0.6 + i * 4.12
        D.card(x, 4.6, 3.86, 1.95)
        D.text(x + 0.25, 4.7, 3.4, 0.45, head, 16, True, NAVY)
        D.text(x + 0.25, 5.17, 3.4, 1.3, body, 14, color=INK)
    D.text(0.6, 6.6, 12.1, 0.35, "Durations are planning estimates from the requirements specification, not a committed schedule. Phases may overlap.", 12, color=MUTED)

    # 15 --------------------------------------------------------------- decisions
    D.slide("Four decisions we need from you", dark=True,
            notes="Four decisions. First, confirm the actual source systems at each property and grant access, because that determines the integration work. Second, adopt the KPI definitions as the enterprise standard. "
                  "Third, name a business owner for each domain who will approve recommendations. Fourth, fund discovery and the platform foundation. With those, the build can start.")
    asks = [("Confirm the source systems", "PMS, POS, ticketing, CRM and ERP at each property, with access for the team"),
            ("Adopt the KPI definitions", f"Approve the {F['kpis']} definitions as the single enterprise standard"),
            ("Name the business owners", "One accountable owner per domain to approve recommendations"),
            ("Fund phases 1 and 2", "Discovery and platform foundation, 7 to 10 weeks in total")]
    for i, (head, body) in enumerate(asks):
        x, y = 0.6 + (i % 2) * 6.15, 1.8 + (i // 2) * 2.35
        D.card(x, y, 5.95, 2.1, NAVY2)
        D.text(x + 0.3, y + 0.25, 1.0, 1.1, str(i + 1), 54, True, GOLD, HEAD, anchor="m")
        D.text(x + 1.35, y + 0.22, 4.4, 0.6, head, 22, True, WHITE, anchor="m")
        D.text(x + 1.35, y + 0.9, 4.4, 1.05, body, 16, color="D7DDE3")

    OUT.parent.mkdir(exist_ok=True)
    D.prs.core_properties.title, D.prs.core_properties.author = "Leadership Briefing - Hospitality Intelligence Platform", "Aurelian Collection"
    D.prs.save(OUT)
    D.render()
    print("wrote", OUT, f"{OUT.stat().st_size / 1e6:.1f} MB,", len(D.prs.slides), "slides")
    print("\n".join(D.warnings) if D.warnings else "text fit check: no overflow found")


if __name__ == "__main__":
    build()
