"""Build docs/Database Schema.drawio: the logical schema of the reporting database and its relationships.

It reads powerbi/model.json (written by tools/build_powerbi.py), so the diagram always shows the same
tables, keys and relationships as the Power BI model and the Gold and AI layers they are loaded from.

Pages: a fact-by-dimension matrix, then one entity-relationship diagram per subject area.

    python tools/build_powerbi.py
    python tools/build_schema_diagram.py
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zlib
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MODEL = json.loads((ROOT / "powerbi" / "model.json").read_text(encoding="utf-8"))
OUT = ROOT / "docs" / "Database Schema.drawio"
PREVIEW = ROOT / "data" / "docs_build" / "schema"
NAVY, GOLD, MUTED, LINE = "#0F2233", "#B8894A", "#5F6B76", "#C9CED3"
T = {t["name"]: t for t in MODEL["tables"]}
RELS = MODEL["relationships"]
DIMS = [t["name"] for t in MODEL["tables"] if t["kind"] == "dim"]
FACTS = [t["name"] for t in MODEL["tables"] if t["kind"] == "fact"]
AREAS = [
    ("Executive and Hotels", ["Executive Daily", "Revenue by Vertical", "Room Occupancy", "Room Revenue"]),
    ("Restaurants to Spa", ["Restaurant Covers", "Waterpark Daily", "Retail Sales", "Spa Appointments"]),
    ("Marketing and Finance", ["Marketing", "Finance", "Budget"]),
    ("AI Control Tower", ["Forecasts", "Recommendations", "Alerts", "Models"]),
    ("Energy and Assets", ["Energy Daily", "Energy End Use", "Energy Forecast", "Device Risk", "Contract Reminders", "Energy Findings"]),
]
W, ROW, HEAD, GAP = 250, 20, 44, 70
SHORT = {"string": "text", "double": "decimal", "int64": "integer", "dateTime": "date", "boolean": "yes/no"}


class Page:
    def __init__(self, name: str):
        self.name, self.cells, self.n, self.boxes, self.lines = name, [], 0, [], []

    def nid(self) -> str:
        self.n += 1
        return f"{zlib.crc32(self.name.encode()) % 9973}-{self.n}"

    def cell(self, value: str, style: str, x, y, w, h, parent: str = "1") -> str:
        i = self.nid()
        self.cells.append(f'<mxCell id="{i}" value="{escape(value, quote=True)}" style="{style}" vertex="1" parent="{parent}">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return i

    def edge(self, src: str, dst: str, dashed: bool, ports: tuple | None = None, via: list | None = None) -> None:
        """Many-to-one line from a fact to a dimension. `ports` fixes where it leaves and arrives; `via` are the corner points."""
        if ports:
            ex, ey, nx, ny = ports
            route = f"edgeStyle=orthogonalEdgeStyle;exitX={ex:.3f};exitY={ey};exitDx=0;exitDy=0;entryX={nx:.3f};entryY={ny};entryDx=0;entryDy=0;"
        else:
            route = "edgeStyle=entityRelationEdgeStyle;"
        style = (route + "html=1;rounded=1;endArrow=ERmandOne;endFill=0;startArrow=ERmany;startFill=0;"
                 f"strokeColor={GOLD if dashed else MUTED};strokeWidth=1;" + ("dashed=1;" if dashed else ""))
        pts = ('<Array as="points">' + "".join(f'<mxPoint x="{x:.0f}" y="{y:.0f}"/>' for x, y in via) + "</Array>") if via else ""
        self.cells.append(f'<mxCell id="{self.nid()}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">'
                          f'<mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>')

    def text(self, value: str, x, y, w, h, size=12, bold=False, color=NAVY) -> None:
        self.cell(value, f"text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;whiteSpace=wrap;fontSize={size};fontColor={color};fontStyle={1 if bold else 0};", x, y, w, h)
        self.boxes.append(("text", x, y, w, h, value, size))

    def entity(self, table: str, x: int, y: int, fk_cols: set[str]) -> dict[str, str]:
        """Draw one table; return {column name: row cell id} for its key rows."""
        t = T[table]
        dim = t["kind"] == "dim"
        cols = t["columns"]
        h = HEAD + ROW * len(cols) + 6
        fill = NAVY if dim else "#F1F4F7"
        head = f'{table}<br><span style="font-size:9px;font-weight:normal;">{t["source"]}</span>'
        box = self.cell(head, f"swimlane;html=1;fontStyle=1;fontSize=13;startSize={HEAD};horizontal=1;collapsible=0;rounded=1;arcSize=4;"
                              f"fillColor={fill};fontColor={'#FFFFFF' if dim else NAVY};strokeColor={NAVY if dim else LINE};swimlaneFillColor=#FFFFFF;", x, y, W, h)
        rows = {}
        for k, col in enumerate(cols):
            pk = dim and k == 0
            fk = col["name"] in fk_cols
            tag = "PK" if pk else "FK" if fk else ""
            label = f'<b>{tag}</b>&nbsp;&nbsp;{col["name"]}' if tag else f'&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{col["name"]}'
            label += f' <span style="color:#8A939B;">{SHORT.get(col["type"], col["type"])}</span>'
            rid = self.cell(label, f"text;html=1;strokeColor=none;fillColor={'#FBF3E4' if tag else 'none'};align=left;verticalAlign=middle;spacingLeft=8;overflow=hidden;"
                                   "rotatable=0;points=[[0,0.5],[1,0.5]];portConstraint=eastwest;fontSize=11;fontColor=#1E1E1E;", 0, HEAD + k * ROW, W, ROW, parent=box)
            rows[col["name"]] = (rid, y + HEAD + k * ROW + ROW / 2)
        rows["_box"] = (box, x, y, h)
        self.boxes.append(("entity", x, y, W, h, table, dim))
        return rows

    def xml(self) -> str:
        return (f'<diagram id="{zlib.crc32(self.name.encode()) % 99991}" name="{escape(self.name, quote=True)}"><mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" '
                'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1654" pageHeight="1169" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
                + "".join(self.cells) + "</root></mxGraphModel></diagram>")


def legend(p: Page, x: int, y: int) -> None:
    p.text("<b>How to read this</b><br>Navy tables are dimensions (one row per thing). Grey tables are facts (many rows, measured over time).<br>"
           "<b>PK</b> is a table's key; <b>FK</b> is a column that points at another table's key.<br>"
           "Each line joins a fact's FK to a dimension's PK: many fact rows to one dimension row (the crow's foot is the 'many' end).<br>"
           "Grey solid lines are relationships defined in the Power BI model. Gold dashed lines exist in the data but are left out of the model, "
           "because every fact already joins to Property directly.<br>The small text under each name is the lakehouse table it is loaded from.",
           x, y, 520, 150, 11, color="#1E1E1E")


def matrix() -> Page:
    p = Page("Overview matrix")
    p.text("Aurelian Collection: logical schema of the reporting database", 40, 30, 1100, 34, 20, True)
    p.text("Which fact table joins to which dimension. A filled cell is a relationship defined in the model; the label is the joining column.", 40, 68, 1200, 24, 12, color=MUTED)
    x0, y0, cw, ch, lw = 40, 110, 150, 30, 250
    p.cell("Fact table  (rows are loaded from)", f"rounded=0;html=1;fillColor={NAVY};fontColor=#FFFFFF;strokeColor=#FFFFFF;fontStyle=1;align=left;spacingLeft=8;fontSize=12;", x0, y0, lw, ch + 10)
    for j, d in enumerate(DIMS):
        p.cell(d, f"rounded=0;html=1;fillColor={NAVY};fontColor=#FFFFFF;strokeColor=#FFFFFF;fontStyle=1;fontSize=12;", x0 + lw + j * cw, y0, cw, ch + 10)
    area_of = {f: a for a, fs in AREAS for f in fs}
    y = y0 + ch + 10
    for a, fs in AREAS:
        p.cell(a, f"rounded=0;html=1;fillColor=#F3E9D8;fontColor={NAVY};strokeColor=#FFFFFF;fontStyle=1;align=left;spacingLeft=8;fontSize=11;", x0, y, lw + len(DIMS) * cw, 22)
        y += 22
        for f in fs:
            p.cell(f'{f} <span style="color:#8A939B;font-size:9px;">{T[f]["source"]}</span>',
                   f"rounded=0;html=1;fillColor=#F1F4F7;fontColor={NAVY};strokeColor=#FFFFFF;align=left;spacingLeft=8;fontSize=11;", x0, y, lw, ch)
            for j, d in enumerate(DIMS):
                r = next((r for r in RELS if r["from"] == f and r["to"] == d and r["in_model"]), None)
                p.cell(r["from_column"] if r else "", f"rounded=0;html=1;fillColor={'#DCE9F7' if r else '#FAFAFA'};fontColor={NAVY};strokeColor=#FFFFFF;fontSize=10;",
                       x0 + lw + j * cw, y, cw, ch)
            y += ch
    assert set(area_of) == set(FACTS), set(FACTS) ^ set(area_of)
    n_in = sum(1 for r in RELS if r["in_model"])
    p.text(f"{len(DIMS)} dimensions, {len(FACTS)} fact tables, {n_in} relationships in the model, all many-to-one from fact to dimension with single-direction filtering. "
           "The next pages draw each subject area with its columns.", x0, y + 16, 1200, 40, 12, color="#1E1E1E")
    p.boxes.append(("entity", x0, y0, lw + len(DIMS) * cw, y - y0, "matrix", False))
    return p


def area(name: str, facts: list[str]) -> Page:
    p = Page(name)
    rels = [r for r in RELS if r["from"] in facts or (not r["in_model"] and r["to"] in {x["to"] for x in RELS if x["from"] in facts} and r["from"] in {x["to"] for x in RELS if x["from"] in facts})]
    dims = [d for d in DIMS if any(r["to"] == d or r["from"] == d for r in rels)]
    p.text(f"{name}: tables and relationships", 40, 30, 1100, 34, 20, True)
    top, bottom = facts[: (len(facts) + 1) // 2], facts[(len(facts) + 1) // 2:]
    height = lambda t: HEAD + ROW * len(T[t]["columns"]) + 6  # noqa: E731
    width = max(len(top), len(bottom), len(dims)) * (W + GAP) - GAP
    y_top = 90
    lane = 14                                            # vertical space each relationship line gets in the gap between rows
    n_top = sum(1 for r in rels if r["from"] in top)
    n_bot = sum(1 for r in rels if r["from"] in bottom)
    y_dim = y_top + max(height(t) for t in top) + 60 + lane * n_top
    y_bot = y_dim + max(height(d) for d in dims) + 60 + lane * n_bot
    rows = {}

    def place(tables, y):
        x = 40 + (width - (len(tables) * (W + GAP) - GAP)) / 2
        for t in tables:
            fk = {r["from_column"] for r in rels if r["from"] == t}
            rows[t] = p.entity(t, round(x), y, fk)
            x += W + GAP

    place(top, y_top); place(dims, y_dim); place(bottom, y_bot)
    # Facts sit above and below the dimensions. Each line leaves the edge of the fact that faces the dimensions, runs
    # along its own lane in the gap, and arrives on the facing edge of the dimension, so no line crosses a table.
    for group, above in ((top, True), (bottom, False)):
        mine = sorted((r for r in rels if r["from"] in group), key=lambda r: (rows[r["from"]]["_box"][1], rows[r["to"]]["_box"][1]))
        out_n = {f: sum(1 for r in mine if r["from"] == f) for f in group}
        in_n = {d: sum(1 for r in mine if r["to"] == d) for d in dims}
        out_i, in_i = dict.fromkeys(group, 0), dict.fromkeys(dims, 0)
        dims_bottom = y_dim + max(height(d) for d in dims)
        for k, r in enumerate(mine):
            f, d = r["from"], r["to"]
            fb, db = rows[f]["_box"], rows[d]["_box"]
            out_i[f] += 1; in_i[d] += 1
            ex, nx = out_i[f] / (out_n[f] + 1), in_i[d] / (in_n[d] + 1)
            y_lane = (y_dim - 30 - lane * k) if above else (dims_bottom + 30 + lane * k)
            p.edge(fb[0], db[0], False, (ex, 1 if above else 0, nx, 0 if above else 1), [(fb[1] + ex * W, y_lane), (db[1] + nx * W, y_lane)])
            p.lines.append((f, d, False))
    for r in rels:
        if not r["in_model"] and r["from"] in rows and r["to"] in rows:      # dimension to dimension, side to side
            p.edge(rows[r["from"]][r["from_column"]][0], rows[r["to"]][r["to_column"]][0], True)
            p.lines.append((r["from"], r["to"], True))
    y_end = (y_bot + max(height(t) for t in bottom)) if bottom else (y_dim + max(height(d) for d in dims))
    legend(p, 40, y_end + 40)
    return p


def preview(p: Page, i: int) -> None:
    """Rough picture of the layout, to check that nothing overlaps."""
    boxes = [b for b in p.boxes if b[0] == "entity"]
    w = int(max(b[1] + b[3] for b in p.boxes) + 60)
    h = int(max(b[2] + b[4] for b in p.boxes) + 60)
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("C:/Windows/Fonts/calibri.ttf", 15)
    except OSError:
        f = ImageFont.load_default()
    centre = {b[5]: (b[1] + b[3] / 2, b[2] + b[4] / 2) for b in boxes}
    for a, b, dashed in p.lines:
        d.line([centre[a], centre[b]], fill="#B8894A" if dashed else "#9AA5AE", width=1)
    for _, x, y, bw, bh, name, dim in boxes:
        d.rectangle([x, y, x + bw, y + bh], outline=NAVY, fill="#E7ECF1" if dim else "#FFFFFF", width=2)
        d.text((x + 8, y + 8), name, fill=NAVY, font=f)
    for a in boxes:
        for b in boxes:
            if a is not b and a[5] < b[5] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3] and a[2] < b[2] + b[4] and b[2] < a[2] + a[4]:
                print(f"  OVERLAP on {p.name}: {a[5]} and {b[5]}")
    PREVIEW.mkdir(parents=True, exist_ok=True)
    im.save(PREVIEW / f"page-{i}.png")


def main() -> None:
    pages = [matrix()] + [area(n, f) for n, f in AREAS]
    xml = '<mxfile host="app.diagrams.net" agent="tools/build_schema_diagram.py" version="24.7.17" type="device">' + "".join(p.xml() for p in pages) + "</mxfile>"
    ET.fromstring(xml)                                   # must be well-formed
    OUT.write_text(xml, encoding="utf-8", newline="\n")
    for i, p in enumerate(pages, 1):
        preview(p, i)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e3:.0f} KB): {len(pages)} pages, {len(T)} tables, {len(RELS)} relationships")


if __name__ == "__main__":
    main()
