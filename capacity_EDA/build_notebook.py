"""Rebuild the standalone Databricks notebook from the model module."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent


def cell(kind, text):
    result = {"cell_type": kind, "metadata": {}, "source": text.strip() + "\n"}
    if kind == "code":
        result.update(execution_count=None, outputs=[])
    return result


cells = [
    cell("markdown", """
# Random forest: predict facility capacity

Reads `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities`
and predicts the numeric `capacity` column using only columns strictly **after
`countries`** in the table's column order, excluding the target. This notebook
is self-contained: import this `.ipynb` into Databricks and attach serverless compute.

This is an exploratory model. Descriptions or bed-count fields may
already state the answer. Review the leakage flags before interpreting the score.
Training uses only observed, valid capacity values; performance on those records
does not establish performance on facilities whose capacity is missing. Predictions
are estimates, not verified capacity or current bed availability.
    """),
    cell("code", """
%pip install "scikit-learn==1.7.2" "cloudpickle==3.1.1" "pandas>=2,<3" "matplotlib>=3.7,<4"
    """),
    cell("code", "dbutils.library.restartPython()"),
    cell("markdown", """
## Configuration

`capacity` is the column, not a fourth part of the table name. If there is a reliable
facility ID shared by repeated records, set `GROUP_COLUMN` to that column. Grouping
keeps its records on the same side of the split. An ID at or before `countries`
is used for grouping only and does not become a predictor.
Without one, grouping protects against exact duplicate predictor rows only.

The row guard stops collection if the table exceeds the expected small dataset;
it does not silently train on an arbitrary subset. Optional output storage must be
an absolute, writable Unity Catalog volume or workspace directory. Nothing writes
back to the source table.
    """),
    cell("code", """
TABLE = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities"
TARGET = "capacity"
AFTER_COLUMN = "countries"  # Exclusive boundary, using the original table column order.
GROUP_COLUMN = None
MAX_ROWS = 20000
SEED = 42
N_TREES = 300
PERMUTATION_REPEATS = 3
# Example: "/Volumes/<catalog>/<schema>/<volume>/capacity_EDA"
OUTPUT_DIR = None
    """),
    cell("markdown", "## Model and preprocessing functions\nFitted imputations, categories, and text vocabularies use the training split only."),
    cell("code", (ROOT / "capacity_rf.py").read_text()),
    cell("markdown", "## Read the facility table"),
    cell("code", """
import platform
from datetime import datetime, timezone
from pathlib import Path
import json
import sklearn
import matplotlib.pyplot as plt
import cloudpickle

source = spark.table(TABLE)
if TARGET not in source.columns:
    raise ValueError(f"Target {TARGET!r} was not found. Columns: {source.columns}")
if AFTER_COLUMN not in source.columns:
    raise ValueError(f"Boundary {AFTER_COLUMN!r} was not found. Columns: {source.columns}")
predictor_columns = [c for c in source.columns[source.columns.index(AFTER_COLUMN) + 1:] if c != TARGET]
if not predictor_columns:
    raise ValueError(f"No predictors remain strictly after {AFTER_COLUMN!r}, excluding {TARGET!r}.")
schema = pd.DataFrame(source.dtypes, columns=["column", "spark_type"])
display(schema)
data = source.limit(MAX_ROWS + 1).toPandas()
SOURCE_READ_AT_UTC = datetime.now(timezone.utc).isoformat()
if len(data) > MAX_ROWS:
    raise ValueError(f"The table has more than {MAX_ROWS:,} rows. Review memory limits before raising MAX_ROWS.")
print(f"Loaded {len(data):,} rows and {data.shape[1]} columns from {TABLE}")
print(f"Predictors strictly after {AFTER_COLUMN!r}: {predictor_columns}")
    """),
    cell("markdown", """
## Fit and evaluate

An approximately 80/20 group split separates training and test records. The forest
learns nonlinear associations and interactions automatically. MAE and RMSE are in
the original capacity units; lower is better. R² can be negative when the model
performs poorly. Mean and median baselines make the result easier to assess.
    """),
    cell("code", """
result = run_capacity_rf(
    data, target=TARGET, after_column=AFTER_COLUMN, group_column=GROUP_COLUMN,
    test_size=0.2, random_state=SEED, n_estimators=N_TREES,
    permutation_repeats=PERMUTATION_REPEATS,
    max_text_features=128, max_categories=40, n_jobs=2,
)
display(pd.DataFrame([result["summary"]]))
display(result["column_selection"])
display(result["metrics"])
    """),
    cell("markdown", """
## Data and leakage checks

Review rejected targets and the column encoding audit. Numeric target strings are
parsed conservatively; ranges and unknown values are not silently replaced with zero.
Leakage flags are heuristics, not proof that the remaining columns are leakage-free.
The model retains suspected leakage columns that fall within the selected range.
    """),
    cell("code", """
display(result["target_audit"])
display(result["feature_audit"])
display(result["leakage_flags"])
    """),
    cell("markdown", """
## Which columns help prediction?

Permutation importance shuffles one original column at a time in the test data,
including all its encoded text/category features together. Positive MAE increase
means predictions worsened after shuffling. Correlated columns can share or hide
importance; this measures predictive association, not causation. Variation across
three shuffles is not a confidence interval. Do not repeatedly tune the model on
this test set; reserve another final holdout if using these results to select features.
    """),
    cell("code", """
display(result["importance"])
predictions = result["predictions"]
fig, axes = plt.subplots(1, 2, figsize=(12, 4))
axes[0].scatter(predictions["actual_capacity"], predictions["predicted_capacity"], alpha=0.5, s=18)
low = min(predictions["actual_capacity"].min(), predictions["predicted_capacity"].min())
high = max(predictions["actual_capacity"].max(), predictions["predicted_capacity"].max())
axes[0].plot([low, high], [low, high], "k--", linewidth=1)
axes[0].set(xlabel="Observed capacity", ylabel="Predicted capacity", title="Held-out predictions")
axes[1].scatter(predictions["predicted_capacity"], predictions["residual"], alpha=0.5, s=18)
axes[1].axhline(0, color="black", linestyle="--", linewidth=1)
axes[1].set(xlabel="Predicted capacity", ylabel="Observed − predicted", title="Held-out residuals")
fig.tight_layout()
display(fig)
    """),
    cell("markdown", """
## Optional: save the run

Set `OUTPUT_DIR` above to retain the evaluation tables, split, model, schema, plot,
and environment metadata. Each run gets its own timestamped directory. The saved
model is the training-split model used for evaluation; it is not silently refitted
on the test set. Source capacity values are never overwritten.
    """),
    cell("code", """
if OUTPUT_DIR is None:
    print("Results are displayed above. Set OUTPUT_DIR to persist this run.")
else:
    root = Path(OUTPUT_DIR)
    if not root.is_absolute() or not str(root).startswith(("/Volumes/", "/Workspace/")):
        raise ValueError("Use an absolute writable /Volumes/... or /Workspace/... output directory.")
    run_dir = root / datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%S_%fZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    for key in ("metrics", "predictions", "importance", "column_selection", "feature_audit", "target_audit", "leakage_flags", "split"):
        result[key].to_csv(run_dir / f"{key}.csv", index=False)
    schema.to_csv(run_dir / "source_schema.csv", index=False)
    metadata = {
        "table": TABLE, "target": TARGET, "after_column": AFTER_COLUMN, "group_column": GROUP_COLUMN,
        "read_completed_at_utc": SOURCE_READ_AT_UTC,
        "seed": SEED, "trees": N_TREES, "permutation_repeats": PERMUTATION_REPEATS,
        "summary": result["summary"],
        "versions": {"python": platform.python_version(), "sklearn": sklearn.__version__,
                     "pandas": pd.__version__, "numpy": np.__version__, "cloudpickle": cloudpickle.__version__},
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2, default=str))
    # Cloudpickle includes notebook-defined preprocessing classes in the saved artifact.
    with (run_dir / "capacity_model.pkl").open("wb") as handle:
        cloudpickle.dump(result["pipeline"], handle)
    fig.savefig(run_dir / "held_out_predictions.png", dpi=160, bbox_inches="tight")
    print(f"Saved run to {run_dir}")
    """),
]

notebook = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 4,
}
path = ROOT / "capacity_random_forest.ipynb"
path.write_text(json.dumps(notebook, indent=1) + "\n")
print(path)
