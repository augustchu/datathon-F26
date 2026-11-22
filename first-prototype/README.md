# First prototype: Databricks care finder

This folder is a self-contained Databricks prototype. It reads the Marketplace facility, PIN, and NFHS tables in a preparation notebook, reads the supplied geoBoundaries ZIP from a Unity Catalog Volume, writes prepared Delta tables, and serves an interactive Streamlit app from those tables.

The Streamlit app has service, location, urgency, result-count, and a 0–2% district-population scenario slider. It returns citable text matches and a transparent score. For each district, expected cases at the selected share are split evenly across the evidence-backed facilities in that district that mention the service. This is an illustrative allocation, not measured congestion or a demand forecast. All displayed facility fields and service evidence come from the cleaned newest-source release; the Marketplace table is used to validate alignment and cross-check capacity. The app displays confidence, phone, and website when available, and adds a confidence adjustment to every ranking. Emergency ranking skips regional demand load but still accounts for confidence.

All prototype assumptions and implementation choices are recorded in [DECISIONS.md](DECISIONS.md).

## 1. Sync this folder to Databricks

The full repository should already be connected as a Databricks Git folder. Confirm both `first-prototype/` and `max-supplemented-data/capacity_and_confidence/` appear in that folder. Open `prepare_snapshot.py` as a notebook in Databricks. The Streamlit app needs `dashboard.py`, `pipeline.py`, `app.yaml`, and `requirements.txt`.

## 2. Confirm Marketplace and Volume access

The user running preparation needs `SELECT` on the three source tables and read access to:

`/Volumes/additional_india_data/default/additional_india_data/geoBoundaries-IND-ADM2-all.zip`

The population scenario also needs `nfhs_district_population_2020.csv` uploaded to the same Volume directory. The configured path is `/Volumes/additional_india_data/default/additional_india_data/nfhs_district_population_2020.csv`. If uploaded under another filename, change `POPULATION_CSV` at the top of `prepare_snapshot.py`. It must include district/state names and `pop_2020`; unmatched or duplicate regions are left without a population-based penalty.

The capacity inputs are already tracked in this repository at `max-supplemented-data/capacity_and_confidence/facilities_newest_capacity.csv` and `facility_confidence.csv`. Databricks receives them when the full repository Git folder syncs, so you do not need to download or upload those two files separately. The notebook checks both files' row counts and the capacity file's 0-based `row_index`, pairs confidence by the documented CSV order, then matches release rows to the Marketplace table by `unique_id`. It compares the selected capacity with the current Marketplace capacity, checks the release's `original_capacity` against the Marketplace field for source alignment, reports both results, and uses the selected release value for load calculations. If the release marks a count `xgboost_estimate`, the app labels it **“This is an estimated capacity.”**

The notebook configuration uses the corrected Marketplace catalog (`databricks_virtue_foundation_dataset_dais_2026`) and writes to the existing `additional_india_data.default` schema, which is the catalog/schema shown by your Volumes browser. It does not create a schema. Change `OUTPUT_CATALOG` / `OUTPUT_SCHEMA` at the top if your team has another writable target.

## 3. Prepare the snapshot

Run all cells in `prepare_snapshot.py`. The notebook:

1. Checks source table access and displays schemas/row counts.
2. Collects the facility table into pandas for the prototype's row-wise text processing.
3. Reads the tracked capacity and confidence release files, validates their row counts/order, and matches release rows to the Marketplace source by stable ID. The release becomes the source for displayed name, location, coordinates, service text, URLs, phone, website, and capacity; Marketplace capacity is kept only for cross-checking. The snapshot also carries release status, source fields, estimate intervals, and confidence score.
4. Uses release capacity values for all capacity calculations. A release estimate stays labeled as estimated; an explicitly withheld/missing release value is not replaced with a potentially unsafe older field or the prototype MLP.
5. Extracts structured service text, source URLs, and coordinates.
6. Reads the full GeoJSON member from the ADM2 archive in the Volume using the Python standard library, maps facility points to polygons, and joins district/state names to NFHS only on normalized exact names; unresolved matches remain labeled. This avoids requiring GeoPandas in the serverless notebook runtime.
7. Joins modeled 2020 district populations from the uploaded population CSV using exact normalized district/state keys, leaving ambiguous and missing matches unavailable.
8. Writes `first_prototype_facilities`, `first_prototype_pin_directory`, `first_prototype_nfhs_district_indicators`, and `first_prototype_snapshot_metadata` as Delta tables under `additional_india_data.default`.

Inspect the printed samples, region-match counts, and metadata. If the archive does not contain a `.geojson` member, the notebook reports its member names; update `read_geojson_archive` to select the correct member if the filename differs.

If a helper import reports that `pipeline.py` is missing `read_geojson_archive`, pull/sync the entire `first-prototype` folder in the Databricks Git folder, restart the notebook's Python process, and rerun. Databricks notebook kernels can keep an older imported module cached after files change; the notebook now reloads the module when it starts.

## 4. Create and configure the Databricks App

Create a Databricks App with this folder as its source and choose the Streamlit framework. The launch command is in `app.yaml`. The App runtime needs the packages in `requirements.txt`.

Add these resources in the Databricks Apps UI using the resource keys referenced by `app.yaml`:

- SQL warehouse key `sql-warehouse`, permission **Can use**.
- Unity Catalog table key `facilities-table`, table `additional_india_data.default.first_prototype_facilities`, permission **Can select**.
- Unity Catalog table key `snapshot-table`, table `additional_india_data.default.first_prototype_snapshot_metadata`, permission **Can select**.
- Secret key `traveltime-app-id`, secret scope/key `traveltime-carefinder` / `TRAVELTIME_APP_ID`, permission **Can read**.
- Secret key `traveltime-api-key`, secret scope/key `traveltime-carefinder` / `TRAVELTIME_API_KEY`, permission **Can read**.

The secret **resource keys** must match `traveltime-app-id` and `traveltime-api-key` in `app.yaml`; the underlying scope and secret names are the ones shown above. The manifest maps these resources to `TRAVELTIME_APP_ID` and `TRAVELTIME_API_KEY` environment variables. The app makes one TravelTime Fast one-to-many driving request per origin/service candidate set and caches the result for 30 minutes. It uses a typical weekday-morning arrival period and searches up to three hours. If credentials, network access, or the provider call fails (or returns no reachable candidates), it visibly falls back to the existing straight-line kilometer ranking. If only some destinations are unreachable, those are omitted from the TravelTime-ranked results and are not silently assigned a straight-line route.

TravelTime ranking uses returned route minutes as its base cost. The existing load and confidence adjustments were authored in km-equivalent units, so TravelTime mode multiplies them by 2 (the stated demo conversion of 1 minute â‰ˆ 0.5 km). The route time and converted penalties therefore add in minutes. Straight-line fallback retains the original kilometer formula. Neither route estimates nor heuristic penalties represent live traffic, staffed availability, or clinical suitability.

The app manifest receives the warehouse ID and constructs the SQL HTTP path. The app identity also needs `USE CATALOG` and `USE SCHEMA` privileges as required by your workspace's Unity Catalog setup. Change the table resource targets if you chose a different output catalog/schema.

Set these table variables in `app.yaml` if the curated tables are in a different location:

```text
FACILITY_SNAPSHOT_TABLE=additional_india_data.default.first_prototype_facilities
SNAPSHOT_METADATA_TABLE=additional_india_data.default.first_prototype_snapshot_metadata
```

The app queries facilities and metadata; the PIN/NFHS copies are retained for next iterations. Deploy. Free Edition Apps require authenticated workspace/account access and stop after 24 hours, so restart the app when needed.

## 5. Use the app

Choose a care need and urgency. Set the share of each district's modeled population assumed to need that care (0–2%). Enter a city, district, or PIN for an approximate centroid based on facility points, or switch on coordinate entry and provide a point. Click **Find facility options** once. After results appear, changing the controls recalculates the ranking immediately.

Each card includes the service term and excerpt, source links, distance, score components, district demand allocation, release capacity/provenance, confidence score, phone, and website when available. Confidence adds +0 km for High (50–100), +40 km for Medium (25–49), and +80 km for Low (0–24) or unknown. These are explicit km-equivalent ranking rules: with comparable load adjustments a 50 km High option outranks a 25 km Medium option, which outranks a 5 km Low option. Confidence is a fixed-denominator data-quality score, not a probability. For routine and urgent searches, load adds a capped 0–12 km-equivalent adjustment. Missing regional population produces no load penalty and is disclosed. The app abstains if no record passes the evidence-link and coordinate checks. When TravelTime is active, it displays estimated driving minutes; otherwise it displays straight-line distance. A facility must still be contacted to verify its branch, service, and current availability.

## Current scope and next iteration

The prototype uses a small, name-hint mapping from selected services to NFHS indicators to scale the load penalty. Those indicators are relative survey proxies, not disease incidence. The population share and equal allocation are scenario assumptions; hospitals' true catchments, market share, service-specific capacity, current staffing, and occupancy are unknown. Review the NFHS mapping and population file provenance before presenting it as more than a demonstration. Independent-source corroboration, validity dates, better service entailment/negation handling, PIN-directory geocoding quality, measured service-specific load, and clinical review remain open work.
