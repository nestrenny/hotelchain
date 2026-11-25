"""Build docs/Dashboard User Guide.docx from the captured screenshots and guide_content.py.

    python tools/capture_screens.py     # once, with the app running
    python tools/build_guide.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guide_content as G  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "data" / "docs_build" / "shots"
OPT = ROOT / "data" / "docs_build" / "opt"
OUT = ROOT / "docs" / "Dashboard User Guide.docx"
NAVY, GOLD, INK, MUTED = RGBColor(0x0F, 0x22, 0x33), RGBColor(0x9A, 0x6F, 0x33), RGBColor(0x22, 0x22, 0x22), RGBColor(0x5A, 0x5A, 0x57)
TEXT_W = 17.0   # cm, A4 with 2 cm margins
INDEX = json.loads((SHOTS / "index.json").read_text(encoding="utf-8"))


def prep(name: str, photo: bool = False, max_ratio: float | None = None) -> Path:
    """Shrink a screenshot for embedding: palette PNG for UI captures, JPEG where a photograph is present."""
    OPT.mkdir(parents=True, exist_ok=True)
    src = SHOTS / f"{name}.png"
    dst = OPT / f"{name}{'_c' if max_ratio else ''}.{'jpg' if photo else 'png'}"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        im = Image.open(src).convert("RGB")
        if max_ratio and im.height > im.width * max_ratio:      # keep the top of a very long list
            im = im.crop((0, 0, im.width, round(im.width * max_ratio)))
        if im.width > 2000:
            im = im.resize((2000, round(im.height * 2000 / im.width)), Image.LANCZOS)
        if photo:
            im.save(dst, quality=86, optimize=True)
        else:
            im.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(dst, optimize=True)
    return dst


def shade(cell_or_par, fill: str) -> None:
    pr = cell_or_par._tc.get_or_add_tcPr() if hasattr(cell_or_par, "_tc") else cell_or_par._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
    pr.append(shd)


def field(par, code: str) -> None:
    for kind, text in (("begin", None), (None, code), ("separate", None), (None, "Update field"), ("end", None)):
        run = par.add_run()
        if kind:
            el = OxmlElement("w:fldChar"); el.set(qn("w:fldCharType"), kind); run._r.append(el)
        elif text == code:
            el = OxmlElement("w:instrText"); el.set(qn("xml:space"), "preserve"); el.text = code; run._r.append(el)
        else:
            run.text = text


class Guide:
    def __init__(self):
        self.d = d = Document()
        s = d.sections[0]
        s.page_width, s.page_height = Cm(21.0), Cm(29.7)
        s.left_margin = s.right_margin = Cm(2.0)
        s.top_margin, s.bottom_margin = Cm(2.0), Cm(1.9)
        st = d.styles
        st["Normal"].font.name, st["Normal"].font.size, st["Normal"].font.color.rgb = "Calibri", Pt(10.5), INK
        st["Normal"].paragraph_format.space_after, st["Normal"].paragraph_format.line_spacing = Pt(6), 1.12
        for name, size, before, after, color in (("Title", 30, 0, 6, NAVY), ("Heading 1", 20, 0, 10, NAVY), ("Heading 2", 14, 14, 6, NAVY), ("Heading 3", 11.5, 12, 4, GOLD)):
            h = st[name]
            h.font.name, h.font.size, h.font.bold, h.font.color.rgb = "Calibri", Pt(size), name != "Title", color
            h.element.rPr.rFonts.set(qn("w:asciiTheme"), "minorHAnsi"); h.element.rPr.rFonts.set(qn("w:hAnsiTheme"), "minorHAnsi")
            h.paragraph_format.space_before, h.paragraph_format.space_after, h.paragraph_format.keep_with_next = Pt(before), Pt(after), True
        st["Heading 1"].paragraph_format.page_break_before = True
        st["List Bullet"].font.size = Pt(10.5)
        f = s.footer.paragraphs[0]
        f.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = f.add_run("Aurelian Collection  |  Dashboard User Guide  |  Page ")
        r.font.size, r.font.color.rgb = Pt(8.5), MUTED
        field(f, "PAGE")
        for run in f.runs:
            run.font.size, run.font.color.rgb = Pt(8.5), MUTED
        self.n = 0

    # ------------------------------------------------------------ primitives
    def p(self, text: str = "", bold_lead: str | None = None, style: str | None = None, size: float | None = None, color=None, after: float | None = None, keep: bool = False):
        par = self.d.add_paragraph(style=style)
        if bold_lead:
            r = par.add_run(bold_lead + " ")
            r.bold = True
            r.font.color.rgb = NAVY
        r = par.add_run(text)
        for run in par.runs:
            if size:
                run.font.size = Pt(size)
            if color is not None and run is r:
                run.font.color.rgb = color
        if after is not None:
            par.paragraph_format.space_after = Pt(after)
        par.paragraph_format.keep_with_next = keep
        return par

    def bullets(self, items):
        for lead, text in items:
            par = self.d.add_paragraph(style="List Bullet")
            if lead:
                r = par.add_run(lead + " ")
                r.bold = True
            par.add_run(text)
            par.paragraph_format.space_after = Pt(3)

    def note(self, text: str):
        par = self.d.add_paragraph()
        shade(par, "F3E9D8")
        par.paragraph_format.left_indent = par.paragraph_format.right_indent = Cm(0.25)
        par.paragraph_format.space_before, par.paragraph_format.space_after = Pt(6), Pt(10)
        r = par.add_run("Note  ")
        r.bold = True
        r.font.color.rgb = NAVY
        par.add_run(text)

    def img(self, name: str, max_w: float = TEXT_W, max_h: float = 11.0, photo: bool = False, keep: bool = True, alt: str = "", max_ratio: float | None = None):
        path = prep(name, photo, max_ratio)
        w, h = Image.open(path).size
        width = min(max_w, max_h * w / h)
        par = self.d.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_after = Pt(6)
        par.paragraph_format.keep_with_next = keep
        pic = par.add_run().add_picture(str(path), width=Cm(width))
        pic._inline.docPr.set("descr", alt or name.replace("_", " "))
        return par

    def table(self, header: list[str], rows: list[list[str]], widths: list[float], size: float = 9.0):
        t = self.d.add_table(rows=1, cols=len(header))
        t.style, t.alignment, t.autofit = "Table Grid", WD_TABLE_ALIGNMENT.CENTER, False
        tblPr = t._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "single"); el.set(qn("w:sz"), "4"); el.set(qn("w:space"), "0"); el.set(qn("w:color"), "D5D2C8")
            borders.append(el)
        tblPr.append(borders)
        mar = OxmlElement("w:tblCellMar")
        for edge, v in (("top", 60), ("left", 100), ("bottom", 60), ("right", 100)):
            el = OxmlElement(f"w:{edge}"); el.set(qn("w:w"), str(v)); el.set(qn("w:type"), "dxa"); mar.append(el)
        tblPr.append(mar)
        grid = t._tbl.tblGrid
        for i, gc in enumerate(grid.findall(qn("w:gridCol"))):
            gc.set(qn("w:w"), str(int(widths[i] * 567)))
        hdr = t.rows[0]
        trPr = hdr._tr.get_or_add_trPr()
        rep = OxmlElement("w:tblHeader"); rep.set(qn("w:val"), "true"); trPr.append(rep)
        for i, text in enumerate(header):
            c = hdr.cells[i]
            c.width = Cm(widths[i])
            shade(c, "0F2233")
            run = c.paragraphs[0].add_run(text)
            run.bold, run.font.size, run.font.color.rgb = True, Pt(size), RGBColor(0xFF, 0xFF, 0xFF)
            c.paragraphs[0].paragraph_format.space_after = Pt(0)
        for k, row in enumerate(rows):
            cells = t.add_row().cells
            cant = OxmlElement("w:cantSplit"); cant.set(qn("w:val"), "true"); t.rows[-1]._tr.get_or_add_trPr().append(cant)
            for i, text in enumerate(row):
                cells[i].width = Cm(widths[i])
                if k % 2:
                    shade(cells[i], "F7F5F0")
                par = cells[i].paragraphs[0]
                par.paragraph_format.space_after = Pt(0)
                run = par.add_run(text)
                run.font.size = Pt(size)
                run.bold = i == 0
                if i == 0:
                    run.font.color.rgb = NAVY
        self.d.add_paragraph().paragraph_format.space_after = Pt(2)

    def h1(self, text: str, numbered: bool = True):
        if numbered:
            self.n += 1
            text = f"{self.n}. {text}"
        self.d.add_heading(text, 1)

    def panel(self, title: str, image: str, content: tuple, wide: bool, whole: bool = False):
        self.d.add_heading(title, 3)
        self.img(image, TEXT_W if wide else 12.0, 9.5 if wide else 7.2, alt=f"Screenshot of the {title} panel", max_ratio=None if whole else 0.55 if wide else 0.75)
        self.p(content[0], "What it shows.", after=2, keep=True)
        self.p(content[1], "How to read it.", after=2, keep=True)
        self.p(content[2], "Use it to", after=8)

    # ------------------------------------------------------------ sections
    def cover(self):
        d = self.d
        self.img("executive_hero", TEXT_W, 6.0, photo=True, keep=False, alt="Aurelian Collection resort at sunset")
        d.add_paragraph().paragraph_format.space_after = Pt(30)
        t = d.add_paragraph(style="Title"); t.add_run("Dashboard User Guide")
        self.p("Hospitality Intelligence Platform", size=16, color=GOLD, after=4)
        self.p("What every dashboard, card and graph shows, how each figure is calculated, and how to read it.", size=12, color=MUTED, after=40)
        self.p(f"Aurelian Collection  |  Prototype release  |  {date.today():%d %B %Y}", size=10.5, color=MUTED)
        self.note("This guide describes the local prototype. Every figure in the screenshots comes from synthetic data generated for a fictional six-property chain, "
                  "with business date 12 November 2026. The numbers illustrate how the dashboards work; they are not real results.")
        d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        h = d.add_paragraph(); r = h.add_run("Contents"); r.bold = True; r.font.size = Pt(20); r.font.color.rgb = NAVY
        field(d.add_paragraph(), 'TOC \\o "1-2" \\h \\z \\u')

    def basics(self):
        self.h1("About this guide")
        self.p("The Hospitality Intelligence Platform brings data from the hotel, restaurant, waterpark, retail, spa, marketing, finance and building systems into one place. "
               "It presents that data in three groups of pages, listed in the left-hand menu:")
        self.bullets([("Performance.", "Eight dashboards that report what has happened, one for the executive team and one for each business domain, and the CxO Morning Report that answers the thirteen daily management questions."),
                      ("AI Control Tower.", "Six pages that look ahead: forecasts, energy and equipment predictions, recommended actions, alerts and the health of the models behind them."),
                      ("Platform.", "Where the data came from and whether it can be trusted, and a record of who approved or exported what.")])
        self.p("Chapter 2 explains the controls and conventions that are the same on every page. Chapter 3 covers the Executive dashboard and chapter 4 the CxO Morning Report. Chapters 5 to 11 take each domain dashboard in turn, first its cards and then each graph and table. "
               "Chapters 12 to 18 cover the AI Control Tower and the governance page. The appendices list the governed KPI definitions and what each role can see.")
        self.note("All amounts are shown in US dollars, the reporting currency. Local-currency amounts are converted at the daily exchange rate on the transaction date.")

        self.h1("Finding your way around")
        self.d.add_heading("The page layout", 2)
        self.p("Every page has the same three parts: the menu on the left, the filter bar across the top, and the content below it. The picture shows the Hotel Performance dashboard.")
        self.img("hotels_top", TEXT_W, 10.5, alt="Hotel Performance dashboard showing menu, filter bar and content")
        self.d.add_heading("The filter bar", 2)
        self.img("shell_topbar", TEXT_W, 3.0, alt="Filter bar with property, period, comparison and signed-in role")
        self.bullets([("Property.", "Choose one or more properties, then Apply. With nothing ticked, all properties you are allowed to see are included."),
                      ("Period.", "Last 7, 30 or 90 days, month, quarter or year to date, last 12 months, or a custom range. Periods end on the last closed business date. Longer periods are charted by week or month instead of by day."),
                      ("Compare with.", "The previous period of the same length, or the same period last year. This sets the comparison shown under every card."),
                      ("Signed in as.", "In the prototype this selects a role. Each role sees only its own properties and business domains, and menu items it cannot use are hidden. Appendix B lists the roles.")])
        self.p("Under the filter bar a single line confirms the dates, the comparison period and the properties in view, and shows the line styles used on every chart.")
        self.img("executive_ctx", TEXT_W, 1.6, alt="Context line showing dates, comparison and chart legend")

        self.d.add_heading("How to read a card", 2)
        self.p("Cards are the tiles at the top of each dashboard. Each one reports a single headline measure for the selected properties and period.")
        self.img("hotels_kpis", TEXT_W, 6.0, alt="Row of cards on the Hotel Performance dashboard")
        self.bullets([("The label", "names the measure. Where an information icon appears beside it, hover or tap to read the governed definition."),
                      ("The large figure", "is the value for the selected period. Amounts of a million or more are shortened to $1.23M and tens of thousands to $45K."),
                      ("The arrow and percentage", "show the change against the comparison period. For measures that are already percentages, such as occupancy, the change is given in percentage points (pts)."),
                      ("The colour", "shows whether the change is favourable, not simply whether it went up. Green is favourable and red is unfavourable. For costs, cancellations and queues a fall is green. "
                                     "Measures where neither direction is better, such as lead time, are left uncoloured.")])

        self.d.add_heading("How to read a graph", 2)
        self.p("The same conventions apply to every chart, so a line or colour always means the same thing.")
        self.bullets([("Solid blue line:", "actual results."), ("Grey dashed line:", "budget."),
                      ("Orange dashed line with a shaded band:", "forecast. The band is the 80% interval: the range in which the model expects the result to fall eight times out of ten."),
                      ("Solid grey line or grey bars:", "the comparison period, or an earlier forecast shown against what then happened."),
                      ("Dotted line:", "capacity, such as sellable rooms or licensed park capacity."),
                      ("Bar charts:", "a single measure is blue; stacked bars use one colour per category with a legend above."),
                      ("Heat maps:", "a grid where darker cells mean higher values. The figure is printed in each cell.")])
        self.p("Hover over any line, bar or cell to see the exact values for that point, as in the picture below.")
        self.img("shell_tooltip", 13.0, 7.0, alt="Line chart with a tooltip showing values for one day")
        self.d.add_heading("Tables, drill-through and export", 2)
        self.bullets([("Sorting.", "Click any column heading to sort by it; click again to reverse the order."),
                      ("Drill-through.", "Where a note says so, clicking a property row filters the whole platform to that property and opens its detail."),
                      ("Export.", "Roles with export permission see an Export link on each panel and an Export KPIs link above the cards. It downloads that panel as a CSV file, and each export is recorded in the audit trail.")])
        self.d.add_heading("AI-generated insights and recommended actions", 2)
        self.p("Between the cards and the graphs, two boxes summarise what matters. The left box states the main movements in plain language and names the measure each statement is based on. "
               "The right box lists the highest-priority recommendations for the view; click one to open it in the AI Control Tower.")
        self.img("executive_insights", TEXT_W, 6.0, keep=False, alt="AI-generated insights and recommended actions boxes")
        self.d.add_heading("On a phone", 2)
        self.p("On a narrow screen the menu collapses behind the button at the top left, the filters stack, cards form two columns and every graph takes the full width.", keep=True)
        self.img("mobile_executive", 6.2, 11.5, photo=True, keep=False, alt="Executive dashboard on a phone")

    def morning(self):
        m = G.MORNING
        self.h1(m["title"])
        self.p(m["intro"])
        self.p(m["who"] + ".", "Who uses it:")
        self.img("morning_top", TEXT_W, 10.2, keep=False, alt="CxO Morning Report")
        self.d.add_heading("How to read a question card", 2)
        self.img("morning_q9", 12.5, 9.0, alt="Example question card: Are guests satisfied?")
        self.bullets([(a + ".", b) for a, b in m["card"]])
        self.d.add_heading("How the verdict is decided", 2)
        self.p(m["verdict"])
        self.table(["KPI", "Management target"], [list(x) for x in m["targets"]], [8.5, 8.5])
        self.note("The targets are illustrative values for the prototype. In production they would come from the approved budget and brand standards.")
        self.d.add_heading("The insights box", 2)
        self.p("Above the cards, the left box counts how many questions are on track, to watch and needing action, and repeats every Act now item with its action. "
               "The right box lists the highest-priority recommendations from the AI Control Tower.")
        self.d.add_heading("The thirteen questions", 2)
        for n, question, kpis, actions in m["questions"]:
            self.d.add_heading(f"{n}. {question}", 3)
            self.img(f"morning_q{n}", 12.5, 8.5, alt=f"Question card: {question}")
            self.p(kpis, "KPIs.", after=2, keep=True)
            self.p(actions, "What it can advise.", after=8)
        self.d.add_heading("Who sees what", 2)
        self.p(m["roles"])
        self.p("Roles with export permission can download the whole report as a CSV file, one row per KPI with its question, verdict, answer and action.")

    def dashboards(self):
        for page in ["executive", "hotels", "restaurants", "waterpark", "retail", "spa", "marketing", "finance"]:
            title, who, intro = G.INTRO[page]
            self.h1(f"{title} dashboard")
            self.p(intro)
            self.p(who + ".", "Who uses it:")
            self.img(f"{page}_top", TEXT_W, 10.2, photo=page == "executive", keep=False, alt=f"{title} dashboard")
            self.d.add_heading("Cards", 2)
            self.img(f"{page}_kpis", TEXT_W, 7.0, alt=f"Cards on the {title} dashboard")
            self.table(["Card", "What it shows", "How it is calculated", "How to read it"], [list(c) for c in G.CARDS[page]], [3.1, 4.3, 5.4, 4.2])
            self.d.add_heading("Graphs and tables", 2)
            titles = INDEX[page]["panels"]
            assert len(titles) == len(G.PANELS[page]), (page, len(titles), len(G.PANELS[page]))
            spec = SPANS[page]
            for i, (t, content) in enumerate(zip(titles, G.PANELS[page])):
                self.panel(t, f"{page}_p{i}", content, spec[i] >= 7)
            if page == "executive":
                self.morning()

    def tower(self):
        for page in ["tower", "energy", "forecasting", "recommendations", "alerts", "models", "platform"]:
            t = G.TOWER[page]
            self.h1(("AI Control Tower: " if page != "platform" else "") + t["title"])
            self.p(t["intro"])
            self.p(t["who"] + ".", "Who uses it:")
            self.img(f"{page}_top", TEXT_W, 10.2, keep=False, alt=t["title"])
            if t["cards"]:
                self.d.add_heading("Cards", 2)
                self.img(f"{page}_kpis", TEXT_W, 6.0, alt=f"Cards on the {t['title']} page")
                self.table(["Card", "What it shows", "How it is calculated", "How to read it"], [list(c) for c in t["cards"]], [3.6, 4.4, 5.0, 4.0])
            if page == "platform":
                self.d.add_heading("The data flow strip", 2)
                self.img("platform_flow", TEXT_W, 3.2, alt="Data flow from sources through Bronze, Silver and Gold to AI")
                self.p("Five tiles follow the data from left to right: the number of source feeds, then the rows held in the Bronze (raw), Silver (cleaned and standardised) and Gold "
                       "(business-ready) layers, and finally the rows produced by the AI models.")
            if t.get("findings"):
                self.d.add_heading("Insights, findings and reminders", 2)
                self.p(t["findings"], keep=True)
                self.img(f"{page}_insights", TEXT_W, 7.5, keep=False, alt=f"Insights and findings boxes on the {t['title']} page")
            sections, spans = t.get("sections", {}), t.get("spans")
            if not sections:
                self.d.add_heading("Graphs and tables", 2)
            titles = INDEX[page]["panels"]
            assert len(titles) == len(t["panels"]), (page, len(titles), len(t["panels"]))
            for i, (title, content) in enumerate(zip(titles, t["panels"])):
                if i in sections:                      # the page groups its graphs under its own headings
                    self.d.add_heading(sections[i][0], 2)
                    self.p(sections[i][1], keep=True)
                self.panel(RENAME.get((page, i), title), f"{page}_p{i}", content, spans[i] >= 7 if spans else True, i in t.get("uncropped", []))
            for shot, title, text in G.DRAWERS:
                if shot.endswith({"recommendations": "recommendation", "alerts": "alert", "models": "model"}.get(page, "~")):
                    self.d.add_heading(title, 3)
                    self.p(text, keep=True)
                    self.img(shot, 9.5, 15.5, keep=False, alt=title)
        self.h1("Audit Trail")
        self.p("The Audit Trail page lists every approval, rejection, reassignment, alert action and data export, with the person, their role, the time, the status change and any comment. "
               "Enterprise and administrator roles see all records; other roles see their own. It is empty until the first action is taken.")

    def appendices(self):
        self.h1("Appendix A: governed KPI definitions", numbered=False)
        self.p("These definitions are held centrally and are the ones shown behind the information icon on each card.")
        kpis = json.loads((ROOT / "config" / "kpi_definitions.json").read_text(encoding="utf-8"))["kpis"]
        self.table(["KPI", "Business definition", "Rules and exclusions", "Owner"], [[k["kpi"], k["definition"], k["exclusions"] or "None.", k["owner"]] for k in kpis], [3.2, 5.6, 5.2, 3.0])
        self.h1("Appendix B: roles and access", numbered=False)
        self.p("The prototype offers eleven roles through the Signed in as selector. In production these would be Microsoft Entra ID groups.")
        self.table(["Role", "Can see", "Approve recommendations", "Export"], [list(r) for r in G.ROLES], [5.6, 6.6, 2.7, 2.1])

    def build(self):
        self.cover(); self.basics(); self.dashboards(); self.tower(); self.appendices()
        OUT.parent.mkdir(exist_ok=True)
        self.d.core_properties.title, self.d.core_properties.author = "Dashboard User Guide - Hospitality Intelligence Platform", "Aurelian Collection"
        self.d.save(OUT)
        print("wrote", OUT, f"{OUT.stat().st_size / 1e6:.1f} MB")


RENAME = {("forecasting", 0): "Actual versus forecast", ("recommendations", 0): "Recommendation list", ("alerts", 0): "Alert list", ("platform", 1): "Data quality rules"}
SPANS = {"executive": [8, 4, 8, 4, 6, 6, 4], "hotels": [6, 6, 8, 4, 7, 5, 7, 5], "restaurants": [6, 6, 8, 4, 7, 5], "waterpark": [8, 4, 7, 5, 7, 5],
         "retail": [6, 6, 6, 6, 6, 6, 6, 6], "spa": [7, 5, 5, 7, 6, 6], "marketing": [7, 5, 7, 5, 7, 5, 6], "finance": [6, 6, 6, 6, 6, 6, 7, 5]}

if __name__ == "__main__":
    Guide().build()
