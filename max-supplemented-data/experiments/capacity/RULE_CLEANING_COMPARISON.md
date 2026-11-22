# Rule-based supplementation and capacity prediction

**Newest-report selection with NFHS socioeconomic features had the lowest observed test MAE, 134.99 beds.** Both averaging rules tied it because the same dated inputs yielded the same selected numbers. The original FDR control averaged 136.41 beds. The difference of 1.42 beds had a paired 95% interval from 5.58 beds of improvement to 2.67 beds of worsening. We selected **publisher ranking plus district population** on validation before reading this round's test scores. That model generated the final export. It averaged 136.44 beds of absolute error on the shared test.

## 1. Fix and replay the insurer scraper

The [separate fidelity review](../verification/scraper-audit/README.md) found that IFFCO printed `04` doctors for Suraksha Hospital in Belagavi. The extractor's integer rule started with a nonzero digit. It now accepts decimal digit strings and keeps positive counts, so `04` becomes 4 and zero remains outside the positive-count extract. We replayed it on the same saved HTML and diffed all 7,380 rows against the earlier output. Exactly one cell changed, for source record `8900080583832:4271`. Positive doctor cells went from 6,458 to 6,459. The branch join emitted the same 1,426 count claims byte for byte at the CSV row level. The recovered source record remains in the corrected IFFCO table without an approved exact FDR branch match. [Extractor](../../../scripts/extract_iffco_capacity.py) · [Printed row](https://www.iffcotokio.co.in/health-insurance/network-hospital-quality-parameters#:~:text=8900080583832)

The corrected match replay used the existing IFFCO 2025 and SBI 2022 whole-hospital disclosures, Delhi's approved bed strength, and Kerala's sanctioned capacity. Delhi and Kerala remain scope-specific. The previous 12-case audit supplied six bed and six doctor holds because all 16 insurer values were copied correctly and the numerical or scope disputes remained. [Count sources and matching](../../../data/recovery/capacity-evidence/README.md) · [Earlier review](../../SUPPLEMENTATION_AND_VERIFICATION_HISTORY.md#8-we-reviewed-12-more-cases-for-scraping-errors)

## 2. Check stronger branch evidence in In The Loop

We targeted official operator pages and government hospital charters that could provide exact whole-branch observations. Codex recorded nine document reviews in the link-only app and checked its exported decisions. Three fields were supported and six remained unresolved. The sample was targeted toward finding usable evidence and does not estimate a national verification rate. [Evidence ledger](../../field-reviews/rule-cleaning-source-review.json) · [In The Loop export](../../local/rule-cleaning-v1/verification/reviewed/branch-count-review-reviewed.csv) · [Completion screenshot](../../local/rule-cleaning-v1/verification/reviewed/in-the-loop-completed.png)

| Claim inspected | Decision and reason |
| --- | --- |
| MGM Healthcare, beds | Operator reports **400** for the Chennai hospital. It identifies ICU beds separately. |
| MGM Healthcare, doctors | `250+` is a lower bound. |
| Paras Hospital, Panchkula, beds | Operator reports **284** for the Sector 22 branch. |
| Paras Hospital, Panchkula, doctors | `68+ experienced doctors` gives a minimum and a staffing descriptor. |
| Jorhat Medical College and Hospital, beds | The charter gives 952 and the official About page gives 964. Their exact observation periods could not be aligned. |
| Jorhat Medical College and Hospital, doctors | The hospital charter explicitly reports **165**. |
| Tinsukia Medical College, beds | Charter's `400+` is a lower bound. |
| Tinsukia Medical College, doctors | Charter's 173 excludes junior residents and medical officers. |
| Silchar Medical College and Hospital, beds | The public page's stated total of 1,350 differs from the sum of its stated components, 1,352. |

The three supported values are exact documentary counts with unspecified observation dates. Their registered reviews carry the source URL, branch PIN and definition. We leave the six other selected fields blank. Jorhat's supported doctor count remains usable alongside an unresolved bed field. [MGM](https://mgmhealthcare.in/about/) · [Paras Panchkula](https://www.parashospitals.com/panchkula) · [Jorhat charter](https://jorhatmch.assam.gov.in/sites/default/files/public_utility/CITIZEN%20CHARTER%20OF%20JMCH.pdf#page=1) · [Tinsukia charter](https://tinsukiamch.assam.gov.in/sites/default/files/public_utility/Citizen%20Charter_TMCH.pdf#page=1) · [Silchar hospital statistics](https://silchar-mch.assam.gov.in/department-highlights-level-three/hospital-beds/statistics/hospital)

## 3. Compare four deterministic source rules

Every rule preserves the original record and keeps the evidence history. Location holds and direct review decisions take precedence. Eligible alternatives refer to a linked branch and whole-facility total. Reports from an insurer already cited by FDR still appear in the dispute history. They are kept outside the independent candidates used for source selection. When multiple totals remain eligible, the following rules apply. The 48-case pilot supplies stored publisher agreement scores for the ranked rule. The cleaning stage reads those descriptive scores and does not load or score any fitted selector. [Cleaning code](../../src/build_dataset.py) · [Publisher pilot](../../iterations/02-extension.md)

| Rule | Choice |
| --- | --- |
| Publisher ranked | Prefer the highest-ranked eligible publisher. Resolve ties using observation date. The stored order is FDR, IFFCO, then SBI for this pilot. |
| Newest report | Prefer the newest eligible dated report. A cross-publisher date tie uses publisher rank. A contradictory same-day duplicate within a publisher goes to review. |
| Same-year average | Use eligible reports in the newest calendar year. Keep the latest figure per publisher family and round the equally weighted mean to the nearest whole count, with half rounded up. |
| Two-year average | Use the same averaging rule with an inclusive window from the newest eligible observation back two calendar years. Use the last calendar day if a leap-day anniversary does not exist. |

Date-based rules use a single eligible date when only one family qualifies. They fall back to the ranked candidate when all candidates are undated. The original FDR observation date remains unknown unless a reviewed source supplies it. Suspected unit counts, licensed beds, approximate claims and staffing subsets cannot serve as generic whole-facility totals. The local ledger preserves those distinctions, and any synthesized mean has a distinct method and contributor list.

The average rules produced **zero multi-publisher means** on this extract. IFFCO's eligible whole-facility figures date to March 2025 and SBI's to March 2022, three years apart. Direct reviews take priority and supply no independently comparable dated pair within either window. The three date-based rules therefore selected identical values and produced identical fitted predictions for each matched feature tier. Two same-publisher same-date conflicts were withheld under the date-based rules. On a synthetic boundary check, two publishers exactly two calendar years apart averaged correctly and a report one day older was excluded.

Publisher ranking yielded 2,708 nonmissing bed counts and 3,699 doctor counts on 10,073 linked branches before model estimation and the final target-defect hold. Each date-based rule yielded 2,707 and 3,698. Compared with ranking, the date-based rules changed 178 bed fields and 231 doctor fields, including the two added abstentions. This experiment compares ranking with recency on the available evidence. The distinct benefit of taking an average needs comparable dates and independent publishers in the selected window.

## 4. Fit and score the 16 cleaned datasets and original-data control

Each rule had four projections of the same facility data. The facility inputs include the policy's selected doctor count, coordinates cleared by the existing identity audit, organization categories and structured website and social metadata. The enriched arms add NFHS-5 district indicators, log district population, or both. The population file is the teammate's population total in confirmed NFHS row order. We reproduced the state–district key order against the cached NFHS response and rejoined by keys. Population lacks an area denominator for density and has unresolved vintage and boundary questions. On test, 275 branches have NFHS inputs and 272 have population. The models retain all eligible test rows and use XGBoost's numeric missing-value handling. [Population revision](https://github.com/augustchu/datathon-F26/blob/d6bb601d61882d8f42e200793cd11d629a9f7fe0/data/population/nfhs_population_only.csv) · [Input assembly](prepare_data.py)

The original-data control uses reported FDR bed and doctor counts and the same facility features and partitioning rules, with basic parsing and existing safety checks shared. Source supplementation and district enrichment are applied in their own arms. Across all comparisons, we exclude target-like description text and model-generated counts from predictors. We use 1,608 training labels per cleaned arm and 1,417 for the control. Jorhat's original bed count was removed from the earlier test of 456 under the new explicit hold. All 17 runs use the remaining **455 original test labels**, the same **213 validation** branches and **213 interval-calibration** branches. We use validation MAE to choose stopping rounds, the objective and depth, and the final cleaned release. The test comparison reports performance for these frozen choices. The test cohort has previously been used in the project, so an independent future audit should use fresh labels. [Fitting code](train_evaluate.py) · [Input and split receipt](../../local/rule-cleaning-v2/inputs/input_audit.json)

### Test MAE in beds

| Source rule | Facility only | Socioeconomic | Population | Both |
| --- | ---: | ---: | ---: | ---: |
| Publisher ranked | 138.65 | 137.37 | 136.44 | 139.04 |
| Newest report | 136.69 | **134.99** | 137.47 | 136.69 |
| Same-year average | 136.69 | **134.99** | 137.47 | 136.69 |
| Two-year average | 136.69 | **134.99** | 137.47 | 136.69 |
| Original FDR control | 136.41 | — | — | — |

### Validation error, large errors and bias

MAE weights each bed of error equally. RMSE gives more weight to large misses. Bias is the average prediction minus the reported value. Each of the three date-based rules has the scores listed in the shared rows below. All values are in beds.

| Source rule | Features | Validation MAE | Test RMSE | Test bias |
| --- | --- | ---: | ---: | ---: |
| Publisher ranked | Facility only | 129.76 | 319.19 | −90.94 |
| Publisher ranked | Socioeconomic | 132.91 | 323.08 | −95.20 |
| Publisher ranked | Population | **129.09** | 316.89 | −91.41 |
| Publisher ranked | Both | 131.64 | 325.81 | −95.67 |
| Date-based rules | Facility only | 129.58 | 319.28 | −93.48 |
| Date-based rules | Socioeconomic | 131.41 | 321.35 | −95.23 |
| Date-based rules | Population | 130.38 | 321.12 | −93.95 |
| Date-based rules | Both | 132.30 | 326.39 | −98.69 |
| Original FDR control | Facility only | 129.97 | **313.21** | −86.05 |

The winning test combination improved over ranked + socioeconomic by **2.37 beds**, with a paired district-bootstrap 95% interval of **0.44 to 4.82 beds of improvement**. Its interval against the original-data control includes zero. The original control has the lowest test RMSE of every arm, 313.21 beds, versus 321.35 for the lowest-test-MAE cleaned fit. Publisher ranking plus population led validation at **129.09 beds**. It had 90.5% coverage of its nominal 90% interval on the reported test cohort and a median interval width of 459 beds. At facilities with more than 200 reported beds, that same model averaged **465.05 beds** of absolute error, underpredicted by **444.87 beds** on average, and covered **58%** of observations. Neither added feature block consistently improved performance across source rules, and adding both caused higher MAE than the best single addition for each rule. These are exploratory paired comparisons. Calibration and error on reported facilities can differ from those for facilities with genuinely missing values. [All 17 fits, rounds, errors and 56 paired contrasts](../../local/rule-cleaning-v2/training/metrics.json)

### Reproduce the data comparison

The [source replay](../../../data/recovery/capacity-evidence/README.md#replay) lists the archived inputs to the scraper and matcher. Use identical source snapshots across arms. Apply the original iteration 02 reviewed sample and descriptive rank report to each data-build call. Combine the [12 earlier reviews](../../field-reviews/scrape-audit-12.json) and [nine new reviews](../../field-reviews/rule-cleaning-source-review.json) in one JSON list for `--field-reviews`. Write each run to its own directory, named `iteration-01` through `iteration-04`, with `dataset/` as the builder output. Use `--count-policy ranked`, `latest_year_mean`, `newest`, and `recent_two_year_mean` respectively. Use `prepare_data.py` with all four iteration flags and the same NFHS/population inputs from the [experiment replay](../README.md#replay). Run `train_evaluate.py --rule-cleaning` and `publish_rule_comparison.py --run <run> --contact-evidence <snapshot>/contact_evidence.jsonl`. The experiment stores the inputs, all 17 private feature projections, 17 boosters and encoders, per-round tuning histories, test predictions and release checksums. The first nine-model comparison is archived separately as `local/rule-cleaning-v1`.

## 5. Workbook, verification and pipeline handoff

The workbook contains 10,088 original source rows, with selected and original counts side by side. The selected validation model filled **6,829 eligible branches**, which represent 6,833 rows after reattaching duplicate source records. The overlay also validated through the same `load_estimates` function used by notebook 05. Unsupported branch/field holds stay blank. Model intervals and model identity travel with all estimates. [Verification table](../../local/rule-cleaning-v2/release/facilities_with_verification.csv) · [Notebook 05 overlay](../../local/rule-cleaning-v2/release/load_distribution_capacity_overlay.csv) · [Release audit](../../local/rule-cleaning-v2/release/release_audit.json)

The `verification` column says `confident` when at least one populated reported count has a reviewed source or corroborating comparable external evidence and every populated reported count passes that criterion. Other statuses distinguish partial evidence, disagreement, unresolved review, reported-only fields, estimates and missing values. Bed and doctor verification are visible separately. Fourteen original rows are fully `confident` under the count rule. There are 125 corroborated or directly reviewed bed values and 13 such doctor values. Source evidence confirms the reporting claim, and unknown dates stay marked by blank observation cells.

The facility score uses **70 × verified important fields / 5 + 30 × populated important fields / 5**. Five equally weighted fields are evaluated. A routable branch with a name and PIN gets verification credit from an exact matched count source or a linked review, with the coordinates retaining their separate audit. Phone credit requires the same published number in two linked non-FDR domains. Services receive credit from a corroborated or directly approved service claim. Beds and doctors receive credit from the corresponding `confident` field status. Model estimates increase completeness only. The score describes evidence and data availability and has no probability interpretation. The workbook preserves both underlying field counts so the formula is reproducible. [Score and release code](publish_rule_comparison.py)

The import file uses one row per canonical branch and the `capacity`, `ml`, `beds`, point, lower, upper, provenance and detail columns expected by the existing loader. The load-distribution rank uses occupancy or a separately labeled scenario load. A capacity estimate provides facility scale and requires a separate load input for a crowding estimate. The prepared file is ready for staging with the team, with live recommendations and load ranking outside this experiment. [Notebook 05 import](../../../non-ml/notebooks/05_field_updates.ipynb) · [Load-ranking implementation](../../../dashboard/dashboard.py)

## 6. Feedforward follow-up

The follow-up tested whether blending outputs from the source rules improved the population-tier models above. The development-selected fixed blend scored 136.81 beds MAE and the feedforward blend scored 136.96 on the same 455 held-out facilities. The best population-tier individual rule scored 136.44. The release above keeps its original XGBoost estimates. [Model decision with screenshots](CAPACITY_MODEL_DECISION.md) · [Feedforward run and evaluation](FEEDFORWARD_EVALUATION.md) · [Feedforward ensemble notebook](../../../ML/notebooks/Source%20Rule%20Feedforward%20Ensemble.ipynb)
