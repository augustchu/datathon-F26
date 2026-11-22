# Capacity models and Weights & Biases

Open the model notebooks from the Databricks Git folder so their shared files are available. Run the dependency cell before training. The [model evaluation](MODEL_EVALUATION.md) records the comparison on 522 facilities and why the facilities-only neural network is the next candidate to validate.

The [population density notebooks](models%20with%20population%20density/) compare three MLPs on matching split and holdout fingerprints. Run the facilities-only baseline, then the density and density-plus-NFHS models. The [data handoff](models%20with%20population%20density/CLAUDE_HANDOFF.md) explains district and PIN formats and Databricks configuration.

The [XGBoost forest notebook](models%20with%20population%20density/Capacity%20with%20XGBoost%20Forests.ipynb) compares a random forest and boosted forests with explicit missing-predictor indicators. Run the first three `non-ml` notebooks in the same input mode and snapshot to build the audit checkpoints. The XGBoost notebook uses those exclusions and facility partitions. Population density and NFHS inputs are optional, and an opt-in CSV export saves estimated bed counts privately.

## Experiment tracking

The model-export cells use the project in `WANDB_PROJECT` or default to `datathon-f26`. Runs default to offline. For a shared project, set `WANDB_MODE=online`, `WANDB_ENTITY` and `WANDB_PROJECT` before running. Provide `WANDB_API_KEY` through the notebook environment or a Databricks secret.

To connect a Databricks notebook to a shared project, set the team’s project and secret names before the first tracking cell.

```python
import os

os.environ["WANDB_MODE"] = "online"
os.environ["WANDB_ENTITY"] = "your-team"
os.environ["WANDB_PROJECT"] = "datathon-f26"
os.environ["WANDB_API_KEY"] = dbutils.secrets.get(scope="your-secret-scope", key="wandb-api-key")
```

Run the tracking cell after fitting and evaluating. In `capacity_EDA/capacity_random_forest.ipynb`, set `LOG_MODEL_TO_WANDB = True` in the setup cell to enable tracking. Each run records estimator settings, data inputs, split sizes, held-out scores and the baseline where available. The count comparison and XGBoost notebooks attach aggregate selection and evaluation tables. The MLP notebooks log training and validation loss by epoch and the XGBoost notebook logs loss by round. The manifest distinguishes Huber or RMSE on `log1p(count)` during training from errors in beds or doctors during held-out evaluation.

Each tracked fit receives a W&B artifact. Neural-network bundles pair the training-fitted preprocessor with the exact evaluated weights. Scikit-learn bundles contain the fitted pipeline. XGBoost bundles contain the native UBJ model and the fitted preprocessor. Applicable bundles include `metadata.json`, `metrics.json`, `training_history.json`, and `evaluation_tables.json`. Notebook 09 records its fold and sensitivity comparisons as aggregate tables. The main XGBoost notebook records both forests, including the one the calibration set did not select.

In W&B, compare runs with matching holdout fingerprints. Use the logged baseline, held-out MAE, and large errors together. Inspect the run config and the artifact manifest before comparing two runs that used different predictors, source snapshots or splits. The notebook prints the local run directory in offline mode and a run URL when online logging is enabled.

Use a pinned `entity/project/artifact:vN` version when downloading a bundle. `wandb.Api().artifact(...).download()` fetches its files and `artifact.verify(...)` checks them against the manifest. For inference, follow the input preparation and target transformation recorded for that model. [W&B artifact guide](https://docs.coreweave.com/products/wandb/artifacts/download-and-use-an-artifact)

When loading the shared trainer's `preprocessor.skops` from a trusted run, review the types reported by `skops.io.get_untrusted_types`. Fitted scikit-learn imputers can include NumPy's `numpy.dtype` metadata. Verify that reloaded preprocessing reproduces the fitted transform before use.

An offline run stays on the machine where it was logged. Preserve the W&B run directory and artifact staging directory if you will sync it later. [W&B environment variables](https://docs.coreweave.com/products/wandb/track/environment-variables)
