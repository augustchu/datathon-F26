# Historical load-balancing replay across Care needed options

For the later exact-service-match replay with typical-month forecasts and modeled throughput, see the [network first-result audit](NETWORK_CASELOAD.md#first-result-replay-across-all-18-care-needed-options). The synchronized-period replay below uses its own period and capacity requirements.

**November 22, 2026.** The expanded replay has **41 reported and 24 predicted caseloads** across **22 linked facilities and 13 non-dialysis care options**. Four predictions are new. They fill comparable October 2026 periods at Himalayan Hospital. The replay screened **34 origins** across comparable reporting groups. **Zero origins qualified for a capacity-adjusted ranking**, so the evidence supports **zero ranking changes** beyond dialysis. The previous dialysis replay still has one comparable case and no ranking changes. [Machine-readable results](care_options_replay.json) · [Prior dialysis replay](load_planning_replay.json)

## Results by Care needed

Rows count facility, activity definition, and period. A facility can contribute several rows. Of 16 aligned multi-facility cohorts, 14 were too far apart, Odisha MRI lacked comparable capacity, and Delhi and Mumbai casualty attendance was held by the time-critical policy. Screened origins include actual facility coordinates and one hypothetical midpoint for the Odisha MRI pair. A care option with zero screened origins lacked a comparable period at two branches or was held by the time-critical policy. A screened origin without a qualifying comparison has `planning_order: null` and `rank_changes: null`. These outcomes supply no evidence about whether the same first choice would win if the missing capacity data were available.

| Care needed | Linked facilities | Reported caseloads | Predicted caseloads | Screened origins | Reason no ranking changed |
| --- | ---: | ---: | ---: | ---: | --- |
| Dialysis | 5 | 5 | 5 | 6 | Prior replay only. Five March 2025 sites had reported caseload and predicted throughput. One origin allowed comparison. The five separate FY2025–26 caseload estimates have no aligned capacity. |
| Emergency C-section | 0 | 0 | 0 | 0 | No qualifying project-linked caseload. |
| Maternity / delivery | 11 | 8 | 3 | 7 | Same-period groups were too far apart. Two DMS centres have reported CEmONC beds and occupancy. |
| NICU | 1 | 1 | 0 | 0 | Single linked Level III admission series. |
| Pediatric care | 3 | 2 | 3 | 2 | The comparable October outpatient facilities are 347.6 km apart. |
| Cardiac surgery | 3 | 2 | 1 | 0 | Separate programme, cardiac theatre and hospital totals. |
| Cardiology consultation | 2 | 2 | 0 | 0 | Tertiary cardiology and pediatric clinic measures have different scopes and periods. |
| MRI | 7 | 8 | 5 | 14 | One local FY2019–20 pair has workload counts and missing throughput capacity. Other aligned groups are too far apart. |
| CT scan | 6 | 5 | 4 | 7 | Aligned groups were too far apart. The AIIMS Bhubaneswar series excludes separately reported CT angiography. |
| Burn care | 0 | 0 | 0 | 0 | No qualifying project-linked caseload. |
| Orthopedic surgery | 2 | 1 | 3 | 2 | The comparable October major-operation facilities are 347.6 km apart. |
| Fracture care | 0 | 0 | 0 | 0 | No qualifying project-linked caseload. |
| Dental care | 2 | 2 | 0 | 0 | Whole-college registrations and departmental outpatient encounters have different coverage. |
| Oral surgery | 2 | 2 | 0 | 2 | The two same-month dental college surgery sites are 1,318.2 km apart. |
| Cataract surgery | 0 | 0 | 0 | 0 | No qualifying project-linked caseload. |
| Stroke care | 1 | 0 | 1 | 0 | Single linked mechanical thrombectomy forecast. |
| Chemotherapy | 1 | 0 | 1 | 0 | Single linked day-care administration forecast. |
| Trauma / emergency | 7 | 8 | 3 | 0 | A nearby Delhi casualty pair exists. The time-critical policy keeps these cases out of reranking. |

The four options with zero linked caseloads have examples in the earlier public-report survey. Their benchmarks lacked an exact facility or comparable scope for this replay. [Source survey and scope definitions](README.md#what-is-available-by-service) · [Original caseload expansion](EXPANSION.md)

## New caseload estimates

The replay aligns each reviewed series to the closed target periods already selected by another series in its cohort. Cohorts share an activity definition. Calendar years, fiscal years, and individual months stay separate. A branch's actual table entry takes precedence. For a gap, the existing caseload backtest compares **last observed** and **recent median** on earlier data using the same forecast horizon. The minimum is four monthly or two annual held-out periods. The estimate uses only observations before the projection date. Separate dialysis work is carried forward from the earlier historical replay. [Scope mappings and alignment](replay_care_options.py) · [Baseline validation design](EXPANSION.md#baseline-evaluation)

All four new estimates align Himalayan Hospital with the October 2026 dates reported elsewhere. Its latest comparable observation is March 2026, so each estimate is seven reporting periods ahead. The recent median had the lower MAE in all four, across 11 matched historical test periods each. Their target-period values remain provisional. The MAE describes the observed errors in the backtests. It is not a prediction interval. [Himalayan clinical material archive](https://hims.srhu.edu.in/mci-information/) · [October Punjab clinical material](https://www.sgrdimsr.in/page-clinical_material.html) · [October Dahod clinical material](https://www.zmchdahod.in/pdf/Clinical_Material_Month_of_October_2026.pdf)

| Himalayan Hospital, October 2026 | Predicted caseload | Last observed MAE | Recent median MAE |
| --- | ---: | ---: | ---: |
| Pediatric outpatient visits | 5,259 | 440.82 | 346.23 |
| Major orthopedic operations | 146 | 37.00 | 20.27 |
| MRI scans | 651 | 137.55 | 111.45 |
| CT scans | 1,317 | 283.91 | 229.86 |

Alignment also recovered existing observations for U. N. Mehta's September 2025 MRI and CT counts and Himalayan Hospital's December 2025 MRI count. The calendar-2025 Lok Nayak casualty total uses the 12 source months. The Odisha comparison adds the FY2019–20 MRI counts for SCB Cuttack and AIIMS Bhubaneswar. These six additions are marked as reported. Another 23 requested site-periods are withheld because the backtests lack enough comparable periods. The JSON carries the status and source basis, and previously estimated gaps keep their forecast and retrieval labels. [Caseload records](care_options_replay.json)

## Capacity provenance and the local comparisons

**`caseload_predicted` and `capacity_predicted` are independent.** `true` marks a model estimate, `false` marks a reported value, and `null` marks an unavailable value. A forecasted count can be paired with reviewed same-period throughput in the same unit. The replay never substitutes hospital bed counts for scanner, clinic or surgery throughput.

- **Tamil Nadu maternity.** Government Lawley Hospital in Coonoor reported 25 CEmONC beds and 66% average occupancy. Government Hospital, Pattukkottai reported 124 beds and 93%. Both are FY2025–26 DMS taluk figures with `capacity_predicted: false`. The planning input compares reported average occupied bed equivalents with the corresponding reported inventory. Delivery counts stay in their own unit. The branches are **294.3 km apart**. The DME sites use distinct clinical tables. [Capacity transcription and row locators](care_replay_maternity.json) · [Tamil Nadu NHM agenda](https://nhm.tn.gov.in/wp-content/uploads/2026/51st_EC-Part-A-Final.pdf)
- **Odisha MRI.** AIIMS Bhubaneswar and SCB Medical College Hospital, Cuttack are **29.5 km apart** by the release coordinates and reported **7,350** and **6,579** MRI examinations in FY2019–20. Both are within the straight-line limits at the hypothetical midpoint. The reports supply neither hospital's comparable throughput capacity for that period, so the ranking is held. SCB's official contact gives PIN 753007 while the release gives 753001. The name and Mangalabag campus support the branch link. CT is excluded from this pair because the SCB CT scope does not establish the same angiography treatment as AIIMS. [SCB investigations](https://scbmch.odisha.gov.in/en/light/sub-subpage/investigation) · [SCB campus contact](https://scbmch.odisha.gov.in/en/light/page/contact-us) · [AIIMS FY2019–20 annual report](https://aiimsbhubaneswar.nic.in/wp-content/uploads/2024/06/8thAIIMS-Annual-Report-2019-2020.pdf)
- **Delhi casualty.** Lok Nayak and Sanjay Gandhi Memorial are **16.6 km apart**. They recorded **217,349** and **432,564** all-cause casualty attendances in calendar 2025. This establishes a comparable historical workload. The routine policy excludes emergency reranking. [Lok Nayak ED census](https://lnjp.delhi.gov.in/lnjp/annual-report-opd-jan-2025-dec-2025) · [Sanjay Gandhi Memorial statistics](https://sgmh.delhi.gov.in/sgmh/hospital-statistics)
- **Bengaluru imaging.** Whitefield and Vydehi are less than 1 km apart on the released coordinates. Whitefield publishes fiscal-year counts, while Vydehi's public average daily scans have no dated observation window. The pair therefore stays outside the synchronized replay. [Whitefield annual report](https://whitefield.sssihms.org/about-hospital/annual-report-2025-26/) · [Vydehi clinical material](https://vims.ac.in/education/wp-content/uploads/2022/12/Details-of-clinical-material-in-the-hospital.pdf)
- **Prior dialysis.** The combined receipt carries forward the five March 2025 observed session counts with `caseload_predicted: false` and the five modeled session-throughput denominators with `capacity_predicted: true`. The older FY2025–26 caseload estimates are listed separately because they have no corresponding capacity for that fiscal year. [Throughput model and its limits](../LOAD_BALANCING.md#historical-planning-data-and-predictions) · [Prior replay](load_planning_replay.json)

## How to interpret and reproduce the ranking audit

The audit uses the same November 22 facility release and checks its SHA-256 before joining branch IDs and coordinates. Within a cohort, it compares nearest eligible with the existing planning policy. Both use a 50 km straight-line search radius. The planning score accepts at most 10 additional straight-line km and gives travel and workload equal weight. Facility-coordinate origins are sensitive to the locations sampled. The nearest pair's coordinate midpoint is included when the pair can fit inside the search radius. It is a hypothetical test point, with no implied road route or patient origin. Historical workload and reported occupancy describe their source period. [Existing ranking policy](../LOAD_BALANCING.md) · [Per-origin order and status](care_options_replay.json)

The JSON keeps initial order, any supported planning order and each moved branch's previous and new ranks. It also shows how many branches contributed caseload or capacity, and the reason a group or target was held. A targeted test exercises a rank swap on a miniature fixture with both prediction flags and checks that incompatible capacity units are rejected. The fixture has no bearing on the real-data counts.

Run from the repository root with the project's Python dependencies installed.

```sh
python3 non-ml/caseload/replay_care_options.py \
  --facilities "/path/to/facilities_newest_capacity.csv" \
  --output non-ml/caseload/care_options_replay.json
PYTHONPATH=non-ml/src python3 -m unittest discover -s non-ml/tests
python3 -m unittest discover -s non-ml/caseload -p 'test_*.py'
```

The script can also take `--capacity` with a reviewed JSON list of throughput rows. Each row must match an existing usable caseload's `facility_id`, `series_id`, `period_start`, `period_end`, and `unit`. Supply a positive `capacity`, an explicit Boolean `capacity_predicted`, and `capacity_source` with `title`, `url`, and `locator`. For a prediction, supply `capacity_method`. Each record describes throughput in the caseload's unit for its whole reporting period. The checked replay adds no new capacity predictions. [Notebook view](../notebooks/08_load_policy_scenarios.ipynb)
