"""Log fitted capacity models and aggregate evaluation metrics to W&B.

Both artifacts include metadata.json and metrics.json. Torch artifacts contain
weights.safetensors and preprocessor.skops. Scikit-learn artifacts contain a
single model.skops with the fitted preprocessing and target transformation.
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


def _log_model(name, metadata, metrics, config, write_model):
    import wandb

    project = os.environ.get("WANDB_PROJECT") or "datathon-f26"
    entity = os.environ.get("WANDB_ENTITY") or None
    mode = os.environ.get("WANDB_MODE") or "offline"
    config = _json_value(config)
    metrics = _json_value(metrics)
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
        for filename, contents in (
            ("metadata.json", {**metadata, "training_config": config}),
            ("metrics.json", metrics),
        ):
            (directory / filename).write_text(
                json.dumps(contents, indent=2, sort_keys=True, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        with wandb.init(
            project=project,
            entity=entity,
            mode=mode,
            name=name,
            job_type="capacity-training",
            config=config,
            settings=wandb.Settings(console="off", disable_git=True, save_code=False),
        ) as run:
            for path in sorted(directory.iterdir()):
                # The SDK copies mutable files to WANDB_DATA_DIR for later offline sync.
                artifact.add_file(str(path), policy="mutable")
            run.log(logged_metrics)
            run.log_artifact(artifact)
            return {
                "run_id": run.id,
                "project": project,
                "entity": run.entity or entity,
                "mode": mode,
                "artifact_name": name,
                "run_directory": str(Path(run.dir).parent),
            }


def log_torch_capacity(
    *, name, model, preprocessor, numeric_features, categorical_features,
    metrics, baseline_metrics, config,
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
            "feature_stage": "Use columns prepared by the training notebook's feature engineering.",
            "inference": "Transform with preprocessor.skops, then run the MLP in eval mode on a dense float32 tensor.",
        },
        "target_transform": {
            "training": "log1p",
            "prediction": "expm1",
            "application": config.get("prediction_postprocess", "Apply expm1 once to the MLP output to get beds."),
        },
    }
    return _log_model(name, metadata, {"model": metrics, "baseline": baseline_metrics}, config, write_model)


def log_sklearn_capacity(*, name, model, metrics, config, target_transform):
    """Log a fitted prediction pipeline whose predict method returns beds."""
    import skops.io

    def write_model(directory):
        skops.io.dump(model, directory / "model.skops")

    metadata = {
        "format_version": 1,
        "model_family": "scikit_learn",
        "dependencies": _dependencies(),
        "input_contract": {
            "type": "pandas.DataFrame",
            "feature_names": list(getattr(model, "feature_names_in_", ())),
            "numeric_features": list(config.get("numeric_features", ())),
            "categorical_features": list(config.get("categorical_features", ())),
            "feature_stage": "Use columns prepared by the training notebook's feature engineering.",
            "inference": "Call model.predict with the named feature columns. The saved pipeline preprocesses inputs.",
        },
        "target_transform": {
            "training_and_inverse": target_transform,
            "application": "The saved model applies any inverse transformation inside predict and returns beds.",
        },
    }
    return _log_model(name, metadata, {"model": metrics}, config, write_model)
