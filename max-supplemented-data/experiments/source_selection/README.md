# Source-selection model audit

The two saved bed-source selectors chose the same value as the publisher fallback on all **194** unlabeled, conflicting facility records where this comparison could affect a choice. Both also matched the operator-published total on the two independent holdout cases with an adjudicated figure. The paired and independent probability scores favored different checkpoints. The existing publisher fallback remains the best-supported release rule. [Machine-readable metrics](results.json)

## Test design

- **Frozen checkpoint comparison.** Both trained XGBoost artifacts scored the same cases and used the same publisher prior to break ties. The priors came from iteration 02's training evidence. Neither model trained on any branch in the independent challenge. The nine new checks were selected by branch hash before reviewing the evidence, three each for IFFCO only, SBI only and differing dual-insurer claims. Holy Family was added as a separately named earlier review anchor. Of the ten reviewed records, two operator publications supplied a comparable exact whole-facility figure and eight cases remained unresolved. The two source-of-truth pages do not specify an observation date. Exact accuracy here means agreement with the owner-published figure at review, and both dates and scope remain relevant to interpreting differences. [Review manifest and public evidence links](evidence.json) · [Earlier Holy Family screenshot](../../../data/recovery/capacity-evidence/holy-family-bandra-beds.jpg)
- **Paired cross-fit.** Both recipes predicted the five independently supported cases in iteration 01, fitting afresh each time with the entire tested branch removed from the applicable training cohort. The same fold-specific prior from the larger cohort broke ties. One exact value was absent from every candidate claim, which bounds exact selection at four of five. Native group-held-out results, with each checkpoint's own prior and cases, are also available for traceability. They cover different cohorts.
- **Extension-only check.** The frozen iteration 01 checkpoint predicts the two supported cases added later. Iteration 02 trained on them and was excluded from this test.
- **Full-dataset usage diagnostic.** The script reads all 10,073 traced branches in iteration 02. It excludes the sampled or directly labeled branches and the established scope holds. The 194 remaining branches with at least two different numeric candidate counts get comparable selections. The other 9,879 branches lack a meaningful unresolved conflict for this diagnostic or have been held or reviewed. Agreement on these records measures behavior. Accuracy needs an adjudicated total.

Only the bed selectors exist. The source audit checked doctor-count candidates without finding supported totals, so its doctor fallback has no trained checkpoint to compare. A selector can choose among supplied values and has exact ownership only where the right value was among them. The capacity regression work is evaluated separately. [Iteration 02 evidence and release rule](../../iterations/02-extension.md)

## Overall results

| Evidence and split | Method | Exact top choice | Exact when available | MAE, beds | Brier | Log loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Independent, 2 supported of 10 reviewed | Iteration 01 | 2/2 | 2/2 | 0 | 0.107 | 0.392 |
| Independent | Iteration 02 | 2/2 | 2/2 | 0 | 0.154 | 0.494 |
| Independent | Publisher fallback | 2/2 | 2/2 | 0 | 0.147 | 0.479 |
| Independent | Newest report | 0/2 | 0/2 | 143 | — | — |
| Shared five-case cross-fit | Iteration 01 | 4/5 | 4/4 | 2 | 0.246 | 0.685 |
| Shared five-case cross-fit | Iteration 02 | 4/5 | 4/4 | 2 | 0.188 | 0.564 |
| Shared five-case cross-fit | Publisher fallback | 4/5 | 4/4 | 2 | 0.203 | 0.597 |
| Shared five-case cross-fit | Newest report | 0/5 | 0/4 | 24.4 | — | — |
| Supported extension cases held out from iteration 01 | Iteration 01 | 0/2 | 0/1 | 82.5 | 0.359 | 0.924 |
| Extension held out from iteration 01 | Publisher fallback | 0/2 | 0/1 | 82.5 | 0.436 | 1.114 |
| Extension held out from iteration 01 | Newest report | 1/2 | 1/1 | 64.5 | — | — |

Exact scores a selected candidate's count. Conditional exact divides hits by the number of cases with a correct candidate available. MAE uses the selected count, even on cases with no exact candidate. Brier and binary log loss score every available candidate against whether it agrees with the reviewed total, average within each facility, then average across cases. Smaller is better. Newest is a choice rule without probability scores. The two trained models' ranking of probability quality changed between splits. Two independent supported cases are too few to infer a reliable probability or accuracy advantage.

## Where choices held up or failed

| Slice | Iteration 01, native cross-fit | Iteration 02, native cross-fit |
| --- | --- | --- |
| SBI was the only external source | 2/2 exact, MAE 0 | 2/3 exact, MAE 20 |
| IFFCO was the only external source | 1/2 exact, MAE 5 | 1/2 exact, MAE 5 |
| Both insurers agreed with each other | 1/1 exact, MAE 0 | 1/2 exact, MAE 52.5 |
| Reviewed total under 100 beds | 2/2 exact, MAE 0 | 2/2 exact, MAE 0 |
| Reviewed total from 100 to 299 beds | 1/1 exact, MAE 0 | 1/3 exact, MAE 55 |
| Reviewed total at least 300 beds | 1/2 exact, MAE 5 | 1/2 exact, MAE 5 |

Iteration 01's hardest native case was 10 beds off because none of the source candidates matched the reviewed total. It also missed both supported added cases, including a 105-bed miss on Shifa. Iteration 02 improved the Brier score on the shared five, yet still missed Shifa in its native held-out fold. Both insurers supported the exact count in that case and the models chose the other source. Combining claims from multiple insurers is useful evidence there. On shared and independent cases, newest did worse with the dates and claims available, so recency alone is a weak general choice rule in this pilot. [Shifa review and primary evidence](../../review/extension/bed-r40-r42.json)

The independent holdout adjudicated National Hospital and Holy Family Hospital. Both trained selectors and the fallback chose the FDR figure for both. All three differently reporting IFFCO/SBI pairs examined in the new batch lacked comparable exact labels, so this test did not establish an insurer winner when the insurer values differ. Across the 194 full-dataset choices, both trained models and the publisher fallback selected FDR 172 times and IFFCO 22 times. Newest changed 169 of those choices. The 22 IFFCO selections occur among the 31 differently reporting dual-insurer cases. No selected count or fallback rule is modified by this audit. [National Hospital operator statement](https://www.nationalhospitals.com/) · [Holy Family operator statement](https://holyfamilyhospital.in/about-us)

## Reproduce

Run from the repository root after restoring the locally generated, frozen iteration 01 and 02 outputs described in the [supplement build guide](../../README.md). The script reruns the sample selection fingerprint comparison, checks holdout branches against both training sets and records the SHA-256 of each model artifact. Its report contains aggregates only. [Evaluator](evaluate_selectors.py)

```sh
python max-supplemented-data/experiments/source_selection/evaluate_selectors.py \
  --local max-supplemented-data/local \
  --output max-supplemented-data/experiments/source_selection/results.json
```
