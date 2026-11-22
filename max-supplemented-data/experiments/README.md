# Source verification and capacity experiments

The [newest-source release](capacity/NEWEST_CAPACITY_RELEASE.md) records the model, two CSVs, row-order checks and confidence formula. Its supporting reports cover the [deterministic rules](capacity/RULE_CLEANING_COMPARISON.md), [feedforward experiment](capacity/FEEDFORWARD_EVALUATION.md), [source and feature comparison](capacity/SOURCE_FEATURE_FACTORIAL.md), and [description recovery](capacity/DESCRIPTION_RECOVERY_EVALUATION.md). Links into `local/` refer to authorized experiment checkpoints on disk. Figures and aggregate results are also available through the W&B run links in each report.

## Earlier two-selector experiment

**Human review pending.** The current results come from an automated desk review and a provisional model run. The user will independently review the unlabeled In The Loop queue before the team uses or publishes the comparison and capacity export.

This run tests the two frozen source-selection checkpoints and uses the two separately filled facility datasets to fit and compare six capacity models. The [source-selector report](source_selection/README.md) explains where publisher choices agreed and where labels were insufficient. The [In The Loop review](verification/README.md) records the ten branch checks and links the screenshots. The [capacity report](capacity/RESULTS.md) describes the matched test comparison, the change from preliminary to final results, and the output schema.

| Stage | Inputs | Output |
| --- | --- | --- |
| Freeze and verify disputes | Two saved supplement iterations and a stratified independent sample | App-exported decisions, public screenshots, and matched source-selector metrics |
| Assemble features | Original FDR snapshot, both supplements and audited NFHS district keys | One private branch table with two separate selected targets and corresponding doctor-count inputs |
| Compare models | Same branch splits, two supplements and three feature tiers | Six XGBoost checkpoints and encoders, held-out predictions, per-trial learning curves, and paired metrics |
| Audit supervised data | Frozen prepared data and the completed training report | Six separate private CSVs with roles and target provenance, and a manifest with hashes |
| Release estimates | Winning model's predictions and its supplement iteration | Final local CSV and JSONL with original values, selected values, estimates, intervals and field holds |
| Record experiments | Aggregate reports, review receipt and model files | Personal W&B runs with metrics, curves and artifacts |

The Hindi IVR plan follows the browser stage. It proposes a small pre-deployment pilot with branch and service confirmation, then a staff follow-up for comparable bed and doctor counts. [Mass verification runbook](../../non-ml/MASS_VERIFICATION.md#proposed-next-step-after-the-browser-review)

## Replay

Use the [supplement build guide](../README.md) to restore the two frozen iterations and access the original authorized checkpoint. Install the [experiment dependencies](requirements.txt). Run the [browser stage](verification/README.md) and the [selector evaluator](source_selection/README.md#reproduce) first. The scripts below use a new local run path. Set `snapshot` to the full audited checkpoint, and `nfhs_reference` to the original cached NFHS response containing `columns` and `rows`. The position-based population option requires agreement with this raw NFHS order. A future CSV with `state`, `district` and `population` uses the keyed mode directly.

```sh
experiment=max-supplemented-data/experiments
local_run=max-supplemented-data/local
run="$local_run/capacity-comparison-replay"
snapshot=/path/to/authorized/full_audit/export/2026-11-21
nfhs_reference=/path/to/authorized/nfhs_5_district_health_indicators.json.gz
population_revision=d6bb601d61882d8f42e200793cd11d629a9f7fe0
mkdir -p "$run"
git show "$population_revision:data/population/nfhs_population_only.csv" > "$run/nfhs_population_only.csv"

python "$experiment/capacity/prepare_data.py" \
  --snapshot "$snapshot" --iteration-01 "$local_run/iteration-01" \
  --iteration-02 "$local_run/iteration-02" --output-dir "$run/inputs" \
  --population "$run/nfhs_population_only.csv" --population-source-revision "$population_revision" \
  --population-follows-nfhs-order --nfhs-order-reference "$nfhs_reference"
python "$experiment/capacity/train_evaluate.py" \
  --input-dir "$run/inputs" --output-dir "$run/training"
python "$experiment/capacity/export_arms.py" \
  --input-dir "$run/inputs" --metrics "$run/training/metrics.json" --output-dir "$run/datasets"
python "$experiment/capacity/plot_results.py" \
  --metrics "$run/training/metrics.json" --output "$run/capacity-test-results.png"
```

Read `winner.release_run_id` in `training/metrics.json` after fitting. The release below records the winner from the documented run. Use the iteration corresponding to the recorded winner for a new run. `release_capacity.py` checks that each estimate maps to an eligible missing field and that all original rows are preserved.

```sh
python "$experiment/release_capacity.py" \
  --dataset-dir "$local_run/iteration-01/dataset" --input-audit "$run/inputs/input_audit.json" \
  --predictions "$run/training/imputation_predictions.csv" --output-dir "$run/final-output" \
  --iteration iteration-01 --run-id iteration_01__socioeconomic_population
```

To mirror the logged run, sign in to W&B in the environment and set `WANDB_ENTITY` to the account's personal workspace slug. Use `--mode offline` to write equivalent offline logs. The tracker verifies prior receipts on retry and passes aggregate data, model artifacts, and encoders to W&B. The raw FDR snapshot, candidate queues, branch features, per-branch predictions and final data for this earlier experiment stay under the Git-ignored `local/` path. [Completed W&B comparison](https://forge.coreweave.com/wandb/yod347-carnegie-mellon-university/datathon-f26/runs/ei54n4wf)

```sh
python "$experiment/track_runs.py" \
  --source-results "$experiment/source_selection/results.json" \
  --capacity-results "$run/training/metrics.json" --input-audit "$run/inputs/input_audit.json" \
  --release-audit "$run/final-output/release_audit.json" --prior-root "$local_run" \
  --verification-receipt "$local_run/in-the-loop-review-replay/desk-review/app_review_receipt.json" \
  --output-dir "$run/wandb-tracking" --mode online --project datathon-f26
```
