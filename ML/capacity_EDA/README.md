# Capacity random forest

Open or import `capacity_random_forest.ipynb` in Databricks, attach Free Edition serverless compute, and run the cells in order. The notebook embeds its training code for standalone use. The first two code cells install dependencies and restart Python.

The configured source is:

```text
Table:  databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
Target: capacity
Predictors: columns strictly after countries in the original table order, excluding capacity
```

The final `capacity` in the supplied path is treated as a column. Access to the table is required in the Databricks session. No data upload or MCP connection is necessary to run the notebook there.

## What it does

- Uses only columns strictly after `countries` in the original table column order, excluding `capacity`. `countries` and all earlier columns are excluded from predictors. A selection report records each column's role; an encoding audit identifies unusable constants. The notebook stops with a clear error if `countries` is absent.
- Handles numeric missing values, categorical values, free text, and array/struct-like values. Encodings and vocabularies are fitted on training data only. Text features are capped per column to bound memory use; this is not a lossless representation of every word.
- Uses an approximately 80/20 split with exact duplicate predictor rows kept together. Set `GROUP_COLUMN` to a reliable facility identifier to also keep repeated facility records together. An identifier at or before `countries` is used for grouping only, not prediction. Canonical row ordering and stable group labels make the seeded result reproducible across reordered reads of the same records.
- Fits a 300-tree `RandomForestRegressor` and compares held-out MAE, RMSE, and R² against simple baselines.
- Reports importance by permuting each original column on the test set, rather than comparing thousands of individual text features.
- Displays target-quality and potential-leakage flags, predictions, and residual plots. `OUTPUT_DIR` saves detailed audit tables, the split, metadata, and plots in a new run directory.

The source table is read only. Missing target values are not imputed before training, and predictions do not replace source capacity. Rows with missing or invalid capacity are excluded from model fitting and evaluation.

## Interpretation

Descriptions and duplicate bed-count fields can disclose capacity directly. When these fields fall after `countries`, the model retains them and reports potential leakage; a high test score may therefore reflect recovery of an answer already present in the record. Heuristic flags will not catch every form of leakage.

Importance measures predictive association, not causation or a formal pairwise interaction test. The forest can learn nonlinear interactions, but this notebook does not claim which interactions are causal. Correlated predictors can mask each other's importance. Three shuffle repetitions provide variability estimates, not confidence intervals. Using importance to choose predictors requires a new final holdout or nested validation.

Evaluation applies to records with observed capacity and does not establish accuracy on records with missing capacity. Repeated facilities with differing records can cross the split unless a suitable `GROUP_COLUMN` is supplied. This is an exploratory model, not evidence of actual facility capacity or beds available today.

## W&B model artifact

Set `LOG_MODEL_TO_WANDB = True` to log the fitted training-split forest and its encoder with aggregate held-out and baseline metrics. Open the notebook in a Git folder with `capacity_rf.py` and `ML/notebooks/wandb_models.py`, or set `REPO_ROOT` to the absolute path of that checkout. Logging uses the shared W&B helper and defaults to offline. The detailed audit stays in the notebook and any configured `OUTPUT_DIR`. The encoder retains learned categories and text tokens. Review the artifact before sharing it or syncing the offline run.

To load the artifact from a trusted project run, put the matching checkout's `ML/capacity_EDA` directory on the Python import path and use the versions recorded in `metadata.json`. Inspect `skops.io.get_untrusted_types(file=path)` before loading `model.skops`. The expected project-specific types are `capacity_rf.FacilityEncoder` and `sklearn.tree._tree.Tree`. Review any new types before granting trust. The fitted model takes the predictor columns recorded in its configuration and returns capacity in the original units.

## Files and development

- `capacity_random_forest.ipynb`: Databricks notebook with cleared outputs and optional Git folder model logging.
- `capacity_rf.py`: reusable preprocessing and modeling functions.
- `build_notebook.py`: rebuilds the notebook after editing its cells or `capacity_rf.py`.
- `requirements.txt`: local dependencies; the notebook installs its own dependencies.
- `test_capacity_rf.py`: synthetic-data checks; no real facility records or Databricks credentials are used.

From this directory, after installing dependencies:

```bash
python -m unittest test_capacity_rf.py
python build_notebook.py
```

The notebook and functions can be checked locally with synthetic data, but actual facility-model metrics require running the notebook against the Databricks table. No real-data accuracy is claimed by these files.

References: [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations), [serverless limitations](https://docs.databricks.com/aws/en/compute/serverless/limitations), [notebook Python dependencies](https://docs.databricks.com/aws/en/libraries/notebooks-python-libraries), [scikit-learn random forest regression](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestRegressor.html).
