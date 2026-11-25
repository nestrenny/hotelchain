"""Synthetic guest-experience source extracts: the overall-satisfaction answer on the post-stay survey,
and the guest-relations complaint log.

Runs at the end of source generation, reading the reservation and survey extracts already written.
It uses its own random generator, so every other extract is unchanged.

  crm/guest_feedback.csv     gains an overall_satisfaction column (1 = very dissatisfied ... 5 = very satisfied),
                             answered on the same survey as the NPS question and correlated with it
  crm/guest_complaints.csv   one row per complaint raised during a stay, from the guest-relations log

Known pattern planted for the morning report to find:
  - Singapore (P006): complaints about Wi-Fi and in-room technology climb over the last 30 days
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from hip.settings import AS_OF, LANDING

RNG = np.random.default_rng(55031)
CATEGORIES = ["Room cleanliness", "Noise", "Check-in wait", "Restaurant service", "Maintenance", "Billing", "Wi-Fi and in-room technology", "Air conditioning"]
CAT_P = np.array([.18, .14, .14, .14, .14, .08, .10, .08])
BASE_RATE = {"P001": 0.022, "P002": 0.030, "P003": 0.026, "P004": 0.042, "P005": 0.028, "P006": 0.024}   # complaints per checked-out stay
CODE = {"SS-COMO": "P001", "IK-LON": "P002", "SS-DXB": "P003", "IK-DOH": "P004", "SS-HKT": "P005", "IK-SIN": "P006"}


def stays() -> pd.DataFrame:
    """Checked-out stays from both PMS extracts, in one shape."""
    out = []
    for f in sorted((LANDING / "pms_staysuite").glob("reservations_*.csv")):
        d = pd.read_csv(f, usecols=["ConfirmationNo", "PropertyCode", "ArrivalDate", "DepartureDate", "Status"])
        d = d[d["Status"] == "CHECKED_OUT"]
        out.append(pd.DataFrame({"ref": d["ConfirmationNo"], "pid": d["PropertyCode"].map(CODE), "arr": pd.to_datetime(d["ArrivalDate"], errors="coerce"), "dep": pd.to_datetime(d["DepartureDate"], errors="coerce")}))
    for f in sorted((LANDING / "pms_innkeeper").glob("reservations_*.csv")):
        d = pd.read_csv(f, usecols=["res_id", "hotel_id", "checkin", "checkout", "state"])
        d = d[d["state"] == "O"]
        out.append(pd.DataFrame({"ref": d["res_id"], "pid": d["hotel_id"].map(CODE), "arr": pd.to_datetime(d["checkin"], format="%d/%m/%Y", errors="coerce"), "dep": pd.to_datetime(d["checkout"], format="%d/%m/%Y", errors="coerce")}))
    s = pd.concat(out, ignore_index=True).dropna(subset=["arr", "dep"])      # the extracts carry some malformed dates on purpose
    return s[s["dep"] <= pd.Timestamp(AS_OF)].reset_index(drop=True)


def main() -> None:
    print("Generating guest-experience extracts")
    # ---- overall satisfaction on the existing survey, correlated with the NPS answer
    fb = pd.read_csv(LANDING / "crm" / "guest_feedback.csv")
    sat = np.clip(np.round(1.0 + fb["nps_score"].values * 0.42 + RNG.normal(0, 0.55, len(fb))), 1, 5).astype(int)
    fb["overall_satisfaction"] = sat
    fb.to_csv(LANDING / "crm" / "guest_feedback.csv", index=False)
    print(f"  {'crm/guest_feedback.csv (+ overall_satisfaction)':<40}{len(fb):>10,} rows")

    # ---- complaint log
    s = stays()
    low_nps = set(fb.loc[fb["nps_score"] <= 6, "reservation_ref"])
    p = s["pid"].map(BASE_RATE).values * np.where(s["ref"].isin(low_nps), 4.0, 1.0)
    recent = (s["pid"] == "P006").values & (s["dep"] > pd.Timestamp(AS_OF) - pd.Timedelta(days=30)).values
    p = np.where(recent, p * 2.4, p)                                                  # planted: Singapore technology complaints
    c = s[RNG.random(len(s)) < p].copy()
    cat_p = np.tile(CAT_P, (len(c), 1))
    wifi = CATEGORIES.index("Wi-Fi and in-room technology")
    sel = ((c["pid"] == "P006") & (c["dep"] > pd.Timestamp(AS_OF) - pd.Timedelta(days=30))).values
    cat_p[sel, wifi] = 0.55
    cat_p = cat_p / cat_p.sum(axis=1, keepdims=True)
    cats = np.array([RNG.choice(len(CATEGORIES), p=row) for row in cat_p])
    nights = ((c["dep"] - c["arr"]).dt.days.clip(lower=1)).values
    logged = c["arr"].values + pd.to_timedelta(RNG.integers(0, nights), unit="D").values
    sev = RNG.choice(["Low", "Medium", "High"], len(c), p=[.55, .35, .10])
    hours = np.round(RNG.lognormal(np.log(np.where(sev == "High", 2.0, np.where(sev == "Medium", 5.0, 9.0))), 0.6, len(c)), 1)
    out = pd.DataFrame({"complaint_id": [f"GR-{i + 1:06d}" for i in range(len(c))], "reservation_ref": c["ref"].values,
                        "logged_date": pd.DatetimeIndex(logged).strftime("%Y-%m-%d"), "category": np.array(CATEGORIES)[cats], "severity": sev,
                        "hours_to_resolve": hours, "resolved": (RNG.random(len(c)) < 0.97).astype(int)})
    out = out.sort_values(["logged_date", "complaint_id"]).reset_index(drop=True)
    path = LANDING / "crm" / "guest_complaints.csv"
    out.to_csv(path, index=False)
    print(f"  {'crm/guest_complaints.csv':<40}{len(out):>10,} rows")


if __name__ == "__main__":
    main()
