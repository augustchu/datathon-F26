# Facility evidence and recommendation notebooks

Start with fictional data to see all three modes. Each notebook reads and writes named checkpoints. Open any notebook to inspect its inputs and decisions, and rerun the dependent notebooks after an edit.

## Run locally

Use Python 3.10 or newer with pandas and NumPy. JupyterLab provides a notebook runner, and Matplotlib provides the optional EDA plots.

```sh
python -m pip install "pandas>=2.2" "numpy>=1.26" matplotlib jupyterlab
jupyter lab
```

Run notebooks 01, 02, 04, 04b, 05, 06b, and 07 in order for the same evidence date. For fitted overlays, run 03 and 09 after 02 and before 05. Notebook 09 needs scikit-learn. Notebook 06 is optional district context after 02. Notebook 08 exercises the selector using the same checkpoints as 07. The generators are only needed when changing notebook source through code.

- **`demo`** is the default. Facilities, contacts, and estimates are fictional. TravelTime stays off.
- **`public`** reads the saved public extracts. Start Jupyter with `DATATHON_MODE=public`. The default search in 07 is for the demo, so change the location and service to ones covered by the selected public sources.
- **`export`** reads a private local copy of the original facility table. Set `DATATHON_MODE=export` and `DATATHON_FACILITY_EXPORT` to its path. Keep all original columns in order. Supported inputs are CSV, Parquet with a local engine installed, and a JSON or gzip JSON object with `columns` and `rows`. This mode joins the saved public extracts. Set `DATATHON_PIN_EXPORT` or `DATATHON_NFHS_EXPORT` to private local copies of the corresponding tables to enable postal or district checks.
- **`databricks`** is for an authorized workspace. Set `DATATHON_MODE=databricks` before running 01, make the shared code available alongside the notebooks, and check the catalog names and output location. It reads the original dataset with the notebook's existing Spark session. This path awaits verification in Databricks.

Set `DATATHON_REPO_ROOT` if the notebook cannot find the repository. Checkpoints go under the ignored `non-ml/outputs/<mode>/<as_of>/` by default. Set `DATATHON_OUTPUT_DIR` to a writable project location if needed. Keep the evidence date and upstream inputs consistent across related runs.

## What each notebook does

| Notebook | What to inspect |
| --- | --- |
| [01 · Source EDA](notebooks/01_source_eda.ipynb) | Per-source row counts, usable coordinate and phone-format coverage, original schema and raw checkpoints |
| [02 · Facility and geography](notebooks/02_facility_and_geography.ipynb) | Branch linkage, original versus selected points, review flags and postal-area context |
| [03 · Count EDA](notebooks/03_count_field_eda.ipynb) | Bed/doctor label quality, reporting gaps, supported predictors after `countries`, interactions and group splits |
| [04 · Service evidence](notebooks/04_service_evidence.ipynb) | Quoted service text, branch, validity, scheme scope, ambiguous matches and contradictions |
| [04b · Service verification](notebooks/04b_verify_services.ipynb) | Independent published corroboration, exact branch matching, disagreement checks and reviewed links |
| [05 · Field updates](notebooks/05_field_updates.ipynb) | Cited reported fields, contact provenance, optional model overlays, and separate load inputs |
| [06 · District context](notebooks/06_district_context.ipynb) | Optional NFHS indicators, unique district joins, suppressed values and small-sample flags |
| [06b · Prepare lookup snapshots](notebooks/06b_prepare_lookup.ipynb) | Stored city, district and PIN origins, per-service evidence decisions and candidates, scored leads, and cited contacts |
| [07 · Compare and follow up](notebooks/07_compare_and_feedback.ipynb) | Side-by-side ranks, citations, phone and fit explanations, travel estimates, and call feedback |
| [08 · Load-policy scenarios](notebooks/08_load_policy_scenarios.ipynb) | Effects of detour caps and travel weights, compared with the default ranking |
| [09 · Count model comparison](notebooks/09_count_model_comparison.ipynb) | Training-fold feature selection, median/log-ridge/random forest, calibration, test and supported overlays |

Notebook 02 exports `location_records`, `location_entity_review`, and `location_quality_summary`. It writes `location_routing_eligible`, `location_hold_reason`, the review flags, and the source links on each facility. A reliable branch match can supply published coordinates when the base record lacks them. Review likely swaps, duplicates, invalid points, and postal conflicts with their source links. A branch whose published state conflicts with all matches for its PIN is held out of distance ranking. Shared points and large offsets from a postal-office centroid remain review flags. The search uses the saved routing decision and the selected published point.

Notebook 06b consumes those decisions and saves the service evidence as of the snapshot date. It builds approximate city, district and PIN origins, resolves known ambiguities, selects cited contacts, attaches prior field estimates, and partitions the candidates and load inputs by service. Refresh 06b after changing the evidence date, upstream records, contacts, eligibility rules, or model overlays. The search reads the prepared service and place tables. Distance from the request, time-dependent routes or slot age, opt-in ranking, and call feedback are computed per search.

Notebook 04b checks for explicit corroboration across independent publishers for the same facility branch and service. The [reviewed evidence registry](config/reviewed_service_evidence.json) stores the primary-source statements, dates and branch identifiers for manually checked cases. The notebook records unsuccessful attachments and applicable contradictions. It distinguishes a single direct listing from corroboration. Both are published service evidence with a scope and review date. A call is still needed for current service availability.

## Compare a single search

Enter one recognized service and a city, district, PIN, or latitude and longitude. All modes use the same rules for documented services.

- **Confirmed** uses cited service listings and reported or recovered facility fields. It shows scope, validity and source text to help the caller verify the result.
- **ML-assisted** adds imported ML estimates for missing fields. A model-only service estimate appears as a lead for verification.
- **Regression-assisted** shows the interpretable log-ridge estimates where the validation supports them. The demo estimates are hand-written. A service-level logistic regression still requires independently labeled service outcomes.

Notebook 05 accepts reviewed, snapshot-matched CSV or JSONL overlays. Each row provides a facility ID, field, method, value, unit, optional bounds, and model provenance. Use `capacity` in beds, `numberDoctors` in doctors or `service:<key>` for eligible lead scores. Missing mode outputs are labeled in the comparison. See the [project summary](PROJECT_SUMMARY.md) for model comparisons and evaluation criteria.

Notebook 07 displays a cited phone when available and a fit score with the factors used to calculate it. The heuristic combines direct service evidence, independent corroboration, validity, origin precision and straight-line proximity. Call the facility to check the service and current availability. A No removes the branch for the current caller and service and returns the next supported results across modes.

## Travel settings and load balancing

Within each evidence tier, the default ranking uses distance along TravelTime's estimated driving routes. Set `TRANSPORT_MODE` to `walking`, `cycling`, or `public_transport` in 07 to view or sort the same search by that mode. Driving, walking, and cycling can sort by route distance or estimated time. Public transport sorts by estimated time and coverage is partial in India. A route is estimated for the selected mode and departure time. [TravelTime matrix documentation](https://docs.traveltime.com/api/reference/travel-time-distance-matrix) · [Coverage by country](https://docs.traveltime.com/api/overview/supported-countries)

Live routing requires `TRAVELTIME_APP_ID` and `TRAVELTIME_API_KEY` in the environment. The request sends the origin and shortlisted candidate coordinates. Set `USE_TRAVELTIME=False` to use the local distance calculation. When route coverage is incomplete for the comparison, the ranking records its straight-line fallback separately from any displayed route estimates. The local verification used mocked route data.

The non-emergency load setting starts off. When enabled, the selector can trade extra travel against condition-specific demand divided by equivalent service capacity. Evidence and detour limits apply before the weighted selection. Demand and capacity need the same unit and period across compared facilities, with provenance. Total bed counts are resource estimates. Measured current service slots and historical or simulated pressure have different labels and a separate input table.

## Moderation stack

Set `MODERATION_DB` in notebook 07 to a private local SQLite path to save negative feedback for review. Use a separate database for demo reports. Reports carry the facility ID, service, fixed reason, timestamps and disposition. Newest open reports appear first. Retried reports keep their original position, and changes to review outcomes retain their history. The DB is private operational data. The CLI works on a local copy or host with access to the same database. Hosted access and team sign-in are outside this prototype.

```sh
python non-ml/moderate_reports.py --db /private/path/reports.sqlite3 list
python non-ml/moderate_reports.py --db /private/path/reports.sqlite3 review 12 temporarily_unavailable --moderator reviewer_key --expected-version 0
python non-ml/moderate_reports.py --db /private/path/reports.sqlite3 history 12
```

Moderation records review decisions. Changes to shared evidence or model training require a separate, sourced update.

## Tests

```sh
PYTHONPATH=non-ml/src python -m unittest discover -s non-ml/tests -v
```
