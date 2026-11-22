# Project context and complete reviewed Git history

**Review conclusion:** the strongest next steps improve the evidence behind missing values, establish where estimates generalize, and acquire new facts selectively. The existing models and evidence pipeline provide a substantial foundation, but source coverage, independent missing-label evaluation, and integration into the displayed recommendation are the key unresolved issues.

Review date: **21 November 2026, America/New_York**. Repository: [augustchu/datathon-F26](https://github.com/augustchu/datathon-F26). Reviewed GitHub head: [`f2a1e0ebf447471c515d42679a7d983a8ef0fae0`](https://github.com/augustchu/datathon-F26/commit/f2a1e0ebf447471c515d42679a7d983a8ef0fae0).

## Review scope and evidence status

The review covered all **24 commits** reachable from the observed remote branch, including merge commits and two recent dashboard updates. The remote branch listing showed only `main`; remote information was fetched and the local checkout was fast-forwarded before preparing these documents. The cutoff is the head above, not an assertion that future teammate changes have been reviewed.

The prior project chat, **“Review Databricks marketplace listing,”** was read across all six returned history pages, covering 54 turns. This recovered the original pasted competition prompt, your later scope changes, and reported outcomes. The review also included the pasted FDR/PIN/NFHS supplement, repository documentation, substantive code and notebook outputs, action logs, source manifests, the backup summary, and ongoing local model work. Large raw-data additions and saved model weights were inventoried; this task did not independently revalidate every source row or rerun the experiments.

The original challenge export remains private. No private raw rows, credentials or full chat transcript are added here. Project measurements below are **reported results from reviewed artifacts**, not newly executed experiments. The latest XGBoost, all-column, description-recovery and W&B changes were uncommitted at the review cutoff and remain separate from this documentation change. Their metrics are recorded here so the proposal remains intelligible even before that work is published.

## The original competition goal

The user-supplied **CMU Datathon — Fall 2026** brief asks:

> A patient needs a specific kind of care. Where should they go, and why should they trust that answer?

The required input is a **service** and a **location**. The output is the best facility option or options, with evidence explaining why each was chosen. Examples include dialysis, emergency C-section, NICU, cardiac surgery, MRI and burn care; origins may be a city, district, PIN or coordinates. The intended audience can include a community health worker, NGO coordinator or district planner.

The brief explicitly identifies merged facilities, contradictory locations and unsupported service claims as central challenges. Every recommendation must explain **why this facility** and **how the system decided**. It permits an app, dashboard, notebook, agent, Genie space or combination; the explanation and audience fit matter more than a particular interface.

| Explicit submission requirement | Consequence for next work |
| --- | --- |
| Run on Databricks Free Edition | Verify a real end-to-end run and save its configuration/output; local success is insufficient. |
| Use the provided facility dataset | Keep Virtue Foundation facility records as the backbone and link supplemental claims to it. |
| Support a clear nontechnical workflow | Show a simple service/place request with understandable reasons and uncertainty. |
| Cite underlying facility text for important claims, scores and rankings | Preserve attributable passages and distinguish modeled estimates from source statements. Explain score/model logic separately. |
| Communicate uncertainty rather than weak evidence as fact | Retain conflicts, branch holds, abstention, date/scope limitations and uncertainty on estimates. |

The brief describes about 10,000 facilities and 51 attributes. Its published field coverage is description 100%, capability 99.7%, procedure 92.5%, equipment 77.0%, year established 47.8%, doctors 36.4%, and capacity 25.2%. These are the brief's figures, not independently audited correctness or a replacement for snapshot-specific accepted-value counts.

The supplied supplement describes FDR as public-source ingestion, GenAI extraction and entity consolidation. It warns that PIN rows are **post offices**, so a raw PIN join can duplicate facilities and ambiguous district/state mappings need review. NFHS-5 is historical district context from 2019–2021: suppressed values remain missing, parenthesized small-sample estimates retain flags, and district context must not be presented as a facility measurement. The supplement suggests access/planning maps, but those are optional use cases rather than additional mandatory submission criteria. No unverified claim about a later NFHS release is used in these proposals.

## Your direction, in context

Your requests evolved, and the later instructions matter when choosing next work:

1. **Begin with capacity, then broaden missing-field prediction.** You first requested a random forest, narrowed its predictors to fields after `countries`, and later explicitly requested all source columns for comparison. The original restricted model remains a baseline; the later broader experiments are not constrained by that original boundary. Capacity was a starting target, not the entire intended product.
2. **Compare interpretable and flexible methods.** You requested thorough EDA, literature-informed model design, interpretable regression and ML alternatives. The three recommendation modes are documented-data/confirmed, ML-assisted and regression-assisted. Classical regression still learns coefficients; fixed rules form the no-training mode.
3. **Use sparse records without cascading estimates.** You first considered complete observed predictors for each target, then requested explicit missing handling and XGBoost. The persistent concern is to avoid feeding one uncertain estimated field into the next model as if observed. Compare missing-data handling empirically, as the flags experiment already does.
4. **Recover stated values before inventing estimates.** The later request to populate capacity from descriptions and rerun models motivates source-grounded recovery. The subsequent request for a full report, EDA/output screenshots and comprehensive W&B tracking is ongoing work in the other chat, not completed by this planning task.
5. **Keep recommendations grounded.** Require service verification, exact branches, cited phone numbers, location flags computed before query time, TravelTime-based travel choices, and reasons for ranking. Earlier phone-exclusion preferences remain the safer recommendation for prospective models; the explicitly requested all-column experiment is documented as an exploratory comparison, with phone-excluded ablations retained.
6. **Keep preparation modular and mostly offline.** You asked for inspectable notebooks and stored intermediate results rather than one opaque pipeline. The project reorganized model work under `ML/` and the modular evidence/statistical workflow under `non-ml/`.
7. **Allow feedback and review.** You requested negative call responses to replenish the shortlist, a newest-first moderation stack, and a pre-deployment Hindi service-verification prototype. A new report is not automatically an accepted source fact.
8. **Make “best” broader than nearest.** You asked about assessments, patient ratings and reviews. The real pilot found insufficient comparable branch-level rating data to change ranking under its rules; that limitation should remain explicit.
9. **Respect team ownership.** You later stated that your teammate would handle load balancing and asked to focus primarily on filling missing columns. The three selected directions honor that division. They can supply better evidence to the teammate without claiming current crowding from bed totals.

The earlier large statistical scraping audit was paused. This document proposes future research; it does not resume that audit or initiate calls.

## What the project currently has

| Component | Evidence and reusable work | Limitation that matters now |
| --- | --- | --- |
| Data and provenance | 10,088 VF source rows, 51 fields; 31,507 saved supplemental source rows; raw snapshots, manifests and cited field origins | Supplemental rows include repeated branches; scraped fields are claims, not verified truth. |
| Branches and counts | 10,073 linked FDR branches, 2,509 accepted exact bed counts and 3,626 accepted exact doctor counts | Missing targets are common and their reporting process may differ from labeled branches. |
| Contacts | 9,749 VF rows with a listed phone; 9,722 with plausibly formatted numbers | Formatting does not establish a working number or the right branch. |
| Location checks | PIN/address/coordinate checks, saved holds, cited Faridkot correction; 839 additional combined-branch holds | Plausible coordinates can still be wrong; unknown or conflicting points require explicit handling. |
| Service evidence | Exact quotes, scope and validity; seven manually checked statements for three independently corroborated branch–service pairs | Saved audit has 6,617 single-publisher, 4,563 insufficient and 10 conflicting pairs. The classifications are not true service prevalence. |
| Public-data linkage | Conservative matching and preserved unresolved records; documented comparison accepted 26 FDR-to-supplemental links | Large registries add little usable branch evidence if they remain unlinked. |
| User workflow | Precomputed lookup, three modes, travel adapter, feedback, moderation and opt-in load scenarios | A complete Free Edition run and actual end-to-end routing behavior remain to be demonstrated. |
| Verification and ratings | Local Hindi IVR prototype, mass-verification runbook, reviewed assessment scopes and branch-review research | No completed live campaign; no authorized comparable rating aggregate set in the snapshot. |

Sources: [project summary](../non-ml/PROJECT_SUMMARY.md), [data comparison](../non-ml/DATA_COMPARISON.md), [location audit](../non-ml/LOCATION_AUDIT.md), [quality method](../non-ml/QUALITY_METHOD.md), [review pilot](../non-ml/PATIENT_REVIEW_PILOT.md), [action log](../ACTION_LOG.md).

## Model evidence: keep the cohorts separate

| Cohort and status at review | Reported result | Correct interpretation |
| --- | --- | --- |
| **522 audited observed-count branches**, documented in committed comparison | Median MAE 150.57 beds; ridge 141.94; random forest 141.41; facilities-only neural refits average 121.9 across three seeds | Useful internal comparison on a common cohort; previously inspected holdout and missing-target selection remain limitations. Saved-artifact replay has training-overlap concerns. |
| **504 test rows / 503 distinct branches**, separate density-control experiment | Original MLP MAE 3,042.13, driven by a 1,469,024-bed prediction; bounded preprocessing replay MAE 126.48 | A diagnosed extrapolation was removed. This retrospective test repair is not independent confirmation of generalization. Actual density gains remain unmeasured. |
| **467 audited observed-count branches**, newer uncommitted XGBoost work | Median MAE 156.29; core boosted forest 123.00 with flags / 123.14 without; masked phone-excluded 48-field forest 112.25 | Wider fields show signal on this cohort. These scores cannot be ranked directly against the 522- or 504-case results. |
| Same 467-branch exploratory cohort | Flags helped only 3 of 10 additional paired seeds; seed-42 paired MAE difference about −0.15 beds, interval −1.30 to +1.01 | No consistent benefit from extra numeric flags in this experiment. Native missing-value handling remains present. |
| Text evidence audit and recovery, uncommitted | Matching capacity numbers appear in raw text for 332/467 test branches; 12 previously blank counts accepted from descriptions, nine in training | Direct extraction and statistical inference need separate evaluation. Recovered labels and structured values share a source; retraining gains were inconsistent. |

The masked wide model's result is not proof that all leakage disappeared: phrases can be missed and source identifiers recur across partitions. The current model report also notes long-tail errors and broad intervals, including a median nominal 90% interval width of 653 beds in the earlier ridge/forest comparison. Independently recovered **originally missing** labels remain the central evaluation gap. See the [model evaluation](../ML/MODEL_EVALUATION.md), [count comparison notebook](../non-ml/notebooks/09_count_model_comparison.ipynb), and [density handoff](../ML/models%20with%20population%20density/CLAUDE_HANDOFF.md).

## Integration and feasibility findings

The two newest dashboard commits materially improve the app: it now accepts original facility exports, loads saved legacy base/enriched networks, displays tagged missing-bed estimates and produces a notebook-05-compatible overlay. It is no longer accurate to describe the dashboard as synthetic-data-only.

It still uses independent keyword and related-service matching rather than the prepared `non-ml` service claims and location holds. The legacy model adapter applies heuristic input/output guards rather than calibrated intervals. Unknown-load handling in mixed-coverage lists and emergency behavior also differ from the stricter notebook logic. These are reasons to connect the dashboard to one shared recommendation backend before presenting improvements as end-to-end behavior. [Dashboard](../dashboard/dashboard.py) · [capacity adapter](../dashboard/capacity_model.py) · [prepared lookup](../non-ml/src/facility_tool/lookup.py)

The project summary records that the real evidence/count/lookup analyses ran locally on authorized exports; a complete Free Edition execution remains outstanding. A historic trial H100 failure should not be treated as proof about today's Free Edition entitlements. The initial pilots should work with modest compute and verified dependencies. There are no real service-specific slot/load measurements or measured patient outcome gains in the reviewed work.

## All reviewed commits

All commits below are dated **21 November 2026**. The order follows the reviewed history, including merges. Each link opens the exact commit; the notes describe substantive changes rather than repeating every filename.

| Commit | Recorded change | Implication for next work |
| --- | --- | --- |
| [6f889b5](https://github.com/augustchu/datathon-F26/commit/6f889b5a27f8f31aa1ee7dd65ad70faa697adfa4) | Initial one-line README. | Repository starting point. |
| [13bb976](https://github.com/augustchu/datathon-F26/commit/13bb9768b205e30b06e72324633d77f61452f6e2) | Created placeholder `dataCleaning`. | No implemented cleaning workflow in this initial file. |
| [bc26c25](https://github.com/augustchu/datathon-F26/commit/bc26c257f86f0b315a8b63ffd2c8b4428f88a576) | Added `.python-version` to ignore rules. | Environment housekeeping. |
| [22a534c](https://github.com/augustchu/datathon-F26/commit/22a534c391f898f7e79cdc9b799234128064b3d6) | Added Python version and a four-line dashboard skeleton. | Early UI scaffold. |
| [101c923](https://github.com/augustchu/datathon-F26/commit/101c9235fd36b88149548fa2f7c162d9c55f8247) | Merged teammate main changes. | Preserves both early lines of work. |
| [06b370c](https://github.com/augustchu/datathon-F26/commit/06b370c4a2722d91d434555a4fe5acd3973b6399) | Added source scrapers, raw public snapshots/manifests, focused supplemental/government exports, action log, preliminary verification artifacts, and standalone capacity random forest notebook/module/tests. | Establishes substantial data/provenance assets, but bounded NABH coverage and paused statistical audit remain explicit. Model respects predictors strictly after `countries`, target excluded. |
| [9168412](https://github.com/augustchu/datathon-F26/commit/91684123f7c0301310f13ba64316167b252d5fb3) | Added Databricks facility-only/enriched GPU capacity training, interpretable models and NFHS enrichment notebooks. | Introduced parallel ML approaches and district context; later common-split evaluation is essential. |
| [8735c9d](https://github.com/augustchu/datathon-F26/commit/8735c9dd58412d8955627f06b3e2c9a935c59adc) | Implemented 495-line Streamlit dashboard with service taxonomy, location entry, synthetic/CSV data, scoring and optional occupancy-based balancing. | UI exists, but its policy logic is still independent of the stricter evidence pipeline. |
| [cb6ac1d](https://github.com/augustchu/datathon-F26/commit/cb6ac1da400b4d9465209e1a7ac72cde517c44be) | Merged dashboard and other main work. | Integrates files, not necessarily the underlying recommendation logic. |
| [10673ea](https://github.com/augustchu/datathon-F26/commit/10673eaffbf6a7d9d8a5c67062e4f6d6e7e82139) | Moved earlier model work into `ML/`; added modular non-ML source/geographic/count/service evidence notebooks, checkpoint store, three recommendation modes, travel adapter, opt-in load scenarios and moderation. | Core decision architecture, provenance and abstention are now reusable. |
| [e31f035](https://github.com/augustchu/datathon-F26/commit/e31f035e2bacaaf0739c3e1e03a0479fbcc750aa) | Added precomputed lookup snapshots and origin index; documented challenge versus supplemental data; prepared evidence/contact/location decisions before search. | A feasible fast interface can consume stored query assets rather than recompute ingestion. |
| [67a4464](https://github.com/augustchu/datathon-F26/commit/67a4464f321dcce9392e9d8efb11c0aac38dd5e2) | Added real-data count audit/fitting, primary-source branch/service verification, reviewed claims, recovered contacts and evaluated recommendation modes; updated travel fields. | Real evidence and model comparisons exist, with limited corroboration and internal-holdout caveats. |
| [193415c](https://github.com/augustchu/datathon-F26/commit/193415c9e618c1d7f75dae14391fbd54e14e3665) | Added local Hindi IVR state machine, preview/TwiML and tests for branch gating, service answers and optional recording. | Enables a verification pilot design; no real calls were placed. |
| [6ce3f1a](https://github.com/augustchu/datathon-F26/commit/6ce3f1a2019f7cf6c6a6f3d2b188a6bbc7495253) | Added base/enriched saved neural preprocessors, metadata and weights. | Artifact scores later found potentially overlapping the shared test cohort. |
| [a0e4aa6](https://github.com/augustchu/datathon-F26/commit/a0e4aa6b1dde5ecb7b1ef0a559bb0f317b11b34a) | Added mass-verification runbook and README link. | Operational plan and costing exist; live dispatch/callback/result import remain unimplemented. |
| [ecf58de](https://github.com/augustchu/datathon-F26/commit/ecf58de708bf5e20c04576441e5000210c4b97ce) | Added assessment evidence, optional branch-level patient-experience input and bounded ranking credit; new quality/patient-feedback modules and notebooks. | Supports transparent multicriteria ranking but constants have no clinical validation and comparable real ratings are absent. |
| [0cb61fa](https://github.com/augustchu/datathon-F26/commit/0cb61fa7df7ba4922c5e12e55bfc9fca4f928751) | Documented branch-rating research and real versus fictional reranking pilot. | Real ratings did not qualify; fictional sensitivity tests must not be presented as measured patient experience. |
| [d443323](https://github.com/augustchu/datathon-F26/commit/d4433231c0163d795179f65b8b86c5f796523376) | Added cited location holds and stronger state/district/PIN/shared-point checks before lookup/routing. | Material safety/quality gain: 839 extra branches held; erroneous Faridkot point removed from Delhi candidates. |
| [ae25e60](https://github.com/augustchu/datathon-F26/commit/ae25e603a20e8f7b35a106f3bb3af074d5363933) | Updated saved base/enriched neural weights and committed notebook output. | Second artifact revision, still descriptive because source training memberships were not recorded. |
| [64bcbff](https://github.com/augustchu/datathon-F26/commit/64bcbffdf854a7ea0ad7d53c13b929288ddb2b7a) | Added population-density/NFHS experiment notebooks, shared trainer/data loader/handoff and W&B model artifacts; documented common 522-facility refit comparison. | Reproducible models and ablation architecture; actual density data still missing. |
| [af6238e](https://github.com/augustchu/datathon-F26/commit/af6238e6f9031025e1bae2a2b0ea8b92768478e7) | Added facilities-only control, shared holdout fingerprint and control results for density experiment. | Essential ablation control revealed one extreme extrapolation rather than claiming general benefit. |
| [ec15b98](https://github.com/augustchu/datathon-F26/commit/ec15b983dc37c1bdab53474e42ae17762956e580) | Replaced unbounded scaled numeric inputs with training-fitted bounded quantile transforms; kept indicators 0/1; tested behavior and serialization. | Removed inspected million-bed extrapolation; retrospective rerun is not independent generalization evidence. |
| [0cdec29](https://github.com/augustchu/datathon-F26/commit/0cdec297ec7d280db32a39476a766ba2b0c72287) | Added `dashboard/capacity_model.py`, NumPy inference bridge for saved legacy PyTorch artifacts, guarded missing-capacity overlays, original-table export input, capacity estimator, contact/hour/map display, and explicit keyword-match labeling. | UI now consumes real exports and model artifacts, but remains separate from prepared evidence/location rules; legacy model guards are heuristic and no intervals are supplied. |
| [f2a1e0e](https://github.com/augustchu/datathon-F26/commit/f2a1e0ebf447471c515d42679a7d983a8ef0fae0) | Updated map/results sizing, responsive metric/card CSS, numbered marker layout, side-by-side results and map with a scrollable result pane. | Improves demonstration layout; does not integrate the evidence backend or change validation coverage. |


## Why this history leads to the selected directions

The history shows a progression from data collection and capacity modeling to exact branch evidence, location holds, three-mode recommendations, verification and better model comparisons. Its most informative results include negative findings: ratings did not qualify, NFHS gains were inconsistent, explicit missingness flags did not reliably help, and a large prediction failure required a preprocessing repair.

The next steps should preserve that discipline. [Source recovery](01-source-grounded-field-recovery/README.md) expands facts that can be cited; [validated imputation](02-validated-missing-field-models/README.md) tests estimates where they will actually be used; [targeted verification](03-targeted-verification/README.md) supplies new evidence and independent labels. The [brainstorm](BRAINSTORM.md) also records alternatives, dependencies and reasons to defer them.
