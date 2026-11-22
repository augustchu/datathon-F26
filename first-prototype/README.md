# First prototype: Databricks care finder

This folder is a self-contained Databricks prototype. It reads the Marketplace facility, PIN, and NFHS tables in a preparation notebook, reads the supplied geoBoundaries ZIP from a Unity Catalog Volume, writes prepared Delta tables, and serves an interactive Streamlit app from those tables.

The Streamlit app has service, location, urgency, result-count, and concurrent-case intensity controls. It returns citable text matches and a transparent score. Intensity routes hypothetical routine cases sequentially before ranking the user's case; this is not measured congestion or a demand forecast. Emergency results rank by distance and ignore the simulated cases. The capacity MLP estimates total beds only; it cannot determine service eligibility, open beds, or actual availability.

All prototype assumptions and implementation choices are recorded in [DECISIONS.md](DECISIONS.md).

## 1. Sync this folder to Databricks

The full repository should already be connected as a Databricks Git folder. Confirm `first-prototype/` appears in that folder. Open `prepare_snapshot.py` as a notebook in Databricks. The app deployment source must include `dashboard.py`, `pipeline.py`, `app.yaml`, `requirements.txt`, `capacity_model.py`, and `models/enriched/`.

## 2. Confirm Marketplace and Volume access

The user running preparation needs `SELECT` on the three source tables and read access to:

`/Volumes/additional_india_data/default/additional_india_data/geoBoundaries-IND-ADM2-all.zip`

The notebook configuration at its top contains the corrected Marketplace catalog (`databricks_virtue_foundation_dataset_dais_2026`), source table names, and output catalog/schema. Change them there if the PIN/NFHS object names differ in your workspace or `main.first_prototype` is not writable.

## 3. Prepare the snapshot

Run all cells in `prepare_snapshot.py`. The notebook:

1. Checks source table access and displays schemas/row counts.
2. Collects the facility table into pandas for the prototype's row-wise text processing.
3. Extracts structured service text, URLs, coordinates, and reported bed counts.
4. Attempts inference with the bundled enriched bed-count MLP for missing capacities that pass the existing model guard and doctor-count policy. A model loading error is reported and does not block preparation.
5. Reads the full GeoJSON member from the ADM2 archive in the Volume using the Python standard library, maps facility points to polygons, and joins district/state names to NFHS only on normalized exact names; unresolved matches remain labeled. This avoids requiring GeoPandas in the serverless notebook runtime.
6. Writes `main.first_prototype.facilities`, `main.first_prototype.pin_directory`, `main.first_prototype.nfhs_district_indicators`, and `main.first_prototype.snapshot_metadata` as Delta tables.

Inspect the printed samples, region-match counts, and metadata. If the archive does not contain a `.geojson` member, the notebook reports its member names; update `read_geojson_archive` to select the correct member if the filename differs.

If a helper import reports that `pipeline.py` is missing `read_geojson_archive`, pull/sync the entire `first-prototype` folder in the Databricks Git folder, restart the notebook's Python process, and rerun. Databricks notebook kernels can keep an older imported module cached after files change; the notebook now reloads the module when it starts.

## 4. Create and configure the Databricks App

Create a Databricks App with this folder as its source and choose the Streamlit framework. The launch command is in `app.yaml`. The App runtime needs the packages in `requirements.txt`.

Add these resources in the Databricks Apps UI using the resource keys referenced by `app.yaml`:

- SQL warehouse key `sql-warehouse`, permission **Can use**.
- Unity Catalog table key `facilities-table`, table `main.first_prototype.facilities`, permission **Can select**.
- Unity Catalog table key `snapshot-table`, table `main.first_prototype.snapshot_metadata`, permission **Can select**.

The app manifest receives the warehouse ID and constructs the SQL HTTP path. The app identity also needs `USE CATALOG` and `USE SCHEMA` privileges as required by your workspace's Unity Catalog setup. Change the table resource targets if you chose a different output catalog/schema.

Set these table variables in `app.yaml` if the curated tables are in a different location:

```text
FACILITY_SNAPSHOT_TABLE=main.first_prototype.facilities
SNAPSHOT_METADATA_TABLE=main.first_prototype.snapshot_metadata
```

The app queries facilities and metadata; the PIN/NFHS copies are retained for next iterations. Deploy. Free Edition Apps require authenticated workspace/account access and stop after 24 hours, so restart the app when needed.

## 5. Use the app

Choose a care need and urgency. Enter a city, district, or PIN for an approximate centroid based on facility points, or switch on coordinate entry and provide a point. Click **Find facility options** once. After results appear, changes to the controls (including concurrent-case intensity from 0–300) recalculate the ranking immediately.

Each card includes the service term, source excerpt, source URL, distance, score components, and whether beds were reported or estimated. The app abstains if no record passes the evidence-link and coordinate checks. It does not estimate road travel times; distance is straight-line. A facility must still be contacted to verify its branch, service, and current availability.

## Current scope and next iteration

The prototype uses a small, name-hint mapping from selected services to NFHS indicators to scale regional simulated load pressure. Those indicators are relative survey proxies, not disease incidence. The current synthetic cases share the user's resolved origin rather than sampling a population-weighted case geography. Review the mapping against the real NFHS schema before presenting it as more than a demonstration. Independent-source corroboration, validity dates, better service entailment/negation handling, PIN-directory geocoding, actual TravelTime connectivity, measured service-specific load, and clinical review remain open work.
