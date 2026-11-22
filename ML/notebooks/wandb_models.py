"""Log fitted capacity models and aggregate evaluation metrics to W&B.

All artifacts include metadata.json and metrics.json. Torch artifacts contain
weights.safetensors and preprocessor.skops. Scikit-learn artifacts contain a
single model.skops with the fitted preprocessing and target transformation.
XGBoost artifacts contain model.ubj and preprocessor.skops.
W&B defaults to offline mode. WANDB_PROJECT, WANDB_ENTITY, WANDB_MODE, and the
standard W&B directory settings can be supplied by the caller's environment.
"""

import json
import math
import os
import platform
from importlib.metadata import version
from numbers import Integral, Real
from pathlib import Path
from tempfile import TemporaryDirectory


def _json_value(value):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError(f"Expected JSON metadata, got {type(value).__name__}")


def _dependencies(*extra):
    packages = ("numpy", "pandas", "scipy", "scikit-learn", "skops", "wandb", *extra)
    return {"python": platform.python_version(), **{package: version(package) for package in packages}}


def _log_model(name, metadata, metrics, config, write_model, *, history=None, evaluation_tables=None):
    import wandb

    project = os.environ.get("WANDB_PROJECT") or "datathon-f26"
    entity = os.environ.get("WANDB_ENTITY") or None
    mode = os.environ.get("WANDB_MODE") or "offline"
    config = _json_value(config)
    metrics = _json_value(metrics)
    history = _json_value(list(history or ()))
    evaluation_tables = _json_value(evaluation_tables or {})
    logged_metrics = {
        f"{group}/{key}": value
        for group, values in metrics.items()
        for key, value in values.items()
        if value is not None
    }
    artifact = wandb.Artifact(name=name, type="model")
    with TemporaryDirectory(prefix="wandb-capacity-") as model_dir:
        directory = Path(model_dir)
        write_model(directory)
        documents = {"metadata.json": {**metadata, "training_config": config}, "metrics.json": metrics}
        if history:
            documents["training_history.json"] = history
        if evaluation_tables:
            documents["evaluation_tables.json"] = evaluation_tables
        for filename, contents in documents.items():
            (directory / filename).write_text(
                json.dumps(contents, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        with wandb.init(
            project=project,
            entity=entity,
            mode=mode,
            name=name,
            job_type=f"{config.get('target_field', 'capacity')}-training",
            config=config,
            settings=wandb.Settings(console="off", disable_git=True, save_code=False),
        ) as run:
            for path in sorted(directory.iterdir()):
                # The SDK copies mutable files to WANDB_DATA_DIR for later offline sync.
                artifact.add_file(str(path), policy="mutable")
            if history:
                run.define_metric("fit_step")
                run.define_metric("training/*", step_metric="fit_step")
                for step, values in enumerate(history, start=1):
                    run.log({"fit_step": step, **{f"training/{key}": value for key, value in values.items()
                                                 if value is not None}})
            for table_name, rows in evaluation_tables.items():
                if rows:
                    columns = list(dict.fromkeys(key for row in rows for key in row))
                    table = wandb.Table(columns=columns, data=[[row.get(key) for key in columns] for row in rows])
                    run.log({f"evaluation/{table_name}": table})
            run.log(logged_metrics)
            run.log_artifact(artifact)
            return {
                "run_id": run.id,
                "project": project,
                "entity": run.entity or entity,
                "mode": mode,
                "run_url": run.url if mode == "online" else None,
                "artifact_name": name,
                "run_directory": str(Path(run.dir).parent),
            }


def log_torch_capacity(
    *, name, model, preprocessor, numeric_features, categorical_features,
    metrics, baseline_metrics, config, history=None, evaluation_tables=None,
):
    """Log the fitted preprocessor and portable CPU state dict of a capacity MLP."""
    import skops.io
    from safetensors.torch import save_file

    def write_model(directory):
        state = {
            key: tensor.detach().to(device="cpu", copy=True).contiguous()
            for key, tensor in model.state_dict().items()
        }
        save_file(state, str(directory / "weights.safetensors"))
        skops.io.dump(preprocessor, directory / "preprocessor.skops")

    metadata = {
        "format_version": 1,
        "model_family": "pytorch_mlp",
        "architecture": repr(model),
        "dependencies": _dependencies("torch", "safetensors"),
        "input_contract": {
            "type": "pandas.DataFrame",
            "numeric_features": list(numeric_features),
            "categorical_features": list(categorical_features),
            "feature_stage": config.get("feature_stage", "Use columns prepared by the training notebook's feature engineering."),
            "inference": "Transform with preprocessor.skops, then run the MLP in eval mode on a dense float32 tensor.",
        },
        "target_transform": {
            "training": "log1p",
            "prediction": "expm1",
            "application": config.get("prediction_postprocess", "Apply expm1 once to the MLP output to get beds."),
        },
    }
    return _log_model(name, metadata, {"model": metrics, "baseline": baseline_metrics}, config, write_model,
                      history=history, evaluation_tables=evaluation_tables)


def log_sklearn_capacity(*, name, model, metrics, config, target_transform,
                         baseline_metrics=None, evaluation_tables=None, history=None):
    """Log a fitted count prediction pipeline and its output units and transform."""
    import skops.io

    def write_model(directory):
        skops.io.dump(model, directory / "model.skops")

    metadata = {
        "format_version": 1,
        "model_family": "scikit_learn",
        "dependencies": _dependencies(),
        "target_field": config.get("target_field", "capacity"),
        "target_unit": config.get("target_unit", "beds"),
        "input_contract": {
            "type": "pandas.DataFrame",
            "feature_names": list(getattr(model, "feature_names_in_", ())),
            "numeric_features": list(config.get("numeric_features", ())),
            "categorical_features": list(config.get("categorical_features", ())),
            "feature_stage": config.get("feature_stage", "Use columns prepared by the training notebook's feature engineering."),
            "inference": "Call model.predict with the named feature columns. The saved pipeline preprocesses inputs.",
        },
        "target_transform": {
            "training_and_inverse": target_transform,
            "application": config.get("prediction_postprocess", "The saved pipeline returns counts from predict."),
        },
    }
    groups = {"model": metrics}
    if baseline_metrics is not None:
        groups["baseline"] = baseline_metrics
    return _log_model(name, metadata, groups, config, write_model,
                      history=history, evaluation_tables=evaluation_tables)


def log_xgboost_capacity(*, name, model, preprocessor, metrics, baseline_metrics, config,
                         history=None, evaluation_tables=None):
    """Log the fitted capacity forest, preprocessor and aggregate scores."""
    import skops.io

    def write_model(directory):
        model.save_model(str(directory / "model.ubj"))
        skops.io.dump(preprocessor, directory / "preprocessor.skops")

    metadata = {
        "format_version": 1,
        "model_family": "xgboost_forest",
        "dependencies": _dependencies("xgboost"),
        "input_contract": {
            "type": "pandas.DataFrame",
            "feature_names": list(getattr(preprocessor, "feature_names_in_", ())),
            "numeric_features": list(config.get("numeric_features", ())),
            "categorical_features": list(config.get("categorical_features", ())),
            "feature_stage": "Prepare audited facilities with the same feature engineering and enrichment as the training notebook.",
            "missing_predictors": (
                "Numeric inputs retain NaN with a binary missing indicator. "
                "Missing categories use an explicit missing level."
            ),
            "load": "Load model.ubj with xgboost.XGBRegressor.load_model and load preprocessor.skops after reviewing its types.",
            "inference": "Call xgboost_capacity.predict_capacity(model, preprocessor, prepared_frame) to predict beds.",
        },
        "target_transform": {
            "training": "log1p",
            "prediction": "expm1",
            "application": "The inference helper applies expm1, rounds to whole beds and clamps at zero.",
        },
    }
    return _log_model(name, metadata, {"model": metrics, "baseline": baseline_metrics}, config, write_model,
                      history=history, evaluation_tables=evaluation_tables)
