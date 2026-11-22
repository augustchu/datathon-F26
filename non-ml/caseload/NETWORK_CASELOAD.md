# Facility caseload and load-balancing scenarios

The November 2026 release includes a typical monthly caseload and a modeled monthly throughput for all **181,386 combinations of facility records and Care needed options**. Both quantities are in the same service activity unit. The planner uses those quantities to compare nearest eligible routing with a simulated distribution of additional activity. The current availability comparison still uses time-stamped provider measurements. The forecast contains zero live availability rows. [Forecast CSV.gz](network_caseload.csv.gz) · [Build receipt](network_caseload_receipt.json) · [Current availability comparison](../LOAD_BALANCING.md)

## Coverage and identity

| Coverage | Count |
|---|---:|
| Rows in the reviewed facility release | 10,088 |
| Distinct exact project facility IDs | 10,073 |
| Additional source rows with a repeated ID | 11 |
| Source rows without an exact ID | 4 |
| Exact facility IDs with a routing-eligible location | 9,574 |
| Care needed options | 18 |
| Numeric caseload and throughput pairs | 181,386 |
| Caseload taken from exact-branch historical reporting | 25 |
| Caseload predicted from exact-branch historical reporting | 13 |
| Caseload predicted from comparable service reporters | 181,348 |
| Throughput marked as predicted | 181,386 |
| Throughput predictions with an exact programme resource count | 7 |

Each exact ID and each unlinked source row has 18 entries. Matching duplicate IDs are collapsed after comparing the planning metadata. A duplicate with disagreeing metadata fails the build. The four unlinked rows are retained under `release-row:<source row number>` identifiers for scenario inventory and held from routing. A routing-eligible location means the project can place the branch geographically. Service evidence is a separate field. [Pinned facility release manifest](baseline_coverage.json) · [Build receipt](network_caseload_receipt.json)

Across the 181,386 rows, **38 branch–service pairs at 19 exact branches** have historical activity in the modeled scope. Four others have broader verified service evidence, 13 have an unverified service listing, and 181,331 have unknown service status. A peer estimate answers “how much activity would be typical if this branch provided a service comparable to the reporting peers?” Positive estimates can therefore appear for services the branch does not provide. These estimates cannot verify facility services and cannot be summed to estimate regional demand. The 38 scope-matched rows can support historical service verification when the original source and its dates are attached. [Forecast CSV.gz](network_caseload.csv.gz) · [Build receipt](network_caseload_receipt.json)

## Why use a service-specific peer median

The model uses named-facility reports and gives each reporting branch one typical-month value per comparable service. It takes the median of published monthly values or divides a period total by its known month count. Training scopes keep materially different workload definitions apart. For example, a count of dialysis sessions measures repeated treatments, CEmONC deliveries count delivery events at participating centres, a fracture admission cohort includes only patients meeting that study’s rules, and chemotherapy counts administrations. The 171 usable reporting site–service pairs cover 12 states and union territories, with substantial concentration in public programmes and specialty networks. [Caseload source survey](README.md) · [Model profile source rules](network_model.py) · [Build receipt, profiles](network_caseload_receipt.json)

1. Prefer a monthly rate derived from the branch’s own comparable observations. Use a newer same-branch forecast when a prior backtest supports its approach and its scope matches. Keep the branch’s reference dates and mark each projection.
2. For other rows, take the median across reporting sites in the same state when at least three are available in the modeled service scope. Otherwise use all comparable reporting sites. Repeated months and reports from the same site do not add weight. When a service has a single reporting site, the estimate repeats that observation as a scenario anchor.
3. Keep the service scope, state or pooled cohort, peer count, observed minimum and maximum, and links to the reports with the estimate. The min and max describe the selected historical peers. No calibrated prediction interval is available for the all-facility export.

This choice reflects what held up in tests and what can be applied consistently. The earlier XGBoost experiment found that a machine-scaled median had a mean absolute error of **52.83 sessions/month** against **61.32** for XGBoost when predicting later activity in held-out Himachal Pradesh districts. For the 23 already-observed dialysis sites, carrying forward each site’s prior rate yielded **45.85** against **59.95**. In the Tamil Nadu maternity cohort, the median scaled by CEmONC beds within facility type had an MAE of **36.86 deliveries/month** against **39.76** for XGBoost in held-out districts. Those evaluations support the use of historical branch rates and same-service resources where available. The release has no aligned specialty resources for most branches, and the other service cohorts have too few comparable outcomes for a national feature model. [XGBoost experiments and validation splits](XGBOOST.md)

Adding district population to the earlier held-out-district XGBoost tests increased MAE from **60.21 to 66.09 sessions/month** for dialysis and from **39.76 to 40.19 deliveries/month** for maternity. Around **5,978 of 10,073** linked branches have a numeric district population. A district count measures residents. Allocating their expected care across branches would require a reliable catchment, referral flows, service coverage and each provider’s share. Likewise, the latest release’s hospital-wide bed counts and physician counts cannot translate directly into scans, operations or outpatient visits. The model preserves a reproducible estimate for every row without assigning volume from those less specific inputs. [Population experiments](XGBOOST.md) · [Facility release audit](BASELINES.md)

The national HMIS facility-month collection is documented. The public Open Government Data catalog located for named facilities publishes quarterly high and low performing facilities for selected indicators. Public HMIS geography tables and PM-JAY publications add useful regional or scheme context. The search did not establish a downloadable nationwide file of named-facility monthly workload covering the private and public branches and all 18 care options in this release. The model instead uses scope-checked programme reports, hospital statistics and published cohorts. [MoHFW annual report, p. 44](https://www.mohfw-dohfw.gov.in/static/uploads/2025/09/45b06af4508a53a059c74efc930d955e.pdf) · [HMIS public quarterly catalog](https://data.gov.in/catalog/quarterly-maximum-and-minimum-performing-public-health-facilities-selected-data-items) · [PM-JAY insights](https://insights.pmjay.gov.in/) · [Source survey](README.md#public-reporting-coverage)

### Why keep a complete forecast table

The confirmed lookup already checks the requested service and branch before using a caseload estimate. Default network planning also requires historical activity at that branch in the modeled scope. Keeping one row for every facility–service pair makes the join predictable and lets the lookup apply its existing evidence rules in one place. Each peer estimate in the export remains conditional on the facility providing the service. The CLI admits unknown providers only when the user explicitly names them for a hypothetical replay. [Shared lookup](../src/facility_tool/lookup.py) · [Planning CLI](plan_network_load.py)

Branch history with a service-specific median as the fallback is the best fit for the data currently available. It uses the closest comparable evidence, is easy to reproduce from the source counts, and gives unusually high-volume reporting sites limited influence on the fallback. The held-out dialysis and maternity tests support simple baselines in those programmes. With few comparable reports for most other services, the peer median is a planning benchmark with uncertainty that varies by service and setting. [Model comparison](XGBOOST.md) · [Per-service coverage and holdout results](network_caseload_receipt.json)

## What each Care needed option estimates

Every row below has 10,077 numeric scenarios. The last column counts exact-site reported rates and same-site predictions, respectively. The median and p90 are pooled profile summaries before applying the state and exact-site rules. The leave-one-site-out MAE tests the pooled median on the available reporters. It measures transfer within the published sample and can be large. Four profiles have a single anchor and cannot be scored this way. Two or three reports also provide limited validation. No test establishes calibration for the full release or for rural and private settings. [Held-out rows, scope limits and source links](network_caseload_receipt.json)

| Care needed | Modeled activity per month | Sites | Median | p90 | Holdout MAE | Exact reported / same-site predicted |
|---|---|---:|---:|---:|---:|---:|
| Dialysis | Completed Himachal Pradesh PMNDP haemodialysis sessions | 24 | 167.58 | 428.08 | 103.28 | 0 / 5 |
| Emergency C-section | Emergency caesareans at study hospitals in Uttar Pradesh | 2 | 89.14 | 107.00 | 35.72 | 0 / 0 |
| Maternity / delivery | Deliveries at Tamil Nadu DMS CEmONC centres | 94 | 98.71 | 248.33 | 80.67 | 2 / 0 |
| NICU | Admissions to named neonatal intensive care units | 2 | 32.42 | 35.33 | 5.83 | 1 / 0 |
| Pediatric care | General pediatric outpatient visits, including follow-ups | 3 | 5,259.00 | 6,702.42 | 1,389.61 | 2 / 1 |
| Cardiac surgery | Hospital cardiac operations reported in the total surgery line | 2 | 392.88 | 708.67 | 631.59 | 2 / 0 |
| Cardiology consultation | General cardiology consultations, including follow-ups | 2 | 2,453.67 | 2,477.50 | 47.67 | 2 / 0 |
| MRI | Hospital radiology MRI examinations | 10 | 630.00 | 964.33 | 231.77 | 5 / 2 |
| CT scan | Hospital radiology examinations in the CT category | 9 | 1,144.00 | 3,825.33 | 922.27 | 4 / 2 |
| Burn care | Burn cases categorized by the reporting emergency department | 1 | 1.00 | 1.00 | — | 0 / 0 |
| Orthopedic surgery | Major orthopedic operations | 3 | 185.50 | 199.00 | 30.83 | 1 / 1 |
| Fracture care | Eligible fracture admissions via emergency in the UPUMS cohort | 1 | 90.92 | 90.92 | — | 0 / 0 |
| Dental care | Hospital dentistry outpatient encounters | 2 | 222.75 | 239.50 | 33.50 | 1 / 0 |
| Oral surgery | Oral and maxillofacial operations, excluding separately tabulated extractions and implants | 2 | 117.00 | 136.00 | 38.00 | 2 / 0 |
| Cataract surgery | Cataract operations at named Aravind branches | 7 | 4,109.25 | 8,388.08 | 2,337.45 | 0 / 0 |
| Stroke care | Stroke cases categorized by the reporting emergency department | 1 | 12.00 | 12.00 | — | 0 / 0 |
| Chemotherapy | Administrations across the reported hospital day-care departments | 1 | 4,359.42 | 4,359.42 | — | 0 / 1 |
| Trauma / emergency | All-cause casualty or emergency department attendances | 5 | 12,333.00 | 36,047.00 | 10,425.10 | 3 / 1 |

## Throughput estimates

- **Seven programme branches use resource-based estimates.** Five exact Himachal Pradesh branches use the reported number of functional machines in March 2025 multiplied by the nearest-rank p90 of completed March 2024 sessions per functional machine at other programme sites. Two exact Tamil Nadu DMS branches use their reported FY2025–26 CEmONC beds multiplied by the p90 monthly deliveries per CEmONC bed among other same-type sites in the reporting year. Both methods preserve the resource reference and mark the output as predicted. [Dialysis resource evidence](load_evidence.json) · [Tamil Nadu NHM, Agenda 24 Annexure 1](https://nhm.tn.gov.in/wp-content/uploads/2026/51st_EC-Part-A-Final.pdf)
- **All other pairs use an observed peer throughput benchmark.** Select the nearest-rank p90 of comparable monthly activity, excluding the exact target site if other reporters are available. A single reporting anchor supplies the value when it is the only observation. The p90 describes relatively high activity in the selected sample. It has no measured service limit, staffing schedule, downtime, or booking inventory behind it. Reported workload and the throughput benchmark can share their underlying sources. For conditional rows with identical peer pools, both numbers may repeat across many sites. [Build code](build_network_caseload.py) · [Model receipt](network_caseload_receipt.json)

The common period label identifies a historical typical-month scenario. Source dates can differ by site and by numerator or denominator. Every unit is monthly and the workload stays in the stated scope. The resulting ratio is a dimensionless comparison with modeled throughput and can exceed one. Historical completed activity omits people who were diverted or are waiting. The planner’s throughput is a soft scoring input, reflecting neither actual remaining appointments nor patient waiting times. [Prior measured comparison and capacity audit](../LOAD_BALANCING.md)

## How the planner allocates activity

The planner first chooses candidates that pass the entry point’s service and location checks. The replay compares with the nearest eligible branch within the leading evidence tier, and limits alternatives to comparable scopes, periods and units. Time-critical services keep the nearest eligible allocation. The CLI and dashboard use a 50 km straight-line search radius and a default 10 km extra-travel cap. The CLI can use complete comparable route data for an origin, then uses a road distance cap of 10 additional km and a time cap of 15 additional minutes. Without routes, the comparison tests straight-line distance. [Planner](../src/facility_tool/network_load.py) · [CLI](plan_network_load.py)

For each additional synthetic activity, the planner evaluates:

```text
projected_activity = typical_monthly_caseload + activities_already_assigned + 1
pressure_penalty = projected_activity / (projected_activity + modeled_monthly_throughput)
score = 0.5 × normalized_extra_travel + 0.5 × pressure_penalty
```

The travel penalty is extra distance or time divided by the corresponding cap and clipped to the interval from zero to one. The activity goes to the lowest score, with nearer candidates first on ties. The planner updates the winning site’s score before assigning the next activity. The nearest-only comparison assigns the entire synthetic batch to the nearest eligible option. When there is no comparable alternative, the planner uses that nearest allocation. When no branches pass eligibility, the CLI reports the batch as unassigned. These are deterministic scenarios. They do not simulate variable interarrival times, service durations, multi-visit treatment schedules or a stochastic booking queue. The earlier literature review discusses those extensions and how to evaluate travel versus access against a nearest-eligible baseline. [Literature review](../LOAD_BALANCING_LITERATURE.md)

## Replayed examples

Each comparison starts with 100 synthetic activities from one hypothetical origin. Both use straight-line distances and exact branches with published historical activity in the modeled service scope.

| Scenario | Branch | Distance | Monthly caseload / throughput | Caseload predicted | Capacity predicted | Nearest only | Load balanced |
|---|---|---:|---:|---|---|---:|---:|
| Himachal Pradesh dialysis | Netaji Subhash Chandra Bose Zonal Hospital, Mandi | 16.020 km | 385.33 / 424.80 sessions | Yes | Yes | 100 | 42 |
| Himachal Pradesh dialysis | Regional Hospital Kullu | 16.343 km | 275.92 / 377.60 sessions | Yes | Yes | 0 | 58 |
| Odisha MRI | AIIMS Bhubaneswar | 14.697 km | 1,245.00 / 964.33 scans | Yes | Yes | 100 | 0 |
| Odisha MRI | SCB Medical College and Hospital, Cuttack | 14.754 km | 548.25 / 1,245.00 scans | No | Yes | 0 | 100 |

The dialysis replay allocates 58 of 100 additional sessions away from the nearest branch. The caseload forecasts end in March 2026 and the throughput estimates use March 2025 machinery. The MRI replay allocates 100 of 100 scans away from the nearest branch. Its SCB workload comes from 6,579 scans reported for FY2019–20 and its AIIMS caseload is a FY2025–26 projection. Treat the MRI result as sensitivity to historical assumptions and data age. Neither result establishes a shorter real-world wait or a usable road detour. These batch comparisons measure how many synthetic activities change destination. The earlier fixed-period ranking replay has a different comparison and remains documented separately. [Machine-readable example results](network_planning_examples.json) · [Earlier Care needed ranking replay](CARE_OPTIONS_REPLAY.md)

## First-result replay across all 18 Care needed options

The replay found **three first-result changes at 14 comparable origins**. Dialysis changed at one origin and MRI at two. The nearest facility stayed first at the other 11 comparable origins. The audit screened **56 origins across 38 exact branch–service matches**. Only dialysis and MRI had a comparable alternative within the detour limit at a sampled routine origin. [Full replay receipt](network_care_replay.json)

| Care needed | Origin | Nearest matched branch | First with load balancing | Extra straight-line distance |
|---|---|---|---|---:|
| Dialysis | 31.83300, 77.01800 | Netaji Subhash Chandra Bose Zonal Hospital, Mandi | Regional Hospital Kullu | 0.323 km |
| MRI | 20.35014, 85.83190 | AIIMS Bhubaneswar | SCB Medical College and Hospital, Cuttack | 0.587 km |
| MRI | 20.35232, 85.83294 | AIIMS Bhubaneswar | SCB Medical College and Hospital, Cuttack | 0.057 km |

Both sides of each comparison used the same facilities. Eligibility required a routing-eligible release ID and historical activity for the exact branch and modeled service scope. Forecast-only rows, unverified listings, and manual assumptions stayed outside the candidate set. The baseline ranked by straight-line distance within the evidence tier. The planner used the existing equal travel and pressure weights, a 50 km search radius, and a 10 km detour cap. The receipt records the first ranked branch and independently checks the first destination of a one-activity replay. They agreed at all 14 comparable origins. [Replay implementation](replay_network_care.py) · [Shared ranking policy](../src/facility_tool/recommend.py)

The origin set used each matched branch's coordinates and points 49% and 51% along the coordinate segment joining each pair at most 98 km apart when both branches were within 50 km of the point. It also included the two earlier hypothetical origins in this report. Both earlier origins produced the same first-choice change in this replay. Among the 54 generated origins, one MRI probe changed. These sampled origins describe where the policy changes the result under the supplied inputs. They provide no estimate of how often that would happen for patients. The MRI workload estimates use different source years, FY2019–20 at SCB and a FY2025–26 projection at AIIMS. Both MRI throughput values and both sets of dialysis inputs are modeled. [Original scenario receipts](network_planning_examples.json) · [Per-origin inputs and dates](network_care_replay.json)

| Care needed | Exact matched branches | Origins screened | Comparable origins | Top changed | Outcome |
|---|---:|---:|---:|---:|---|
| Dialysis | 5 | 18 | 11 | 1 | Mandi to Kullu |
| Emergency C-section | 0 | 0 | 0 | 0 | Time-critical, no exact match |
| Maternity / delivery | 2 | 2 | 0 | 0 | No comparable nearby alternative |
| NICU | 1 | 1 | 0 | 0 | Time-critical |
| Pediatric care | 3 | 3 | 0 | 0 | No comparable nearby alternative |
| Cardiac surgery | 2 | 2 | 0 | 0 | Time-critical |
| Cardiology consultation | 2 | 2 | 0 | 0 | No comparable nearby alternative |
| MRI | 7 | 10 | 3 | 2 | AIIMS Bhubaneswar to SCB Cuttack |
| CT scan | 6 | 6 | 0 | 0 | No comparable nearby alternative |
| Burn care | 0 | 0 | 0 | 0 | Time-critical, no exact match |
| Orthopedic surgery | 2 | 2 | 0 | 0 | No comparable nearby alternative |
| Fracture care | 0 | 0 | 0 | 0 | Time-critical, no exact match |
| Dental care | 1 | 1 | 0 | 0 | One matched branch |
| Oral surgery | 2 | 2 | 0 | 0 | No comparable nearby alternative |
| Cataract surgery | 0 | 0 | 0 | 0 | No exact match |
| Stroke care | 0 | 0 | 0 | 0 | Time-critical, no exact match |
| Chemotherapy | 1 | 1 | 0 | 0 | One matched branch |
| Trauma / emergency | 4 | 6 | 0 | 0 | Time-critical |
| **Total** | **38** | **56** | **14** | **3** | **Changes in two care options** |

At nine time-critical origins the routine policy withheld reranking. For service rows with zero comparable origins, zero changes indicates that no load-balanced comparison ran.

## Running it

The builder and CLI require the exact `facilities_newest_capacity.csv` recorded in [baseline_coverage.json](baseline_coverage.json). A renamed copy with identical contents works. The release is a workspace input. The forecast is distributed here with the receipt and its SHA-256 hash. From the repository root, set `FACILITIES` to that release path and run:

```sh
python3 non-ml/caseload/build_network_caseload.py \
  --facilities "$FACILITIES" \
  --output non-ml/caseload/network_caseload.csv.gz \
  --receipt non-ml/caseload/network_caseload_receipt.json

python3 non-ml/caseload/plan_network_load.py \
  --facilities "$FACILITIES" \
  --forecasts non-ml/caseload/network_caseload.csv.gz \
  --service dialysis --lat 31.833 --lon 77.018 --requests 100

python3 non-ml/caseload/plan_network_load.py \
  --facilities "$FACILITIES" \
  --forecasts non-ml/caseload/network_caseload.csv.gz \
  --service mri --lat 20.35231764 --lon 85.83294129 --requests 100

python3 non-ml/caseload/replay_network_care.py \
  --facilities "$FACILITIES" \
  --forecasts non-ml/caseload/network_caseload.csv.gz \
  --output non-ml/caseload/network_care_replay.json
```

- **CLI.** Default candidates have scope-matched historical activity. Add `--include-conditional` to admit verified broader services and unverified service listings as hypothetical providers. Add `--assume-facility-ids` followed by exact routable IDs to admit named providers whose service status is unknown. The receipt labels the assumed facilities. `--routes` accepts a file with an `origin` object containing matching numeric `lat` and `lon`, a provider `source`, a `mode` of `driving`, `walking` or `cycling`, and a `routes` list. Supply one row with `facility_id`, `route_distance_km` and `travel_minutes` for every selected candidate within 50 km. An incomplete file fails the replay. [CLI source](plan_network_load.py)
- **Dashboard.** Load the real Facilities table export and search for a routine service. Expand **Historical load planning** and select **Run a monthly planning scenario**. The table shows the scope evidence, typical activity, modeled throughput, dates, independent prediction flags and both allocations. By default it admits exact scope-matched activity and ignores service keyword coverage for this comparison. If choosing the optional service leads, the table explicitly shows the assumptions. `DATATHON_NETWORK_CASELOAD_CSV` can override the default checked-in forecast. This analysis panel is separate from the patient search cards and measured availability comparison. [Dashboard](../../dashboard/dashboard.py)
- **Notebook 08 and shared lookup.** Set `RUN_NETWORK_PLANNING = True` in the dedicated network cell and use a supported routine service with a real prepared lookup snapshot. The cell uses eligible branches from the lookup trace and attaches history only when its reported scope matches the model. The lookup accepts `network_forecasts` in planning mode. It retains the independent service claim and location checks, with holds on urgent requests and peritoneal dialysis. The CLI is available for all 18 forecast service codes, including options outside the notebook’s claim vocabulary. [Planning notebook](../notebooks/08_load_policy_scenarios.ipynb) · [Lookup](../src/facility_tool/lookup.py)

## Reading the export

| Fields | Meaning |
|---|---|
| `facility_id`, `service`, `care_needed` | Exact project ID or unlinked source-row ID, stable service code and UI label |
| `unit`, `scope`, `period` | Counted activity, precise workload definition and common historical scenario window |
| `caseload`, `caseload_predicted`, `caseload_method` | Monthly activity, independent estimate flag and reported-history, same-site forecast or peer method |
| `capacity`, `capacity_predicted`, `capacity_method` | Monthly throughput scenario, independent estimate flag and throughput method |
| `reference_start`, `reference_end`, `capacity_reference_period` | Period of the branch activity or forecast, and the resource-based throughput reference when applicable. For pooled estimates, peer reporting dates appear in the receipt. |
| `service_evidence`, `routing_eligible`, `location_state` | Scope-matched reporting, broader verification, unverified listing or unknown service status. The routing flag comes from the reviewed release’s location audit. |
| `caseload_peer_group`, `caseload_peer_count`, `capacity_peer_group`, `capacity_peer_count`, `peer_min_caseload`, `peer_max_caseload`, `capacity_excludes_target_site` | Historical peer grouping and sample size. The min and max describe the selected service peers. The exclusion flag indicates whether the selected pool can exclude the target site. |
| `source_url`, `capacity_source_url` | Report supplying the branch value, a reference observation for the peer statistic, or the programme resource. All observations and their source periods are in the receipt. |
| `release_sha256` | Hash of the reviewed facility input. The CLI checks that it matches the release used for the replay. |

The built-in checks validate the release hash, the one-row-per-branch–service matrix and finite, nonnegative caseloads with positive throughput. The receipt stores input hashes and per-service coverage and diagnostics. A rebuild against the pinned input produced the same gzip bytes. Run the two test suites from the repository root:

```sh
PYTHONPATH=non-ml/src python3 -m unittest discover -s non-ml/tests
PYTHONPATH=non-ml/src python3 -m unittest discover -s non-ml/caseload -p 'test_*.py'
```
