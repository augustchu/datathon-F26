# Baseline caseload coverage in the facility dataset

The [expanded audit](EXPANSION.md) adds exact-site published activity and new estimates across a broader range of services and recent periods.

The **FY2025–26** audit supports a baseline estimate for **five distinct project facilities**, all for dialysis. The estimates are saved in [baseline_coverage.json](baseline_coverage.json) by facility ID, care option, and period. This is an offline file for caseload analysis. Each value estimates sessions during a missing reporting period using the same site's earlier programme activity.

The audit uses the **November 22, 2026 final capacity release**, which includes service evidence recovered after the base capacity export. It contains 10,088 source rows, of which four lack a facility ID and 11 repeat an existing ID. There are 10,073 distinct linked facility IDs, including 9,574 with a routing-eligible row. The output records the SHA-256 of the exact input file, which matches the release delivery receipt. The facility release has no service-by-period caseload field. Missingness for the audit therefore comes from the scoped caseload manifests and their observed totals. [Facility release report](../../max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md)

## Counts by Care needed

The first two columns count distinct routing-eligible facilities in the release with a canonical `documented_services` entry. The verified column counts sites whose release service cell has a `verified_*` grade. This field is a limited inventory of source evidence. It can omit offerings that have evidence in another source. The five PMNDP sites appear in the **baseline fills** column because their named programme reports establish dialysis activity for the exact branch. None of those five has a dialysis entry in this release's service field.

| Care needed | Documented in release | Verified service cell in release | Baseline fills |
| --- | ---: | ---: | ---: |
| Dialysis | 7 | 2 | **5** |
| Emergency C-section | 0 | 0 | 0 |
| Maternity / delivery | 0 | 0 | 0 |
| NICU | 4 | 1 | 0 |
| Pediatric care | 0 | 0 | 0 |
| Cardiac surgery | 1 | 1 | 0 |
| Cardiology consultation | 0 | 0 | 0 |
| MRI | 5 | 0 | 0 |
| CT scan | 0 | 0 | 0 |
| Burn care | 0 | 0 | 0 |
| Orthopedic surgery | 0 | 0 | 0 |
| Fracture care | 0 | 0 | 0 |
| Dental care | 0 | 0 | 0 |
| Oral surgery | 0 | 0 | 0 |
| Cataract surgery | 0 | 0 | 0 |
| Stroke care | 0 | 0 | 0 |
| Chemotherapy | 0 | 0 | 0 |
| Trauma / emergency | 0 | 0 | 0 |

The 22 release entries for blood banks are outside the dashboard's 18 Care needed options. The service fields and programme reports cover different sites and dates. The full number of facilities that offer each service is unknown, so the table gives **counts**, without inferring a service-specific coverage rate from the release. [Care needed taxonomy](../../dashboard/dashboard.py#L98) · [Release evidence definitions](../../max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md#2-calculate-confidence-from-five-important-cells)

## Apply the prior-year dialysis average

The selected baseline carries forward the latest whole fiscal year's PMNDP sessions divided by 12. It had **45.85 sessions/month mean absolute error** in 23 forward-tested centres, compared with **59.95** for XGBoost. The test trains on FY2023–24 information and scores FY2024–25. The prior-year method requires an observed count for the same reporting site. The machine-scaled baseline did better than XGBoost for held-out districts, and a machine-based transfer would require a reported functional machine count for a comparable programme site. [Held-out comparisons](XGBOOST.md#results) · [Test results and predictions](xgboost_results.json)

All five fills use the **FY2024–25 PMNDP total** to estimate the FY2025–26 monthly average. For instance, Regional Hospital Bilaspur had 3,078 reported sessions in FY2024–25, yielding **256.50 sessions per month**. This carries forward observed utilisation. The estimate does not depend on a modeled bed count or a March machine inventory remaining stable.

| Facility | FY2024–25 observed sessions | FY2025–26 estimated sessions/month |
| --- | ---: | ---: |
| Regional Hospital Bilaspur | 3,078 | 256.50 |
| Government Medical College Chamba, historical Regional Hospital site | 5,161 | 430.08 |
| Zonal Hospital Dharamshala | 5,570 | 464.17 |
| Regional Hospital Kullu | 3,311 | 275.92 |
| Netaji Subhash Chandra Bose Zonal Hospital, Mandi | 4,624 | 385.33 |

The programme manifest has 24 sites with a comparable FY2024–25 total and no validated FY2025–26 site total in the reviewed programme sources. Five have an exact, reviewed project-ID and PIN link and pass the release location check. The other 19 retain their programme identities and appear in the output with the reason the project-level join is unavailable. Civil Hospital Manali's FY2024–25 figures indicate a start-up year. Its fiscal-year average includes that ramp-up, which would need to be considered if its branch record is linked later. The reference for Chamba identifies the historical branch and reporting period. An identity at a later campus needs its own source. [Himachal programme source and reviewed branch links](hp_dialysis_model_data.json)

The output includes an empirical error band using **112.25 sessions/month**, the 90th-percentile absolute error of this baseline on the 23 forward-test sites, with a floor at zero. The band describes historical errors on a small cohort. It has no independently calibrated coverage for these five estimates. The state reported 48,048 sessions across its 24 units from April through November 2025, which suggests the programme's monthly pace may have risen. The state total supplies neither a complete fiscal year nor site-level shares. [Forward-test metrics](xgboost_results.json) · [Economic Survey, p. 310](https://himachalservices.nic.in/economics/pdf/en-economic_survey_2025-26.pdf)

## Why the other counts are zero

**Maternity / delivery.** In Tamil Nadu, the best baseline multiplied reported CEmONC beds by the median observed deliveries per CEmONC bed among the training facilities of the same type. It scored **36.86 deliveries/month MAE** in the district holdout, compared with **39.76** for XGBoost. The source already has FY2025–26 totals for all 94 comparable centres. The release's general capacity field describes whole-facility beds and includes model estimates. The tested maternity baseline uses beds for comprehensive emergency obstetric and newborn care and the same programme facility classification. [Tamil Nadu source manifest](tn_maternity_model_data.json) · [Tamil Nadu NHM report](https://nhm.tn.gov.in/wp-content/uploads/2026/51st_EC-Part-A-Final.pdf)

**Other 16 options.** The [46 named-site benchmarks](typical_caseload.json) cover all 18 options and include scoped reports for services such as MRI, NICU, chemotherapy and fracture care. They have different reporting units, catchments and periods. The rolling monthly tests check prediction at sites with reported months. Those tests do not measure transfer to project facilities without activity history. The zero counts here mean that the evaluated fiscal-year baselines have no supported application for those options. They are not a count of service availability.

Additional coverage requires either a prior comparable site count or a tested transfer cohort with the same service definition, reporting scope, and equipment or service-unit capacity features available at the intended site. Link the published reporting unit to the exact branch before counting an application. Import a same-period observed count as reported activity if it is recovered later. Keep any forecast in a separate field with the target period and source basis. A service-pressure ratio also requires same-service staffed capacity for that period. [Caseload definitions and integration](README.md#from-a-benchmark-to-load-distribution)

## Reproduce

Run from the repository root with Python 3.10 or newer. The script uses the standard library. It reads the existing test receipt to record the baseline comparison and calculates the new estimates from the reported annual totals.

```sh
python3 non-ml/caseload/baseline_coverage.py \
  --facilities "/path/to/facilities_newest_capacity.csv" \
  --output non-ml/caseload/baseline_coverage.json
python3 -m unittest discover -s non-ml/caseload -p 'test_*.py'
```

Use the final delivery CSV from the [November 22 release](../../max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md) with the service evidence grades. The output records the row and ID denominators, per-service counts, comparison metrics, data-source hashes and the five estimates. Each estimate carries the observed count, the source and table locator, the target year, and the reviewed branch link. The original facility release remains the source for the branch data.
