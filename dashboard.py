from pathlib import Path
import sqlite3
import pandas as pd
import plotly.express as px
import streamlit as st
from src.pipeline import run_pipeline, DB_FILE, RAW_FILE, PROCESSED

st.set_page_config(page_title="LogiFlow | Trade & Logistics Analytics", page_icon="🚚", layout="wide")
st.title("LogiFlow | Logistics & Trade Operations Analytics")
st.caption("Portfolio demo using synthetic shipment data. Not affiliated with Volvo Group and not based on Volvo data.")

if st.sidebar.button("Run / refresh ETL pipeline", type="primary"):
    with st.spinner("Validating, transforming and loading data..."):
        summary = run_pipeline()
    st.sidebar.success(f"Pipeline complete: {summary['shipments_processed']:,} shipments loaded.")

st.sidebar.header("Data controls")
uploaded = st.sidebar.file_uploader("Optional: upload your own shipment CSV", type=["csv"])
if uploaded is not None:
    try:
        df_upload = pd.read_csv(uploaded)
        df_upload.to_csv(RAW_FILE, index=False)
        run_pipeline()
        st.sidebar.success("CSV loaded and pipeline rerun.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Could not process CSV: {e}")
        st.sidebar.caption("Expected columns are documented in README.md.")

if not DB_FILE.exists():
    with st.spinner("First run: preparing demo data and pipeline..."):
        run_pipeline()

with sqlite3.connect(DB_FILE) as conn:
    df = pd.read_sql_query("SELECT * FROM shipments", conn, parse_dates=["shipment_date"])
    quality = pd.read_sql_query("SELECT * FROM pipeline_quality", conn)

if df.empty:
    st.warning("No shipment rows found. Check your source CSV and rerun the pipeline.")
    st.stop()

st.sidebar.markdown("---")
st.sidebar.caption("Filters apply to all charts and the exception table.")
countries = sorted(set(df["origin_country"].dropna()) | set(df["destination_country"].dropna()))
selected_countries = st.sidebar.multiselect("Countries (origin or destination)", countries, default=countries)
modes = sorted(df["transport_mode"].dropna().unique().tolist())
selected_modes = st.sidebar.multiselect("Transport mode", modes, default=modes)
date_min = pd.to_datetime(df["shipment_date"]).min().date()
date_max = pd.to_datetime(df["shipment_date"]).max().date()
date_range = st.sidebar.date_input("Shipment date range", value=(date_min, date_max), min_value=date_min, max_value=date_max)

filtered = df[
    df["origin_country"].isin(selected_countries) | df["destination_country"].isin(selected_countries)
]
filtered = filtered[filtered["transport_mode"].isin(selected_modes)]
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    filtered = filtered[
        (pd.to_datetime(filtered["shipment_date"]).dt.date >= date_range[0]) &
        (pd.to_datetime(filtered["shipment_date"]).dt.date <= date_range[1])
    ]

if filtered.empty:
    st.info("No rows match the selected filters.")
    st.stop()

total = len(filtered)
on_time = (~filtered["is_delayed"].astype(bool)).mean() * 100
delayed = int(filtered["is_delayed"].astype(bool).sum())
customs = int(filtered["customs_status"].isin(["Documentation Hold", "Pending Review", "Inspection"]).sum())
duty = filtered["estimated_duty_usd"].sum()
avg_transit = filtered["actual_transit_days"].mean()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Shipments", f"{total:,}")
c2.metric("On-time rate", f"{on_time:.1f}%")
c3.metric("Delayed shipments", f"{delayed:,}")
c4.metric("Customs exceptions", f"{customs:,}")
c5.metric("Estimated duties", f"${duty:,.0f}")

st.markdown("### Operational performance")
left, right = st.columns(2)
with left:
    trend = filtered.assign(shipment_date=pd.to_datetime(filtered["shipment_date"]).dt.to_period("W").dt.start_time)
    trend = trend.groupby("shipment_date", as_index=False).agg(
        shipments=("shipment_id", "count"), on_time_rate=("is_delayed", lambda s: (~s.astype(bool)).mean()*100)
    )
    fig = px.line(trend, x="shipment_date", y="on_time_rate", markers=True,
                  title="Weekly on-time shipment rate (%)")
    fig.update_layout(yaxis_range=[0, 100], xaxis_title="Week", yaxis_title="On-time rate (%)")
    st.plotly_chart(fig, use_container_width=True)
with right:
    by_mode = filtered.groupby("transport_mode", as_index=False).agg(
        shipments=("shipment_id", "count"), avg_delay=("delay_days", "mean")
    )
    fig = px.bar(by_mode, x="transport_mode", y="shipments", color="avg_delay",
                 title="Shipment volume by transport mode", labels={"avg_delay":"Avg delay (days)"})
    st.plotly_chart(fig, use_container_width=True)

left, right = st.columns(2)
with left:
    exc = filtered["exception_type"].value_counts().rename_axis("exception_type").reset_index(name="count")
    fig = px.bar(exc, x="exception_type", y="count", title="Exception categories",
                 color="exception_type")
    fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="Shipments")
    st.plotly_chart(fig, use_container_width=True)
with right:
    route = filtered.groupby(["origin_country", "destination_country"], as_index=False).agg(
        shipments=("shipment_id", "count"), avg_delay_days=("delay_days", "mean")
    ).sort_values("shipments", ascending=False).head(12)
    route["route"] = route["origin_country"] + " → " + route["destination_country"]
    fig = px.bar(route.sort_values("shipments"), x="shipments", y="route", orientation="h",
                 color="avg_delay_days", title="Top routes by shipment volume")
    st.plotly_chart(fig, use_container_width=True)

st.markdown("### Data quality & pipeline audit")
q1, q2, q3, q4 = st.columns(4)
q1.metric("Raw rows", f"{int(quality.iloc[0]['raw_rows']):,}")
q2.metric("Clean rows", f"{int(quality.iloc[0]['clean_rows']):,}")
q3.metric("Rows removed", f"{int(quality.iloc[0]['removed_rows']):,}")
q4.metric("Duplicate IDs detected", f"{int(quality.iloc[0]['duplicate_ids_removed']):,}")
st.caption("Pipeline applies ID de-duplication, positive declared-value checks, required-field checks, and median imputation for missing weight/transit values.")

st.markdown("### Shipment exception worklist")
exceptions = filtered[filtered["exception_type"] != "On Track"].copy()
show_cols = ["shipment_id", "shipment_date", "origin_country", "destination_country",
             "transport_mode", "customs_status", "documents_complete", "planned_transit_days",
             "actual_transit_days", "delay_days", "exception_type", "declared_value_usd", "estimated_duty_usd"]
if not exceptions.empty:
    st.dataframe(exceptions[show_cols].sort_values(["exception_type", "delay_days"], ascending=[True, False]),
                 use_container_width=True, hide_index=True)
    st.download_button("Download filtered exception worklist (CSV)",
                       data=exceptions[show_cols].to_csv(index=False).encode("utf-8"),
                       file_name="shipment_exceptions_filtered.csv", mime="text/csv")
else:
    st.success("No exceptions for the current filters.")

with st.expander("Preview filtered shipment records"):
    st.dataframe(filtered, use_container_width=True, hide_index=True)
    st.download_button("Download filtered shipments (CSV)", data=filtered.to_csv(index=False).encode("utf-8"),
                       file_name="shipments_filtered.csv", mime="text/csv")

st.markdown("---")
st.caption("Demo note: all initial records are synthetic. Duty is an illustrative estimate (declared value × configured duty rate), not a customs/tax calculation.")
