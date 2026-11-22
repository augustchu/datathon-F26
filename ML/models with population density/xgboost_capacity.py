"""Compare XGBoost forests on the existing, audited facility partitions."""

from hashlib import sha256

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.metrics import r2_score
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBRegressor

from density_data import DENSITY_FEATURES, NFHS_FEATURES
from facility_tool.count_models import CATEGORY_NAMES
from facility_tool.geo_audit import HOLD_REASONS


MISSING = {"", "-", "(missing)", "missing", "n/a", "na", "nan", "none", "null", "unknown", "not available", "not stated", "<na>"}
WRONG_LOCATION_FLAGS = (set(HOLD_REASONS) - {"missing_coordinates", "non_site_coordinate_quality"}) | {
    "declared_pin_conflicts_with_address", "multiple_explicit_address_pins", "pin_district_conflict",
}
CORE_NUMERIC = ("numberDoctors", "latitude", "longitude")
TEXT_FIELDS = ("affiliationTypeIds", "specialties", "procedure", "equipment", "capability")
METADATA_NUMERIC = (
    "distinct_social_media_presence_count", "number_of_facts_about_the_organization", "post_metrics_post_count",
    "engagement_metrics_n_followers", "engagement_metrics_n_likes", "engagement_metrics_n_engagements",
)
METADATA_CATEGORIES = ("affiliated_staff_presence", "custom_logo_presence")
DATE_FIELDS = ("recency_of_page_update", "post_metrics_most_recent_social_media_post_date")
SPLITS = ("train", "calibration", "test")
FORESTS = {
    "xgboost_random_forest": dict(n_estimators=1, num_parallel_tree=160, learning_rate=1.0),
    "xgboost_boosted_forest": dict(n_estimators=24, num_parallel_tree=8, learning_rate=0.12),
}


def _clean(values):
    values = values.astype("string").str.strip().str.lower()
    return values.mask(values.isin(MISSING | {"[]", "{}"}))


def _numeric(values):
    values = pd.to_numeric(_clean(values), errors="coerce").astype(float)
    return values.where(np.isfinite(values))


def _audit_index(frame, fields, name):
    required = {"facility_id", *fields}
    if not required.issubset(frame.columns) or frame["facility_id"].duplicated().any():
        raise ValueError(f"{name} must have unique facility IDs and columns {sorted(required)}.")
    return frame.set_index("facility_id")


def _wrong_location(flags):
    if not isinstance(flags, (list, tuple, np.ndarray)):
        raise ValueError("Location audit flags must be arrays. Rebuild notebook 02 for this snapshot.")
    return bool(WRONG_LOCATION_FLAGS.intersection(flags))


def _eligible_rows(frame, capacity_audit, doctor_audit, location_records, location_entity_review):
    frame = frame.reset_index(drop=True).copy()
    capacity = _audit_index(capacity_audit, ("status", "value", "split"), "Capacity audit")
    doctors = _audit_index(doctor_audit, ("status", "value", "split"), "Doctor-count audit")
    branches = _audit_index(location_entity_review, ("review_flags",), "Branch location audit")
    source = location_records.loc[location_records["source"].eq("fdr")].sort_values("input_position")
    required_ids = frame.loc[frame["facility_id"].fillna("").ne(""), "facility_id"].unique()
    # The count audit strips whitespace from IDs. Keep the original IDs for locations and overlay output.
    count_ids = pd.Index(required_ids).astype(str).str.strip()
    if (not count_ids.is_unique or not count_ids.isin(capacity.index).all()
            or not count_ids.isin(doctors.index).all() or not pd.Index(required_ids).isin(branches.index).all()):
        raise ValueError("The facility and audit checkpoints must come from the same snapshot with unambiguous IDs.")
    if (source["input_position"].to_list() != list(range(len(frame)))
            or source["facility_id"].fillna("").to_list() != frame["facility_id"].fillna("").to_list()
            or source["source_record_id"].fillna("").astype(str).to_list()
            != frame["unique_id"].fillna("").astype(str).to_list()):
        raise ValueError("Pass linked_fdr in its original order with the matching notebook 02 location audit.")
    capacity, doctors = (audit.reindex(count_ids).set_axis(required_ids) for audit in (capacity, doctors))
    for audit in (capacity, doctors):
        reported = audit["status"].eq("reported")
        values = _numeric(audit.loc[reported, "value"])
        if (not audit["split"].isin(SPLITS).all()
                or not audit["status"].isin(("reported", "missing", "conflicting", "unparseable")).all()
                or not (values.between(0, 2**53) & values.mod(1).eq(0)).all()):
            raise ValueError("Run notebook 03 to produce valid target audits and saved partitions.")
    if not capacity["split"].equals(doctors["split"]):
        raise ValueError("Use bed and doctor-count audits with the same saved facility partitions.")

    # A contradictory FDR record holds out its linked branch. A plain missing location stays eligible.
    wrong_source_ids = source.loc[source["review_flags"].map(_wrong_location), "facility_id"].fillna("")
    wrong_branches = branches.loc[required_ids, "review_flags"].map(_wrong_location)
    facility_id = frame["facility_id"]
    wrong_location = facility_id.isin(wrong_source_ids[wrong_source_ids.ne("")]) | facility_id.isin(
        wrong_branches.index[wrong_branches],
    )
    bad_category = pd.Series(False, index=frame.index)
    for column, categories in CATEGORY_NAMES.items():
        values = _clean(frame[column])
        bad_category |= values.notna() & ~values.isin(categories)
    bad_ids = facility_id[bad_category & facility_id.fillna("").ne("")]
    status = facility_id.map(capacity["status"])
    reason = np.select([
        facility_id.fillna("").eq(""), wrong_location, facility_id.isin(bad_ids),
        status.isin(("conflicting", "unparseable")),
    ], ["unlinked facility", "wrong location", "invalid facility or operator category", "unusable capacity"],
        default="eligible")
    exclusions = (pd.DataFrame({"facility_id": facility_id.fillna(""), "reason": reason, "capacity_status": status.fillna("unlinked")})
                  .loc[lambda rows: rows["reason"].ne("eligible")]
                  .groupby(["reason", "capacity_status"], sort=True)
                  .agg(source_rows=("facility_id", "size"), facilities=("facility_id", lambda ids: ids[ids.ne("")].nunique()))
                  .reset_index())
    keep = np.flatnonzero(reason == "eligible")
    # Erase coordinates with inadequate precision or missing point data before selecting predictors.
    no_site_point = source["review_flags"].map(
        lambda flags: "missing_coordinates" in flags or "non_site_coordinate_quality" in flags,
    ).to_numpy()
    frame.loc[no_site_point, ["latitude", "longitude"]] = np.nan
    frame = (frame.iloc[keep].sort_values(["facility_id", "unique_id"], kind="stable")
             .drop_duplicates("facility_id").set_index("facility_id"))
    frame["numberDoctors"] = doctors["value"].where(doctors["status"].eq("reported")).reindex(frame.index)
    return frame, capacity.reindex(frame.index), exclusions


def build_capacity_features(frame):
    """Prepare audited facility rows, with optional joined density and NFHS fields."""
    if "countries" not in frame.columns or not frame.columns.is_unique:
        raise ValueError("Preserve the original facility column order, including countries.")
    allowed = set(frame.columns[frame.columns.get_loc("countries") + 1:])
    columns = {}
    core, extra, categorical = [], [], []

    def numeric(name, values, block):
        columns[name] = values
        columns[f"{name}_missing"] = values.isna().astype(float)
        block.extend((name, f"{name}_missing"))

    def category(name, values, block):
        columns[name] = values.fillna("missing").astype(str)
        block.append(name)
        categorical.append(name)

    for name in CORE_NUMERIC:
        if name in allowed:
            numeric(name, _numeric(frame[name]), core)
    for name, categories in CATEGORY_NAMES.items():
        if name in allowed:
            category(name, _clean(frame[name]).map(categories), core)
    for name in TEXT_FIELDS:
        if name in allowed:
            numeric(f"{name}_length", _clean(frame[name]).str.len().astype(float), core)
    for name in (*METADATA_NUMERIC, DENSITY_FEATURES[0], *NFHS_FEATURES):
        if name in allowed:
            values = _numeric(frame[name])
            numeric(name, values.where(values.ge(0)), extra)
    for name in METADATA_CATEGORIES:
        if name in allowed:
            values = _clean(frame[name])
            category(name, values.where(values.isin(("true", "false"))), extra)
    for name in DATE_FIELDS:
        if name in allowed:
            values = pd.to_datetime(_clean(frame[name]).str[:10], format="%Y-%m-%d", errors="coerce")
            numeric(f"{name}_day", (values - pd.Timestamp("1970-01-01")).dt.days.astype(float), extra)
    return pd.DataFrame(columns, index=frame.index), core, extra, categorical


def predict_capacity(model, preprocessor, frame):
    """Predict whole, nonnegative beds from the prepared feature DataFrame."""
    log_beds = model.predict(preprocessor.transform(frame)).astype(float)
    beds = np.expm1(log_beds)
    if not np.isfinite(beds).all():
        raise ValueError("The forest produced a nonfinite bed estimate.")
    return np.maximum(0, np.rint(beds))


def _scores(name, actual, predicted):
    predicted = np.broadcast_to(predicted, actual.shape)
    delta = predicted - actual
    error = np.abs(delta)
    return dict(model=name, facilities=len(error), mae=float(error.mean()), median_ae=float(np.median(error)),
                p90_ae=float(np.quantile(error, .9)), rmse=float(np.sqrt(np.mean(delta ** 2))),
                r2=float(r2_score(actual, predicted)), mean_bias=float(delta.mean()), max_ae=float(error.max()))


def train_xgboost_capacity(frame, capacity_audit, doctor_audit, location_records, location_entity_review,
                          *, seed=42, n_jobs=2):
    """Use training labels to fit, calibration to select, and the saved test labels to score."""
    rows, labels, exclusions = _eligible_rows(frame, capacity_audit, doctor_audit, location_records, location_entity_review)
    features, core, extra, categories = build_capacity_features(rows)
    reported = labels["status"].eq("reported")
    train, validation, test = (reported & labels["split"].eq(split) for split in SPLITS)
    if not core or any(part.sum() < 2 for part in (train, validation, test)):
        raise ValueError("The audited data must supply facility predictors and at least two bed labels in each partition.")
    target = _numeric(labels["value"])
    baseline = float(np.rint(target.loc[train].median()))
    feature_sets = {"facility": core}
    if extra:
        feature_sets["all_safe_features"] = core + extra

    candidates, fitted = [], {}
    for feature_set, fields in feature_sets.items():
        categorical = [key for key in fields if key in categories]
        numeric = [key for key in fields if key not in categories]
        train_features = features.loc[train, fields]
        levels = [sorted({"missing", *train_features[key].unique()}) for key in categorical]
        processor = ColumnTransformer([
            ("numeric", "passthrough", numeric),
            ("category", OneHotEncoder(categories=levels, handle_unknown="ignore", sparse_output=False), categorical),
        ]).fit(train_features)
        training = processor.transform(train_features)
        calibration = processor.transform(features.loc[validation, fields])
        training_log = np.log1p(target.loc[train].to_numpy())
        calibration_log = np.log1p(target.loc[validation].to_numpy())
        models = {}
        for name, config in FORESTS.items():
            model = XGBRegressor(
                **config, objective="reg:squarederror", tree_method="hist", max_depth=5,
                min_child_weight=5, subsample=.8, colsample_bynode=.8, reg_lambda=2,
                n_jobs=n_jobs, random_state=seed,
            ).fit(training, training_log, eval_set=[(training, training_log), (calibration, calibration_log)], verbose=False)
            models[name] = model
            points = predict_capacity(model, processor, features.loc[validation, fields])
            candidates.append({"feature_set": feature_set, "features": len(fields),
                               **_scores(name, target.loc[validation].to_numpy(), points)})
        fitted[feature_set] = (models, processor, numeric, categorical)

    # Resolve equal MAEs in favor of the broader feature set, then the smaller model.
    candidate_metrics = pd.DataFrame(candidates).sort_values(["mae", "features", "model"], ascending=[True, False, False])
    winner = candidate_metrics.iloc[0]
    selected, selected_set = winner["model"], winner["feature_set"]
    models, processor, numeric, categorical = fitted[selected_set]
    training_history = {}
    for name, model in models.items():
        loss = model.evals_result()
        training_history[name] = [
            {"round": step, "train_rmse_log1p": train_loss, "validation_rmse_log1p": validation_loss}
            for step, (train_loss, validation_loss) in enumerate(zip(loss["validation_0"]["rmse"], loss["validation_1"]["rmse"]), 1)
        ]
    on_validation = candidate_metrics.loc[candidate_metrics["feature_set"].eq(selected_set)].to_dict("records")
    on_validation.append(_scores("training_median", target.loc[validation].to_numpy(), baseline))
    actual = target.loc[test].to_numpy()
    test_features = features.loc[test, feature_sets[selected_set]]
    on_test = [_scores(name, actual, predict_capacity(model, processor, test_features)) for name, model in models.items()]
    on_test.append(_scores("training_median", actual, baseline))

    missing = labels["status"].eq("missing")
    prediction_features = features.loc[missing, feature_sets[selected_set]]
    estimates = predict_capacity(models[selected], processor, prediction_features) if missing.any() else np.array([])
    predictions = pd.DataFrame({"facility_id": prediction_features.index, "estimated_capacity": estimates})
    values = features.loc[:, ~features.columns.str.endswith("_missing")]
    coverage = values.notna() & values.ne("missing")
    feature_coverage = pd.DataFrame({"train_observed": coverage.loc[train].sum(), "test_observed": coverage.loc[test].sum(),
                                     "prediction_observed": coverage.loc[missing].sum()}).rename_axis("predictor").reset_index()
    holdout = "\n".join(f"{facility_id}:{value:.0f}" for facility_id, value in target.loc[test].items())
    return {
        "models": models, "preprocessor": processor, "selected_model": selected, "selected_feature_set": selected_set,
        "training_history": training_history,
        "numeric_features": numeric, "categorical_features": categorical,
        "candidate_metrics": candidate_metrics.reset_index(drop=True), "validation_metrics": pd.DataFrame(on_validation),
        "test_metrics": pd.DataFrame(on_test), "exclusions": exclusions, "feature_coverage": feature_coverage,
        "predictions": predictions, "holdout_fingerprint": sha256(holdout.encode()).hexdigest(),
        "facility_counts": {"train": int(train.sum()), "validation": int(validation.sum()), "test": int(test.sum()),
                            "prediction": int(missing.sum())},
    }
