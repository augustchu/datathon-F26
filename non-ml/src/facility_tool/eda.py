"""Audit candidate count targets before choosing an estimator.

The caller supplies data and a canonical facility ID. This module reads no
tables and fits no models. Whole-workspace data-quality summaries are separate
from predictor relationships computed on the proposed training facilities.
"""

from datetime import date
from itertools import combinations
import re

import numpy as np
import pandas as pd


UPDATE_COLUMNS = [
    "facility_id", "field", "method", "value", "unit", "lower", "upper",
    "provenance", "detail",
]
COUNT_UNITS = {"capacity": "beds", "numberDoctors": "doctors"}
STRUCTURED_CATEGORIES = ("facilityTypeId", "operatorTypeId", "area", "affiliated_staff_presence", "organization_type")
STRUCTURED_NUMERIC = (*COUNT_UNITS, "yearEstablished")
DEFAULT_CATEGORIES = STRUCTURED_CATEGORIES[:2]
MISSING_TEXT = {"", "n/a", "na", "none", "nan", "null", "unknown", "<na>", "[]", "not stated"}
COUNT_NUMBER = r"(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?:\.0+)?"
COUNT_PATTERNS = {
    target: re.compile(rf"(?:{unit}s?(?:\s*:\s*|\s+))?({COUNT_NUMBER})(?:[\s-]+{unit}s?)?", re.I)
    for target, unit in (("capacity", "bed"), ("numberDoctors", "doctor"))
}
AUDIT_GROUPS = {
    "source": ("source_types", "source"),
    "state": ("state", "address_stateOrRegion"),
    "district": ("district", "address_district"),
    "facility_type": ("facilityTypeId",),
    "operator_type": ("operatorTypeId",),
}


def _text(value):
    if not pd.api.types.is_scalar(value):
        return str(value)
    return "" if pd.isna(value) else str(value).strip()


def _missing(value):
    return _text(value).casefold() in MISSING_TEXT


def parse_count(value, target):
    """Return a literal nonnegative integer count, or NaN if it cannot be read."""
    if target not in COUNT_UNITS:
        raise ValueError(f"Choose an available count target from {list(COUNT_UNITS)}.")
    if isinstance(value, (bool, np.bool_)):
        return np.nan
    match = COUNT_PATTERNS[target].fullmatch(_text(value))
    if match is None:
        return np.nan
    text = match.group(1).replace(",", "").split(".")[0].lstrip("0") or "0"
    # Preserve values that fit exactly in the floating-point column.
    return float(int(text)) if len(text) <= 16 and int(text) <= 2**53 else np.nan


def _parse_status(raw, parsed):
    if _missing(raw):
        return "missing"
    if pd.notna(parsed):
        return "exact_count"
    text = _text(raw)
    if re.search(r"\d\s*(?:[-–—]|\bto\b)\s*\d|[~<>≤≥+]|\b(?:about|over|under|approximately|at least)\b", text, re.I):
        return "range_or_qualified_count"
    if re.fullmatch(r"-\d[\d,.]*\s*(?:beds?|doctors?)?", text, re.I):
        return "negative_count"
    if re.search(r"\b\d[\d,]*\.\d*[1-9]\d*\b", text):
        return "fractional_count"
    return "unclear_unit_or_format"


def _audit_input(frame, target):
    if target not in COUNT_UNITS or target not in frame:
        raise ValueError(f"Choose an available count target from {list(COUNT_UNITS)}.")
    if "facility_id" not in frame or not frame.columns.is_unique:
        raise ValueError("Provide canonical facility_id values and unique column names.")
    audit = pd.DataFrame({"source_row": np.arange(len(frame)), "facility_id": frame["facility_id"].map(_text),
                          "raw_value": frame[target].map(_text)})
    if "unique_id" in frame:
        audit["source_record_id"] = frame["unique_id"].map(_text).to_numpy()
    audit["value"] = frame[target].map(lambda value: parse_count(value, target)).to_numpy()
    audit["parse_status"] = [_parse_status(raw, parsed) for raw, parsed in zip(frame[target], audit["value"])]
    audit.loc[audit["facility_id"].map(_missing), "facility_id"] = ""
    return audit.reset_index(drop=True)


def _facility_target_audit(source_audit):
    rows = []
    for facility_id, group in source_audit.loc[source_audit["facility_id"].ne("")].groupby("facility_id", sort=True):
        known = group["value"].dropna().unique()
        ambiguous = bool((~group["parse_status"].isin(["exact_count", "missing"])).any())
        if len(known) > 1 or (len(known) == 1 and ambiguous):
            status = "conflicting"
        elif len(known) == 1:
            status = "reported"
        elif ambiguous:
            status = "unparseable"
        else:
            status = "missing"
        rows.append({
            "facility_id": facility_id, "status": status, "source_rows": len(group),
            "raw_values": sorted({raw for raw in group["raw_value"] if raw}),
            "value": float(known[0]) if status == "reported" else np.nan,
        })
    return pd.DataFrame(rows, columns=["facility_id", "status", "source_rows", "raw_values", "value"])


def reported_count_updates(frame, target):
    """Expose FDR counts with unambiguous values alongside their original text."""
    source_audit = _audit_input(frame, target)
    audit = _facility_target_audit(source_audit)
    exact = source_audit.loc[source_audit["parse_status"].eq("exact_count")]
    records = (exact.groupby("facility_id")["source_record_id"].agg(lambda ids: sorted(set(ids) - {""})).to_dict()
               if "source_record_id" in exact else {})
    rows = []
    for row in audit.loc[audit["status"].eq("reported")].itertuples(index=False):
        ids = records.get(row.facility_id, [])
        provenance = f"FDR field {target}"
        if ids:
            provenance += f"; source record IDs: {', '.join(ids)}"
        rows.append({
            "facility_id": row.facility_id, "field": target, "method": "reported",
            "value": row.value, "unit": COUNT_UNITS[target], "lower": None, "upper": None,
            "provenance": provenance,
            "detail": f"Recorded value: {', '.join(row.raw_values)}. This is not a live availability count.",
        })
    return pd.DataFrame(rows, columns=UPDATE_COLUMNS)


def _columns(frame, target, ordered_columns, feature_columns):
    schema = list(ordered_columns)
    if "countries" not in schema or len(schema) != len(set(schema)):
        raise ValueError("Supply unique original FDR columns in order, including 'countries'.")
    eligible = set(schema[schema.index("countries") + 1:])
    allowed = {*STRUCTURED_CATEGORIES, *STRUCTURED_NUMERIC} - {target}
    available = allowed & eligible & set(frame.columns)
    if feature_columns is None:
        defaults = [*DEFAULT_CATEGORIES, *(field for field in STRUCTURED_NUMERIC if field != target)]
        return eligible, [field for field in defaults if field in available]
    requested = list(dict.fromkeys(feature_columns))
    invalid = set(requested) - available
    if invalid:
        raise ValueError(f"Unsupported EDA predictors: {sorted(invalid)}. Choose structured fields after 'countries'.")
    return eligible, requested


def _established_year(value, reference_year):
    match = re.fullmatch(r"(\d{4})(?:\.0+)?", _text(value))
    if match:
        year = int(match.group(1))
        if 1000 <= year <= reference_year:
            return float(year)
    return np.nan


def _one_value(values, field, reference_year):
    if field in STRUCTURED_NUMERIC:
        if field == "yearEstablished":
            parsed = values.map(lambda value: _established_year(value, reference_year))
        else:
            parsed = values.map(lambda value: parse_count(value, field))
        known = parsed.dropna().unique()
        return known[0] if len(known) == 1 else np.nan
    known = {text.casefold() for text in values.map(_text) if text.casefold() not in MISSING_TEXT}
    if len(known) > 1:
        return "(conflicting)"
    return next(iter(known), "(missing)")


def _facilities(frame, source_audit, features, seed, reference_year):
    groups = {name: next((field for field in fields if field in frame), None)
              for name, fields in AUDIT_GROUPS.items()}
    groups = {name: field for name, field in groups.items() if field is not None}
    audit = _facility_target_audit(source_audit)
    columns = sorted(set(features) | set(groups.values()))
    ids = source_audit["facility_id"].to_numpy()
    rows = []
    for facility_id, group in frame.loc[ids != ""].groupby(ids[ids != ""], sort=True):
        rows.append({"facility_id": facility_id,
                     **{field: _one_value(group[field], field, reference_year) for field in columns}})
    collapsed = pd.DataFrame(rows, columns=["facility_id", *columns])
    facilities = audit.merge(collapsed, on="facility_id", validate="one_to_one")
    # Sort before shuffling so partitions do not depend on table read order.
    ids = np.asarray(sorted(facilities["facility_id"]), dtype=object)
    shuffled = np.random.default_rng(seed).permutation(ids)
    holdout = int(len(ids) * 0.2)
    partitions = {facility_id: "test" for facility_id in shuffled[:holdout]}
    partitions.update({facility_id: "calibration" for facility_id in shuffled[holdout:2 * holdout]})
    facilities["split"] = facilities["facility_id"].map(partitions).fillna("train")
    return facilities, groups


def _column_audit(frame, ordered_columns, target, eligible, features, train):
    quality, missingness = [], []
    unit = r"beds?" if target == "capacity" else r"doctors?"
    count_mention = rf"\b\d[\d,]*[-\s]+{unit}\b|\b{unit}\s*[:=]\s*\d"
    for field in ordered_columns:
        if field not in frame:
            missingness.append({"field": field, "status": "not_loaded", "source_rows": 0,
                                "raw_missing": None, "raw_missing_fraction": None})
            continue
        # These raw column counts describe the source and use every source row.
        text = frame[field].map(_text).astype("string")
        missing = text.str.casefold().isin(MISSING_TEXT)
        missingness.append({"field": field, "status": "loaded", "source_rows": len(text),
                            "raw_missing": int(missing.sum()), "raw_missing_fraction": missing.mean()})
        if field == target:
            role = "target"
        elif field not in eligible:
            role = "outside_predictor_boundary"
        elif field in features:
            role = "selected_for_eda"
        else:
            role = "not_selected"
        mentions = int(text.str.contains(count_mention, case=False, regex=True).sum()) if field != target else 0
        pair_count, agreement = 0, None
        if field in features and field in COUNT_UNITS:
            pairs = train[[field, "value"]].dropna()
            pair_count = len(pairs)
            agreement = float(pairs[field].eq(pairs["value"]).mean()) if pair_count else None
        quality.append({"field": field, "role": role, "target_count_mentions_source_rows": mentions,
                        "training_exact_match_pairs": pair_count, "training_exact_match_fraction": agreement})
    return pd.DataFrame(quality), pd.DataFrame(missingness)


def _numeric_row(frame, feature):
    pairs = frame[[feature, "value"]].dropna()
    varying = len(pairs) >= 3 and pairs[feature].nunique() > 1 and pairs["value"].nunique() > 1
    return {
        "paired_facilities": len(pairs),
        "pearson": pairs[feature].corr(pairs["value"]) if varying else np.nan,
        "spearman": pairs[feature].rank().corr(pairs["value"].rank()) if varying else np.nan,
    }


def _coverage_counts(frame):
    counts = frame["status"].value_counts()
    return {
        "facilities": len(frame), "reported": int(counts.get("reported", 0)),
        "missing": int(counts.get("missing", 0)), "unparseable": int(counts.get("unparseable", 0)),
        "conflicting": int(counts.get("conflicting", 0)),
    }


def _stratum(frame):
    known = frame["value"].dropna()
    return {
        **_coverage_counts(frame),
        "median_target": known.median() if len(known) else np.nan,
        "target_q25": known.quantile(0.25) if len(known) else np.nan,
        "target_q75": known.quantile(0.75) if len(known) else np.nan,
    }


def _relation_tables(train, features):
    numeric = [field for field in features if field in STRUCTURED_NUMERIC]
    categorical = [field for field in features if field not in STRUCTURED_NUMERIC]
    correlations = [{"feature": field, **_numeric_row(train, field)} for field in numeric]
    categories = []
    interactions = []
    for field in categorical:
        for level, group in train.groupby(field, sort=True):
            categories.append({"feature": field, "level": level, **_stratum(group)})
            for count in numeric:
                interactions.append({"feature_a": count, "level_a": "(numeric)", "feature_b": field,
                                     "level_b": level, **_stratum(group), **_numeric_row(group, count)})
    for a, b in combinations(categorical, 2):
        for (a_level, b_level), group in train.groupby([a, b], sort=True):
            interactions.append({"feature_a": a, "level_a": a_level, "feature_b": b, "level_b": b_level,
                                 **_stratum(group)})
    return pd.DataFrame(correlations), pd.DataFrame(categories), pd.DataFrame(interactions)


def _group_coverage(facilities, groups):
    rows = []
    for grouping, field in groups.items():
        for level, group in facilities.groupby(field, sort=True):
            summary = _coverage_counts(group)
            rows.append({"grouping": grouping, "source_field": field, "level": level,
                         **summary,
                         "reported_fraction": summary["reported"] / summary["facilities"]})
    return pd.DataFrame(rows)


def capacity_eda(frame, target, ordered_columns, seed=42, feature_columns=None, reference_year=None):
    """Return descriptive EDA tables for bed capacity or doctor counts.

    ``frame`` supplies raw FDR rows, with canonical ``facility_id`` attached.
    ``ordered_columns`` is the source schema before joins. IDs/geography can
    describe reporting gaps even when outside the strict post-countries
    predictor set. Source profiles and label readiness use all supplied rows,
    but predictor relationships and subgroup target distributions use only the
    proposed training partition. A held-out reported-count distribution can
    describe the evaluation cohort; it cannot validate unreported counts.

    No tests of significance, fitted models, imputations or changes to the
    provided DataFrame are performed.
    """
    source_audit = _audit_input(frame, target)
    eligible, selected = _columns(frame, target, ordered_columns, feature_columns)
    snapshot_year = date.today().year if reference_year is None else reference_year
    facilities, groups = _facilities(frame, source_audit, selected, seed, snapshot_year)
    train = facilities.loc[facilities["split"].eq("train")]
    feature_audit, missingness = _column_audit(frame, ordered_columns, target, eligible, selected, train)
    numeric, categorical, interactions = _relation_tables(train, selected)
    readiness = []
    for part, least in (("train", 10), ("calibration", 9), ("test", 2)):
        cohort = facilities.loc[facilities["split"].eq(part)]
        observed = cohort["value"].dropna()
        readiness.append({"split": part, "facilities": len(cohort), "usable_labels": len(observed),
                          "distinct_labels": observed.nunique(), "illustrative_minimum_labels": least,
                          "has_minimum_labels": len(observed) >= least})
    summary = [
        ("target", target), ("unit", COUNT_UNITS[target]), ("input_rows", len(frame)),
        ("rows_without_facility_id", int(source_audit["facility_id"].eq("").sum())),
        ("linked_facilities", len(facilities)), ("proposed_split_seed", seed),
        *((f"{status}_facilities", int(facilities["status"].eq(status).sum()))
          for status in ("reported", "missing", "unparseable", "conflicting")),
        ("reported_zero_facilities", int(facilities["value"].eq(0).sum())),
    ]
    return {
        "summary": pd.DataFrame(summary, columns=["metric", "value"]),
        "source_target_audit": source_audit, "target_audit": facilities,
        "feature_audit": feature_audit, "missingness": missingness,
        "numeric_relationships": numeric, "categorical_relationships": categorical,
        "interaction_summary": interactions, "group_coverage": _group_coverage(facilities, groups),
        "analysis_rows": train[["facility_id", "status", "value", *selected]].copy(),
        "split": facilities[["facility_id", "status", "split"]].copy(),
        "split_readiness": pd.DataFrame(readiness),
    }
