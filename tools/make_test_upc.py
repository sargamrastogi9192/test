"""
Make a small FAKE UPC in the bronze output format, to test the silver layer without real data.

    python tools/make_test_upc.py                          # writes test_data/bronze/... (repo folder)
    python tools/make_test_upc.py --out D:\\tmp\\bronze      # somewhere else
    python tools/make_test_upc.py --ca-interval roughness=500 rutting=100 cracking=1000

Then run silver on it (from the silver folder):

    python main.py --project ../test_data/bronze/Zone=A/Phase=1/Cycle=1/UPC=N_01002_01001_GJ --out ../test_data/silver --no-excel

What it writes (every value as text, like bronze):
    d4/run=…/d4_defect_report_data_sheet.parquet   lanes L1, L2, R1, 0–2000 m, one row per 10 m
    d4/run=…/d4_nhai_one_potholes.parquet          2 potholes (L1 High at 250 m, R1 Low at 1320 m)
    d2/run=…/d2_comparison_comparison_ca.parquet   reporting intervals (default roughness / rutting 1000 m, cracking 500 m)
    d2/run=…/d2_comparison_comparison_irc.parquet
    d2/run=…/meta/d2_comparison_data_dictionary.json   thresholds: CA BI 2000, rut 5, crack 5; IRC 2400, 10, 30

The data is chosen so the results are easy to check by hand; the expected values are printed at the end.
"""
import argparse
import json
import os
import shutil

import numpy as np
import pandas as pd

UPC = "N_01002_01001_GJ"
RUN = "run=20260115_120000"
CA_THR = {"roughness": 2000, "rutting": 5, "cracking": 5}
IRC_THR = {"roughness": 2400, "rutting": 10, "cracking": 30}
D2_GROUPS = {"roughness": "Roughness BI (in mm/km)", "rutting": "Rut Depth (in mm)", "cracking": "Cracking Lane Area (%)"}


def text(df):
    """Every value as text, empty → None (bronze writes all columns as text)."""
    return df.astype(object).where(df.notna(), None).map(lambda v: None if v is None else str(v))


def bi(iri):
    return 630.0 * iri ** 1.12


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=os.path.join(here, "test_data", "bronze"), help="bronze root to write to")
    ap.add_argument("--ca-interval", nargs="*", default=[], metavar="DEFECT=M",
                    help="D2 CA reporting interval per defect, e.g. roughness=500 (default 1000 / 1000 / 500)")
    a = ap.parse_args()
    iv = {"roughness": 1000, "rutting": 1000, "cracking": 500}
    for x in a.ca_interval:
        k, v = x.split("=")
        iv[k.strip().lower()] = int(v)

    P = os.path.join(a.out, "Zone=A", "Phase=1", "Cycle=1", f"UPC={UPC}")
    shutil.rmtree(P, ignore_errors=True)
    for d in ["d1", "d2", "d4"]:
        os.makedirs(os.path.join(P, d, RUN), exist_ok=True)
    os.makedirs(os.path.join(P, "d2", RUN, "meta"), exist_ok=True)

    # ── D4 defect report: L1 alternates every 10 m, L2 and R1 constant ──────────
    ch = np.arange(0, 2000, 10)
    k = (ch // 10) % 2
    lanes = {   # lane: (IRI m/km, rut mm, crack %)
        "L1": (np.where(k == 0, 2.0, 4.0), np.where(k == 0, 4.0, 8.0), np.where(k == 0, 1.0, 9.0)),
        "L2": (np.full(len(ch), 1.25), np.full(len(ch), 2.0), np.full(len(ch), 0.5)),
        "R1": (np.full(len(ch), 4.2), np.full(len(ch), 6.0), np.full(len(ch), 2.0)),
    }
    d4 = {"NH Number": ["NH-48"] * len(ch), "Chainage | Start": ch, "Chainage | End": ch + 10}
    for ln, (iri, rut, crk) in lanes.items():
        d4[f"{ln} | Start Latitude"] = 22.0 + ch / 1e5
        d4[f"{ln} | Start Longitude"] = 72.0 + ch / 1e5
        d4[f"{ln} | End Latitude"] = 22.0 + (ch + 10) / 1e5
        d4[f"{ln} | End Longitude"] = 72.0 + (ch + 10) / 1e5
        d4[f"Roughness IRI (in m/km) @ 10 mtr | {ln}"] = iri
        d4[f"Roughness BI (in mm/km) @ 10 mtr | {ln}"] = np.round(bi(iri), 2)
        d4[f"Rut Depth (in mm) @ 10 mtr | {ln}"] = rut
        d4[f"Cracking Lane Area (%) @ 10 mtr | {ln}"] = crk
        d4[f"Cracking Lane Area (sq. m.) @ 10 mtr | {ln}"] = np.round(crk * 35 / 100, 4)
        d4[f"Raveling Lane Area (%) @ 10 mtr | {ln}"] = np.full(len(ch), 0.35)
    text(pd.DataFrame(d4)).to_parquet(os.path.join(P, "d4", RUN, "d4_defect_report_data_sheet.parquet"))

    # ── D4 NHAI One potholes ─────────────────────────────────────────────────────
    pot = pd.DataFrame([
        {"UPC": UPC.replace("_", "/"), "Datetime": "2026-01-10 10:00:00", "NH No.": "NH-48", "Chainage": 250,
         "FROM_CH_KM": 0, "FROM_CH_M": 250, "SIDE": "LHS", "TYPE OF DEFECT": "Pothole", "DefectID": 1, "Lane No.": "L1",
         "Area (in Sq m)": 0.4, "Severity": "High", "Image Name": "p1.jpg", "Latitude": 22.0025, "Longitude": 72.0025},
        {"UPC": UPC.replace("_", "/"), "Datetime": "2026-01-10 10:05:00", "NH No.": "NH-48", "Chainage": 1320,
         "FROM_CH_KM": 1, "FROM_CH_M": 320, "SIDE": "RHS", "TYPE OF DEFECT": "Pothole", "DefectID": 2, "Lane No.": "R1",
         "Area (in Sq m)": 0.9, "Severity": "Low", "Image Name": "p2.jpg", "Latitude": 22.0132, "Longitude": 72.0132}])
    text(pot).to_parquet(os.path.join(P, "d4", RUN, "d4_nhai_one_potholes.parquet"))

    # ── D2 comparison sheets (reporting intervals) + thresholds in the data dictionary ──
    st = np.arange(0, 2000, 100)
    d2 = {"NH Number": ["NH-48"] * len(st), "Chainage | Start": st, "Chainage | End": st + 100,
          "Overall Reporting Interval": 100}
    for d, g in D2_GROUPS.items():
        d2[f"Current Cycle | {g} | Reporting Interval"] = iv[d]
    for std in ["ca", "irc"]:
        text(pd.DataFrame(d2)).to_parquet(os.path.join(P, "d2", RUN, f"d2_comparison_comparison_{std}.parquet"))
    dd = {f"comparison_{std}": {"sheet_name": f"Comparison as per {std.upper()}", "columns": {
        f"Current Cycle | {g} | L1": {"row_2": str(thr[d])} for d, g in D2_GROUPS.items()}}
        for std, thr in [("ca", CA_THR), ("irc", IRC_THR)]}
    with open(os.path.join(P, "d2", RUN, "meta", "d2_comparison_data_dictionary.json"), "w", encoding="utf-8") as f:
        json.dump(dd, f, indent=1)

    # ── expected results ─────────────────────────────────────────────────────────
    flag = lambda v, t: "breach" if v >= t else "no breach"
    lhs_iri, lhs_rut = (3.0 + 1.25) / 2, (6.0 + 2.0) / 2
    print(f"Test UPC written: {P}\n")
    print("Expected silver results (CA thresholds: BI 2000, rut 5 mm, crack 5 %):")
    print(f"  master_roughness_parent  LHS: IRI {lhs_iri:.3f} → BI {bi(lhs_iri):.1f} ({flag(bi(lhs_iri), 2000)}) | "
          f"RHS: IRI 4.200 → BI {bi(4.2):.1f} ({flag(bi(4.2), 2000)})   (every km)")
    print(f"  master_rutting_parent    LHS: {lhs_rut:.1f} mm ({flag(lhs_rut, 5)}) | RHS: 6.0 mm (breach)")
    print(f"  master_roughness_as_per_ca  {iv['roughness']} m rows: L1 BI {bi(3.0):.1f} (breach), "
          f"L2 {bi(1.25):.1f}, R1 {bi(4.2):.1f} (breach)")
    print(f"  master_rutting_as_per_ca    {iv['rutting']} m rows: L1 6.0 (breach), L2 2.0, R1 6.0 (breach)")
    print(f"  master_cracking_as_per_ca   {iv['cracking']} m rows: L1 5.0 % (breach), L2 0.5 %, R1 2.0 %")
    print("  10 m tables: L1 alternates (roughness 1369.3 / 2976.1, rut 4 / 8, crack 1 / 9 %); 600 rows each")
    print("  master_pothole: L1 High → breach, R1 Low → no breach")


if __name__ == "__main__":
    main()
