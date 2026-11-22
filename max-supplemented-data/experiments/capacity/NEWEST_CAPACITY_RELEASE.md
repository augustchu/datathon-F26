# Newest-source bed capacity and confidence release

The newest eligible source rule and its saved XGBoost model filled capacity for **6,829 distinct facilities**, corresponding to **6,833 source rows**. The release keeps all **10,088 source rows** in their original order. The separate confidence CSV has the same 10,088 data rows and one column, `confidence_score`. The two files can be joined by row position. [GitHub release and downloads](https://github.com/augustchu/datathon-F26/releases/tag/capacity-newest-2026-11-22)

## 1. Select and apply capacity

1. Start with `iteration-03` of the frozen publisher comparison. It selects the newest eligible dated claim for the same branch and whole-facility count. The publisher ranking breaks eligible date ties and ranks candidates when none has an observation date. Explicit review evidence and identity or count holds take precedence. The underlying count ledger keeps dates, evidence and disagreements. [Source-rule definitions](RULE_CLEANING_COMPARISON.md#3-compare-four-deterministic-source-rules)
2. Replay the description parser used during training and compare every accepted recovery with the recorded experiment evidence. It recovered 13 service fields and supplied no additional bed or doctor counts in this run. The delivered table includes the exact description excerpts with the recovered service claims. The model uses the selected structured values and keeps the description column outside training and inference. [Description evaluation](DESCRIPTION_RECOVERY_EVALUATION.md)
3. Recreate the encoder from the same 1,608 training labels, check the archived feature manifest, and load the saved booster from `description-wide-v3`. The chosen configuration was depth 5 with 111 boosting rounds on log-transformed capacity. Recomputed predictions matched all **455** held-out predictions exactly before release. That model had **110.22 beds MAE** and **260.35 beds RMSE** on the historical test set. [Training run](https://forge.coreweave.com/wandb/yod347-carnegie-mellon-university/datathon-f26/runs/b0360372)
4. Estimate only where the original count audit and the cleaned source decision both say missing, the branch is routable, and no count or identity hold applies. Restore each estimate to every corresponding original source row. Published counts keep their selected value and provenance. The earlier known bed-count defect stays withheld, with its original number retained for audit.

| Capacity outcome | Original source rows |
| --- | ---: |
| Selected published count retained | 2,713 |
| Capacity filled by XGBoost | 6,833 |
| Capacity still blank | 542 |
| Total | 10,088 |

### Why 542 capacity cells are blank

| Source rows | `capacity_status` | Why the imputation gate withheld the value |
| ---: | --- | --- |
| 503 | `branch_identity_hold` | A branch identity or location issue prevents a safe match between the estimate and the facility. |
| 26 | `review_unresolved` | The review could not establish a usable whole-facility count. The evidence remains open for review. |
| 1 | `target_quality_hold` | A patient total was incorrectly recorded as beds. The documented target-quality hold takes precedence. |
| 12 | `missing` | The original source explicitly recorded zero. The cleaner leaves `capacity` blank and preserves the reported zero in `original_capacity`. The imputation gate requires an absent original count. |
| **542** | | |

### How to identify and interpret estimates

The model estimates ranged from 3 to 1,372 beds, with a median of 29 by source row. They carry `capacity_status=xgboost_estimate`, the model ID, and lower and upper bounds calibrated on 213 separate cases. The interval is the point estimate plus or minus **278 beds**, with a floor of zero. Observed coverage was **89.9%** on the historical test cohort. These bounds measure residual error for reported test counts, and that coverage may differ for genuinely missing counts. The earlier evaluation also showed substantial underprediction above 200 beds. [Test performance by facility size](DESCRIPTION_RECOVERY_EVALUATION.md#4-check-where-the-errors-remain)

## 2. Calculate confidence from five important cells

Confidence summarizes documentary evidence and completeness for the branch name/PIN, selected phone, documented services, beds, and doctors. Each cell can contribute up to 20 points. Add the five values. This is a 0–100 data-quality score with a fixed denominator. It has no probability interpretation.

| Cell status | Points per cell | Cells across the release |
| --- | ---: | ---: |
| Verified unchanged | 20 | 820 |
| Verified correction to a populated original cell | 15 | 20 |
| Verified fill into a previously empty cell | 15 | 21 |
| Corrected using available source evidence, without independent confirmation | 5 | 391 |
| Populated, without independent confirmation | 5 | 23,883 |
| Model estimate | 0 | 6,833 |
| Still empty or held | 0 | 18,472 |

Linked external documents or branch-specific reviews support the name/PIN check. The phone check reuses the saved matching across publishers after confirming that the selected phone is identical in both source-rule snapshots. The service cell is verified only when all selected services have corroborated or approved evidence and a supporting source link. Source-only descriptions can fill that cell and receive the populated credit. A blank documented-service cell means the release lacks an eligible service entry for the branch. The original service-related fields remain visible separately.

For counts, verification requires an explicit source review or matching eligible reports under the existing verification rules. A value chosen solely through publisher ranking or recency gets populated credit until corroborated. For each verified cell, compare the selected value with the original value using normalized name/PIN, phone, numeric counts, or the configured service aliases. The five `*_evidence_status` fields and the per-status `*_cells` counts in the main CSV explain every score. The separate CSV contains only the numeric score. The median observed score was **10**, reflecting how often cells were populated without independent verification. [Scoring and export implementation](publish_newest_capacity.py)

## 3. Verify the two exports together

Both files were written from the same row loop, then reopened and checked together. For each of the 10,088 rows, the source row index and facility ID were compared with the source-order checkpoint, the confidence score was recalculated from the main CSV’s cell statuses, and model estimates and bounds were checked against the saved output. Repeated facility IDs share the capacity estimate and remain separate source rows. The confidence score evaluates the original cell evidence for each row. Use the `row_index` in the main CSV if aligning the files after a transformation. Join them before sorting, filtering, or deduplicating either file independently.

The main CSV retains the original schema alongside cleaned fields, source URLs, observation dates, modeled intervals, recovery excerpts and confidence components. The CSV writer removes embedded machine control characters outside standard whitespace and protects formula-like text when the file opens in a spreadsheet. The GitHub release also contains the saved booster, feature encoding manifest, and delivery receipt with the SHA-256 checksums for both CSVs. [Delivery receipt](https://github.com/augustchu/datathon-F26/releases/download/capacity-newest-2026-11-22/delivery_receipt.json)

## 4. Reproduce

Use the private frozen source snapshot and the earlier trained booster. From the repository root, use the [experiment dependencies](../requirements.txt). The Mac run used a single OpenMP runtime at `/opt/homebrew/opt/libomp/lib` with `KMP_DUPLICATE_LIB_OK=FALSE` and `OMP_NUM_THREADS=2`. Direct the command to fresh output and audit paths.

```sh
python max-supplemented-data/experiments/capacity/publish_newest_capacity.py \
  --archive max-supplemented-data/local/rule-cleaning-v2 \
  --run max-supplemented-data/local/description-wide-v3 \
  --audit-dir max-supplemented-data/local/newest-capacity-release-replay \
  --output /path/to/newest-capacity-delivery \
  --as-of 2026-11-22
```
