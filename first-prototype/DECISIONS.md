# First prototype: implementation decisions

This file records the implementation choices made for the first Databricks prototype. It is intentionally explicit so the team can replace assumptions when better data becomes available.

## Product and architecture

1. The prototype is a Databricks App using Streamlit. It is intended for an authenticated hackathon demo, not anonymous/public patient access.
2. Data preparation runs separately in a Databricks notebook against Unity Catalog. The app reads a prepared Unity Catalog snapshot through a Databricks SQL warehouse. The app does not start a Spark session or rerun the full preparation pipeline after every UI interaction.
3. The app lives in this folder and imports only code shipped in this folder. No original project files are modified.
4. The preparation notebook explicitly reloads the shared `pipeline` module because Databricks notebook kernels cache imports across edits. It checks for required helpers and reports the loaded module path if the Git-folder copy is stale.
5. Prepared outputs use the existing `additional_india_data.default` schema confirmed by the team in the Volumes browser. Tables use the `first_prototype_` prefix to avoid collisions. This avoids assuming the absent `main` catalog or requiring `CREATE SCHEMA`. The notebook prints exact table names for App setup.
6. The source tables use catalog `databricks_virtue_foundation_dataset_dais_2026`, schema `virtue_foundation_dataset`, and the facilities, PIN directory, and NFHS district indicator table names. The catalog was corrected after the first Databricks run returned `TABLE_OR_VIEW_NOT_FOUND`; the notebook prints schemas/row counts so any further workspace-specific name changes are visible.
7. The supplied polygon archive is read from `/Volumes/additional_india_data/default/additional_india_data/geoBoundaries-IND-ADM2-all.zip`. The notebook reads its full GeoJSON member using `zipfile` and `json`, then does a WGS84 point-in-polygon join in Python. This avoids adding a GIS dependency to serverless compute. If no GeoJSON member is present, preparation stops with the archive member names in the error.
8. Regional population is joined from `nfhs_district_population_2020.csv` by normalized exact NFHS district and state names. The estimate is a modeled 2020 value from a constrained WorldPop raster scaled to MoHFW/NCP state projections, not a fresh census or current population count. Missing, duplicate, or unmatched population keys remain unavailable; they are never filled with a guessed population. The notebook expects this CSV at `/Volumes/additional_india_data/default/additional_india_data/nfhs_district_population_2020.csv`; change `POPULATION_CSV` if the uploaded Volume path differs.

## Facility identity, service evidence, and citations

7. The first prototype preserves one source row as one facility candidate. It does not merge facilities with similar names because a wrong branch merge can make a recommendation unsafe.
8. Service evidence is extracted from the facility's structured specialties, capabilities, procedures, equipment, and description fields. Matched terms and the original source text are kept in the prepared table.
9. A result is eligible only when a requested service term appears in facility text and at least one source URL is present. This is a conservative prototype rule, not proof of current operation. A facility lacking a link is not recommended, even if its text matches.
10. The app shows the matched excerpt and source URL on every result. This first prototype does not establish independent corroboration or source validity dates; these remain required next improvements before real use.
11. The prototype suppresses a match if the nearby text contains a simple negation such as “does not provide” or “not available.” This rule is intentionally basic and must be reviewed against false positives and false negatives.
12. The prototype detects only simple text negation and ADM2/NFHS name-join status. It does not fully detect merged facilities or contradictory addresses. Geography flags are shown on result cards; routing distance still uses the original source coordinates.

## Geographies and ranking

13. Users may enter coordinates, a PIN, a city, or a district. Text locations resolve to a centroid of matching facility coordinates when no geocoder is available; the app labels this origin approximate. Coordinates are used as entered and labeled user-provided.
14. The prototype uses haversine straight-line distance as its default travel measure so it works without a third-party API. It labels this as straight-line distance, not road travel time.
15. The slider is the percentage of each facility's NFHS district population assumed to need the selected service (0.0–2.0%, 0.1 point increments). For every district represented by an evidence-backed, coordinate-valid candidate, expected demand = modeled district population × percentage / 100. That district demand is divided evenly across all such facility source records in the district. The app does not route all demand to the nearest hospital. It also does not claim that all population lives within that hospital's actual catchment.
16. Emergency requests prioritize the closest eligible, evidence-backed facility and ignore the simulated load penalty. Routine/urgent score = straight-line kilometers + `12 × (1 − exp(−allocated cases / bed proxy × relative NFHS factor))`. The result is a capped 0–12 km-equivalent heuristic adjustment, not a measured waiting time, occupancy, probability, or calibrated utility. The exponential curve and cap are hand-set demo assumptions.
17. Bed count is not current staffed capacity, available beds, or service-specific slots. It is only a coarse resource proxy. Reported/model bed count is used when positive; otherwise a disclosed 50-bed fallback is used. The UI shows the denominator and allocated hypothetical cases. Missing regional population means no load penalty is applied, and the UI discloses that uncertainty.
18. Missing or invalid coordinates and records without citable service evidence are omitted. If no eligible record remains, the app abstains and explains that it could not find a sufficiently supported option.
19. For services with a matching NFHS field, the preparation step computes a district value relative to the national mean, halves parenthesized low-confidence values, and clips the result to 0.5–2.0. That factor scales synthetic load pressure on facilities mapped to the district. The service-to-field mappings are name-hint heuristics; unsupported services and unmatched regions use 1.0. NFHS percentages are survey indicators, not disease incidence or case counts.

## Models

20. The existing saved neural model estimates total bed capacity only. It may fill missing capacity in the preparation notebook; it never determines whether the hospital offers the requested service.
21. Model output retains an `estimated` provenance label. Reported values take precedence. If model loading or inference fails, preparation continues and beds remain unknown.
22. The prototype limits model display values to the same 5,000-bed ceiling as `dashboard/capacity_model.py` and records the model source/version in the output table.

## Interactive controls and persistence

23. The app exposes service, origin, severity, regional population share (0–2%), and result count. Every control is passed to the ranking function. The Find button starts a search; after that, Streamlit reruns and updates results on widget changes.
24. Population share creates a deterministic hypothetical district-level scenario for ranking only. Equal allocation is over selected-service facility source records with citable text and valid coordinates in each district; the dataset may contain duplicates, merged records, and facilities that no longer operate. The calculation does not create real patients or change source data. Emergency ranking ignores simulated load.
25. The app reads from a named prepared table and uses a bounded cache. Re-run preparation and increment/update the snapshot timestamp to refresh cached data.
26. The TravelTime API is not called in the first prototype because outbound network availability and credentials are not guaranteed in Free Edition. The adapter can be added after endpoint access is verified in the deployed App.
27. The app only reads data. It does not persist patient location, searches, feedback, or health information.

## Known limitations before any real-world use

28. The app is a hackathon decision-support demonstration. It must not be used as a clinical triage system, emergency dispatch service, or guarantee that treatment is currently available.
29. Text search, negation, address resolution, population joins, and heuristic ranking can be wrong. Service confirmation, source date/validity, exact branch matching, calibrated evidence tiers, validated issue-to-indicator mappings, actual catchment/population assignment, and observed service-level availability/load are future work.
30. The preparation notebook is designed to surface schema and geography mismatches instead of silently guessing. Team members must review its output counts, rejected rows, and sample recommendations in Databricks before presenting results.
