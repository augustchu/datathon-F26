"""Capacity regression from columns after a specified schema boundary.

The model learns from recorded capacities only. Its held-out score does not
establish accuracy for the many facilities with missing capacity, and capacity
mentions in other columns can make the score optimistic. No source
values are overwritten and no missing targets are imputed.
"""

import hashlib
import json
import re
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline


_MISSING = {"", "nan", "none", "null", "na", "n/a", "unknown", "<na>", "nat"}
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_COMMA_NUMBER = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def cell_text(value):
    """Stable text for scalars, Spark Rows, arrays, structs and list columns."""
    if value is None or value is pd.NA:
        return ""
    if hasattr(value, "asDict"):
        value = value.asDict(recursive=True)
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in _MISSING else text


def parse_number(value):
    """Parse an unambiguous finite number; do not guess ranges or unit strings."""
    if isinstance(value, (bool, np.bool_)):
        return np.nan
    text = cell_text(value)
    if _COMMA_NUMBER.fullmatch(text):
        text = text.replace(",", "")
    if not _NUMBER.fullmatch(text):
        return np.nan
    result = float(text)
    return result if np.isfinite(result) else np.nan


def _texts(series):
    return series.map(cell_text)


class FacilityEncoder(BaseEstimator, TransformerMixin):
    """Train-only numeric, categorical and bounded text encodings per column.

    All nonconstant columns are represented. Text vocabularies are deliberately
    capped; this is not a lossless representation of every unique string.
    Missing and novel values have explicit handling. Output is sparse float32.
    """

    def __init__(self, max_text_features=128, max_categories=40):
        self.max_text_features = max_text_features
        self.max_categories = max_categories

    def fit(self, X, y=None):
        self.columns_ = list(X.columns)
        self.specs_, audit, feature_names = [], [], []
        for column in self.columns_:
            series = X[column]
            text = _texts(series)
            nonempty = text[text.ne("")]
            missing = float(text.eq("").mean())
            unique = int(nonempty.nunique())
            spec = {"column": column, "kind": "excluded", "names": []}
            reason = "all missing in training" if unique == 0 else "constant in training"
            if text.nunique() > 1:
                numeric = series.map(parse_number)
                parse_rate = numeric.notna().sum() / max(len(nonempty), 1)
                if parse_rate >= 0.95:
                    spec.update(kind="numeric", median=float(numeric.median()),
                                names=[f"{column}::value", f"{column}::missing"])
                elif unique <= self.max_categories and nonempty.str.len().mean() <= 80:
                    categories = sorted(nonempty.unique().tolist())
                    spec.update(kind="categorical", categories=categories,
                                names=[f"{column}::category={c}" for c in categories]
                                + [f"{column}::missing", f"{column}::unknown"])
                else:
                    vectorizer = TfidfVectorizer(max_features=self.max_text_features,
                                                min_df=2, dtype=np.float32,
                                                token_pattern=r"(?u)\b\w+\b")
                    try:
                        vectorizer.fit(text.tolist())
                        tokens = vectorizer.get_feature_names_out().tolist()
                    except ValueError as exc:
                        if "empty vocabulary" not in str(exc) and "no terms remain" not in str(exc).lower():
                            raise
                        vectorizer, tokens = None, []
                    spec.update(kind="text", vectorizer=vectorizer,
                                names=[f"{column}::token={t}" for t in tokens]
                                + [f"{column}::characters", f"{column}::words", f"{column}::missing"])
                reason = "retained; encoding learned from training rows"
            self.specs_.append(spec)
            feature_names.extend(spec["names"])
            audit.append({"column": column, "training_dtype": str(series.dtype),
                          "encoding": spec["kind"], "training_nonmissing_unique": unique,
                          "training_missing_fraction": missing,
                          "encoded_features": len(spec["names"]), "note": reason,
                          "feature_names": json.dumps(spec["names"], ensure_ascii=False)})
        if not feature_names:
            raise ValueError("No varying predictors remain in the training data.")
        self.feature_names_ = np.asarray(feature_names, dtype=object)
        self.audit_ = pd.DataFrame(audit)
        return self

    def transform(self, X):
        missing_columns = set(self.columns_) - set(X.columns)
        if missing_columns:
            raise ValueError(f"Missing predictor columns: {sorted(missing_columns)}")
        blocks = []
        for spec in self.specs_:
            kind, column = spec["kind"], spec["column"]
            if kind == "excluded":
                continue
            text = _texts(X[column])
            if kind == "numeric":
                numeric = X[column].map(parse_number)
                values = numeric.fillna(spec["median"]).to_numpy(dtype=float)
                # sklearn tree inputs are float32; bound pathological magnitudes.
                values = np.clip(values, -1e30, 1e30)
                block = np.column_stack([values, numeric.isna().to_numpy(dtype=float)])
            elif kind == "categorical":
                categories = spec["categories"]
                block = np.column_stack([text.eq(c).to_numpy() for c in categories]
                                        + [text.eq("").to_numpy(),
                                           (~text.isin(categories) & text.ne("")).to_numpy()])
            else:
                if spec["vectorizer"] is not None:
                    blocks.append(spec["vectorizer"].transform(text.tolist()))
                block = np.column_stack([text.str.len().to_numpy(),
                                         text.str.split().str.len().to_numpy(),
                                         text.eq("").to_numpy()])
            blocks.append(sparse.csr_matrix(block, dtype=np.float32))
        return sparse.hstack(blocks, format="csr", dtype=np.float32)

    def get_feature_names_out(self, input_features=None):
        return self.feature_names_.copy()


def _groups(X, group_values=None):
    """Union exact predictor duplicates and, if supplied, shared facility IDs."""
    parents = np.arange(len(X))

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def merge(keys):
        seen = {}
        for i, key in enumerate(keys):
            if key == "":
                continue
            if key in seen:
                parents[root(i)] = root(seen[key])
            else:
                seen[key] = i

    text = X.apply(lambda s: _texts(s))
    row_keys = [hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()
                for row in text.itertuples(index=False, name=None)]
    merge(row_keys)
    if group_values is not None:
        merge(_texts(group_values).tolist())
    # Stable group labels make the seeded split independent of input row order.
    labels = {}
    for i, key in enumerate(row_keys):
        group_root = root(i)
        labels[group_root] = min(key, labels.get(group_root, key))
    return np.array([labels[root(i)] for i in range(len(X))])


def _leakage_flags(X, y):
    records = []
    for column in X:
        text, numeric = _texts(X[column]), X[column].map(parse_number)
        observed = numeric.notna().to_numpy()
        if re.search(r"bed|capacity", column, re.I):
            records.append({"column": column, "flag": "capacity_or_bed_related_name",
                            "evidence": "Column name may encode the target or a close proxy."})
        if observed.sum() >= 5:
            agreement = np.isclose(numeric.to_numpy()[observed], y[observed], rtol=0, atol=1e-8).mean()
            if agreement >= 0.95:
                records.append({"column": column, "flag": "near_duplicate_target",
                                "evidence": f"{agreement:.1%} exact agreement on {observed.sum()} paired training values."})
        mentions = text.str.contains(r"\b\d[\d,]*(?:\.\d+)?\s*[- ]?\s*beds?\b|\bbeds?\s*[:=]?\s*\d", case=False, regex=True).sum()
        if mentions:
            records.append({"column": column, "flag": "bed_count_in_text",
                            "evidence": f"Numeric bed mentions in {mentions} training rows."})
    return pd.DataFrame(records, columns=["column", "flag", "evidence"])


def run_capacity_rf(data, target="capacity", after_column="countries", group_column=None, test_size=0.2,
                    random_state=42, n_estimators=300, permutation_repeats=3,
                    max_text_features=128, max_categories=40, n_jobs=2):
    """Fit once and report held-out regression; never changes the input table.

    Exact predictor duplicates are kept together. Supply a reliable facility ID
    as group_column to also keep repeat records for that facility together.
    Only columns strictly after after_column in the supplied schema are used as
    predictors, excluding target. Earlier columns can be used for grouping only.
    test_size is a fraction of groups, so the realized row fraction can differ.
    """
    if not isinstance(data, pd.DataFrame) or not data.columns.is_unique:
        raise ValueError("Pass a pandas DataFrame with unique column names.")
    if any(not isinstance(c, str) for c in data.columns):
        raise ValueError("Column names must be strings.")
    if target not in data:
        raise ValueError(f"Target column {target!r} not found. Available: {list(data.columns)}")
    if after_column not in data:
        raise ValueError(f"Boundary column {after_column!r} not found. Available: {list(data.columns)}")
    if group_column is not None and group_column not in data:
        raise ValueError(f"Grouping column {group_column!r} not found.")
    if group_column == target:
        raise ValueError("Use a facility identifier for grouping, not the capacity target.")
    boundary = data.columns.get_loc(after_column)
    predictors = [c for c in data.columns[boundary + 1:] if c != target]
    if not predictors:
        raise ValueError(f"No predictor columns remain after {after_column!r}, excluding {target!r}.")
    selection = pd.DataFrame({"column": list(data.columns), "role": [
        "target" if c == target else "predictor" if c in predictors else
        "grouping_only" if c == group_column else "excluded_at_or_before_boundary"
        for c in data.columns]})
    if len(data) > 50000 or len(data.columns) > 200:
        raise ValueError("This local-memory workflow is limited to 50,000 rows and 200 columns.")
    if not 0 < test_size < 1 or permutation_repeats < 1:
        raise ValueError("Use 0 < test_size < 1 and at least one permutation repeat.")
    raw_target = _texts(data[target])
    numeric_target = data[target].map(parse_number)
    status = np.where(raw_target.eq(""), "missing", np.where(numeric_target.isna(), "unparseable",
                      np.where(numeric_target.lt(0), "negative", "valid")))
    target_audit = pd.DataFrame({"source_row": np.arange(len(data)), "raw_capacity": raw_target.to_numpy(),
                                 "parsed_capacity": numeric_target.to_numpy(), "status": status})
    valid = status == "valid"
    if valid.sum() < 20:
        raise ValueError(f"Need at least 20 valid nonnegative numeric capacities; found {valid.sum()}.")
    X = data.loc[valid, predictors].copy().reset_index(drop=True)
    y = numeric_target.to_numpy(dtype=float)[valid]
    source_rows = np.flatnonzero(valid)
    group_values = data.loc[valid, group_column].reset_index(drop=True) if group_column is not None else None
    # Canonical ordering also stabilizes forest bootstraps when Spark changes row order.
    canonical = [hashlib.sha256(json.dumps([row, float(label)], ensure_ascii=False).encode()).hexdigest()
                 for row, label in zip(X.apply(_texts).itertuples(index=False, name=None), y)]
    order = np.argsort(canonical, kind="stable")
    X, y, source_rows = X.iloc[order].reset_index(drop=True), y[order], source_rows[order]
    if group_values is not None:
        group_values = group_values.iloc[order].reset_index(drop=True)
    groups = _groups(X, group_values)
    if np.unique(groups).size < 5:
        raise ValueError("Need at least five independent facility/duplicate groups for this evaluation.")
    train, test = next(GroupShuffleSplit(n_splits=1, test_size=test_size,
                                        random_state=random_state).split(X, y, groups))
    if len(train) < 10 or len(test) < 2 or np.unique(y[train]).size < 2:
        raise ValueError("Split has too few train/test rows or no variation in training capacity.")
    X_train, X_test = X.iloc[train], X.iloc[test]
    y_train, y_test = y[train], y[test]
    pipeline = Pipeline([
        ("encode", FacilityEncoder(max_text_features, max_categories)),
        ("forest", RandomForestRegressor(n_estimators=n_estimators, max_features=0.7,
                                         min_samples_leaf=2, n_jobs=n_jobs,
                                         random_state=random_state))])
    pipeline.fit(X_train, y_train)
    prediction = pipeline.predict(X_test)

    def metrics(name, prediction):
        return {"model": name, "test_rows": len(test),
                "mae": float(mean_absolute_error(y_test, prediction)),
                "rmse": float(np.sqrt(mean_squared_error(y_test, prediction))),
                "r2": float(r2_score(y_test, prediction)) if np.unique(y_test).size > 1 else np.nan}

    metric_rows = [metrics("random_forest", prediction)]
    for strategy in ("mean", "median"):
        baseline = DummyRegressor(strategy=strategy).fit(np.zeros((len(train), 1)), y_train)
        metric_rows.append(metrics(f"baseline_{strategy}", baseline.predict(np.zeros((len(test), 1)))))
    baseline_mae = metric_rows[0]["mae"]
    rng, importance = np.random.default_rng(random_state), []
    for column in X.columns:
        differences = []
        for _ in range(permutation_repeats):
            shuffled = X_test.copy()
            shuffled[column] = X_test[column].to_numpy()[rng.permutation(len(test))]
            differences.append(mean_absolute_error(y_test, pipeline.predict(shuffled)) - baseline_mae)
        importance.append({"column": column, "mae_increase_mean": float(np.mean(differences)),
                           "mae_increase_std": float(np.std(differences)), "repeats": permutation_repeats})
    predictions = pd.DataFrame({"source_row": source_rows[test], "group": groups[test],
                                "actual_capacity": y_test, "predicted_capacity": prediction,
                                "residual": y_test - prediction})
    split = pd.DataFrame({"source_row": source_rows, "group": groups,
                          "split": np.where(np.isin(np.arange(len(X)), train), "train", "test")})
    return {"pipeline": pipeline, "metrics": pd.DataFrame(metric_rows), "predictions": predictions,
            "importance": pd.DataFrame(importance).sort_values("mae_increase_mean", ascending=False),
            "feature_audit": pipeline.named_steps["encode"].audit_.copy(),
            "column_selection": selection,
            "target_audit": target_audit, "leakage_flags": _leakage_flags(X_train, y_train),
            "split": split,
            "summary": {"input_rows": len(data), "valid_capacity_rows": int(valid.sum()),
                        "missing_capacity_rows": int((status == "missing").sum()),
                        "rejected_capacity_rows": int(np.isin(status, ["unparseable", "negative"]).sum()),
                        "zero_capacity_rows": int((y == 0).sum()), "predictor_columns": list(X.columns),
                        "after_column": after_column,
                        "encoded_features": len(pipeline.named_steps["encode"].feature_names_),
                        "train_rows": len(train), "test_rows": len(test),
                        "test_row_fraction": len(test) / len(X), "group_column": group_column,
                        "groups": int(np.unique(groups).size), "seed": random_state,
                        "n_estimators": n_estimators, "permutation_repeats": permutation_repeats,
                        "limitations": ["Evaluation applies to records with observed valid capacity.",
                                        "Selected predictors can repeat capacity or bed counts.",
                                        "Without a reliable group column, nonidentical duplicates may cross splits.",
                                        "Text vocabularies are capped and importance is predictive, not causal.",
                                        "Permutation spread is not a confidence interval; correlated columns can share importance."]}}
