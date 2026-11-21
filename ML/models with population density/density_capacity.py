"""Train comparable capacity MLPs from already joined facility features."""

from hashlib import sha256

import numpy as np
import pandas as pd
import torch
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, QuantileTransformer
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from density_data import DENSITY_FEATURES, NFHS_FEATURES


NUMERIC = (
    "numberDoctors", "yearEstablished", "latitude", "longitude",
    "engagement_metrics_n_followers", "engagement_metrics_n_likes",
    "engagement_metrics_n_engagements", "post_metrics_post_count",
    "number_of_facts_about_the_organization", "distinct_social_media_presence_count",
)
CATEGORICAL = (
    "facilityTypeId", "operatorTypeId", "address_stateOrRegion",
    "organization_type", "affiliated_staff_presence", "custom_logo_presence",
)
TEXT = ("equipment", "capability", "procedure", "specialties", "affiliationTypeIds")
MISSING = {"null", "none", "nan", "n/a", "na", "", "-", "unknown"}


def _clean(series):
    text = series.astype("string").str.strip().str.lower()
    return text.mask(text.isin(MISSING))


def _features(frame, include_density, include_nfhs):
    numeric = [*NUMERIC, *(DENSITY_FEATURES if include_density else ()), *(NFHS_FEATURES if include_nfhs else ())]
    required = {*numeric, *CATEGORICAL, *TEXT, "capacity", "unique_id"}
    if missing := required - set(frame.columns):
        raise ValueError(f"Required facility features are missing: {sorted(missing)}")
    columns = {}
    for key in numeric:
        values = pd.to_numeric(_clean(frame[key]), errors="coerce").astype(float)
        columns[key] = values.where(np.isfinite(values))
    if include_density:
        columns[DENSITY_FEATURES[1]] = columns[DENSITY_FEATURES[0]].isna().astype(float)
    for key in TEXT:
        text = _clean(frame[key])
        columns[f"{key}_len"] = text.str.len().fillna(0).astype(float)
        columns[f"{key}_present"] = text.notna().astype(float)
    numeric = list(columns)
    for key in CATEGORICAL:
        value = _clean(frame[key])
        columns[key] = value.astype(object).where(value.notna(), np.nan)
    return pd.DataFrame(columns, index=frame.index), numeric


def _facility_groups(frame):
    """Link rows that share a source ID or a facility cluster."""
    ids = _clean(frame["unique_id"])
    clusters = _clean(frame["cluster_id"]) if "cluster_id" in frame else pd.Series(pd.NA, index=frame.index)
    parents = {}

    def find(key):
        while key in parents:
            key = parents[key]
        return key

    for source_id, cluster in zip(ids, clusters):
        if pd.notna(source_id) and pd.notna(cluster):
            source, clustered = find(f"id:{source_id}"), find(f"cluster:{cluster}")
            if source != clustered:
                left, right = sorted((source, clustered))
                parents[right] = left
    return pd.Series(
        [find(f"id:{ident}") if pd.notna(ident) else find(f"cluster:{cluster}") if pd.notna(cluster) else None
         for ident, cluster in zip(ids, clusters)],
        index=frame.index,
    )


def _partitions(groups, seed):
    distinct = np.asarray(sorted(groups.unique()))
    if len(distinct) < 10:
        raise ValueError("At least ten distinct facilities with valid bed counts are needed for three partitions.")
    train_val, test = next(GroupShuffleSplit(n_splits=1, test_size=.20, random_state=seed).split(distinct, groups=distinct))
    train, val = next(GroupShuffleSplit(n_splits=1, test_size=.25, random_state=seed).split(
        distinct[train_val], groups=distinct[train_val],
    ))
    partitions = [distinct[train_val[train]], distinct[train_val[val]], distinct[test]]
    plan = "\n".join(f"{split}:{ident}" for split, ids in enumerate(partitions) for ident in ids)
    return [groups.isin(ids).to_numpy() for ids in partitions], sha256(plan.encode()).hexdigest()


def _scores(actual, predicted):
    error = np.abs(actual - predicted)
    scores = {
        "mae": float(mean_absolute_error(actual, predicted)),
        "median_ae": float(np.median(error)),
        "p90_ae": float(np.quantile(error, .9)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
    }
    if len(actual) > 1:
        scores["r2"] = float(r2_score(actual, predicted))
    return scores


def train_capacity(frame, *, include_nfhs, include_density=True, seed=42, epochs=300, patience=30):
    """Fit preprocessing and weights on train only and use validation for early stopping."""
    frame = frame.reset_index(drop=True)
    features, numeric = _features(frame, include_density, include_nfhs)
    target = pd.to_numeric(_clean(frame["capacity"]), errors="coerce").astype(float)
    groups = _facility_groups(frame)
    usable = np.isfinite(target) & (target >= 0) & (target.mod(1) == 0) & groups.notna()
    groups, features, target = (values.loc[usable].reset_index(drop=True) for values in (groups, features, target))
    # Use only shared source columns so join data and source row order cannot change batching.
    common = features.drop(columns=[*DENSITY_FEATURES, *NFHS_FEATURES], errors="ignore")
    source_ids = _clean(frame.loc[usable, "unique_id"]).reset_index(drop=True)
    source_hash = pd.util.hash_pandas_object(
        common.assign(source_id=source_ids, group=groups, beds=target), index=False,
    )
    order = np.argsort(source_hash.to_numpy(), kind="stable")
    groups, features, target = (values.iloc[order].reset_index(drop=True) for values in (groups, features, target))
    (train, validation, test), fingerprint = _partitions(groups, seed)
    holdout = sha256(source_hash.to_numpy(dtype="<u8")[order][test].tobytes()).hexdigest()
    density = features[DENSITY_FEATURES[0]].notna().to_numpy() if include_density else None
    if density is not None and not (density & train).any():
        raise ValueError("No training facilities matched a density value. Check the input keys, columns and units.")
    indicators = [field for field in numeric if field.endswith("_present") or field == DENSITY_FEATURES[1]]
    continuous = [field for field in numeric if field not in indicators]
    # Fit a bounded map on training rows. Unseen extremes saturate at its endpoints.
    # Keep indicators at 0 or 1 so a rare training value cannot amplify their magnitude.
    processor = ColumnTransformer([
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                              ("quantile", QuantileTransformer(
                                  n_quantiles=min(1000, int(train.sum())), output_distribution="uniform",
                                  subsample=None,
                              ))]), continuous),
        ("indicators", "passthrough", indicators),
        ("categorical", Pipeline([("impute", SimpleImputer(strategy="most_frequent", keep_empty_features=True)),
                                  ("encode", OneHotEncoder(handle_unknown="ignore", min_frequency=5,
                                                           sparse_output=False))]), list(CATEGORICAL)),
    ]).fit(features.loc[train])

    torch.manual_seed(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def tensor(mask):
        return torch.as_tensor(processor.transform(features.loc[mask]), dtype=torch.float32, device=device)

    def log_target(mask):
        return torch.as_tensor(np.log1p(target.loc[mask].to_numpy()), dtype=torch.float32, device=device)[:, None]

    x_train, x_val = tensor(train), tensor(validation)
    y_val = log_target(validation)
    loader = DataLoader(
        TensorDataset(x_train, log_target(train)), batch_size=256, shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    model = nn.Sequential(
        nn.Linear(x_train.shape[1], 128), nn.ReLU(), nn.Dropout(.2),
        nn.Linear(128, 64), nn.ReLU(), nn.Dropout(.2), nn.Linear(64, 1),
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, factor=.5, patience=10)
    loss = nn.HuberLoss(delta=1)
    best_loss, bad_epochs, best_weights = float("inf"), 0, None
    for _ in range(epochs):
        model.train()
        for inputs, labels in loader:
            optimizer.zero_grad()
            loss(model(inputs), labels).backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            observed = float(loss(model(x_val), y_val))
        scheduler.step(observed)
        if observed < best_loss - 1e-4:
            best_loss, bad_epochs = observed, 0
            best_weights = {key: value.detach().clone() for key, value in model.state_dict().items()}
        else:
            bad_epochs += 1
            if bad_epochs >= patience:
                break
    if best_weights is None:
        raise ValueError("Training did not produce finite validation predictions.")
    model.load_state_dict(best_weights)
    model.eval()
    with torch.inference_mode():
        prediction = np.maximum(0, np.rint(np.expm1(model(tensor(test)).flatten().cpu().numpy())))
    actual = target.loc[test].to_numpy()
    baseline = np.full(len(actual), round(float(target.loc[train].median())))
    scores = _scores(actual, prediction)
    coverage = [{"cohort": "all", "rows": len(actual), **scores}]
    if density is not None:
        test_matched = density[test]
        for name, mask in (("density matched", test_matched), ("density missing", ~test_matched)):
            if mask.any():
                coverage.append({"cohort": name, "rows": int(mask.sum()), **_scores(actual[mask], prediction[mask])})
    return {
        "model": model, "preprocessor": processor, "numeric_features": numeric,
        "categorical_features": list(CATEGORICAL), "test_metrics": scores,
        "baseline_metrics": _scores(actual, baseline), "test_cohorts": pd.DataFrame(coverage),
        "split_fingerprint": fingerprint, "holdout_fingerprint": holdout, "facility_counts": {
            name: int(groups.loc[mask].nunique())
            for name, mask in (("train", train), ("validation", validation), ("test", test))
        },
        "row_counts": {
            name: int(mask.sum()) for name, mask in (("train", train), ("validation", validation), ("test", test))
        },
    }
