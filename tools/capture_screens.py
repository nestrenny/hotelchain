"""Capture screenshots of every dashboard card block and panel from the running app, for the
user guide and the leadership deck. Uses the locally installed Microsoft Edge through Playwright
(no browser download).  Requires the app on http://localhost:8000.

    python tools/capture_screens.py
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "data" / "docs_build" / "shots"
BASE = "http://localhost:8000/#/"
DASH = ["executive", "hotels", "restaurants", "waterpark", "retail", "spa", "marketing", "finance"]
TOWER = ["tower", "energy", "forecasting", "recommendations", "alerts", "models", "platform"]


def settle(page, selector=".panel"):
    page.wait_for_selector(selector, timeout=20000)
    page.wait_for_function("!document.querySelector('#content').hasAttribute('aria-busy')", timeout=20000)
    page.wait_for_timeout(700)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2, color_scheme="light")
        page = ctx.new_page()
        page.goto(BASE + "executive")
        settle(page)
        for name in DASH + TOWER:
            page.goto(BASE + name)
            page.reload()
            settle(page)
            page.screenshot(path=str(OUT / f"{name}_top.png"))
            items = []
            for sel, tag in [(".hero", "hero"), (".ctx", "ctx"), (".kpis", "kpis"), (".insights", "insights"), (".flow", "flow"), (".banner", "banner")]:
                loc = page.locator(sel).first
                if loc.count():
                    loc.screenshot(path=str(OUT / f"{name}_{tag}.png"))
                    items.append(tag)
            panels = page.locator("#content .panel")
            titles = []
            for i in range(panels.count()):
                el = panels.nth(i)
                el.scroll_into_view_if_needed()
                page.wait_for_timeout(120)
                el.screenshot(path=str(OUT / f"{name}_p{i}.png"))
                titles.append(el.locator("h3").first.inner_text())
            index[name] = {"blocks": items, "panels": titles}
            print(f"{name:<16}{len(titles):>3} panels  {items}")
        # filter bar, a tooltip, and the detail drawers
        page.goto(BASE + "hotels")
        page.reload()
        settle(page)
        page.locator("#topbar").screenshot(path=str(OUT / "shell_topbar.png"))
        page.locator("#sidebar").screenshot(path=str(OUT / "shell_sidebar.png"))
        box = page.locator("#content .panel .chart svg").first.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.5)
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "shell_tooltip.png"), clip={"x": box["x"] - 30, "y": box["y"] - 70, "width": box["width"] + 60, "height": box["height"] + 110})
        for name, shot in [("recommendations", "drawer_recommendation"), ("alerts", "drawer_alert"), ("models", "drawer_model")]:
            page.goto(BASE + name)
            page.reload()
            settle(page)
            page.locator(".tbl tr.click").first.click()
            page.wait_for_selector("#drawer:not([hidden]) h2")
            page.wait_for_timeout(600)
            page.locator("#drawer").screenshot(path=str(OUT / f"{shot}.png"))
        # phone layout of the executive dashboard
        m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="light")
        mp = m.new_page()
        mp.goto(BASE + "executive")
        settle(mp)
        mp.screenshot(path=str(OUT / "mobile_executive.png"))
        browser.close()
    (OUT / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print("saved to", OUT)


if __name__ == "__main__":
    main()
