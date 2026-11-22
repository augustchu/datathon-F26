# First prototype: implementation decisions

This file records the implementation choices made for the first Databricks prototype. It is intentionally explicit so the team can replace assumptions when better data becomes available.

## Product and architecture

1. The prototype is a Databricks App using Streamlit. It is intended for an authenticated hackathon demo, not anonymous/public patient access.
2. Data preparation runs separately in a Databricks notebook against Unity Catalog. The app reads a prepared Unity Catalog snapshot through a Databricks SQL warehouse. The app does not start a Spark session or rerun the full preparation pipeline after every UI interaction.
3. The app lives in this folder and imports only code shipped in this folder. No original project files are modified.
4. Prepared output tables use a configurable catalog and schema, defaulting to `main.first_prototype`. The preparation notebook creates the schema if the workspace permits it.
5. The source tables default to the Marketplace names already recorded in the project config: facilities, PIN directory, and NFHS district indicators. The notebook checks for the sources, reports their columns, and lets the team adjust table names at the top if the workspace uses different names.
6. The supplied polygon archive is read from `/Volumes/additional_india_data/default/additional_india_data/geoBoundaries-IND-ADM2-all.zip`. GeoPandas reads its contained vector dataset. If the archive has multiple layers or an unexpected internal file name, the notebook displays available layers and the path can be changed in its configuration cell.

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
15. The intensity slider is a count of hypothetical concurrent routine cases (0–300). Those cases are placed at the same resolved origin and assigned sequentially across selected-service candidates before the user's case is ranked. This illustrates reranking without claiming to forecast population or disease incidence.
16. Emergency requests prioritize the closest eligible, evidence-backed facility and ignore simulated load. For urgent and routine user requests, score = straight-line kilometers + accumulated simulated assignments × relative regional-demand factor × 12 / square-root of bed count (50 beds is the fallback when unknown). The factor and fallback are demonstration assumptions, not medically validated policy. The app prints each score component.
17. Bed count is not current staffed capacity, available beds, or service-specific slots. It is only a coarse resource proxy. The UI labels its use and warns that real load data is absent.
18. Missing or invalid coordinates and records without citable service evidence are omitted. If no eligible record remains, the app abstains and explains that it could not find a sufficiently supported option.
19. For services with a matching NFHS field, the preparation step computes a district value relative to the national mean, halves parenthesized low-confidence values, and clips the result to 0.5–2.0. That factor scales synthetic load pressure on facilities mapped to the district. The service-to-field mappings are name-hint heuristics; unsupported services and unmatched regions use 1.0. NFHS percentages are survey indicators, not disease incidence or case counts.

## Models

20. The existing saved neural model estimates total bed capacity only. It may fill missing capacity in the preparation notebook; it never determines whether the hospital offers the requested service.
21. Model output retains an `estimated` provenance label. Reported values take precedence. If model loading or inference fails, preparation continues and beds remain unknown.
22. The prototype limits model display values to the same 5,000-bed ceiling as `dashboard/capacity_model.py` and records the model source/version in the output table.

## Interactive controls and persistence

23. The app exposes service, origin, severity, concurrent-case intensity (0–300), and result count. Every control is passed to the ranking function. The Find button starts a search; after that, Streamlit reruns and updates results on widget changes.
24. Intensity creates a deterministic synthetic scenario for ranking only. It does not create real patients or change source data. Emergency ranking ignores simulated load.
25. The app reads from a named prepared table and uses a bounded cache. Re-run preparation and increment/update the snapshot timestamp to refresh cached data.
26. The TravelTime API is not called in the first prototype because outbound network availability and credentials are not guaranteed in Free Edition. The adapter can be added after endpoint access is verified in the deployed App.
27. The app only reads data. It does not persist patient location, searches, feedback, or health information.

## Known limitations before any real-world use

28. The app is a hackathon decision-support demonstration. It must not be used as a clinical triage system, emergency dispatch service, or guarantee that treatment is currently available.
29. Text search, negation, address resolution, and heuristic ranking can be wrong. Service confirmation, source date/validity, exact branch matching, calibrated evidence tiers, validated issue-to-indicator mappings, NFHS-weighted case-origin generation, and observed service-level availability/load are future work.
30. The preparation notebook is designed to surface schema and geography mismatches instead of silently guessing. Team members must review its output counts, rejected rows, and sample recommendations in Databricks before presenting results.
