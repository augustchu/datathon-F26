# First prototype: Databricks care finder

This folder is a self-contained Databricks prototype. It reads the Marketplace facility, PIN, and NFHS tables in a preparation notebook, reads the supplied geoBoundaries ZIP from a Unity Catalog Volume, writes prepared Delta tables, and serves an interactive Streamlit app from those tables.

The Streamlit app returns facility options with evidence for the requested service and a usable location. It matches reviewed claims from `service_confidence_evidence.json` by release branch ID, PIN, and service. The branch record can also supply explicit listings and passing mentions. For each district, the population scenario divides expected cases evenly across eligible facilities. The Marketplace table supplies the alignment and capacity cross-checks.

The scoring rules, ranking weights, fallback behavior, and replay history are recorded in [DECISIONS.md](DECISIONS.md). The [November 22 replay outputs](replays/2026-11-22/) include the per-service score changes, rank changes, and input receipts.

## 1. Sync this folder to Databricks

The full repository should already be connected as a Databricks Git folder. Confirm both `first-prototype/` and `max-supplemented-data/capacity_and_confidence/` appear in that folder. Open `prepare_snapshot.py` as a notebook in Databricks. Deploy the `first-prototype/` folder as the Streamlit app source.

## 2. Confirm Marketplace and Volume access

The user running preparation needs `SELECT` on the three source tables and read access to:

`/Volumes/additional_india_data/default/additional_india_data/geoBoundaries-IND-ADM2-all.zip`

The population scenario uses the tracked `load-balancing-pipeline/outputs/nfhs_district_population_2020.csv` file from the synced repository; it does not need a separate Volume upload. If you prefer to read a Volume copy or use another filename, set the `POPULATION_CSV` environment variable in the notebook before running. It must include district/state names and `pop_2020`; unmatched or duplicate regions are left without a population-based penalty.

The capacity inputs are already tracked in this repository at `max-supplemented-data/capacity_and_confidence/facilities_newest_capacity.csv` and `facility_confidence.csv`. Databricks receives them when the full repository Git folder syncs, so you do not need to download or upload those two files separately. The notebook checks both files' row counts and the capacity file's 0-based `row_index`, pairs confidence by the documented CSV order, then matches release rows to the Marketplace table by `unique_id`. It compares the selected capacity with the current Marketplace capacity, checks the release's `original_capacity` against the Marketplace field for source alignment, reports both results, and uses the selected release value for load calculations. If the release marks a count `xgboost_estimate`, the app labels it **Estimated bed capacity**.

The notebook configuration uses the corrected Marketplace catalog (`databricks_virtue_foundation_dataset_dais_2026`) and writes to the existing `additional_india_data.default` schema, which is the catalog/schema shown by your Volumes browser. It does not create a schema. Change `OUTPUT_CATALOG` / `OUTPUT_SCHEMA` at the top if your team has another writable target.

## 3. Prepare the snapshot

Run all cells in `prepare_snapshot.py`. The notebook:

1. Checks source table access and displays schemas/row counts.
2. Collects the facility table into pandas for the prototype's row-wise text processing.
3. Reads the tracked capacity and confidence release files, validates their row counts/order, and matches release rows to the Marketplace source by stable ID. The release becomes the source for displayed name, location, coordinates, service text, URLs, phone, website, and capacity; Marketplace capacity is kept only for cross-checking. The snapshot also carries release status, source fields, estimate intervals, and confidence score.
4. Uses release capacity values for all capacity calculations. A release estimate stays labeled as estimated; an explicitly withheld/missing release value is not replaced with a potentially unsafe older field or the prototype MLP.
5. Attaches active reviewed service evidence for the exact branch, distinguishes explicit listings from passing mentions in the facility record, and applies release routing flags and the [November 21 location audit](../non-ml/LOCATION_AUDIT.md). `location_routing_holds.json` records rejected branch and coordinate pairs from that audit. The saved hold applies to the rejected point, so an updated point can be checked separately.
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

The secret **resource keys** must match `traveltime-app-id` and `traveltime-api-key` in `app.yaml`. The manifest maps these resources to `TRAVELTIME_APP_ID` and `TRAVELTIME_API_KEY`. The app makes one TravelTime Fast request per origin and service, caches it for 30 minutes, and searches up to three hours using a typical weekday-morning arrival period. A partial response ranks the reachable facilities. A successful response with no routes shows a banner above the map and lists the closest eligible facilities by straight-line distance. Credential, network, or provider errors use the regular straight-line ranking.

Ranking starts with route minutes in TravelTime mode or straight-line kilometers in distance mode. The service match adjustment scales with the gap from 100 and is capped at 10 minute-equivalents. The working baseline for evidence confidence uses a logistic curve with a 60-minute ceiling, midpoint 55, and width 3.5. The penalty rises sharply in the 50s, giving explicit listings with confidence in the 60s more room to compete on travel. Distance mode uses half the minute-equivalent adjustment in kilometers. Routine and urgent searches also include the regional demand adjustment. The broader weighting decision remains open. [Confidence-weight tradeoff](DECISIONS.md#confidence-weight-tradeoff)

The app manifest receives the warehouse ID and constructs the SQL HTTP path. The app identity also needs `USE CATALOG` and `USE SCHEMA` privileges as required by your workspace's Unity Catalog setup. Change the table resource targets if you chose a different output catalog/schema.

Set these table variables in `app.yaml` if the curated tables are in a different location:

```text
FACILITY_SNAPSHOT_TABLE=additional_india_data.default.first_prototype_facilities
SNAPSHOT_METADATA_TABLE=additional_india_data.default.first_prototype_snapshot_metadata
```

The app queries facilities and metadata; the PIN/NFHS copies are retained for next iterations. Deploy. Free Edition Apps require authenticated workspace/account access and stop after 24 hours, so restart the app when needed.

## 5. Use the app

Choose a care need and urgency. Set the share of each district's modeled population assumed to need that care (0–2%). Enter a city, district, or PIN for an approximate centroid based on facility points, or switch on coordinate entry and provide a point. Click **Find facility options** to show the map and results. Open **Search options** above the results to update the ranking, or use **Back to search** to return to the full controls with your selections intact.

Each eligible result shows its excerpt, evidence label, and two scores. **Service match** is 100 for an explicit listing of the requested service or 40 for a passing mention. **Evidence confidence** adds up to 20 branch points to service evidence points: 80 for official evidence with independent corroboration, 75 for an official listing, 70 for a reviewed directory listing, 50 for an explicit facility-record listing, or 15 for a passing mention. An explicit record listing can be a service entry in a specialty, capability, or procedure field, or direct wording that the branch offers the service. Related procedures and broader aliases can earn passing-mention credit. The text screen skips obvious negations and referrals elsewhere. Invalid coordinates and audited holds are excluded from routing and city/PIN centroids. Rebuild the snapshot to apply the updated text classification.

Use **More info** beside a calculated value to see what it means, how it was calculated, why the prototype uses that method, and the repository sources. The **Ranking score** is a unitless number ordered lowest first. The map starts with an India overview and places a red marker at the resolved search origin. Numbered facility pins follow the result cards in order. Select a pin for the travel estimate, listed phone, bed capacity, and directions. Confirm service and current availability with the facility before travel.

### Replay the confidence comparison

From the repository root, use the frozen Marketplace raw facility checkpoint. The replay uses the same release files for both scores and reports candidate counts, means, medians, and High counts for each service. It also reports values for the candidates that both versions returned.

```sh
python first-prototype/replay_service_confidence.py \
  --raw-facilities /path/to/raw_facilities.jsonl \
  --as-of 2026-11-22 \
  --output /path/to/confidence_by_service.csv
```

### Replay ranking changes

Save the earlier prototype's `pipeline.py` before changing its ranking. Run both rankers on the same frozen inputs and search origins. The comparison fixes the regional demand share at 0% to isolate the ranking change. Pass `--routes-csv` with `origin,facility_id,travel_time_minutes` for saved reachable drive routes, or use the app's regular straight-line ranking.

```sh
python first-prototype/replay_service_ranking.py \
  --baseline-pipeline /path/to/earlier/pipeline.py \
  --raw-facilities /path/to/raw_facilities.jsonl \
  --as-of 2026-11-22 \
  --place 'Jaipur, Rajasthan' \
  --output-dir /path/to/ranking-replay
```

## Current scope and next iteration

The prototype uses a small, name-hint mapping from selected services to NFHS indicators to scale the load penalty. Those indicators are relative survey proxies, not disease incidence. The population share and equal allocation are scenario assumptions; hospitals' true catchments, market share, service-specific capacity, current staffing, and occupancy are unknown. Review the NFHS mapping and population file provenance before presenting it as more than a demonstration. Independent-source corroboration, validity dates, better service entailment/negation handling, PIN-directory geocoding quality, measured service-specific load, and clinical review remain open work.
