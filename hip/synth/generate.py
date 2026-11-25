"""Synthetic source-system extract generator.

Produces the files the Azure ingestion layer would land from each operational system:
two different PMS vendors (different schemas, codes and date formats), restaurant POS,
table reservations, waterpark ticketing, retail POS + inventory, spa booking, CRM /
marketing / loyalty, and ERP (GL, budget, procurement). Everything is driven by one
demand model per property so the verticals move together, and a handful of known
problems are injected so data quality rules, anomaly detection and recommendations
have something real to find.

Run:  python -m hip.synth.generate
"""
from __future__ import annotations

import shutil

import numpy as np
import pandas as pd

from hip.settings import AS_OF, HISTORY_START, LANDING, load

RNG = np.random.default_rng(20261112)
D0 = pd.Timestamp(HISTORY_START) - pd.Timedelta(days=30)
D1 = pd.Timestamp(AS_OF) + pd.Timedelta(days=120)
DATES = pd.date_range(D0, D1, freq="D")
N = len(DATES)
IA = (pd.Timestamp(AS_OF) - D0).days            # index of last closed business date
IH = (pd.Timestamp(HISTORY_START) - D0).days    # index of first reported business date
HIST = np.arange(IH, IA + 1)

FX_BASE = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "AED": 0.2723, "QAR": 0.2747, "THB": 0.0285, "SGD": 0.745}
PEGGED = {"USD", "AED", "QAR"}

MONTH_PROFILES = {
    "north_summer": [.45, .48, .62, .85, 1.05, 1.22, 1.30, 1.30, 1.15, .90, .55, .62],
    "city_eu": [.84, .87, .96, 1.02, 1.10, 1.14, 1.10, 1.00, 1.10, 1.08, .98, 1.06],
    "gulf": [1.20, 1.22, 1.18, 1.05, .88, .70, .62, .66, .85, 1.05, 1.20, 1.25],
    "tropical": [1.25, 1.25, 1.15, 1.00, .82, .76, .86, .88, .76, .82, 1.00, 1.25],
    "city_asia": [.95, 1.00, 1.02, 1.00, .98, 1.02, 1.05, 1.02, 1.10, 1.02, 1.00, 1.02],
}
DOW = {
    "north_summer": [.90, .90, .93, .97, 1.08, 1.15, 1.05],
    "tropical": [.92, .92, .94, .98, 1.07, 1.12, 1.04],
    "city_eu": [1.0, 1.08, 1.10, 1.05, .98, .95, .85],
    "gulf": [.92, .95, .98, 1.05, 1.12, 1.10, .92],
    "city_asia": [.98, 1.05, 1.08, 1.05, 1.02, 1.0, .88],
}
EVENTS = [  # (name, [(start, end)...], multiplier, property filter)
    ("Festive Season", [("2024-12-20", "2025-01-03"), ("2025-12-20", "2026-01-03"), ("2026-12-20", "2027-01-03")], 1.22, None),
    ("Easter", [("2025-04-18", "2025-04-21"), ("2026-04-03", "2026-04-06")], 1.15, {"P001", "P002"}),
    ("Eid al-Fitr", [("2025-03-30", "2025-04-02"), ("2026-03-20", "2026-03-23"), ("2027-03-10", "2027-03-12")], 1.28, {"P003", "P004"}),
    ("Eid al-Adha", [("2025-06-06", "2025-06-09"), ("2026-05-27", "2026-05-30")], 1.25, {"P003", "P004"}),
    ("Lunar New Year", [("2025-01-29", "2025-02-02"), ("2026-02-17", "2026-02-21"), ("2027-02-06", "2027-02-10")], 1.22, {"P005", "P006"}),
    ("Songkran", [("2025-04-12", "2025-04-16"), ("2026-04-12", "2026-04-16")], 1.22, {"P005"}),
    ("Singapore Grand Prix", [("2025-10-02", "2025-10-05"), ("2026-10-08", "2026-10-11")], 1.30, {"P006"}),
    ("Summer Championships", [("2025-06-30", "2025-07-13"), ("2026-06-29", "2026-07-12")], 1.12, {"P002"}),
]

CHANNELS = ["Direct Web", "OTA", "GDS", "Phone", "Corporate", "Wholesale"]
SS_CHANNEL = ["WEB", "OTA", "GDS", "PHONE", "CORPORATE", "WHOLESALE"]
IK_CHANNEL = ["W", "O", "G", "T", "C", "H"]
SEGMENTS = ["Leisure", "Corporate", "Group", "Package"]
SEG_CH = np.array([[.40, .38, .02, .12, 0, .08], [.12, .05, .33, .10, .40, 0], [.05, 0, .05, .30, .10, .50], [.15, .25, 0, .05, 0, .55]])
SEG_LEAD = [48, 11, 75, 60]
CH_CANCEL = np.array([0.0, 0.9, 0.2, -0.2, -0.4, 0.5])
CH_RATE = np.array([1.0, 0.93, 0.97, 1.0, 0.86, 0.80])
ROOM_TYPES = ["DLX", "PRM", "STE", "VIL"]
IK_ROOM = {"DLX": "DELUXE", "PRM": "PREMIER", "STE": "SUITE", "VIL": "SIGNATURE"}
LOSP = np.array([.22, .30, .22, .12, .07, .04, .03])

FIRST = ["Olivia", "Liam", "Sofia", "Noah", "Amira", "Omar", "Mei", "Kenji", "Aisha", "Lucas", "Emma", "Hugo", "Layla", "Yusuf",
         "Priya", "Arjun", "Chloe", "Mateo", "Hana", "Wei", "Elena", "Marco", "Fatima", "Khalid", "Isla", "Oscar", "Nora", "Ravi",
         "Sara", "Daniel", "Anya", "Felix", "Leila", "Tariq", "Yuna", "Jonas", "Clara", "Ethan", "Zara", "Viktor"]
LAST = ["Rossi", "Bennett", "Al-Mansoori", "Tanaka", "Schmidt", "Dubois", "Khan", "Lim", "Novak", "Garcia", "Haddad", "Wong",
        "Ivanov", "Sato", "Mehta", "Clarke", "Moretti", "Al-Thani", "Nguyen", "Andersson", "Silva", "Kapoor", "Fischer", "Laurent",
        "Chen", "Hughes", "Rahman", "Costa", "Petrov", "Yamamoto", "Bianchi", "Al-Farsi", "Tan", "Muller", "Reyes", "Okafor",
        "Lindqvist", "Farouk", "Park", "Walsh"]
COUNTRIES = ["GB", "US", "DE", "FR", "IT", "AE", "SA", "QA", "IN", "CN", "JP", "SG", "AU", "TH", "RU", "CH"]
COUNTRY_P = np.array([.13, .12, .08, .07, .06, .07, .06, .03, .08, .08, .04, .04, .04, .03, .04, .03])

MENUS = {  # name, category, price_usd, cost_pct, popularity
    "fine": [("Oscietra Caviar", "Starter", 145, .52, 2), ("Seared Scallops", "Starter", 42, .30, 7), ("Burrata & Heirloom Tomato", "Starter", 32, .24, 8),
             ("Lobster Bisque", "Starter", 36, .28, 5), ("Wagyu Striploin", "Main", 165, .55, 9), ("Dover Sole Meuniere", "Main", 92, .36, 6),
             ("Truffle Risotto", "Main", 68, .26, 9), ("Rack of Lamb", "Main", 84, .34, 7), ("Wild Sea Bass", "Main", 76, .33, 6),
             ("Garden Tasting Menu", "Main", 120, .22, 3), ("Chocolate Souffle", "Dessert", 28, .18, 7), ("Cheese Selection", "Dessert", 34, .40, 2),
             ("Signature Cocktail", "Beverage", 26, .16, 10), ("Sparkling Water", "Beverage", 12, .08, 10), ("Grand Cru by the Glass", "Wine", 48, .30, 8),
             ("Sommelier Pairing", "Wine", 110, .32, 3), ("Vintage Champagne Bottle", "Wine", 420, .48, 1)],
    "allday": [("Breakfast Buffet", "Main", 46, .30, 16), ("Eggs Benedict", "Main", 26, .22, 7), ("Club Sandwich", "Main", 32, .27, 9),
               ("Caesar Salad", "Starter", 24, .21, 8), ("Mezze Platter", "Starter", 28, .24, 6), ("Wagyu Burger", "Main", 44, .46, 10),
               ("Margherita Pizza", "Main", 30, .19, 8), ("Grilled Salmon", "Main", 48, .36, 6), ("Pad Thai", "Main", 34, .23, 5),
               ("Kids Menu", "Main", 18, .25, 4), ("Tiramisu", "Dessert", 18, .20, 6), ("Gelato Selection", "Dessert", 14, .17, 6),
               ("Fresh Juice", "Beverage", 12, .22, 12), ("Barista Coffee", "Beverage", 9, .10, 15), ("House Wine Glass", "Wine", 22, .28, 8),
               ("Afternoon Tea", "Main", 78, .31, 4)],
    "pool": [("Fish Tacos", "Main", 28, .30, 8), ("Grilled Prawns", "Main", 46, .48, 9), ("Poke Bowl", "Main", 32, .33, 7),
             ("Angus Burger", "Main", 34, .35, 10), ("Truffle Fries", "Starter", 16, .15, 11), ("Tropical Fruit Plate", "Starter", 20, .30, 5),
             ("Nachos Grande", "Starter", 22, .20, 6), ("Lobster Roll", "Main", 52, .54, 6), ("Ice Cream Sundae", "Dessert", 15, .18, 7),
             ("Frozen Daiquiri", "Beverage", 21, .17, 12), ("Coconut Water", "Beverage", 10, .25, 9), ("Craft Beer", "Beverage", 14, .28, 10),
             ("Rose by the Glass", "Wine", 24, .27, 7), ("Kids Combo", "Main", 17, .26, 5)],
}
SLOTS = ["Breakfast", "Lunch", "Afternoon", "Dinner"]
SLOT_START = np.array([6.5, 12.0, 15.0, 18.5])
SLOT_LEN = np.array([4.0, 3.0, 3.0, 4.2])
SLOT_P = {"fine": [0, .22, .03, .75], "allday": [.38, .25, .12, .25], "pool": [0, .50, .38, .12]}

PRODUCTS = [  # name, category, price_usd, cost_pct, base daily rate, behaviour
    ("Linen Resort Shirt", "Apparel", 180, .38, 1.4, ""), ("Silk Scarf", "Apparel", 240, .35, 0.9, ""), ("Cashmere Wrap", "Apparel", 520, .42, 0.35, "slow"),
    ("Logo Polo", "Apparel", 110, .36, 1.8, ""), ("Kids Robe", "Apparel", 85, .40, 0.7, ""), ("Sun Hat", "Apparel", 95, .33, 1.6, "tight"),
    ("Destination Candle", "Souvenirs", 58, .30, 2.4, ""), ("Art Print", "Souvenirs", 140, .28, 0.5, "slow"), ("Keepsake Ornament", "Souvenirs", 36, .32, 2.0, ""),
    ("Coffee Table Book", "Souvenirs", 95, .45, 0.6, ""), ("Plush Mascot", "Souvenirs", 42, .34, 2.6, "tight"), ("Postcard Set", "Souvenirs", 18, .25, 2.2, ""),
    ("Spa Body Oil", "Beauty & Wellness", 88, .30, 1.5, ""), ("Bath Salt Ritual", "Beauty & Wellness", 64, .28, 1.3, ""), ("Signature Fragrance", "Beauty & Wellness", 210, .32, 0.8, ""),
    ("Reef-Safe Sunscreen", "Beauty & Wellness", 38, .40, 3.2, "tight"), ("Sleep Mist", "Beauty & Wellness", 46, .27, 1.1, ""), ("Face Serum", "Beauty & Wellness", 165, .34, 0.6, ""),
    ("Pearl Earrings", "Jewellery", 780, .46, 0.18, ""), ("Gold Charm Bracelet", "Jewellery", 1250, .50, 0.10, "slow"), ("Silver Cufflinks", "Jewellery", 320, .44, 0.22, ""),
    ("Gemstone Pendant", "Jewellery", 1650, .52, 0.07, "slow"), ("Leather Watch Strap", "Jewellery", 190, .41, 0.25, ""), ("Enamel Pin", "Jewellery", 28, .30, 1.4, ""),
    ("Swim Shorts", "Swim & Beach", 130, .36, 1.7, ""), ("One-Piece Swimsuit", "Swim & Beach", 210, .37, 1.2, ""), ("Beach Towel", "Swim & Beach", 75, .39, 2.3, "tight"),
    ("Waterproof Phone Pouch", "Swim & Beach", 26, .30, 3.0, ""), ("Kids Goggles", "Swim & Beach", 24, .31, 2.4, ""), ("Flip Flops", "Swim & Beach", 48, .33, 2.6, ""),
    ("Single-Estate Olive Oil", "Gourmet", 44, .42, 1.2, ""), ("Artisan Chocolate Box", "Gourmet", 52, .38, 2.1, ""), ("Reserve Tea Caddy", "Gourmet", 60, .35, 1.0, ""),
    ("Saffron Gift Tin", "Gourmet", 90, .55, 0.5, ""), ("House Blend Coffee", "Gourmet", 32, .36, 1.6, ""), ("Truffle Honey", "Gourmet", 38, .40, 0.9, "slow"),
]
SPA_SERVICES = [  # name, category, minutes, price_usd, popularity
    ("Signature Massage", "Massage", 90, 290, 10), ("Deep Tissue Massage", "Massage", 60, 210, 9), ("Hot Stone Therapy", "Massage", 90, 310, 5),
    ("Couples Ritual", "Massage", 120, 620, 3), ("Hydrating Facial", "Facial", 60, 230, 7), ("Age-Defy Facial", "Facial", 90, 360, 4),
    ("Express Facial", "Facial", 45, 150, 5), ("Body Scrub & Wrap", "Body", 75, 250, 4), ("Hammam Journey", "Body", 90, 330, 4),
    ("Private Yoga Session", "Wellness", 60, 160, 3), ("Sound Bath Meditation", "Wellness", 45, 120, 2), ("Manicure & Pedicure", "Salon", 75, 140, 6),
    ("Blow Dry & Style", "Salon", 45, 110, 4),
]
ATTRACTIONS = [  # name, capacity per hour, popularity share
    ("Leviathan Drop", 420, .16), ("Typhoon Raft Ride", 620, .17), ("Lazy River Odyssey", 950, .20), ("Wave Lagoon", 1200, .18),
    ("Aqua Coaster", 380, .15), ("Kids Splash Cove", 700, .11), ("Twin Racer Slides", 520, .12), ("Cliff Plunge", 300, .09),
]
TICKETS = [("Day Pass", 68, .42), ("Hotel Guest", 22, 0), ("Family Bundle", 54, .30), ("VIP Fast Track", 145, .08), ("Twilight", 39, .20)]
PROC_CATEGORIES = {
    "Food & Beverage": [("Wagyu Beef kg", 95), ("Fresh Seafood kg", 42), ("Dairy & Eggs case", 38), ("Produce crate", 27), ("Wine case", 310), ("Coffee Beans kg", 24)],
    "Linen & Laundry": [("Bed Linen set", 85), ("Bath Towel dozen", 96), ("Laundry Service load", 14)],
    "Energy": [("Electricity MWh", 138), ("Chilled Water MWh", 92), ("LPG cylinder", 48)],
    "Maintenance": [("Pool Chemicals drum", 210), ("HVAC Filter", 64), ("Paint & Finishes lot", 180), ("Elevator Service visit", 950)],
    "Retail Merchandise": [("Apparel assortment", 1400), ("Souvenir assortment", 620), ("Beauty assortment", 880)],
    "Spa Supplies": [("Massage Oil litre", 58), ("Facial Product kit", 240), ("Spa Linen set", 70)],
    "IT & Services": [("Software Licence month", 1900), ("Network Support visit", 420)],
    "Guest Amenities": [("Bath Amenity case", 160), ("Welcome Gift set", 35), ("Minibar restock", 74)],
}
CAMPAIGN_CH = {  # channel: cpc usd, conversion per click (hotel)
    "Email": (0.9, .012), "Paid Search": (3.8, .010), "Social": (2.2, .0040), "Display": (1.6, .0018), "Affiliate": (2.8, .009), "Influencer": (4.5, .0035),
}
AOV = {"Hotels": 1400, "Restaurants": 160, "Waterpark": 150, "Retail": 110, "Spa & Leisure": 240}
THEMES = ["Escape", "Signature Moments", "Members First", "Stay Longer", "Weekend Edit", "Family Adventures", "Taste of Aurelian", "Wellness Reset", "Early Booker", "Last Minute Luxe"]
AUDIENCES = ["Loyalty Members", "Lapsed Guests", "Families", "Couples", "Corporate Travellers", "Local Residents", "High-Value Lookalikes"]


def write(df: pd.DataFrame, rel: str) -> None:
    path = LANDING / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"  {rel:<58}{len(df):>10,} rows")


def season_curve(profile: str) -> np.ndarray:
    m = np.array(MONTH_PROFILES[profile])
    x = (DATES.day_of_year.values - 15) / 30.4375
    i0 = np.floor(x).astype(int) % 12
    f = x - np.floor(x)
    return m[i0] * (1 - f) + m[(i0 + 1) % 12] * f


def event_mult(pid: str):
    mult = np.ones(N)
    rows = []
    for name, windows, m, props in EVENTS:
        if props and pid not in props:
            continue
        for s, e in windows:
            mask = (DATES >= s) & (DATES <= e)
            mult[mask] = np.maximum(mult[mask], m)
            rows += [(pid, d.date(), name) for d in DATES[mask]]
    return mult, rows


def ar_noise(sd: float, rho: float = 0.7) -> np.ndarray:
    e = RNG.normal(0, sd, N)
    out = np.zeros(N)
    for i in range(1, N):
        out[i] = rho * out[i - 1] + e[i]
    return np.exp(out)


def ts_at(day_idx: np.ndarray, hours: np.ndarray) -> pd.DatetimeIndex:
    return DATES[day_idx] + pd.to_timedelta((hours * 3600).astype(np.int64), unit="s")


def pms_id(vendor: str, person: np.ndarray) -> np.ndarray:
    s = pd.Series(person)
    out = ("SS" + (s + 100000).astype(str)) if vendor == "staysuite" else ("IK-" + (s * 7 + 13).astype(str).str.zfill(7))
    return np.where(person >= 0, out, "")


def crm_id(person: np.ndarray) -> np.ndarray:
    return np.where(person >= 0, "C" + pd.Series(person).astype(str).str.zfill(6), "")


# --------------------------------------------------------------------------- hotels
def gen_reservations(p: dict, idx: np.ndarray, persons: dict, res_base: int) -> pd.DataFrame:
    rt_cfg = {r["room_type"]: r for r in load("properties.json")["room_types"]}
    mlos = float((LOSP * np.arange(1, 8)).sum())
    resort = p["brand_id"] == "RST"
    parts = []
    for k, rt in enumerate(ROOM_TYPES):
        cap = p["rooms"][rt]
        occ = np.clip(p["base_occ"] * idx * rt_cfg[rt]["occ_mult"], 0.12, 0.97)
        na = RNG.poisson(occ * cap / mlos / 0.80)
        a = np.repeat(np.arange(N), na)
        parts.append(pd.DataFrame({"a": a, "rtc": k, "los": RNG.choice(np.arange(1, 8), len(a), p=LOSP)}))
    r = pd.concat(parts, ignore_index=True)
    m = len(r)
    a, los, rtc = r["a"].values, r["los"].values, r["rtc"].values
    seg = RNG.choice(4, m, p=[.62, .08, .10, .20] if resort else [.42, .38, .10, .10])
    ch = np.zeros(m, dtype=int)
    for s in range(4):
        mk = seg == s
        ch[mk] = RNG.choice(6, mk.sum(), p=SEG_CH[s])
    lead = np.minimum(RNG.gamma(1.4, np.array(SEG_LEAD)[seg] / 1.4).astype(int), 330)
    person = RNG.choice(len(persons["w"]), m, p=persons["w"])
    tier = persons["tier_code"][person]            # 0 none, 1 silver, 2 gold, 3 platinum
    z = -2.75 + 0.014 * np.minimum(lead, 120) + CH_CANCEL[ch] + np.array([0.3, 0.0, -0.3, -0.6])[tier] - 0.05 * los
    cancel = RNG.random(m) < 1 / (1 + np.exp(-z))
    noshow = ~cancel & (RNG.random(m) < 0.012 + 0.03 * (ch == 1) + 0.02 * (lead == 0))
    active = ~cancel & ~noshow

    # capacity enforcement: thin bookings on nights that would oversell a room category
    drop = np.zeros(m, dtype=bool)
    for k, rt in enumerate(ROOM_TYPES):
        cap = p["rooms"][rt] - (2 if p["rooms"][rt] >= 20 else 0)   # headroom for out-of-order rooms
        for it in range(12):
            mk = np.where(active & ~drop & (rtc == k))[0]
            diff = np.bincount(a[mk], minlength=N + 9).astype(float) - np.bincount(a[mk] + los[mk], minlength=N + 9)
            occ = np.cumsum(diff)
            if occ.max() <= cap:
                break
            ratio = np.minimum(1.0, cap / np.maximum(occ, 1))
            rr = np.ones(len(mk))
            for j in range(7):
                mm = los[mk] > j
                rr[mm] = np.minimum(rr[mm], ratio[a[mk][mm] + j])
            drop[mk[(rr < 1) & (RNG.random(len(mk)) > rr * (0.97 if it < 8 else 0.6))]] = True
    keep = ~drop
    a, los, rtc, seg, ch, lead, person, cancel, noshow = (x[keep] for x in (a, los, rtc, seg, ch, lead, person, cancel, noshow))
    m = len(a)
    bday = a - lead
    cday = bday + np.floor(RNG.random(m) ** 1.3 * (lead + 0.999)).astype(int)
    known = bday <= IA
    status = np.where(a <= IA,
                      np.where(cancel, "CANCELLED", np.where(noshow, "NO_SHOW", np.where(a + los <= IA + 1, "CHECKED_OUT", "CHECKED_IN"))),
                      np.where(cancel & (cday <= IA), "CANCELLED", "RESERVED"))
    rate_mult = np.array([rt_cfg[t]["rate_mult"] for t in ROOM_TYPES])[rtc]
    rate_usd = p["base_adr_usd"] * rate_mult * (0.72 + 0.34 * idx[a]) * CH_RATE[ch] * RNG.lognormal(0, 0.07, m)
    disc_pct = np.where(RNG.random(m) < 0.16, RNG.uniform(0.05, 0.20, m), 0.0)
    df = pd.DataFrame({
        "property_id": p["property_id"], "person": person, "a": a, "los": los, "rt": np.array(ROOM_TYPES)[rtc], "seg": seg, "ch": ch,
        "lead": lead, "bday": bday, "cday": cday, "status": status, "rate_usd": rate_usd, "disc_pct": disc_pct,
        "adults": np.where(seg == 1, 1, RNG.choice([1, 2, 2, 2, 3], m)), "children": np.where(seg == 1, 0, RNG.choice([0, 0, 0, 1, 2], m)),
        "true_cancel": cancel,
    })[known & (a + los > IH)].reset_index(drop=True)
    df["res_no"] = res_base + np.arange(len(df))
    return df


def format_pms(p: dict, df: pd.DataFrame, fx: np.ndarray) -> pd.DataFrame:
    """Render canonical reservations in the vendor's own schema, codes and date formats."""
    fxa = fx[np.minimum(df["a"].values, IA)]
    rate = (df["rate_usd"].values / fxa).round(2)
    gross = rate * df["los"].values
    disc = (gross * df["disc_pct"].values).round(2)
    booked = ts_at(np.maximum(df["bday"].values, 0), RNG.uniform(7, 23, len(df)))
    arr = DATES[df["a"].values]
    dep = arr + pd.to_timedelta(df["los"].values, unit="D")
    cancelled = (df["status"] == "CANCELLED").values
    cts = ts_at(np.clip(df["cday"].values, 0, N - 1), RNG.uniform(8, 22, len(df)))
    modified = np.where(cancelled, cts, np.where(df["status"].isin(["CHECKED_OUT"]).values, dep + pd.Timedelta(hours=11),
                                                 np.where(df["status"].isin(["CHECKED_IN", "NO_SHOW"]).values, arr + pd.Timedelta(hours=15), booked)))
    modified = pd.DatetimeIndex(modified)
    vendor = p["pms"]
    gid = pms_id(vendor, df["person"].values)
    if vendor == "staysuite":
        out = pd.DataFrame({
            "ConfirmationNo": "SS" + df["res_no"].astype(str).str.zfill(8), "PropertyCode": p["source_code"], "GuestProfileId": gid,
            "BookingDate": booked.strftime("%Y-%m-%d %H:%M:%S"), "ArrivalDate": arr.strftime("%Y-%m-%d"), "DepartureDate": dep.strftime("%Y-%m-%d"),
            "Nights": df["los"].values, "RoomTypeCode": df["rt"].values, "RateAmount": rate, "DiscountAmount": disc, "CurrencyCode": p["currency"],
            "Channel": np.array(SS_CHANNEL)[df["ch"].values], "MarketSegment": np.array(SEGMENTS)[df["seg"].values], "Status": df["status"].values,
            "CancelDate": np.where(cancelled, cts.strftime("%Y-%m-%d"), ""), "Adults": df["adults"].values, "Children": df["children"].values,
            "LastModified": modified.strftime("%Y-%m-%d %H:%M:%S"),
        })
        key, mod, stat, first = "ConfirmationNo", "LastModified", "Status", "RESERVED"
        booked_s = out["BookingDate"]
    else:
        state = df["status"].map({"RESERVED": "B", "CHECKED_IN": "I", "CHECKED_OUT": "O", "CANCELLED": "X", "NO_SHOW": "N"}).values
        out = pd.DataFrame({
            "res_id": "IK-" + df["res_no"].astype(str), "hotel_id": p["source_code"], "guest_ref": gid,
            "created_ts": booked.strftime("%Y-%m-%dT%H:%M:%S"), "checkin": arr.strftime("%d/%m/%Y"), "checkout": dep.strftime("%d/%m/%Y"),
            "room_class": df["rt"].map(IK_ROOM).values, "total_room_charge": gross.round(2), "promo_discount": disc, "ccy": p["currency"],
            "source": np.array(IK_CHANNEL)[df["ch"].values], "segment": np.char.lower(np.array(SEGMENTS)[df["seg"].values]), "state": state,
            "cancelled_on": np.where(cancelled, cts.strftime("%d/%m/%Y"), ""), "pax": (df["adults"] + df["children"]).values,
            "updated_ts": modified.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        key, mod, stat, first = "res_id", "updated_ts", "state", "B"
        booked_s = out["created_ts"]
    # earlier versions of ~2% of reservations (CDC history) - silver must keep only the latest
    old = out[(out[stat] != first)].sample(frac=0.02, random_state=7).copy()
    old[stat] = first
    old[mod] = booked_s.loc[old.index]
    old[["CancelDate"] if vendor == "staysuite" else ["cancelled_on"]] = ""
    out = pd.concat([out, old], ignore_index=True)
    # a few defective rows for the quarantine / data quality path
    bad = out.sample(4, random_state=3).copy()
    bad[key] = ""
    bad2 = out.sample(3, random_state=5).copy()
    bad2[key] = bad2[key] + "X"
    bad2["ArrivalDate" if vendor == "staysuite" else "checkin"] = "0000-00-00" if vendor == "staysuite" else "00/00/0000"
    return pd.concat([out, bad, bad2], ignore_index=True)


class GuestPicker:
    """Picks an in-house guest for a business date (room charges, hotel-guest tickets)."""

    def __init__(self, df: pd.DataFrame):
        act = df[df["status"].isin(["CHECKED_OUT", "CHECKED_IN"])]
        days = np.repeat(act["a"].values, act["los"].values) + (np.arange(act["los"].sum()) - np.repeat(act["los"].cumsum().values - act["los"].values, act["los"].values))
        per = np.repeat(act["person"].values, act["los"].values)
        o = np.argsort(days, kind="stable")
        self.days, self.per = days[o], per[o]
        self.off = np.searchsorted(self.days, np.arange(N + 2))
        self.rooms = np.diff(self.off)[:N].astype(float)   # occupied rooms per night

    def pick(self, day: np.ndarray, frac: float) -> np.ndarray:
        cnt = self.off[day + 1] - self.off[day]
        sel = (RNG.random(len(day)) < frac) & (cnt > 0)
        pos = np.minimum(self.off[day] + (RNG.random(len(day)) * cnt).astype(int), len(self.per) - 1)
        return np.where(sel, self.per[pos], -1)


# --------------------------------------------------------------------------- restaurants
def gen_restaurant(p, o, guests, local_idx, picker, fxb, check_base):
    menu = MENUS[o["type"]]
    price = np.array([round(x[2] / fxb / (5 if fxb > 0.5 else 50 if fxb < 0.1 else 10)) * (5 if fxb > 0.5 else 50 if fxb < 0.1 else 10) or x[2] for x in menu], dtype=float)
    cost_pct = np.array([x[3] for x in menu])
    pop = np.array([x[4] for x in menu], dtype=float)
    covers_t = (o["capture"] * guests[HIST] + o["local"] * local_idx[HIST]) * RNG.lognormal(0, 0.08, len(HIST))
    nchk = RNG.poisson(covers_t / 2.35)
    d = np.repeat(HIST, nchk)
    m = len(d)
    covers = np.minimum(1 + RNG.poisson(1.35, m), 8)
    slot = RNG.choice(4, m, p=SLOT_P[o["type"]])
    ts = ts_at(d, SLOT_START[slot] + RNG.random(m) * SLOT_LEN[slot])
    order = np.lexsort((ts.values, d))
    d, covers, slot, ts = d[order], covers[order], slot[order], ts[order]
    check_id = np.char.add(o["code"] + "-", (check_base + np.arange(m)).astype(str))
    has_res = (slot != 0) & (RNG.random(m) < (0.72 if o["type"] == "fine" else 0.38))
    res_id = np.where(has_res, np.char.add("TB-" + o["code"] + "-", np.arange(m).astype(str)), "")
    guest = pms_id(p["pms"], picker.pick(d, 0.42 if o["type"] != "fine" else 0.30))
    table = np.char.add("T", RNG.integers(1, o["tables"] + 1, m).astype(str))
    disc_chk = np.where(RNG.random(m) < 0.08, RNG.uniform(0.10, 0.20, m), 0.0)
    nl = np.minimum(1 + RNG.poisson(0.9 + 0.45 * covers), 7)
    ci = np.repeat(np.arange(m), nl)
    L = len(ci)
    item = RNG.choice(len(menu), L, p=pop / pop.sum())
    qty = 1 + RNG.binomial(np.maximum(covers[ci] - 1, 0), 0.30)
    lines = pd.DataFrame({
        "check_id": check_id[ci], "line_no": np.arange(L) - np.repeat(np.cumsum(nl) - nl, nl) + 1, "outlet_code": o["code"],
        "business_date": DATES[d[ci]].strftime("%Y-%m-%d"), "closed_at": ts[ci].strftime("%Y-%m-%d %H:%M:%S"), "table_id": table[ci],
        "covers": covers[ci], "reservation_ref": res_id[ci], "menu_item_code": np.char.add(o["code"] + "-M", np.char.zfill((item + 1).astype(str), 2)),
        "qty": qty, "unit_price": price[item], "discount_amount": (qty * price[item] * disc_chk[ci]).round(2),
        "unit_food_cost": (price[item] * cost_pct[item] * (1 + 0.04 * (d[ci] - IH) / 365)).round(2), "guest_ref": guest[ci], "currency": p["currency"],
    })
    dup = lines.sample(frac=0.002, random_state=11)   # duplicate transmissions from the POS
    lines = pd.concat([lines, dup], ignore_index=True)
    # table reservations: seated ones tie to checks, plus cancellations and no-shows
    seated = pd.DataFrame({"booking_id": res_id[has_res], "outlet_code": o["code"], "reserved_for": ts[has_res].floor("15min").strftime("%Y-%m-%d %H:%M:%S"),
                           "covers": covers[has_res], "status": "SEATED", "table_id": table[has_res], "guest_ref": guest[has_res]})
    nx = int(has_res.sum() * 0.19)
    xd = RNG.choice(HIST, nx)
    xs = RNG.choice([1, 3], nx, p=[.3, .7])
    extra = pd.DataFrame({"booking_id": np.char.add("TX-" + o["code"] + "-", np.arange(nx).astype(str)), "outlet_code": o["code"],
                          "reserved_for": ts_at(xd, SLOT_START[xs] + RNG.random(nx) * 3).floor("15min").strftime("%Y-%m-%d %H:%M:%S"),
                          "covers": np.minimum(1 + RNG.poisson(1.5, nx), 8), "status": RNG.choice(["CANCELLED", "NO_SHOW"], nx, p=[.7, .3]),
                          "table_id": "", "guest_ref": ""})
    menu_df = pd.DataFrame({"menu_item_code": [f"{o['code']}-M{i + 1:02d}" for i in range(len(menu))], "outlet_code": o["code"],
                            "item_name": [x[0] for x in menu], "category": [x[1] for x in menu], "list_price": price, "currency": p["currency"]})
    net = qty * price[item] * (1 - disc_chk[ci])
    cost = qty * price[item] * cost_pct[item] * (1 + 0.04 * (d[ci] - IH) / 365)
    return lines, pd.concat([seated, extra], ignore_index=True), menu_df, np.bincount(d[ci], net, N), np.bincount(d[ci], cost, N), m


# --------------------------------------------------------------------------- waterpark
def gen_waterpark(p, guests, local_idx, picker, fxb, season):
    w = p["waterpark"]
    month = DATES.month.values
    temp = 24 + 9 * (season if p["season"] == "gulf" else 0.4 * season) * 0 + (np.array([24, 25, 28, 32, 37, 39, 41, 41, 38, 34, 29, 25]) if p["season"] == "gulf"
                                                                                else np.array([31, 32, 33, 33, 32, 31, 31, 31, 30, 30, 30, 30]))[month - 1] + RNG.normal(0, 1.5, N)
    rain_p = (np.array([.02, .03, .03, .02, 0, 0, 0, 0, 0, .01, .02, .03]) if p["season"] == "gulf"
              else np.array([.08, .06, .10, .22, .42, .45, .45, .48, .58, .52, .32, .14]))[month - 1]
    rain = RNG.random(N) < rain_p
    heat = np.where(temp > 39, 0.80, 1.0)
    att = (w["guest_capture"] * guests + w["day_visitors"] * local_idx) * np.where(rain, 0.62, 1.0) * heat * RNG.lognormal(0, 0.07, N)
    att = np.minimum(att, w["capacity"] * 1.04)
    hotel_share = np.clip(w["guest_capture"] * guests / np.maximum(att, 1), 0, 0.9)
    rows = []
    cat_p = np.array([t[2] for t in TICKETS])
    for d in HIST:
        n_txn = RNG.poisson(att[d] / 2.8)
        is_hotel = RNG.random(n_txn) < hotel_share[d]
        cat = np.where(is_hotel, 1, RNG.choice(len(TICKETS), n_txn, p=cat_p))
        rows.append(np.column_stack([np.full(n_txn, d), cat]))
    r = np.vstack(rows)
    d, cat = r[:, 0], r[:, 1]
    m = len(d)
    qty = np.where(cat == 2, 4, np.minimum(1 + RNG.poisson(1.7, m), 8))
    price = np.array([round(t[1] / fxb) for t in TICKETS], dtype=float)[cat]
    disc = np.where(RNG.random(m) < 0.12, 0.15, 0.0)
    adv = np.where((cat != 1) & (RNG.random(m) < 0.42), RNG.integers(1, 15, m), 0)
    entry_h = np.where(cat == 4, RNG.uniform(15, 17, m), np.clip(RNG.gamma(3, 0.7, m) + 9.6, 10, 16))
    exit_h = np.minimum(entry_h + RNG.uniform(2.5, 6, m), 19)
    person = np.where(cat == 1, picker.pick(d, 1.0), np.where(RNG.random(m) < 0.10, RNG.integers(0, 70000, m), -1))
    tickets = pd.DataFrame({
        "ticket_txn_id": np.char.add(w["code"] + "-", np.arange(m).astype(str)), "park_code": w["code"],
        "purchased_at": ts_at(np.maximum(d - adv, 0), np.where(adv > 0, RNG.uniform(8, 22, m), entry_h - 0.1)).strftime("%Y-%m-%d %H:%M:%S"),
        "visit_date": DATES[d].strftime("%Y-%m-%d"), "ticket_category": np.array([t[0] for t in TICKETS])[cat], "qty": qty, "unit_price": price,
        "discount_amount": (qty * price * disc).round(2), "sales_channel": np.where(cat == 1, "Hotel Desk", np.where(adv > 0, RNG.choice(["Online", "Partner"], m, p=[.8, .2]), "Gate")),
        "entry_time": ts_at(d, entry_h).strftime("%Y-%m-%d %H:%M:%S"), "exit_time": ts_at(d, exit_h).strftime("%Y-%m-%d %H:%M:%S"),
        "visitor_ref": np.where(cat == 1, pms_id(p["pms"], person), crm_id(person)), "currency": p["currency"],
    })
    rev = np.bincount(d, qty * price * (1 - disc), N)
    visitors = np.bincount(d, qty, N)
    # hourly attraction throughput and queues
    hours = np.arange(10, 19)
    prof = np.array([.35, .62, .85, .96, 1.0, .93, .80, .58, .30])
    dd, hh, aa = np.meshgrid(HIST, np.arange(len(hours)), np.arange(len(ATTRACTIONS)), indexing="ij")
    dd, hh, aa = dd.ravel(), hh.ravel(), aa.ravel()
    scale = w["capacity"] / 2100
    cap = np.array([round(x[1] * scale) for x in ATTRACTIONS])[aa]
    demand = visitors[dd] * prof[hh] * np.array([x[2] for x in ATTRACTIONS])[aa] * 3.0 * RNG.lognormal(0, 0.12, len(dd))
    rho = demand / cap
    attr = pd.DataFrame({
        "park_code": w["code"], "attraction_code": np.char.add(w["code"] + "-A", (aa + 1).astype(str)), "business_date": DATES[dd].strftime("%Y-%m-%d"),
        "hour": hours[hh], "riders": np.minimum(demand, cap).round().astype(int), "capacity_per_hour": cap,
        "avg_queue_minutes": np.clip(2 + 55 * np.maximum(rho - 0.55, 0) ** 1.4 + RNG.normal(0, 1.2, len(dd)), 0, 120).round(1),
    })
    ops = pd.DataFrame({"park_code": w["code"], "business_date": DATES[HIST].strftime("%Y-%m-%d"), "daily_capacity": w["capacity"], "open_time": "10:00",
                        "close_time": "19:00", "max_temp_c": temp[HIST].round(1), "rain": rain[HIST].astype(int)})
    amaster = pd.DataFrame({"attraction_code": [f"{w['code']}-A{i + 1}" for i in range(len(ATTRACTIONS))], "park_code": w["code"],
                            "attraction_name": [x[0] for x in ATTRACTIONS], "capacity_per_hour": [round(x[1] * scale) for x in ATTRACTIONS]})
    return tickets, attr, ops, amaster, rev, visitors


# --------------------------------------------------------------------------- retail
def gen_retail(p, s, guests, visitors, picker, fxb, txn_base):
    K = len(PRODUCTS)
    T = len(HIST)
    traffic = (guests[HIST] / max(guests[HIST].mean(), 1)) if not s.get("park") else (visitors[HIST] / max(visitors[HIST].mean(), 1))
    rate = np.array([x[4] for x in PRODUCTS]) * s["traffic"] * (np.where(np.array([x[1] for x in PRODUCTS]) == "Swim & Beach", 2.2, 0.5) if s.get("park") else 1.0)
    beh = np.array([x[5] for x in PRODUCTS])
    rate = np.where(beh == "slow", rate * 0.25, rate)
    lt = RNG.integers(5, 11, K)
    rop = np.where(beh == "tight", rate * lt * 0.45, rate * lt * 1.6 + 3)
    oq = np.ceil(np.where(beh == "slow", rate * 400, rate * 24) + 4)
    on = np.where(beh == "slow", rate * 520 + 40, rate * 30 + 5).round()
    onorder = np.zeros(K)
    arrive = np.zeros((T + 12, K))
    dem = RNG.poisson(rate[None, :] * traffic[:, None] * RNG.lognormal(0, 0.10, (T, 1)))
    onhand = np.zeros((T, K))
    sold = np.zeros((T, K), dtype=int)
    recv = np.zeros((T, K))
    for t in range(T):
        on += arrive[t]
        onorder -= arrive[t]
        recv[t] = arrive[t]
        sold[t] = np.minimum(dem[t], on)
        on -= sold[t]
        onhand[t] = on
        need = (on + onorder) <= rop
        arrive[t + lt[need], np.where(need)[0]] += oq[need]
        onorder[need] += oq[need]
    sku = np.array([f"SKU-{i + 1:03d}" for i in range(K)])
    tt, kk = np.meshgrid(np.arange(T), np.arange(K), indexing="ij")
    inv = pd.DataFrame({"store_code": s["code"], "sku": sku[kk.ravel()], "snapshot_date": DATES[HIST[tt.ravel()]].strftime("%Y-%m-%d"),
                        "on_hand_qty": onhand.ravel().astype(int), "received_qty": recv.ravel().astype(int), "sold_qty": sold.ravel(),
                        "reorder_point": np.ceil(rop)[kk.ravel()].astype(int), "lead_time_days": lt[kk.ravel()]})
    # explode units into POS transaction lines
    ut = np.repeat(tt.ravel(), sold.ravel())
    uk = np.repeat(kk.ravel(), sold.ravel())
    per_day = np.bincount(ut, minlength=T)
    ntx = np.maximum(np.ceil(per_day / 1.6).astype(int), 1)
    txn_local = (RNG.random(len(ut)) * ntx[ut]).astype(int)
    g = pd.DataFrame({"t": ut, "x": txn_local, "k": uk}).groupby(["t", "x", "k"], as_index=False).size().rename(columns={"size": "qty"})
    tx = g[["t", "x"]].drop_duplicates().reset_index(drop=True)
    tx["txn_id"] = s["code"] + "-" + (txn_base + np.arange(len(tx))).astype(str)
    tx["hour"] = RNG.uniform(9, 22, len(tx))
    tx["person"] = picker.pick(HIST[tx["t"].values], 0.28)
    tx["online"] = RNG.random(len(tx)) < 0.07
    g = g.merge(tx, on=["t", "x"])
    price = np.array([max(round(x[2] / fxb), 1) for x in PRODUCTS], dtype=float)[g["k"].values]
    cost = price * np.array([x[3] for x in PRODUCTS])[g["k"].values]
    day = HIST[g["t"].values]
    leak = (s["code"] == "HKT-S1") & (day > IA - 21)          # injected discount leakage
    disc = np.where(RNG.random(len(g)) < np.where(leak, 0.38, 0.09), np.where(leak, 0.40, RNG.uniform(0.10, 0.30, len(g))), 0.0)
    lines = pd.DataFrame({
        "txn_id": g["txn_id"].values, "line_no": g.groupby("txn_id").cumcount().values + 1, "store_code": s["code"],
        "sold_at": ts_at(day, g["hour"].values).strftime("%Y-%m-%d %H:%M:%S"), "sku": sku[g["k"].values], "qty": g["qty"].values, "unit_price": price,
        "discount_amount": (g["qty"].values * price * disc).round(2), "unit_cost": cost.round(2), "customer_ref": pms_id(p["pms"], g["person"].values),
        "sales_channel": np.where(g["online"].values, "Online", "In-Store"), "currency": p["currency"],
    })
    rev = np.bincount(day, g["qty"].values * price * (1 - disc), N)
    cogs = np.bincount(day, g["qty"].values * cost, N)
    return lines, inv, rev, cogs, len(tx)


# --------------------------------------------------------------------------- spa
def gen_spa(p, guests, local_idx, picker, fxb, n_members):
    sp = p["spa"]
    days = np.arange(IH, IA + 15)
    slots = sp["therapists"] * 7
    target = np.minimum((sp["capture"] * guests[days] + sp["local"] * local_idx[days]) * RNG.lognormal(0, 0.10, len(days)) * 1.5 / 0.84, slots)
    n = RNG.poisson(target)
    d = np.repeat(days, n)
    m = len(d)
    pop = np.array([x[4] for x in SPA_SERVICES], dtype=float)
    svc = RNG.choice(len(SPA_SERVICES), m, p=pop / pop.sum())
    lead = np.minimum(RNG.poisson(2.5, m) * (RNG.random(m) < 0.7), 20)
    keep = (d - lead) <= IA
    d, svc, lead = d[keep], svc[keep], lead[keep]
    m = len(d)
    u = RNG.random(m)
    pc = 0.07 + 0.012 * lead
    status = np.where(d > IA, "BOOKED", np.where(u < pc, "CANCELLED", np.where(u < pc + 0.045, "NO_SHOW", "COMPLETED")))
    price = np.array([round(x[3] / fxb) for x in SPA_SERVICES], dtype=float)[svc]
    disc = np.where(RNG.random(m) < 0.14, 0.15, 0.0)
    hg = picker.pick(np.minimum(d, IA), 0.62)
    hg = np.where(d > IA, -1, hg)
    loc = np.where((hg < 0) & (RNG.random(m) < 0.45), RNG.integers(0, n_members, m), -1)
    start = ts_at(d, RNG.integers(10, 20, m) + RNG.choice([0, 0.5], m))
    appts = pd.DataFrame({
        "appointment_id": np.char.add(sp["code"] + "-", np.arange(m).astype(str)), "spa_code": sp["code"],
        "guest_ref": np.where(hg >= 0, pms_id(p["pms"], hg), crm_id(loc)), "service_code": np.char.add("SVC-", np.char.zfill((svc + 1).astype(str), 2)),
        "therapist_id": np.char.add(f"E-{p['property_id']}-T", np.char.zfill(RNG.integers(1, sp["therapists"] + 1, m).astype(str), 2)),
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"), "duration_minutes": np.array([x[2] for x in SPA_SERVICES])[svc], "status": status,
        "booked_at": ts_at(np.maximum(d - lead, 0), RNG.uniform(8, 21, m)).strftime("%Y-%m-%d %H:%M:%S"), "list_price": price,
        "discount_amount": (price * disc).round(2), "membership": np.where(loc >= 0, RNG.choice(["Spa Member", ""], m, p=[.5, .5]), ""), "currency": p["currency"],
    })
    done = status == "COMPLETED"
    return appts, np.bincount(d[done], (price * (1 - disc))[done], N)[:N]


# --------------------------------------------------------------------------- main
def main() -> None:
    cfg = load("properties.json")
    props = cfg["properties"]
    if LANDING.exists():
        shutil.rmtree(LANDING)
    print(f"Generating source extracts {HISTORY_START} .. {AS_OF} into {LANDING}")

    fx = {}
    for c, b in FX_BASE.items():
        r = np.full(N, b) if c in PEGGED else b * np.exp(np.cumsum(RNG.normal(0, 0.0014, N)))
        r[IA + 1:] = r[IA]
        fx[c] = r
    write(pd.DataFrame([(DATES[i].date(), c, round(fx[c][i], 6)) for c in fx for i in range(IA + 1)], columns=["rate_date", "currency", "usd_per_unit"]), "erp/fx_rates.csv")

    # ---- people ---------------------------------------------------------
    NPER = 70000
    fi, li = RNG.integers(0, len(FIRST), NPER), RNG.integers(0, len(LAST), NPER)
    member = RNG.random(NPER) < 0.5
    tier_code = np.where(member, RNG.choice([1, 2, 3], NPER, p=[.6, .3, .1]), 0)
    w = RNG.lognormal(0, 1.1, NPER) * np.array([1, 2.2, 3.5, 5.0])[tier_code]
    persons = {"w": w / w.sum(), "tier_code": tier_code}
    email = pd.Series(np.array(FIRST)[fi]).str.lower() + "." + pd.Series(np.array(LAST)[li]).str.lower().str.replace("-", "") + pd.Series(np.arange(NPER)).astype(str) + "@example.com"
    country = RNG.choice(COUNTRIES, NPER, p=COUNTRY_P / COUNTRY_P.sum())
    tiers = np.array(["", "Silver", "Gold", "Platinum"])

    events, res_all, pickers = [], [], {}
    rev = {}      # (property, vertical) -> daily local revenue
    cost = {}     # (property, kind) -> daily local cost
    idx_map, guests_map, vis_map = {}, {}, {}
    outlets, menus, attractions, employees = [], [], [], []
    res_base = 1
    for p in props:
        pid = p["property_id"]
        em, erows = event_mult(pid)
        events += erows
        season = season_curve(p["season"])
        idx = season * np.array(DOW[p["season"]])[DATES.day_of_week.values] * (1 + p["trend"] * np.arange(N) / 365) * em * ar_noise(0.045)
        idx_map[pid] = idx
        df = gen_reservations(p, idx, persons, res_base)
        res_base += len(df) + 1000
        res_all.append(df)
        picker = pickers[pid] = GuestPicker(df)
        guests_map[pid] = picker.rooms * 1.9
        # room revenue recognised per stay night
        act = df[df["status"].isin(["CHECKED_OUT", "CHECKED_IN"])]
        fxa = fx[p["currency"]][np.minimum(act["a"].values, IA)]
        rate = (act["rate_usd"].values / fxa).round(2)
        nightly = (rate * act["los"].values - (rate * act["los"].values * act["disc_pct"].values).round(2)) / act["los"].values
        nd = np.repeat(act["a"].values, act["los"].values) + (np.arange(act["los"].sum()) - np.repeat(act["los"].cumsum().values - act["los"].values, act["los"].values))
        nv = np.repeat(nightly, act["los"].values)
        ok = (nd >= IH) & (nd <= IA)
        rev[pid, "Hotels"] = np.bincount(nd[ok], nv[ok], N)
        write(format_pms(p, df, fx[p["currency"]]), f"pms_{p['pms']}/reservations_{p['source_code']}.csv")
        inv = pd.DataFrame([(p["source_code"], d.strftime("%Y-%m-%d"), rt if p["pms"] == "staysuite" else IK_ROOM[rt], c,
                             int(RNG.random() < 0.06 and c >= 20) * int(RNG.integers(1, 3)))
                            for d in DATES[IH:IA + 91] for rt, c in p["rooms"].items()],
                           columns=["property_code", "business_date", "room_type", "total_rooms", "out_of_order"])
        write(inv, f"pms_{p['pms']}/room_inventory_{p['source_code']}.csv")
        seen = np.unique(df["person"].values)
        em_v = email[seen].values.copy()
        if p["pms"] == "innkeeper":      # messy identity data: upper case, padding
            mess = RNG.random(len(seen)) < 0.3
            em_v = np.where(mess, " " + pd.Series(em_v).str.upper() + " ", em_v)
        em_v = np.where(RNG.random(len(seen)) < 0.03, "", em_v)
        prof = pd.DataFrame({"profile_id": pms_id(p["pms"], seen), "first_name": np.array(FIRST)[fi[seen]], "last_name": np.array(LAST)[li[seen]], "email": em_v,
                             "nationality": country[seen], "loyalty_no": np.where(member[seen], "L" + pd.Series(seen).astype(str).str.zfill(6), ""),
                             "preferences": RNG.choice(["High floor", "Quiet room", "Feather-free", "Late checkout", "Sea view", ""], len(seen)),
                             "property_code": p["source_code"]})
        write(prof, f"pms_{p['pms']}/guest_profiles_{p['source_code']}.csv")

    # ---- restaurants / waterpark / retail / spa ---------------------------
    check_base = txn_base = 100000
    pos_parts, tbl_parts = {}, []
    for p in props:
        pid, fxb = p["property_id"], FX_BASE[p["currency"]]
        local = season_curve(p["season"]) ** 0.5 * np.array(DOW[p["season"]])[DATES.day_of_week.values] * ar_noise(0.04)
        g = guests_map[pid]
        r_rev, r_cost, lines_p = np.zeros(N), np.zeros(N), []
        for o in p["restaurants"]:
            lines, tbl, menu_df, rv, cs, n = gen_restaurant(p, o, g, local, pickers[pid], fxb, check_base)
            check_base += n + 500
            lines_p.append(lines)
            tbl_parts.append(tbl)
            menus.append(menu_df)
            r_rev += rv
            r_cost += cs
            outlets.append((o["code"], o["name"], pid, "Restaurant", {"fine": "Fine Dining", "allday": "All-Day Dining", "pool": "Pool Bar & Grill"}[o["type"]], o["seats"], o["tables"],
                            {"fine": 1.6, "allday": 3.4, "pool": 2.4}[o["type"]]))
        rev[pid, "Restaurants"], cost[pid, "food"] = r_rev, r_cost
        write(pd.concat(lines_p, ignore_index=True), f"pos_restaurant/pos_lines_{pid}.csv")
        vis = np.zeros(N)
        if "waterpark" in p:
            tk, attr, ops, am, wrev, vis = gen_waterpark(p, g, local, pickers[pid], fxb, season_curve(p["season"]))
            rev[pid, "Waterpark"] = wrev
            attractions.append(am)
            write(tk, f"ticketing/tickets_{p['waterpark']['code']}.csv")
            write(attr, f"ticketing/attraction_hourly_{p['waterpark']['code']}.csv")
            write(ops, f"ticketing/park_daily_ops_{p['waterpark']['code']}.csv")
            outlets.append((p["waterpark"]["code"], p["waterpark"]["name"], pid, "Waterpark", "Waterpark", p["waterpark"]["capacity"], 0, 1))
        vis_map[pid] = vis
        t_rev, t_cogs, lp, ip = np.zeros(N), np.zeros(N), [], []
        for s in p["stores"]:
            lines, inv, rv, cg, n = gen_retail(p, s, g, vis, pickers[pid], fxb, txn_base)
            txn_base += n + 500
            lp.append(lines)
            ip.append(inv)
            t_rev += rv
            t_cogs += cg
            outlets.append((s["code"], s["name"], pid, "Retail Store", "Park Shop" if s.get("park") else "Boutique", 0, 0, 1))
        rev[pid, "Retail"], cost[pid, "cogs"] = t_rev, t_cogs
        write(pd.concat(lp, ignore_index=True), f"pos_retail/retail_lines_{pid}.csv")
        write(pd.concat(ip, ignore_index=True), f"pos_retail/inventory_daily_{pid}.csv")
        appts, srev = gen_spa(p, g, local, pickers[pid], fxb, NPER)
        rev[pid, "Spa & Leisure"] = srev
        write(appts, f"spa/appointments_{pid}.csv")
        outlets.append((p["spa"]["code"], p["spa"]["name"], pid, "Spa", "Spa", p["spa"]["therapists"], 0, 1))
        employees += [(f"E-{pid}-T{i + 1:02d}", f"{FIRST[(i * 7 + int(pid[-1])) % 40]} {LAST[(i * 11 + int(pid[-1]) * 3) % 40]}", pid, "Spa & Leisure", "Therapist") for i in range(p["spa"]["therapists"])]
        for dept, role, cnt in [("Hotels", "Front Office", 6), ("Hotels", "Housekeeping", 10), ("Restaurants", "Service", 12), ("Restaurants", "Kitchen", 8), ("Retail", "Sales Associate", 4), ("Finance", "Accounting", 3)]:
            employees += [(f"E-{pid}-{role[:2].upper()}{i + 1:02d}", f"{FIRST[(i * 5 + len(role)) % 40]} {LAST[(i * 13 + len(dept)) % 40]}", pid, dept, role) for i in range(cnt)]
    write(pd.concat(tbl_parts, ignore_index=True), "restaurant_booking/table_reservations.csv")

    # ---- master data ------------------------------------------------------
    write(pd.DataFrame(outlets, columns=["outlet_code", "outlet_name", "property_id", "outlet_type", "sub_type", "capacity", "tables", "turns_per_day"]), "mdm/outlets.csv")
    write(pd.concat(menus, ignore_index=True), "pos_restaurant/menu_items.csv")
    write(pd.concat(attractions, ignore_index=True), "ticketing/attractions.csv")
    write(pd.DataFrame(employees, columns=["employee_id", "employee_name", "property_id", "department", "role"]), "hr/employees.csv")
    write(pd.DataFrame(events, columns=["property_id", "event_date", "event_name"]), "mdm/events.csv")
    write(pd.DataFrame({"sku": [f"SKU-{i + 1:03d}" for i in range(len(PRODUCTS))], "product_name": [x[0] for x in PRODUCTS], "category": [x[1] for x in PRODUCTS],
                        "list_price_usd": [x[2] for x in PRODUCTS]}), "pos_retail/products.csv")
    write(pd.DataFrame({"service_code": [f"SVC-{i + 1:02d}" for i in range(len(SPA_SERVICES))], "service_name": [x[0] for x in SPA_SERVICES],
                        "service_category": [x[1] for x in SPA_SERVICES], "duration_minutes": [x[2] for x in SPA_SERVICES], "list_price_usd": [x[3] for x in SPA_SERVICES]}), "spa/services.csv")

    # ---- CRM, loyalty, feedback ------------------------------------------
    res = pd.concat(res_all, ignore_index=True)
    first_seen = res.groupby("person")["bday"].min()
    mem = np.where(member)[0]
    joined = DATES[np.clip(first_seen.reindex(mem).fillna(IH).astype(int).values - RNG.integers(0, 400, len(mem)), 0, IA)]
    crm = pd.DataFrame({"customer_id": crm_id(mem), "email": email[mem].values, "first_name": np.array(FIRST)[fi[mem]], "last_name": np.array(LAST)[li[mem]],
                        "country": country[mem], "loyalty_id": "L" + pd.Series(mem).astype(str).str.zfill(6), "loyalty_tier": tiers[tier_code[mem]],
                        "marketing_consent": (RNG.random(len(mem)) < 0.8).astype(int), "created_at": joined.strftime("%Y-%m-%d"),
                        "updated_at": pd.DatetimeIndex(np.minimum((joined + pd.to_timedelta(RNG.integers(0, 200, len(mem)), unit="D")).values, np.datetime64(AS_OF))).strftime("%Y-%m-%d")})
    prev = crm[crm["loyalty_tier"].isin(["Gold", "Platinum"])].sample(frac=0.35, random_state=2).copy()   # earlier tier versions -> SCD2
    prev["loyalty_tier"] = prev["loyalty_tier"].map({"Gold": "Silver", "Platinum": "Gold"})
    prev["updated_at"] = prev["created_at"]
    write(pd.concat([prev, crm], ignore_index=True), "crm/customers.csv")

    stayed = res[(res["status"] == "CHECKED_OUT") & member[res["person"].values]]
    pts = (stayed["rate_usd"] * stayed["los"] * (1 - stayed["disc_pct"]) * 10).round().astype(int)
    earn = pd.DataFrame({"loyalty_id": "L" + stayed["person"].astype(str).str.zfill(6), "event_date": DATES[(stayed["a"] + stayed["los"]).clip(upper=IA).values].strftime("%Y-%m-%d"),
                         "event_type": "EARN", "points": pts.values, "property_id": stayed["property_id"].values, "business_vertical": "Hotels"})
    red = earn.sample(frac=0.16, random_state=4).copy()
    red["event_type"] = "REDEEM"
    red["points"] = RNG.integers(5, 40, len(red)) * 1000
    red["business_vertical"] = RNG.choice(["Hotels", "Restaurants", "Spa & Leisure", "Retail"], len(red), p=[.55, .2, .15, .1])
    loy = pd.concat([earn, red], ignore_index=True)
    loy.insert(0, "event_id", "LE" + pd.Series(np.arange(len(loy))).astype(str).str.zfill(7))
    write(loy, "crm/loyalty_events.csv")

    out_ = res[res["status"] == "CHECKED_OUT"].sample(frac=0.22, random_state=9)
    adj = out_["property_id"].map({"P001": 0.3, "P002": -0.2, "P003": 0.0, "P004": -0.7, "P005": 0.2, "P006": 0.1}).values
    vend = out_["property_id"].map({p["property_id"]: p["pms"] for p in props}).values
    fb = pd.DataFrame({"reservation_ref": np.where(vend == "staysuite", "SS" + out_["res_no"].astype(str).str.zfill(8), "IK-" + out_["res_no"].astype(str)),
                       "survey_date": DATES[(out_["a"] + out_["los"]).clip(upper=IA).values].strftime("%Y-%m-%d"),
                       "nps_score": np.clip(np.round(RNG.normal(8.5 + adj, 1.5)), 0, 10).astype(int)})
    write(fb, "crm/guest_feedback.csv")

    # ---- marketing --------------------------------------------------------
    rooms_share = {p["property_id"]: sum(p["rooms"].values()) for p in props}
    tot_rooms = sum(rooms_share.values())
    camps, daily = [], []
    mk_spend = {p["property_id"]: np.zeros(N) for p in props}
    months = pd.date_range(HISTORY_START, AS_OF, freq="MS")
    cno = 1
    for ms in months:
        for _ in range(10):
            vert = RNG.choice(list(AOV), p=[.45, .13, .15, .09, .18])
            pool = [p["property_id"] for p in props if vert != "Waterpark" or "waterpark" in p]
            pid = "ALL" if (RNG.random() < 0.3 and vert != "Waterpark") else str(RNG.choice(pool))
            chn = str(RNG.choice(list(CAMPAIGN_CH), p=[.22, .22, .2, .14, .12, .10]))
            dur = int(RNG.integers(14, 46))
            start = ms + pd.Timedelta(days=int(RNG.integers(0, 28)))
            budget = float(np.round(RNG.lognormal(np.log(15000), 0.6), -2))
            cid = f"CMP-{cno:04d}"
            cno += 1
            camps.append((cid, f"{RNG.choice(THEMES)} | {vert} | {chn} {start:%b %Y}", chn, vert, pid, start.date(), (start + pd.Timedelta(days=dur - 1)).date(), budget, str(RNG.choice(AUDIENCES))))
            cpc, cvr = CAMPAIGN_CH[chn]
            quality = RNG.lognormal(0, 0.3)
            dd = pd.date_range(start, min(start + pd.Timedelta(days=dur - 1), pd.Timestamp(AS_OF)))
            if not len(dd):
                continue
            spend = budget / dur * RNG.lognormal(0, 0.15, len(dd))
            clicks = RNG.poisson(spend / cpc)
            conv = RNG.poisson(clicks * cvr * (1 if vert == "Hotels" else 6) * quality)
            di = (dd - D0).days.values
            for q, sh in ([(pid, 1.0)] if pid != "ALL" else [(k, v / tot_rooms) for k, v in rooms_share.items()]):
                np.add.at(mk_spend[q], di, spend * sh)
            daily.append(pd.DataFrame({"campaign_id": cid, "activity_date": dd.strftime("%Y-%m-%d"), "impressions": (clicks / RNG.uniform(0.01, 0.04)).astype(int),
                                       "clicks": clicks, "leads": RNG.binomial(clicks, 0.08), "conversions": conv, "spend_usd": spend.round(2),
                                       "attributed_revenue_usd": (conv * AOV[vert] * 0.3 * RNG.lognormal(0, 0.2, len(dd))).round(2),
                                       "new_customers": RNG.binomial(conv, 0.45)}))
    write(pd.DataFrame(camps, columns=["campaign_id", "campaign_name", "channel", "target_vertical", "property_id", "start_date", "end_date", "budget_usd", "audience_segment"]), "crm/campaigns.csv")
    write(pd.concat(daily, ignore_index=True), "crm/campaign_daily.csv")

    # ---- ERP: general ledger, budget, procurement -------------------------
    ACC = {"Hotels": ("4000", "Rooms Revenue"), "Restaurants": ("4100", "Food & Beverage Revenue"), "Waterpark": ("4200", "Waterpark Revenue"),
           "Retail": ("4300", "Retail Revenue"), "Spa & Leisure": ("4400", "Spa & Leisure Revenue")}
    gl = []
    for p in props:
        pid, unit = p["property_id"], p["cost_factor"] / FX_BASE[p["currency"]]
        R = {v: rev.get((pid, v), np.zeros(N)) for v in ACC}
        rooms, seats = sum(p["rooms"].values()), sum(o["seats"] for o in p["restaurants"])
        cap = p.get("waterpark", {}).get("capacity", 0)
        climate = season_curve(p["season"]) if p["season"] in ("north_summer",) else 2 - season_curve(p["season"]) if p["season"] == "gulf" else np.ones(N)
        for v, (acct, name) in ACC.items():
            if R[v].sum() > 0:
                amt = R[v].copy()
                if pid == "P004" and v == "Restaurants":          # injected: two days of F&B revenue never posted to the GL
                    amt[IA - 30], amt[IA - 29] = 0, 0
                gl.append((pid, v, v if v != "Hotels" else "Rooms", acct, name, "REVENUE", amt))
        exp = [
            ("Hotels", "Rooms", "5000", "Payroll", rooms * 75 * unit + 0.10 * R["Hotels"]),
            ("Hotels", "Rooms", "5200", "Distribution Commissions", 0.06 * R["Hotels"]),
            ("Hotels", "Rooms", "5300", "Operating Supplies", 0.035 * R["Hotels"]),
            ("Restaurants", "Food & Beverage", "5000", "Payroll", seats * 9 * unit + 0.28 * R["Restaurants"]),
            ("Restaurants", "Food & Beverage", "5100", "Cost of Sales", cost[pid, "food"]),
            ("Retail", "Retail", "5100", "Cost of Sales", cost[pid, "cogs"]),
            ("Retail", "Retail", "5000", "Payroll", len(p["stores"]) * 520 * unit + 0 * R["Retail"]),
            ("Spa & Leisure", "Spa & Leisure", "5000", "Payroll", p["spa"]["therapists"] * 150 * unit + 0.18 * R["Spa & Leisure"]),
            ("Spa & Leisure", "Spa & Leisure", "5300", "Operating Supplies", 0.06 * R["Spa & Leisure"]),
            ("Marketing", "Sales & Marketing", "5600", "Marketing & Advertising", mk_spend[pid] / FX_BASE[p["currency"]] + rooms * 6 * unit),
            ("Finance", "Administrative & General", "5700", "Administration & IT", np.full(N, rooms * 45 * unit)),
            ("Finance", "Administrative & General", "5000", "Payroll", np.full(N, rooms * 40 * unit)),
            ("Finance", "Property Operations", "5500", "Repairs & Maintenance", np.full(N, rooms * 32 * unit)),
            ("Finance", "Utilities", "5400", "Energy & Water", rooms * 28 * unit * (0.8 + 0.25 * climate) * (np.where(np.arange(N) > IA - 24, 1.48, 1.0) if pid == "P003" else 1.0)),
        ]
        if cap:
            exp += [("Waterpark", "Waterpark", "5000", "Payroll", cap * 4.9 * unit + 0.10 * R["Waterpark"]),
                    ("Waterpark", "Waterpark", "5500", "Repairs & Maintenance", np.full(N, cap * 1.5 * unit))]
        for bu, cc, acct, name, amt in exp:
            gl.append((pid, bu, cc, acct, name, "EXPENSE", np.asarray(amt, dtype=float) * (RNG.lognormal(0, 0.05, N) if acct not in ("5100",) else 1.0)))
    erp_code = {p["property_id"]: p["erp_code"] for p in props}
    ccy = {p["property_id"]: p["currency"] for p in props}
    parts = []
    for pid, bu, cc, acct, name, typ, amt in gl:
        parts.append(pd.DataFrame({"company_code": erp_code[pid], "posting_date": DATES[HIST].strftime("%Y-%m-%d"), "business_unit": bu, "cost_centre": cc,
                                   "gl_account": acct, "gl_account_name": name, "account_type": typ, "amount": amt[HIST].round(2), "currency": ccy[pid]}))
    glf = pd.concat(parts, ignore_index=True)
    glf = glf[glf["amount"] > 0].reset_index(drop=True)
    glf.insert(0, "journal_id", "JE" + pd.Series(np.arange(len(glf))).astype(str).str.zfill(8))
    glf["fiscal_period"] = glf["posting_date"].str[:7]
    write(glf, "erp/gl_transactions.csv")

    mon = glf.groupby(["company_code", "business_unit", "cost_centre", "gl_account", "gl_account_name", "account_type", "currency", "fiscal_period"], as_index=False)["amount"].sum()
    cur = f"{AS_OF:%Y-%m}"
    hist_b = mon[mon["fiscal_period"] < cur].copy()
    util_dxb = (hist_b["company_code"] == "2001") & (hist_b["gl_account"] == "5400")
    hist_b["budget_amount"] = (hist_b["amount"] * np.where(hist_b["account_type"] == "REVENUE", RNG.uniform(0.97, 1.08, len(hist_b)), RNG.uniform(0.94, 1.05, len(hist_b)))).round(0)
    hist_b.loc[util_dxb & (hist_b["fiscal_period"] == f"{pd.Timestamp(AS_OF) - pd.DateOffset(months=1):%Y-%m}"), "budget_amount"] /= 1.2
    fut = []
    for k in range(0, 5):
        per = pd.Timestamp(AS_OF).to_period("M") + k
        ly = mon[mon["fiscal_period"] == str(per - 12)].copy()
        ly["fiscal_period"] = str(per)
        ly["budget_amount"] = (ly["amount"] * 1.06).round(0)
        fut.append(ly)
    write(pd.concat([hist_b] + fut, ignore_index=True).drop(columns=["amount", "gl_account_name"]), "erp/budget.csv")

    sup, sid = [], 1
    for cat in PROC_CATEGORIES:
        for j in range(4):
            sup.append((f"SUP-{sid:03d}", f"{['Meridian', 'Atlas', 'Bluewater', 'Summit', 'Heritage', 'Orchid', 'Solstice', 'Harbour'][(sid * 3) % 8]} {cat.split(' ')[0]} {['Trading', 'Supply Co', 'Partners', 'Group'][j]}",
                        cat, str(RNG.choice(COUNTRIES)), round(float(RNG.uniform(0.9, 1.12)), 3), round(float(RNG.uniform(0.62, 0.98)), 2)))
            sid += 1
    supf = pd.DataFrame(sup, columns=["supplier_id", "supplier_name", "category", "country", "price_factor", "on_time_rate"])
    write(supf.drop(columns=["price_factor", "on_time_rate"]), "erp/suppliers.csv")
    npo = 13000
    pd_ = RNG.choice(HIST, npo)
    pcat = RNG.choice(list(PROC_CATEGORIES), npo, p=[.34, .10, .10, .12, .10, .08, .06, .10])
    ppid = RNG.choice([p["property_id"] for p in props], npo, p=np.array(list(rooms_share.values())) / tot_rooms)
    rows = []
    for i in range(npo):
        items = PROC_CATEGORIES[pcat[i]]
        name, base = items[RNG.integers(len(items))]
        s = supf[supf["category"] == pcat[i]].iloc[RNG.integers(4)]
        price = base * s["price_factor"] * (1 + 0.035 * (pd_[i] - IH) / 365) * RNG.lognormal(0, 0.04)
        if s["supplier_id"] == "SUP-002" and ppid[i] == "P005" and pd_[i] > IA - 60:      # injected procurement price anomaly
            price *= 1.38
        promised = int(RNG.integers(2, 10))
        late = RNG.random() > s["on_time_rate"]
        qty = int(RNG.integers(2, 60) if base < 500 else RNG.integers(1, 6))
        lp = price / fx[ccy[ppid[i]]][pd_[i]]
        rows.append((f"PO-{i + 1:06d}", DATES[pd_[i]].strftime("%Y-%m-%d"), erp_code[ppid[i]], s["supplier_id"], pcat[i], name, qty, round(lp, 2), f"INV-{i + 1:06d}",
                     round(qty * lp * (1.0 if RNG.random() > 0.01 else 1.08), 2), promised, promised + (int(RNG.integers(1, 9)) if late else 0), ccy[ppid[i]]))
    write(pd.DataFrame(rows, columns=["po_number", "po_date", "company_code", "supplier_id", "category", "item_description", "quantity", "unit_price", "invoice_id",
                                      "invoice_amount", "promised_days", "delivered_days", "currency"]), "erp/purchase_orders.csv")
    from hip.synth import experience          # own random generator: the extracts above are unchanged
    experience.main()
    print("Done.")


if __name__ == "__main__":
    main()
