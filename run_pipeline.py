from src.pipeline import run_pipeline

if __name__ == "__main__":
    summary = run_pipeline()
    print("\nPipeline completed successfully.")
    for key, value in summary.items():
        print(f"{key}: {value}")
    print("\nOutputs: data/processed/logistics_analytics.db, data/processed/shipments_clean.csv, reports/")
