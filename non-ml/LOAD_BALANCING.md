# Routine load balancing and historical capacity planning

Implemented two comparisons. Routine facility searches can use **current, measured availability for the requested service**. Planning can use **historical workload and predicted service throughput**, with each prediction marked in the output. The November 22, 2026 evidence release supplies historical inputs for the latter. It supplies zero current availability rows. [Historical load evidence](caseload/load_evidence.json) · [Routine availability audit](caseload/ROUTINE_APPOINTMENTS.md)

The [expanded Care needed replay](caseload/CARE_OPTIONS_REPLAY.md) documents aligned caseload projections, separate prediction flags and ranking results across the other care options.

The [network caseload and load-balancing report](caseload/NETWORK_CASELOAD.md) adds typical-month caseload and throughput estimates for all 18 Care needed options across the facility release. The dashboard, notebook and CLI use those inputs in historical scenarios with separate prediction flags.

## Routine facility comparison

The availability comparison is opt-in. It starts from the closest eligible facility and compares only nearby branches with comparable evidence for the same service. An alternative with more measured availability can move to the top. The closest branch remains identified. Missing, stale, conflicting or incomparable measurements leave the ordinary ranking available. Time-critical searches use the ordinary workflow. The comparison describes a measurement as of a stated time. A second search may see the same report before the provider updates it. [Load-balancing literature](LOAD_BALANCING_LITERATURE.md) · [Queueing and interhospital transfers](https://pubsonline.informs.org/doi/10.1287/msom.2025.0497)

In **Streamlit**, turn on **Compare current service availability** on a routine search. Supply a provider-authorised CSV in the sidebar or set `DATATHON_MEASURED_LOAD_CSV` to its path. Branch joins use unique exact facility IDs and direct service matches. Streamlit shows straight-line distance, the observed count, its time and coverage window in India Standard Time, and the source. The default extra-distance limit is 25 km and can be changed in the interface. The user checks actual travel time and confirms the reported availability with the facility.

In [notebook 07](notebooks/07_compare_and_feedback.ipynb), set `SPREAD_DEMAND = True` and provide `MEASURED_LOAD_CSV` or the environment variable above. It can also use a prepared measured-load checkpoint. The lookup uses documented branch and service claims, compares within the nearest branch's evidence tier and claim scope, and requires comparable travel-time estimates. The default caps are 10 additional km and 15 additional minutes. The measurement window must extend past the estimated arrival. When the routing provider supplies time only, straight-line distance supplies the distance bound. A measured comparison takes precedence over the optional equity sort when both are enabled. [Lookup](src/facility_tool/lookup.py) · [Shared selector](src/facility_tool/recommend.py)

| CSV fields | Meaning |
| --- | --- |
| `facility_id`, `service` | Exact project branch ID and canonical service key, such as `dialysis` or `mri` |
| `available_units`, `unit`, `basis` | Nonnegative measured availability for the named service. Use the same unit across sites and `measured` or `observed` as the basis. A provider-reported zero is valid. |
| `as_of`, `window_start`, `window_end` | Timezone-aware ISO 8601 timestamps. Use the same active availability window across sites. By default a sample may be at most 24 hours old and paired samples may differ by at most two hours. The active window can impose a shorter limit. |
| `source_url` | Provider source for the measurement. A row with `capacity_predicted` set to true is excluded from this comparison. |

## Historical planning data and predictions

The [evidence bundle](caseload/load_evidence.json) includes 35 reported caseload rows and 25 previously estimated rows across 26 exact project branches. It carries service scope, unit, period, branch linkage and source at the row level. It also contains 47 March dialysis observations with same-month functional machine counts, and 94 Tamil Nadu fiscal-year delivery totals with CEmONC bed inventories. The latter permit comparison of deliveries per reported CEmONC bed. Their bed inventory supplies neither staffed bed-days nor a count of available appointments. [Caseload expansion](caseload/EXPANSION.md) · [Tamil Nadu NHM, Annexure 1, Table 1](https://nhm.tn.gov.in/wp-content/uploads/2026/51st_EC-Part-A-Final.pdf)

For the dialysis comparison, observed March 2025 sessions supply the workload. A predicted monthly throughput is the reported number of functional machines in March 2025 multiplied by the 90th percentile of March 2024 sessions per functional machine at other Himachal PMNDP sites. The percentile uses the nearest-rank method and excludes the target site from the training calculation. This is a benchmark for plausible throughput under observed programme conditions. It depends on the assumption that earlier machine productivity is informative for the later month. Staffing, machine downtime, unmet demand and the current queue remain unmeasured. [PMNDP source manifests and observations](caseload/hp_dialysis_model_data.json) · [March 2024 programme report](https://nhm.hp.gov.in/storage/app/media/uploaded-files/PMNDP%202023-24.pdf)

| Reviewed branch | March 2025 sessions | Functional machines | Predicted monthly throughput |
| --- | ---: | ---: | ---: |
| Regional Hospital Bilaspur | 311 | 6 | 271.33 |
| Government Medical College Chamba | 473 | 8 | 361.78 |
| Zonal Hospital Dharamshala | 473 | 9 | 424.80 |
| Regional Hospital Kullu | 329 | 8 | 377.60 |
| Zonal Hospital Mandi | 405 | 9 | 424.80 |

Observed activity exceeded the predicted throughput at **8 of 24** programme sites in March 2025, including **3 of the 5** linked branches. This check compares the prediction with completed sessions. It cannot establish whether predicted throughput matches staffed capacity. The planner exports `capacity_predicted: true` alongside the method and source. Notebook results expose the corresponding `pressure_capacity_predicted` field. [Prediction inputs and diagnostics](caseload/load_evidence.json)

## Planning replay

The weighted planner combines extra travel with the ratio of reported workload to predicted throughput for the same service and month. Ratios can exceed 1.0. In this scenario the selector's `demand` field holds completed sessions. The two terms are bounded and weights are explicit. The replay uses equal travel and workload weights and a 10 km straight-line detour limit. A modeled workload/throughput ratio describes the scenario. Nothing in it measures the wait for an individual patient. [Planning notebook](notebooks/08_load_policy_scenarios.ipynb) · [Replayed cases](caseload/load_planning_replay.json)

Five origins used the five reviewed facility coordinates. None had a comparable second facility within the default detour limit. A sixth, hypothetical origin at the coordinate midpoint of the closest pair, made Kullu and Mandi comparable in the straight-line scenario. Kullu remained first, with an activity-to-throughput ratio of 0.871 versus 0.953 at Mandi. **Zero of six first choices changed.** The district government lists Kullu and Mandi towns roughly 70 km apart by road. The midpoint case tests the scoring rule with equal-distance alternatives. It leaves actual routes and patient outcomes open. [Replay receipt](caseload/load_planning_replay.json) · [Kullu district travel information](https://hpkullu.nic.in/how-to-reach/)

## Reproduce and verify

From the repo root, run the generator and replay with the final reviewed facility CSV. The replay verifies the file hash against the original coverage audit.

```sh
python3 non-ml/caseload/build_load_evidence.py
python3 non-ml/caseload/replay_load_planning.py \
  --facilities "/path/to/facilities_newest_capacity.csv" \
  --output non-ml/caseload/load_planning_replay.json
PYTHONPATH=non-ml/src python3 -m unittest discover -s non-ml/tests
python3 -m unittest discover -s non-ml/caseload -p 'test_*.py'
```

Tests cover the measured comparison with routes, detour bounds, an explicit zero, matching windows, arrival after a window closes, time-critical fallbacks, and prediction provenance. A Streamlit test with a synthetic provider CSV exercised promotion and the absence of a usable comparison through the normal UI. The real-data replay measures selected facilities under stated assumptions. A prospective evaluation with participating facilities would measure acceptance, time to completed care, travel burden and receiving-site strain. [Literature evaluation framework](LOAD_BALANCING_LITERATURE.md#evaluate-against-the-current-ranking)
