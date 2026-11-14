"""Synthetic engineering and energy source extracts: building-management meters, weather, the
device register, IoT device telemetry, maintenance work orders and the asset contract register.

Runs after the core Gold layer exists, because energy use is driven by the occupancy already in
the lakehouse. It uses its own random generator, so the rest of the estate is unchanged.

Known problems planted for the models to find:
  - Dubai Palm (P003): public-area cooling runs far above normal for the last 24 days (chiller fault)
  - Doha (P004): vacant guest rooms are barely set back, wasting cooling energy
  - Phuket (P005): a continuous water leak starts 18 days before the business date
  - devices that develop a fault show rising fault counts and power draw before they fail
  - several contracts are about to end, and three have already lapsed
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from hip.settings import AS_OF, HISTORY_START, LANDING, load, properties

RNG = np.random.default_rng(77120)
WINDOWS = 25                     # 30-day telemetry windows ending on the business date
HOURLY = np.array([.62, .58, .56, .55, .56, .62, .78, .95, 1.05, 1.08, 1.10, 1.14, 1.18, 1.20, 1.18, 1.15, 1.16, 1.22, 1.30, 1.34, 1.30, 1.18, .95, .75])
WATER = np.array([.25, .18, .14, .14, .18, .45, 1.35, 1.90, 1.70, 1.25, 1.00, .95, 1.00, .95, .85, .85, .95, 1.20, 1.50, 1.60, 1.35, 1.05, .70, .40])
# one supplier per contract template, in the same order as contract_templates in config/energy.json
SUPPLIERS = ["Meridian Building Services", "Summit Lift Services", "Heritage Lock Systems", "Orchid Media Leasing", "Solstice Refrigeration", "Bluewater Boiler Engineering",
             "Northlight Controls", "Harbour Energy Retail", "Keystone Laundry Systems", "Atlas Kitchen Refrigeration"]


def write(df: pd.DataFrame, rel: str) -> None:
    path = LANDING / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"  {rel:<40}{len(df):>10,} rows")


def main(con) -> None:
    cfg = load("energy.json")
    props = {p["property_id"]: p for p in properties()}
    days = pd.date_range(HISTORY_START, AS_OF)
    fut = pd.date_range(pd.Timestamp(AS_OF) + pd.Timedelta(days=1), periods=30)
    occ = con.execute("SELECT property_id, business_date, rooms_sold, rooms_available FROM gold.mart_executive_daily").df()
    occ["business_date"] = pd.to_datetime(occ["business_date"])
    n = len(days)
    print("Generating engineering and energy source extracts")

    # ---------------------------------------------------------------- weather, energy, water
    weather, daily, hourly = [], [], []
    for pid, e in cfg["properties"].items():
        p, R = props[pid], sum(props[pid]["rooms"].values())
        all_days = days.append(fut)
        m = np.array(e["monthly_temp_c"], dtype=float)
        x = (all_days.day_of_year.values - 15) / 30.4375
        i0 = np.floor(x).astype(int) % 12
        temp = m[i0] * (1 - (x - np.floor(x))) + m[(i0 + 1) % 12] * (x - np.floor(x)) + RNG.normal(0, 1.6, len(all_days))
        weather.append(pd.DataFrame({"property_id": pid, "weather_date": all_days.strftime("%Y-%m-%d"), "avg_temp_c": temp.round(1), "is_forecast": (all_days > pd.Timestamp(AS_OF)).astype(int)}))
        t = temp[:n]
        o = occ[occ["property_id"] == pid].set_index("business_date")["rooms_sold"].reindex(days).fillna(0).values
        cdd, hdd = np.maximum(t - 20, 0), np.maximum(16 - t, 0)
        per_room = 6 + 0.9 * cdd + 0.7 * hdd
        noise = lambda: RNG.lognormal(0, 0.04, n)  # noqa: E731
        recent = np.arange(n) >= n - 24
        use = {
            "Guest room HVAC (occupied rooms)": o * per_room * noise(),
            "Guest room HVAC (vacant rooms)": (R - o) * per_room * e["setback"] * noise(),
            "Public area HVAC": R * (4 + 0.5 * cdd + 0.4 * hdd) * noise() * (np.where(recent, 2.1, 1.0) if pid == "P003" else 1.0),
            "Lighting and power": (R * 5 + o * 4) * noise(),
            "Kitchens": (R * 3 + o * 2.5) * noise(),
            "Laundry": o * 3.5 * noise(),
            "Hot water": (o * 5 + R * 1) * noise(),
            "Pools and waterpark": (9000 if "waterpark" in p else R * 2) * (0.85 + 0.01 * cdd) * noise(),
            "Other": R * 3 * noise(),
        }
        for end_use, v in use.items():
            daily.append(pd.DataFrame({"property_id": pid, "reading_date": days.strftime("%Y-%m-%d"), "end_use": end_use, "kwh": v.round(1)}))
        total = sum(use.values())
        water = (o * 0.45 + R * 0.12 + (150 if "waterpark" in p else 0)) * RNG.lognormal(0, 0.05, n)
        hk = total[:, None] * (HOURLY / HOURLY.sum())[None, :] * RNG.lognormal(0, 0.05, (n, 24))
        hw = water[:, None] * (WATER / WATER.sum())[None, :] * RNG.lognormal(0, 0.10, (n, 24))
        if pid == "P005":
            hw[n - 18:, :] += 1.6                                           # continuous leak, visible in the overnight hours
        ts = (days.values[:, None] + (np.arange(24) * np.timedelta64(1, "h"))[None, :]).ravel()
        hourly.append(pd.DataFrame({"property_id": pid, "reading_ts": pd.DatetimeIndex(ts).strftime("%Y-%m-%d %H:%M:%S"), "kwh": hk.ravel().round(2), "water_m3": hw.ravel().round(3)}))
    write(pd.concat(weather, ignore_index=True), "bms/weather_daily.csv")
    write(pd.concat(daily, ignore_index=True), "bms/energy_daily.csv")
    hourly = pd.concat(hourly, ignore_index=True)
    write(hourly, "bms/meter_hourly.csv")

    # ---------------------------------------------------------------- device register
    rows = []
    for pid, p in props.items():
        R, base = sum(p["rooms"].values()), cfg["properties"][pid]["cohort_year"]
        for d in cfg["device_types"]:
            count = R if d["scope"] == "room" else max(2, round(R / 100 * d["per_100_rooms"]))
            refreshed = RNG.random(count) < (0.25 if d["scope"] == "room" else 0.15)          # part of the estate was renewed recently
            year = np.where(refreshed, RNG.integers(2022, 2026, count), base + RNG.integers(-1, 2, count))
            install = pd.to_datetime(dict(year=year, month=RNG.integers(1, 13, count), day=RNG.integers(1, 28, count)))
            for i in range(count):
                loc = f"Room {100 * (1 + i // 24) + i % 24 + 1}" if d["scope"] == "room" else f"{d['type']} {i + 1}"
                rows.append((f"{pid}-{d['type'][:3].upper()}{i + 1:04d}", pid, loc, d["type"], d["scope"], install[i].date(), d["life_years"], d["replacement_usd"],
                             (install[i] + pd.DateOffset(years=d["warranty_years"])).date()))
    assets = pd.DataFrame(rows, columns=["asset_id", "property_id", "location", "device_type", "scope", "install_date", "expected_life_years", "replacement_cost_usd", "warranty_end"])
    write(assets, "cmms/assets.csv")

    # ---------------------------------------------------------------- telemetry and failures, in 30-day windows
    A = len(assets)
    dt = {d["type"]: d for d in cfg["device_types"]}
    T = assets["device_type"].map(lambda t: dt[t])
    life = assets["expected_life_years"].values.astype(float)
    hazard = T.map(lambda d: d["monthly_failure"]).values
    corr, prev = T.map(lambda d: d["corrective_usd"]).values, T.map(lambda d: d["preventive_usd"]).values
    ooo = T.map(lambda d: d["room_out_of_order"]).values
    plant = (assets["scope"] == "plant").values
    frailty = RNG.lognormal(0, 0.5, A)
    ends = [pd.Timestamp(AS_OF) - pd.Timedelta(days=30 * (WINDOWS - 1 - k)) for k in range(WINDOWS)]
    install = pd.to_datetime(assets["install_date"]).values
    degrading = np.zeros(A, dtype=int)            # windows left until a developing fault becomes a failure
    since_service = RNG.integers(20, 300, A).astype(float)
    tel, wos, wo_no = [], [], 1
    occ_share = occ.groupby("property_id").apply(lambda g: g["rooms_sold"].sum() / g["rooms_available"].sum()).to_dict()
    duty = assets["property_id"].map(occ_share).values
    for k, end in enumerate(ends):
        age = np.maximum((end.to_datetime64() - install) / np.timedelta64(365, "D"), 0.0)
        active = age > 0.02
        wear = np.clip(age / life, 0, 1.6)
        start = active & (degrading == 0) & (RNG.random(A) < 0.006 * (1 + 2.5 * wear) * frailty)
        degrading[start] = RNG.integers(1, 4, start.sum())
        sick = degrading > 0
        faults = RNG.poisson(0.15 + 0.6 * wear + np.where(sick, 4.0, 0.0))
        drift = np.clip(RNG.normal(2 + 9 * wear ** 2, 2.0, A) + np.where(sick, RNG.uniform(8, 25, A), 0), 0, None)
        runtime = 720 * np.where(plant, 0.75, 0.25 + 0.6 * duty) * RNG.lognormal(0, 0.08, A)
        tel.append(pd.DataFrame({"asset_id": assets["asset_id"].values, "period_end": end.strftime("%Y-%m-%d"), "runtime_hours": runtime.round(0), "fault_count": faults,
                                 "power_drift_pct": drift.round(1), "days_since_service": since_service.round(0)})[active])
        # failures happen during the window that follows this snapshot
        due = sick & (degrading == 1)
        fail = active & ((due & (RNG.random(A) < 0.85)) | (RNG.random(A) < hazard * (0.4 + 1.6 * wear ** 2) * frailty))
        degrading[sick] -= 1
        degrading[fail] = 0
        if k < WINDOWS - 1:
            idx = np.where(fail)[0]
            when = end + pd.to_timedelta(RNG.integers(1, 30, len(idx)), unit="D")
            for j, i in enumerate(idx):
                wos.append((f"WO-{wo_no:06d}", assets["asset_id"].values[i], when[j].date(), "Corrective", dt[assets["device_type"].values[i]]["part"],
                            round(float(RNG.uniform(2, 30) if plant[i] else RNG.uniform(1, 14)), 1), round(float(corr[i] * RNG.lognormal(0, 0.25)), 2), int(RNG.random() < ooo[i])))
                wo_no += 1
            since_service[fail] = 0
            pm = active & ~fail & (since_service > np.where(plant, 170, 350))
            for i in np.where(pm)[0]:
                wos.append((f"WO-{wo_no:06d}", assets["asset_id"].values[i], (end + pd.Timedelta(days=int(RNG.integers(1, 30)))).date(), "Preventive", "Planned service", 0.0,
                            round(float(prev[i] * RNG.lognormal(0, 0.15)), 2), 0))
                wo_no += 1
            since_service[pm] = 0
            degrading[pm & (RNG.random(A) < 0.6)] = 0                        # a planned service often clears a developing fault
            since_service += 30
    write(pd.concat(tel, ignore_index=True), "iot/device_telemetry.csv")
    write(pd.DataFrame(wos, columns=["work_order_id", "asset_id", "completed_date", "work_type", "failure_mode", "downtime_hours", "cost_usd", "room_out_of_order"]), "cmms/work_orders.csv")

    # ---------------------------------------------------------------- contract register
    peak = hourly.assign(d=hourly["reading_ts"].str[:10]).groupby(["property_id", "d"])["kwh"].max().groupby("property_id").quantile(0.90)
    offsets = [-52, -31, -9, 6, 14, 22, 27, 38, 44, 57, 66, 71, 83, 88, 104, 126, 150, 171, 195, 220, 243, 262, 290, 315, 338, 360, 395, 420, 455, 490, 520, 560, 600, 640, 700]
    order = RNG.permutation(len(cfg["contract_templates"]) * len(props))
    rows = []
    for n_, (pid, t) in enumerate([(pid, t) for pid in props for t in cfg["contract_templates"]]):
        R = sum(props[pid]["rooms"].values())
        off = offsets[order[n_]] if order[n_] < len(offsets) else 300 + int(RNG.integers(0, 500))      # 35 fixed end dates, the rest further out
        end = pd.Timestamp(AS_OF) + pd.Timedelta(days=int(off))
        years = int(RNG.choice([1, 2, 3], p=[.3, .4, .3]))
        energy = t["kind"] == "Energy supply"
        value = round(R * 2600 * cfg["properties"][pid]["tariff_usd_kwh"] / 0.2 if energy else R * t["value_per_room"] * RNG.uniform(0.9, 1.15), -2)
        rows.append((f"CT-{pid}-{n_ % 10 + 1:02d}", pid, SUPPLIERS[n_ % 10], t["type"], t["kind"], t["covers"], (end - pd.DateOffset(years=years) + pd.Timedelta(days=1)).date(), end.date(),
                     value, int(RNG.random() < 0.45), int(RNG.choice([30, 60, 90])), int(RNG.choice([4, 8, 24])) if not energy else "", round(float(peak[pid]), -1) if energy else ""))
    write(pd.DataFrame(rows, columns=["contract_id", "property_id", "supplier_name", "contract_name", "contract_kind", "covers_device_types", "start_date", "end_date", "annual_value_usd",
                                      "auto_renew", "notice_days", "sla_response_hours", "contracted_demand_kw"]), "contracts/asset_contracts.csv")
