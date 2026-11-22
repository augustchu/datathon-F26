# Databricks notebook source
# COMMAND ----------
# Prepare the facility snapshot once, then let the Streamlit App query its output.
# Run this file as a notebook from the Databricks Git folder (Run all).

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# If you rename Marketplace objects or move the uploaded geography volume, change these values.
FACILITIES_SOURCE = "databricks_virtue_foundation_dataset.virtue_foundation_dataset.facilities"
PINS_SOURCE = "databricks_virtue_foundation_dataset.virtue_foundation_dataset.india_post_pincode_directory"
NFHS_SOURCE = "databricks_virtue_foundation_dataset.virtue_foundation_dataset.nfhs_5_district_health_indicators"
BOUNDARY_ZIP = "/Volumes/additional_india_data/default/additional_india_data/geoBoundaries-IND-ADM2-all.zip"
OUTPUT_CATALOG = "main"
OUTPUT_SCHEMA = "first_prototype"

current = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
repo_root = "/Workspace" + str(Path(current).parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
try:
    from pipeline import SERVICE_TERMS, attach_regions, prepare_facilities
except ImportError:
    # For Git folders where only the workspace path is on sys.path, add this notebook's folder.
    from pipeline import SERVICE_TERMS, attach_regions, prepare_facilities

# COMMAND ----------
# Validate access and inspect schemas before collecting the modest facility table to pandas.
for source_name in (FACILITIES_SOURCE, PINS_SOURCE, NFHS_SOURCE):
    print(f"{source_name}: {spark.table(source_name).count():,} rows")
    print(spark.table(source_name).columns)

raw_spark = spark.table(FACILITIES_SOURCE)
raw = raw_spark.toPandas()
pins_spark = spark.table(PINS_SOURCE)
nfhs_spark = spark.table(NFHS_SOURCE)
nfhs = nfhs_spark.toPandas()

# COMMAND ----------
# Apply the saved enriched bed-count model when present. A model failure is non-fatal;
# service eligibility never comes from a bed-count prediction.
prepared = prepare_facilities(raw)
model_status = "not applied"
try:
    from capacity_model import CapacityModel
    model = CapacityModel(str(Path(repo_root) / "models" / "enriched"))
    detail = model.predict_detailed(raw)
    predicted = {}
    for i, row in prepared.iterrows():
        if pd.isna(row["beds_for_scenario"]) and detail.iloc[i]["status"] == "ok" and bool(detail.iloc[i]["doctors_reported"]):
            beds = float(detail.iloc[i]["beds"])
            if 0 < beds <= 5000:
                predicted[row["facility_id"]] = beds
    prepared = prepare_facilities(raw, predicted_beds=predicted, model_label="model:enriched capacity MLP")
    model_status = f"enriched capacity MLP; filled {len(predicted):,} missing bed counts (doctor-reported and guard-eligible only)"
except Exception as exc:
    model_status = f"not applied; {type(exc).__name__}: {exc}"
    print("Capacity model unavailable; reported capacity and missing values are retained.", model_status)

# Read geoBoundaries from the supplied Unity Catalog Volume and attach conservative ADM2/NFHS joins.
import geopandas as gpd

if not Path(BOUNDARY_ZIP).exists():
    raise FileNotFoundError(f"Boundary archive not found at {BOUNDARY_ZIP}")
try:
    boundaries = gpd.read_file(BOUNDARY_ZIP)
except Exception as exc:
    print("Archive layers:", gpd.list_layers(BOUNDARY_ZIP))
    raise RuntimeError("Could not read the ADM2 archive. Choose the correct layer and set BOUNDARY_ZIP above.") from exc
prepared = attach_regions(prepared, boundaries, nfhs)

# Include source-row traceability and a stable timestamp with each preparation run.
prepared["snapshot_prepared_at"] = datetime.now(timezone.utc).isoformat()
prepared["facility_source_table"] = FACILITIES_SOURCE
prepared["capacity_model_status"] = model_status

# COMMAND ----------
# Create the prototype schema and publish the prepared output as a Delta table.
output_schema = f"{OUTPUT_CATALOG}.{OUTPUT_SCHEMA}"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {output_schema}")

# Use explicit scalar types so nullable model outputs and claim flags stay consistent in Delta.
numeric_cols = {"latitude", "longitude", "beds_for_scenario"}
boolean_cols = {c for c in prepared.columns if c.startswith("claim_citable_")}
for col in numeric_cols:
    prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
for col in boolean_cols:
    prepared[col] = prepared[col].fillna(False).astype(bool)
for col in prepared.columns:
    if col not in numeric_cols | boolean_cols:
        prepared[col] = prepared[col].where(pd.notna(prepared[col]), None).astype(object)
        prepared[col] = prepared[col].map(lambda value: None if value is None else str(value))

facility_sdf = spark.createDataFrame(prepared)
facility_sdf.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{output_schema}.facilities")

# Keep the supplemental source snapshots available for downstream iteration and auditing.
pins_spark.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{output_schema}.pin_directory")
nfhs_spark.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{output_schema}.nfhs_district_indicators")

metadata = spark.createDataFrame([{
    "prepared_at": datetime.now(timezone.utc).isoformat(),
    "facility_source_table": FACILITIES_SOURCE,
    "pin_source_table": PINS_SOURCE,
    "nfhs_source_table": NFHS_SOURCE,
    "boundary_archive": BOUNDARY_ZIP,
    "facility_rows": int(len(prepared)),
    "citable_service_claims": int(sum(prepared[f"claim_citable_{key}"].sum() for key in [
        "dialysis", "emergency_c_section", "nicu", "cardiac_surgery", "mri", "burn_care",
        "trauma_emergency", "maternity_delivery", "general_care"])),
    "capacity_model_status": model_status,
}])
metadata.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{output_schema}.snapshot_metadata")

display(prepared[["facility_name", "city", "state", "adm2_district", "nfhs_district", "region_match_status", "beds_for_scenario", "beds_provenance"]].head(20))
display(spark.table(f"{output_schema}.snapshot_metadata"))
print(f"Published {len(prepared):,} facility rows to {output_schema}.facilities")
