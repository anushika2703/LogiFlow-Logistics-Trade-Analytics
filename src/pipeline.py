from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
RAW_FILE = RAW / "shipments_raw.csv"
DB_FILE = PROCESSED / "logistics_analytics.db"

COLUMNS = [
    "shipment_id", "shipment_date", "origin_country", "destination_country",
    "transport_mode", "product_category", "customs_status", "declared_value_usd",
    "weight_kg", "planned_transit_days", "actual_transit_days", "documents_complete",
    "duty_rate", "supplier_id"
]

def generate_synthetic_data(n=1200, seed=42):
    """Create clearly labelled synthetic shipment data for a portfolio demo."""
    rng = np.random.default_rng(seed)
    countries = np.array(["India", "Germany", "Sweden", "United States", "China", "Poland", "Mexico"])
    modes = np.array(["Road", "Air", "Ocean", "Rail"])
    categories = np.array(["Automotive Parts", "Machinery", "Electronics", "Chemicals", "Packaging"])
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=180, freq="D")
    origin = rng.choice(countries, n)
    dest = rng.choice(countries, n)
    same = origin == dest
    dest[same] = np.where(origin[same] == "India", "Germany", "India")
    planned = rng.integers(2, 22, n)
    delay = rng.choice([-3, -2, -1, 0, 1, 2, 3, 5, 8, 12], n,
                       p=[.03,.05,.12,.30,.16,.12,.10,.07,.035,.015])
    actual = np.maximum(1, planned + delay)
    doc = rng.choice([True, False], n, p=[.91, .09])
    customs = []
    for complete, d in zip(doc, delay):
        if not complete:
            customs.append(rng.choice(["Documentation Hold", "Pending Review"], p=[.7,.3]))
        elif d >= 5:
            customs.append(rng.choice(["Pending Review", "Inspection", "Cleared"], p=[.35,.25,.40]))
        else:
            customs.append(rng.choice(["Cleared", "Pending Review"], p=[.88,.12]))
    df = pd.DataFrame({
        "shipment_id": [f"SHP-{i:05d}" for i in range(1, n+1)],
        "shipment_date": rng.choice(dates, n),
        "origin_country": origin,
        "destination_country": dest,
        "transport_mode": rng.choice(modes, n, p=[.45,.18,.27,.10]),
        "product_category": rng.choice(categories, n),
        "customs_status": customs,
        "declared_value_usd": np.round(np.exp(rng.normal(8.7, .85, n)), 2),
        "weight_kg": np.round(np.exp(rng.normal(5.4, 1.0, n)), 2),
        "planned_transit_days": planned,
        "actual_transit_days": actual,
        "documents_complete": doc,
        "duty_rate": np.round(rng.choice([0.0,.025,.05,.075,.10,.125], n), 3),
        "supplier_id": [f"SUP-{x:03d}" for x in rng.integers(1, 76, n)]
    })
    # Add a few realistic quality issues for the pipeline to detect and resolve.
    if n >= 30:
        df.loc[4, "declared_value_usd"] = -250
        df.loc[11, "weight_kg"] = np.nan
        df.loc[19, "shipment_id"] = df.loc[18, "shipment_id"]  # duplicate ID
        df.loc[24, "planned_transit_days"] = np.nan
    return df[COLUMNS]

def validate_and_transform(df):
    raw_rows = len(df)
    work = df.copy()
    work["shipment_date"] = pd.to_datetime(work["shipment_date"], errors="coerce")
    for col in ["declared_value_usd", "weight_kg", "planned_transit_days",
                "actual_transit_days", "duty_rate"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work["documents_complete"] = work["documents_complete"].astype(str).str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    # Data-quality rules: valid IDs, positive value/weight, non-null key fields,
    # and one row per shipment ID. Invalid numeric values are excluded; missing
    # weight is imputed with category median and missing plan with overall median.
    work = work.dropna(subset=["shipment_id", "shipment_date", "origin_country",
                               "destination_country", "transport_mode", "customs_status"])
    work = work[work["declared_value_usd"] > 0].copy()
    work = work.drop_duplicates(subset=["shipment_id"], keep="first")
    work["weight_kg"] = work.groupby("product_category")["weight_kg"].transform(
        lambda s: s.fillna(s.median())
    )
    work["weight_kg"] = work["weight_kg"].fillna(work["weight_kg"].median())
    work["planned_transit_days"] = work["planned_transit_days"].fillna(
        work["planned_transit_days"].median()
    )
    work["actual_transit_days"] = work["actual_transit_days"].fillna(
        work["actual_transit_days"].median()
    )
    work["documents_complete"] = work["documents_complete"].fillna(False).astype(bool)
    work["delay_days"] = (work["actual_transit_days"] - work["planned_transit_days"]).round(1)
    work["is_delayed"] = work["delay_days"] > 0
    work["is_severely_delayed"] = work["delay_days"] >= 5
    work["estimated_duty_usd"] = (work["declared_value_usd"] * work["duty_rate"]).round(2)
    work["exception_type"] = np.select(
        [
            ~work["documents_complete"],
            work["customs_status"].isin(["Documentation Hold", "Pending Review", "Inspection"]),
            work["delay_days"] >= 5,
            work["delay_days"] > 0,
        ],
        ["Missing Documents", "Customs Review", "Severe Delay", "Transit Delay"],
        default="On Track"
    )
    work = work.sort_values("shipment_date").reset_index(drop=True)
    quality = {
        "raw_rows": int(raw_rows),
        "clean_rows": int(len(work)),
        "removed_rows": int(raw_rows - len(work)),
        "duplicate_ids_removed": int(raw_rows - df["shipment_id"].nunique()),
        "missing_weights_imputed": int(df["weight_kg"].isna().sum()),
        "invalid_values_removed": int((pd.to_numeric(df["declared_value_usd"], errors="coerce") <= 0).sum()),
    }
    return work, quality

def run_pipeline():
    for p in [RAW, PROCESSED, REPORTS]:
        p.mkdir(parents=True, exist_ok=True)
    if RAW_FILE.exists():
        raw = pd.read_csv(RAW_FILE)
        # Re-use user's raw input when supplied, but create demo data on first run.
        source = "existing data/raw/shipments_raw.csv"
    else:
        raw = generate_synthetic_data()
        raw.to_csv(RAW_FILE, index=False)
        source = "generated synthetic demo data"
    clean, quality = validate_and_transform(raw)
    clean.to_csv(PROCESSED / "shipments_clean.csv", index=False)
    with sqlite3.connect(DB_FILE) as conn:
        clean.to_sql("shipments", conn, if_exists="replace", index=False)
        pd.DataFrame([quality]).to_sql("pipeline_quality", conn, if_exists="replace", index=False)
    kpis = {
        "shipments_processed": int(len(clean)),
        "on_time_rate_pct": round(float((~clean["is_delayed"]).mean() * 100), 2),
        "delayed_shipments": int(clean["is_delayed"].sum()),
        "customs_exceptions": int(clean["customs_status"].isin(
            ["Documentation Hold", "Pending Review", "Inspection"]).sum()),
        "estimated_duty_usd": round(float(clean["estimated_duty_usd"].sum()), 2),
        "avg_transit_days": round(float(clean["actual_transit_days"].mean()), 2),
        **quality
    }
    pd.DataFrame([kpis]).to_csv(REPORTS / "pipeline_summary.csv", index=False)
    exceptions = clean[clean["exception_type"] != "On Track"].copy()
    exceptions.to_csv(REPORTS / "shipment_exceptions.csv", index=False)
    return {"source": source, **kpis}
