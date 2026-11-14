"""Build docs/Platform Explainer.mp4 - a narrated, guided tour of the running app.

A virtual camera moves over full-page captures of the real application: it opens wide, eases in on
the area being discussed, spotlights it with a highlight and a pointer, then moves on. Each camera
move is timed to its own line of narration, so picture and voice stay together.

Story: an introduction, then seven hotel-industry problems, each followed by the screens that
solve it, and a close.

Everything runs offline. Narration uses the Piper neural voice in data/voices (no text is sent to
any online service); frames are drawn with Pillow; the ffmpeg bundled with imageio-ffmpeg encodes.

    pip install piper-tts imageio-ffmpeg playwright
    python -m piper.download_voices en_US-ryan-high --download-dir data/voices
    python tools/build_video.py            # the app must be running on http://localhost:8000
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data" / "docs_build" / "video"
VOICE = ROOT / "data" / "voices" / "en_US-ryan-high.onnx"
OUT = ROOT / "docs" / "Platform Explainer.mp4"
BASE = "http://localhost:8000/#/"
W, H, FPS = 1920, 1080, 30
PACE = 1.22                      # >1 is slower; 1.22 gives an unhurried presenter pace of about 155 words a minute
NAVY, GOLD, WHITE, SOFT = (15, 34, 51), (217, 173, 108), (255, 255, 255), (215, 221, 227)

# page -> {focus key: CSS selector, or "panel:<start of the panel title>"}
FOCUS = {
    "home": {"menu": ".splash-rail", "welcome": ".splash-panel"},
    "platform": {"flow": ".flow", "quality": "panel:Data quality rules"},
    "executive": {"cards": ".kpis", "revenue": "panel:Revenue: actual", "compare": "panel:Property comparison"},
    "forecasting": {"chart": "panel:actual versus forecast", "cards": ".kpis"},
    "hotels": {"pickup": "panel:Reservation pickup", "actions": ".insights .ins:nth-child(2)"},
    "waterpark": {"queues": "panel:Average queue time"},
    "restaurants": {"menu": "panel:Menu performance"},
    "retail": {"stock": "panel:Inventory exceptions"},
    "alerts": {"cards": ".kpis", "list": "panel:alert(s)"},
    "energy": {"chart": "panel:Energy use: actual", "findings": ".insights .ins:nth-child(2)", "devices": "panel:Devices most likely to fail"},
    "recommendations": {"list": "#content .panel"},
    "drawer": {"evidence": "#drawer"},
    "drawer2": {"decide": "#drawer textarea"},        # the same panel, scrolled to the decision controls
}

C = "card"
STORY = [
    # (kind, shot, focus, label, caption / headline, narration)
    ("title", None, None, "AURELIAN COLLECTION", "From fragmented reporting to predictive hospitality",
     "Running a luxury hotel group means running many businesses at once. Hotels, restaurants, waterparks, shops and spas, across several countries."),
    (C, None, None, "THE CHALLENGE", "Simple questions. Slow answers.",
     "Each one has its own systems, and its own version of the numbers. So even simple questions, like how full are we, and are we making money, can take days to answer."),
    ("app", "home", None, "THE PLATFORM", "One platform for the whole group",
     "This is the Hospitality Intelligence Platform. A working prototype that brings all of it together."),
    ("app", "home", "menu", "THE PLATFORM", "Every line of business, in one menu",
     "Every part of the business is here, in a single menu. Let's look at the problems it solves, one at a time."),

    (C, None, None, "PROBLEM 1 OF 7", "The data is scattered",
     "The first problem is scattered data. Different hotels use different booking systems, and every property calculates occupancy and profit in its own way."),
    ("app", "platform", "flow", "ONE SOURCE OF TRUTH", "36 source feeds, cleaned and checked",
     "The platform collects thirty six feeds from every source system. Then it cleans, matches and checks them, in three stages."),
    ("app", "executive", None, "ONE SOURCE OF TRUTH", "The whole group on one page",
     "The result is one trusted view. This is the executive dashboard, covering every property and every line of business."),
    ("app", "executive", "cards", "ONE SOURCE OF TRUTH", "Each measure defined once",
     "Revenue, occupancy, room rate and profit are each defined once, and shown the same way to everyone."),
    ("app", "executive", "compare", "ONE SOURCE OF TRUTH", "Compare properties side by side",
     "Any leader can compare properties side by side, then open the one that needs attention."),

    (C, None, None, "PROBLEM 2 OF 7", "Reporting only looks backwards",
     "The second problem. Reports only look backwards. By the time they arrive, the month is over, and next month is anyone's guess."),
    ("app", "forecasting", "chart", "LOOKING AHEAD", "Forecasts up to 90 days ahead",
     "Here, every part of the business is forecast up to ninety days ahead. The blue line is what happened. The orange line is what the model expects, with its range of confidence."),
    ("app", "forecasting", "cards", "LOOKING AHEAD", "Tested against what really happened",
     "Each forecast is tested against real outcomes. For this hotel, the error is about ten percent. Better than a simple baseline."),

    (C, None, None, "PROBLEM 3 OF 7", "Revenue slips away unnoticed",
     "Third, revenue slips away. Rooms sit empty in quiet weeks. Rates stay low when demand is high. And bookings cancel too late to resell."),
    ("app", "hotels", "pickup", "PROTECTING REVENUE", "Bookings against the forecast",
     "The hotel dashboard shows the next sixty days. Bookings already made, in blue, against the forecast, in orange."),
    ("app", "hotels", "actions", "PROTECTING REVENUE", "Advice on rates, demand and cancellations",
     "Where a week looks soft, the platform recommends a promotion. Where demand is strong, it recommends raising rates. And it flags the reservations most likely to cancel."),

    (C, None, None, "PROBLEM 4 OF 7", "Every department works in the dark",
     "Fourth, each department works in the dark."),
    ("app", "waterpark", "queues", "A VIEW FOR EVERY TEAM", "Queues by attraction and hour",
     "So every team gets its own view. Park managers see queue times, by attraction, and by hour."),
    ("app", "restaurants", "menu", "A VIEW FOR EVERY TEAM", "Every dish ranked by profit",
     "Chefs see every dish ranked by profit, and which ones to reprice."),
    ("app", "retail", "stock", "A VIEW FOR EVERY TEAM", "What will run out, and what to order",
     "And retail managers see which products will run out before the next delivery, and how much to order."),

    (C, None, None, "PROBLEM 5 OF 7", "Costs creep up quietly",
     "Fifth, costs creep up quietly. A utility bill jumps. A supplier raises prices. Discounts appear that nobody approved."),
    ("app", "alerts", "list", "CATCHING PROBLEMS EARLY", "Alerts for anything unusual",
     "The platform watches for anything unusual, and raises an alert. To test it, we hid several problems in the data. It found every one."),
    ("app", "alerts", "cards", "CATCHING PROBLEMS EARLY", "Assigned, tracked and resolved",
     "Each alert is assigned to an owner, and tracked until it is resolved."),

    (C, None, None, "PROBLEM 6 OF 7", "Energy is wasted. Equipment fails without warning.",
     "Sixth, buildings waste energy, and equipment fails without warning."),
    ("app", "energy", "chart", "ENERGY AND ASSETS", "Energy used against energy justified",
     "The energy page compares what each property used, with what its occupancy and weather justify. The gap between the lines is waste."),
    ("app", "energy", "findings", "ENERGY AND ASSETS", "Leaks, anomalies and lapsing contracts",
     "It brings the important findings to the top. A probable water leak. An energy anomaly. And service contracts about to lapse."),
    ("app", "energy", "devices", "ENERGY AND ASSETS", "6,000 devices scored for failure risk",
     "It also scores more than six thousand devices, from door locks to chillers, for their chance of failing in the next thirty days. So they can be serviced first."),

    (C, None, None, "PROBLEM 7 OF 7", "Insight without action. AI nobody trusts.",
     "And finally. Insight is useless without action, and people are right to be wary of A.I. acting on its own."),
    ("app", "recommendations", "list", "PEOPLE IN CONTROL", "Prioritised, costed recommendations",
     "So every recommendation is listed with its priority, its estimated value, and the model's confidence."),
    ("app", "drawer", "evidence", "PEOPLE IN CONTROL", "The evidence behind every recommendation",
     "Open one, and you see the evidence behind it."),
    ("app", "drawer2", "decide", "PEOPLE IN CONTROL", "A person approves or rejects",
     "Nothing happens automatically. An authorised person approves or rejects it, and every decision is recorded."),

    ("close", None, None, "AURELIAN COLLECTION", "One trusted view. Forecasts ahead. People in control.",
     "One trusted view. Forecasts for every part of the business. And clear actions, with people in control. What you have seen is a working prototype, running on synthetic data. "
     "The next step is to connect the real systems. Thank you for watching."),
]


# ----------------------------------------------------------------------------- capture
def capture() -> None:
    """Full-page captures at 2x, plus the position of every focus area."""
    from playwright.sync_api import sync_playwright

    js = """(sels) => { const out = {};
      const panel = (t) => [...document.querySelectorAll('#content .panel')].find((p) => (p.querySelector('h3') || {}).innerText && p.querySelector('h3').innerText.includes(t));
      for (const [k, s] of Object.entries(sels)) { const el = s.startsWith('panel:') ? panel(s.slice(6)) : document.querySelector(s);
        if (el) { const r = el.getBoundingClientRect(); out[k] = [r.x + scrollX, r.y + scrollY, r.width, r.height]; } } return out; }"""
    boxes = {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        pg = b.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=2, color_scheme="light").new_page()
        for page, sels in FOCUS.items():
            pg.set_viewport_size({"width": 1600, "height": 900})
            pg.goto(BASE + ("recommendations" if page.startswith("drawer") else page))
            pg.reload()
            pg.wait_for_selector(".splash-rail a" if page == "home" else ".panel", timeout=30000)
            pg.wait_for_function("!document.querySelector('#content').hasAttribute('aria-busy')")
            pg.wait_for_timeout(1500)
            if page.startswith("drawer"):
                pg.locator(".tbl tr.click").first.click()
                pg.wait_for_selector("#drawer:not([hidden]) h2")
                if page == "drawer2":
                    pg.evaluate("const d = document.querySelector('#drawer'); d.scrollTop = d.querySelector('textarea').offsetTop - 330")
                pg.wait_for_timeout(700)
            elif page != "home":
                pg.set_viewport_size({"width": 1600, "height": max(900, pg.evaluate("document.documentElement.scrollHeight"))})
                pg.wait_for_timeout(900)
            pg.screenshot(path=str(WORK / f"page-{page}.png"))
            boxes[page] = {k: [v * 2 for v in box] for k, box in pg.evaluate(js, sels).items()}
            missing = set(sels) - set(boxes[page])
            print(f"  captured {page:<16}{'  MISSING ' + str(missing) if missing else ''}")
        b.close()
    d = boxes["drawer2"].get("decide")
    if d:
        d[1] -= 60
        d[3] += 190                      # include the approve / reject buttons under the comment box
    (WORK / "boxes.json").write_text(json.dumps(boxes), encoding="utf-8")


# ----------------------------------------------------------------------------- narration
def narrate(texts: list[str]) -> list[np.ndarray]:
    from piper import PiperVoice, SynthesisConfig

    voice = PiperVoice.load(str(VOICE))
    cfg = SynthesisConfig(length_scale=PACE, noise_scale=0.6, noise_w_scale=0.8)
    clips = []
    for i, text in enumerate(texts):
        path = WORK / f"say-{i:02d}.wav"
        with wave.open(str(path), "wb") as wf:
            voice.synthesize_wav(text, wf, syn_config=cfg)
        with wave.open(str(path)) as wf:
            rate = wf.getframerate()
            clips.append(np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32))
    narrate.rate = rate
    return clips


# ----------------------------------------------------------------------------- drawing helpers
def font(kind: str, size: int):
    return ImageFont.truetype("C:/Windows/Fonts/" + {"head": "georgia.ttf", "body": "segoeui.ttf", "bold": "segoeuib.ttf", "semi": "seguisb.ttf"}[kind], size)


def wrap(d, text, f, width):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=f) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (u * (u * 6 - 15) + 10)          # smootherstep: gentle start and stop


def lerp(a, b, u):
    return tuple(x + (y - x) * u for x, y in zip(a, b))


def hero_base(blur: bool) -> Image.Image:
    im = Image.open(ROOT / "img" / "hero.png").convert("RGB")
    r = max(2112 / im.width, 1188 / im.height)                     # 10% larger than the frame, for a slow push-in
    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    im = im.crop(((im.width - 2112) // 2, (im.height - 1188) // 2, (im.width - 2112) // 2 + 2112, (im.height - 1188) // 2 + 1188))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(14))
    shade = Image.new("RGB", im.size, (9, 20, 31))
    grad = Image.new("L", im.size)
    gd = ImageDraw.Draw(grad)
    for x in range(im.width):
        gd.line([(x, 0), (x, im.height)], fill=int(255 * (0.90 - (0.55 if not blur else 0.22) * (x / im.width) ** 1.5)))
    return Image.composite(shade, im, grad)


def card_layer(kind: str, label: str, head: str) -> Image.Image:
    """Text for a title, problem or closing card, on a transparent layer."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((130, 300 if kind != C else 330), label, font=font("bold", 30), fill=GOLD + (255,))
    f = font("head", 92 if kind == "title" else 84)
    y = 360 if kind != C else 392
    for line in wrap(d, head, f, 1350):
        d.text((126, y), line, font=f, fill=WHITE + (255,))
        y += int(f.size * 1.16)
    if kind == "title":
        d.text((130, y + 30), "Hospitality Intelligence Platform  |  a guided tour", font=font("body", 40), fill=SOFT + (255,))
    if kind == "close":
        d.text((130, y + 34), "Working prototype on synthetic data", font=font("body", 36), fill=SOFT + (255,))
    return layer


def caption_layer(label: str, text: str) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f, fl = font("semi", 40), font("bold", 22)
    width = int(max(d.textlength(text, font=f), d.textlength(label, font=fl))) + 76
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([66, H - 196, 66 + width, H - 60], 20, fill=(0, 0, 0, 120))
    layer = Image.alpha_composite(layer, shadow.filter(ImageFilter.GaussianBlur(16)))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([60, H - 200, 60 + width, H - 66], 20, fill=NAVY + (244,), outline=(255, 255, 255, 46), width=2)
    d.text((98, H - 182), label, font=fl, fill=GOLD + (255,))
    d.text((97, H - 148), text, font=f, fill=WHITE + (255,))
    tag = "Aurelian Collection  |  prototype on synthetic data"
    ft = font("body", 22)
    tw = int(d.textlength(tag, font=ft))
    d.rounded_rectangle([W - tw - 84, H - 118, W - 56, H - 70], 24, fill=NAVY + (224,))
    d.text((W - tw - 70, H - 109), tag, font=ft, fill=SOFT + (255,))
    return layer


def pointer() -> Image.Image:
    s = 4
    im = Image.new("RGBA", (64 * s, 80 * s), (0, 0, 0, 0))
    pts = [(6, 4), (6, 58), (20, 45), (30, 68), (40, 64), (30, 41), (50, 41)]
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([((x + 3) * s, (y + 5) * s) for x, y in pts], fill=(0, 0, 0, 130))
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(3 * s)))
    d = ImageDraw.Draw(im)
    d.polygon([(x * s, y * s) for x, y in pts], fill=(255, 255, 255, 255))
    d.line([(x * s, y * s) for x, y in pts + pts[:1]], fill=(15, 34, 51, 255), width=3 * s, joint="curve")
    return im.resize((64, 80), Image.LANCZOS)


def paste(frame: Image.Image, layer: Image.Image, alpha: float, dy: int = 0) -> None:
    if alpha <= 0:
        return
    mask = layer.getchannel("A") if alpha >= 1 else layer.getchannel("A").point(lambda v: int(v * alpha))
    frame.paste(layer, (0, dy), mask)


DIM = [int(v * 0.42 + 6) for v in range(256)] * 3


# ----------------------------------------------------------------------------- camera
def cam_wide(img) -> tuple:
    return (0.0, 0.0, float(img.width), img.width * 9 / 16)


def cam_focus(img, box) -> tuple:
    x, y, bw, bh = box
    w = max(bw * 1.16, (bh * 1.22) * 16 / 9, img.width / 2.3)       # never zoom in more than about 2.3x
    w = min(w, img.width)
    h = w * 9 / 16
    cy = y + bh / 2 + h * 0.05 if bh * 1.1 <= h else y + h / 2 - h * 0.07      # tall areas: frame their top
    left = min(max(x + bw / 2 - w / 2, 0), img.width - w)
    top = min(max(cy - h / 2, 0), max(img.height - h, 0))
    return (left, top, w, h)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    if "--no-capture" not in sys.argv:
        print("Capturing the application")
        capture()
    boxes = json.loads((WORK / "boxes.json").read_text(encoding="utf-8"))
    print("Narrating")
    clips = narrate([s[5] for s in STORY])
    rate = narrate.rate

    # ---- timeline: one beat per narration line
    beats, t = [], 0.0
    for i, (kind, shot, focus, label, head, say) in enumerate(STORY):
        spoken = len(clips[i]) / rate
        nxt = STORY[i + 1] if i + 1 < len(STORY) else None
        gap = 1.6 if nxt is None else 0.75 if (nxt[0] != kind or nxt[1] != shot) else 0.5
        lead = 0.55 if kind != "app" else 0.45
        beats.append(dict(i=i, kind=kind, shot=shot, focus=focus, label=label, head=head, say=say, start=t, lead=lead, spoken=spoken, dur=lead + spoken + gap))
        t += beats[-1]["dur"]
    total = t

    # ---- audio track
    track = np.zeros(int(total * rate) + rate, dtype=np.float32)
    for bt, clip in zip(beats, clips):
        clip = clip.copy()
        n = min(len(clip), int(0.012 * rate))
        clip[:n] *= np.linspace(0, 1, n)
        clip[-n:] *= np.linspace(1, 0, n)
        a = int((bt["start"] + bt["lead"]) * rate)
        track[a:a + len(clip)] += clip
    track *= 0.84 * 32767 / max(np.abs(track).max(), 1)
    with wave.open(str(WORK / "narration.wav"), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
        wf.writeframes(track.astype(np.int16).tobytes())

    # ---- subtitles
    srt = []
    for bt in beats:
        parts = [x.strip() for x in re.split(r"(?<=[.?!])\s+", bt["say"]) if x.strip()]
        chars, at = sum(len(x) for x in parts), bt["start"] + bt["lead"]
        for part in parts:
            span = bt["spoken"] * len(part) / chars
            st = lambda v: f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}:{int(v % 60):02d},{int(v * 1000 % 1000):03d}"  # noqa: E731
            srt.append(f"{len(srt) + 1}\n{st(at)} --> {st(at + span - 0.05)}\n{part.replace('A.I.', 'AI')}\n")
            at += span
    OUT.parent.mkdir(exist_ok=True)
    OUT.with_suffix(".srt").write_text("\n".join(srt), encoding="utf-8")

    # ---- per-beat camera targets and layers
    pages: dict[str, Image.Image] = {}
    def page(name):
        if name not in pages:
            if len(pages) > 2:
                pages.pop(next(iter(pages)))
            pages[name] = Image.open(WORK / f"page-{name}.png").convert("RGB")
        return pages[name]

    for bt in beats:
        if bt["kind"] == "app":
            img = page(bt["shot"])
            box = boxes[bt["shot"]].get(bt["focus"]) if bt["focus"] else None
            bt["box"] = box
            bt["cam"] = cam_focus(img, box) if box else cam_wide(img)
            bt["cursor"] = (box[0] + min(box[2] * 0.5, 520), box[1] + min(box[3] * 0.5, 300)) if box else None
            bt["cap"] = caption_layer(bt["label"], bt["head"])
        else:
            bt["layer"] = card_layer(bt["kind"], bt["label"], bt["head"])
    heroes = {False: hero_base(False), True: hero_base(True)}
    arrow = pointer()

    def render(bt, prev, tt) -> Image.Image:
        """One frame of a beat, tt seconds after it starts."""
        if bt["kind"] != "app":
            base = heroes[bt["kind"] == C]
            z = 1.10 - 0.07 * (tt / bt["dur"])                              # slow push-in on the photograph
            cw, ch = W * z, H * z
            fr = base.resize((W, H), Image.BICUBIC, box=((base.width - cw) / 2, (base.height - ch) / 2, (base.width + cw) / 2, (base.height + ch) / 2))
            a = ease(tt / 0.7)
            paste(fr, bt["layer"], a, dy=int(18 * (1 - a)))
            return fr
        img = page(bt["shot"])
        same = prev is not None and prev["kind"] == "app" and prev["shot"] == bt["shot"]
        move = 1.25
        u = ease(tt / move) if same else 1.0
        cam = lerp(prev["cam"], bt["cam"], u) if same else bt["cam"]
        hold = max(tt - (move if same else 0), 0) / max(bt["dur"], 0.1)
        zoom = 1 - 0.018 * min(hold, 1)                                       # gentle drift so a held shot never looks frozen
        cx, cy = cam[0] + cam[2] / 2, cam[1] + cam[3] / 2
        cam = (cx - cam[2] * zoom / 2, cy - cam[3] * zoom / 2, cam[2] * zoom, cam[3] * zoom)
        cam = (min(max(cam[0], 0), img.width - cam[2]), min(max(cam[1], 0), max(img.height - cam[3], 0)), cam[2], cam[3])
        fr = img.resize((W, H), Image.BICUBIC, box=(cam[0], cam[1], cam[0] + cam[2], cam[1] + cam[3]))
        s = W / cam[2]
        if bt["box"]:
            a = ease((tt - (move if same else 0.35) + 0.25) / 0.45) * (1 - ease((tt - (bt["dur"] - 0.3)) / 0.3))
            if a > 0.01:
                x, y, bw, bh = bt["box"]
                r = [(x - cam[0]) * s - 12, (y - cam[1]) * s - 12, (x + bw - cam[0]) * s + 12, (y + bh - cam[1]) * s + 12]
                mask = Image.new("L", (W, H), 0)
                ImageDraw.Draw(mask).rounded_rectangle(r, 18, fill=255)
                spot = Image.composite(fr, fr.point(DIM), mask)
                ImageDraw.Draw(spot).rounded_rectangle(r, 18, outline=GOLD, width=5)
                fr = spot if a >= 0.99 else Image.blend(fr, spot, a)
        cur = bt["cursor"]
        if cur or (same and prev.get("cursor")):
            start = prev["cursor"] if same and prev.get("cursor") else (cur[0] + 900, cur[1] + 700) if cur else None
            end = cur or (prev["cursor"][0] + 900, prev["cursor"][1] + 700)
            px, py = lerp(start, end, ease(tt / 1.35))
            sx, sy = int((px - cam[0]) * s), int((py - cam[1]) * s)
            if -80 < sx < W and -80 < sy < H:
                fr.paste(arrow, (sx, sy), arrow)
        new_caption = not (same and prev["head"] == bt["head"])
        paste(fr, bt["cap"], ease(tt / 0.4) if new_caption else 1.0, dy=int(14 * (1 - ease(tt / 0.4))) if new_caption else 0)
        return fr

    # ---- render and encode
    print(f"Rendering {total / 60:.1f} minutes ({int(total * FPS)} frames)")
    ff = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                           "-i", str(WORK / "narration.wav"), "-c:v", "libx264", "-preset", "slow", "-crf", "23", "-maxrate", "2400k", "-bufsize", "6000k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                           "-ac", "2", "-shortest", "-movflags", "+faststart", str(OUT)], stdin=subprocess.PIPE)
    last, bi = None, 0
    for n in range(int(total * FPS)):
        tt = n / FPS
        while bi + 1 < len(beats) and tt >= beats[bi + 1]["start"]:
            bi += 1
        bt, prev = beats[bi], beats[bi - 1] if bi else None
        local = tt - bt["start"]
        fr = render(bt, prev, local)
        cut = prev is not None and not (prev["kind"] == "app" and bt["kind"] == "app" and prev["shot"] == bt["shot"])
        if cut and local < 0.5 and bt.get("_from") is not None:
            fr = Image.blend(bt["_from"], fr, ease(local / 0.5))                # dissolve between shots
        if n == 0 or (tt < 0.6):
            fr = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), fr, ease(tt / 0.6))
        if total - tt < 0.9:
            fr = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), fr, ease((total - tt) / 0.9))
        if bi + 1 < len(beats) and tt + 1 / FPS >= beats[bi + 1]["start"]:
            beats[bi + 1]["_from"] = fr
        ff.stdin.write(fr.tobytes())
        last = fr
        if n % (FPS * 20) == 0:
            print(f"  {tt:6.0f}s  {bt['label']}: {bt['head'][:50]}", flush=True)
    ff.stdin.close()
    ff.wait()
    words = sum(len(s[5].split()) for s in STORY)
    print(f"wrote {OUT}  {OUT.stat().st_size / 1e6:.1f} MB, {total / 60:.1f} minutes, {len(beats)} narrated shots, about {words / (sum(b['spoken'] for b in beats) / 60):.0f} words a minute")


if __name__ == "__main__":
    main()
