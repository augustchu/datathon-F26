# Max-supplemented facility data

This offline pipeline keeps the 51 original Virtue Foundation fields for every input row and adds a source-attributed value where the evidence supports one. It reuses the prepared branch, location, contact and service tables from the Datathon. It also compares IFFCO Tokio and SBI General as possible sources for missing or disputed whole-facility counts. [Earlier source audit](../data/recovery/capacity-evidence/README.md)

## Separate iterations

| Iteration | Design | Results and local files |
| --- | --- | --- |
| 01 | Frozen 36-case pilot | [Results](iterations/01-pilot.md) · `local/iteration-01/` |
| 02 | Twelve additional count checks, preserving the first 36 | [Results and comparison](iterations/02-extension.md) · `local/iteration-02/` |

Each local iteration has its own frozen sample, trained model, held-out predictions, review ledger and dataset. The [initial](review/initial/) and [extension](review/extension/) directories store each public review once. The first audit's two browser checks are outside the training sample and apply to the export as separately recorded decisions. The input snapshot and FDR-derived outputs stay in local authorized storage.

## Labels and source selection

We focused model training on **whole-facility bed capacity** and **total doctors** because these fields are both sparse and relevant to assessing facility scale. Starting with the exact strong branch matches from the earlier audit, we selected numerical disagreements by a deterministic SHA-256 ordering within field and publisher-availability strata. Initial count quotas were 6, 6 and 4 for IFFCO-only, SBI-only and both for beds, and 5, 5 and 4 for doctors. The extension added two per stratum and field. The five website and one establishment-year checks are documented comparisons outside training.

A `supported` count needs a branch-specific public document and a comparable whole-facility figure. The review ledger records dates where the publisher supplied them. A candidate receives label 1 for matching the supported figure and 0 for a different figure. More than one publisher can match. When an outside primary document gives a third value, all existing candidates receive label 0. Unresolved checks supply no labels. For date differences, the label measures agreement with the reviewed figure, so an older report can differ even if it was valid in its own reporting period. An insurer publication may originate with the hospital and may repeat the same submission used by another insurer. [WHO desk review guidance](https://www.who.int/publications/i/item/9789240047389)

The binary XGBoost score estimates whether a candidate matches the reviewed target. Its inputs are field, publisher, date availability, age and publisher agreement on the candidate value. There are no facility IDs, names, phone numbers, raw count sizes, or review outcomes in the feature matrix. Each facility–field case has total training weight one. The fixed settings use 24 trees, depth two and L2 regularization. Leave-one-facility-out evaluation compares the top-ranked candidate with a smoothed publisher baseline and a newest-report baseline. It gives each facility–field case one vote. The release gate requires at least eight supported cases, more correct top choices than either baseline and a lower Brier score than the publisher baseline. [XGBoost tuning notes](https://xgboost.readthedocs.io/en/stable/tutorials/param_tuning.html)

The publisher fallback pools matching counts across the two priority fields and calculates `(matches + 1) / (supported cases where the publisher appeared + 2)`. We also report the stricter head-to-head subset with both insurers on the same case, which anchors the actual insurer comparison. Pooling is tentative and reflects the selected disagreements that could be resolved. For other fields, the pooled ordering applies to comparable choices among observed publishers after the field-specific checks. The doctor-count fallback uses the same pooled order, which was informed only by the supported bed cases in these runs.

## Dataset decisions and provenance

1. **Preserve the original.** `original` stores all FDR fields. The prepared branch ID and location stay separate. Held branches carry blank selected counts and coordinates for routing purposes while preserving their original fields.
2. **Honor direct review.** An exact reviewed figure takes precedence. An unresolved reviewed field has a blank selected value and an evidence link. An approved source-grounded recovery is used when applicable. The earlier bed recovery from the facility's own description remains attributable to that description and its excerpt.
3. **Use comparable, linked reports.** An insurer count can fill a missing value when it refers to the exact facility, uses whole-facility scope, and comes from a publisher absent from that FDR branch's source list. Date and source ID travel with the value. Older observations are useful as historical figures. Licensed, sanctioned, unit, group and proposed-expansion numbers retain their meaning in the preceding audit and never replace a whole-facility total in this export. Source-ranked values carry their method and selected publisher. The experimental model choice for contested beds is recorded separately when available.
4. **Keep other evidence at its proper scope.** Prepared contact and service records retain their source evidence and review flags. The export keeps witnessed website domains, dated staffing and ICU reports, and existing source-grounded services. Phone and email remain contact information. Unlinked equipment, founding-year and other claims go into a separate file with source links. The original capability, equipment, and procedure text stays available in the raw field set.
5. **Preserve estimates as estimates.** Previously fitted regression and ML count estimates are available as attributed overlays and intervals where provided. They sit outside selected documented counts. These exports describe documented infrastructure and carry no live bed availability claims.

## Local files

| File within either iteration | Grain and purpose |
| --- | --- |
| `dataset/facilities.jsonl` | One row per original FDR record with the original data, selected values, geographic holds, documentary contacts and services, extra claims and prior estimates. Suitable for Spark JSON input. |
| `dataset/facility_values.csv` | One row per original FDR record with the commonly used fields and the selected count status, source and source URL. |
| `dataset/count_decisions.jsonl` | One row per linked branch and count field with the original audit, selected and optional experimental values, alternatives and provenance. |
| `dataset/unlinked_public_evidence.jsonl` | Claims from the additional imaging, Puducherry and Yanam sources that could not be linked to an FDR branch. |
| `dataset/dataset_summary.json` | Selection-method and nonmissing counts. |
| `model/model_evaluation.json` | Case counts, candidate-source agreement, paired insurer analysis and held-out results. |
| `model/review_ledger.json` | Per-case evidence, decision, and matching candidate sources. |
| `model/capacity_selector.json` | XGBoost pilot trained on supported bed cases in that iteration. |
| `validation.json` | Checks the original input rows, release counts, scope of holds, and every sampled count decision. |

## Replay

Use the private FDR export and the file names from the [prior audit replay](../data/recovery/capacity-evidence/README.md). The approved recovery files come from notebook 04a and notebook 05 of the source-grounded recovery. `description_capacity.json` is the existing archive of bed counts extracted from descriptions. Each completed run prints a training and dataset summary. Replays should use fresh run directories to preserve previous results.

```bash
python3 -m pip install -r max-supplemented-data/requirements.txt

python3 max-supplemented-data/src/sample_manual_checks.py \
  --counts matched_claims.csv --other-review field_review.csv \
  --output max-supplemented-data/local/iteration-01/sample.json

python3 max-supplemented-data/src/sample_manual_checks.py \
  --counts matched_claims.csv --other-review field_review.csv --extend \
  --output max-supplemented-data/local/iteration-02/sample.json

run=max-supplemented-data/local/iteration-02
decisions=max-supplemented-data/review
python3 max-supplemented-data/src/model_sources.py \
  --sample "$run/sample.json" --reviews "$decisions" --output-dir "$run/model"

python3 max-supplemented-data/src/build_dataset.py \
  --snapshot path/to/fdr_checkpoint --count-links matched_claims.csv \
  --field-links field_matches.csv --unlinked-fields source_only_fields.csv \
  --approved-updates path/to/approved/field_updates.jsonl \
  --approved-services path/to/approved/recovered_service_claims.jsonl \
  --description-capacity description_capacity.json \
  --previous-reviews data/recovery/capacity-evidence/reviews.json \
  --sample "$run/sample.json" --reviews "$decisions" \
  --model-dir "$run/model" --output-dir "$run/dataset"

python3 max-supplemented-data/src/validate_run.py \
  --iteration "$run" --raw path/to/fdr_checkpoint/raw_facilities.jsonl
```

For iteration 01 use `local/iteration-01` and `max-supplemented-data/review/initial`. The two sample receipts are recorded with their iteration results. Verification proceeds from public evidence links in the ledgers. The two earlier screenshots remain in the source audit.
