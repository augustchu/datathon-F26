"""Score frozen, adjudicated recovery cases without changing source evidence."""

from collections.abc import Mapping
from hashlib import sha256
import json
from math import isfinite, sqrt
from numbers import Integral, Real
from statistics import NormalDist

import numpy as np
import pandas as pd


_COUNT_FIELDS = {"capacity", "numberDoctors"}
_FIELDS = _COUNT_FIELDS | {"equipment", "service"}
_STANCES = {"supports", "contradicts"}
_SPLITS = {"development", "test"}
_GROUP_KEYS = ("facility_id", "document_family", "passage_digest")
_CASE_KEYS = {"case_id", "facility_id", "field", "answerable", "expected_value", "expected_scope"}
_DECISION_KEYS = {"case_id", "status"}
_ACCEPTED_KEYS = {"facility_id", "field", "value", "scope", "citation_supported"}


def _records(frame, required, name):
    if isinstance(frame, pd.DataFrame):
        if not frame.columns.is_unique:
            raise ValueError(f"{name} must have unique column names.")
        frame = frame.to_dict("records")
    result = {}
    for row in frame:
        if not isinstance(row, Mapping) or not required <= row.keys():
            raise ValueError(f"Each {name} row requires {', '.join(sorted(required))}.")
        case_id = _string(row["case_id"], "case_id")
        if case_id in result:
            raise ValueError(f"Duplicate {name} case_id {case_id!r}.")
        result[case_id] = row
    return result


def _empty(value):
    return not value.strip() if isinstance(value, str) else pd.api.types.is_scalar(value) and bool(pd.isna(value))


def _string(value, label, *, optional=False):
    if optional and _empty(value):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")
    return value.strip()


def _boolean(value, label, *, optional=False):
    if optional and _empty(value):
        return None
    if not isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{label} must be a boolean.")
    return bool(value)


def _choice(value, options, label):
    value = _string(value, label)
    if value not in options:
        raise ValueError(f"{label} must be one of {', '.join(sorted(options))}.")
    return value


def _value(value, field, label):
    if field not in _COUNT_FIELDS:
        return _string(value, label)
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or value < 0:
        raise ValueError(f"{label} must be an exact nonnegative count.")
    if isinstance(value, Integral):
        return int(value)
    if not isfinite(value):
        raise ValueError(f"{label} must be an exact nonnegative count.")
    integer = int(value)
    if value != integer:
        raise ValueError(f"{label} must be an exact nonnegative count.")
    return integer


def _cases(frame):
    cases = []
    for case_id, row in sorted(_records(frame, _CASE_KEYS, "case").items()):
        field = _choice(row["field"], _FIELDS, "field")
        answerable = _boolean(row["answerable"], "answerable")
        expected = (row["expected_value"], row["expected_scope"], row.get("expected_stance"))
        if not answerable and any(not _empty(value) for value in expected):
            raise ValueError(f"Unanswerable case {case_id!r} must leave its expected claim blank.")
        cases.append({
            "case_id": case_id,
            "facility_id": _string(row["facility_id"], "facility_id"),
            "field": field,
            "answerable": answerable,
            "expected_value": _value(expected[0], field, "expected_value") if answerable else None,
            "expected_scope": _string(expected[1], "expected_scope") if answerable else None,
            "expected_stance": _choice("supports" if _empty(expected[2]) else expected[2], _STANCES, "expected_stance")
            if answerable else None,
            "originally_missing": _boolean(row.get("originally_missing"), "originally_missing", optional=True),
            **{name: _string(row.get(name), name, optional=True)
               for name in ("split", "source_type", "language", "document_family", "passage_digest")},
        })
    if not cases:
        raise ValueError("At least one adjudicated case is required.")
    return cases


def _clusters(cases):
    """Link cases transitively across any shared facility or source identifier."""
    roots = list(range(len(cases)))
    seen = {}

    def root(index):
        while roots[index] != index:
            roots[index] = roots[roots[index]]
            index = roots[index]
        return index

    for index, case in enumerate(cases):
        for key in _GROUP_KEYS:
            if case[key] is not None:
                group = (key, case[key])
                roots[root(index)] = root(seen.setdefault(group, index))
    return [root(index) for index in range(len(cases))]


def _select(cases, cluster_ids, split):
    splits = {case["split"] for case in cases}
    if splits == {None} and split is None:
        return cases, cluster_ids
    if not splits <= _SPLITS:
        raise ValueError("Label all cases development or test when using a split.")
    seen = {}
    for case, cluster in zip(cases, cluster_ids):
        if seen.setdefault(cluster, case["split"]) != case["split"]:
            raise ValueError("Linked facilities, document families or passages cross development and test.")
    if split is None and len(splits) > 1:
        raise ValueError("Choose one split to evaluate.")
    split = next(iter(splits)) if split is None else split
    if split not in splits:
        raise ValueError(f"Evaluation split {split!r} is unavailable.")
    pairs = [(case, cluster) for case, cluster in zip(cases, cluster_ids) if case["split"] == split]
    return tuple(map(list, zip(*pairs)))


def _score_case(case, decision, cluster):
    status = _choice(decision["status"], {"accepted", "abstained"}, "status")
    accepted = status == "accepted"
    if accepted and not _ACCEPTED_KEYS <= decision.keys():
        raise ValueError(f"Accepted decision {case['case_id']!r} lacks claim or citation assessment fields.")
    minutes = decision.get("reviewer_minutes")
    if _empty(minutes):
        minutes = None
    elif (isinstance(minutes, (bool, np.bool_)) or not isinstance(minutes, Real)
          or not isfinite(minutes) or minutes < 0):
        raise ValueError("reviewer_minutes must be finite and nonnegative.")
    else:
        minutes = float(minutes)

    facility_id = field = value = scope = stance = None
    wrong_branch = correct_tuple = citation_supported = False
    if accepted:
        field = _choice(decision["field"], _FIELDS, "predicted field")
        value = _value(decision["value"], field, "predicted value")
        scope = _string(decision["scope"], "predicted scope")
        stated_stance = decision.get("stance")
        stance = _choice("supports" if _empty(stated_stance) else stated_stance, _STANCES, "predicted stance")
        facility_id = _string(decision["facility_id"], "predicted facility_id")
        wrong_branch = facility_id != case["facility_id"]
        citation_supported = _boolean(decision["citation_supported"], "citation_supported")
        correct_tuple = (case["answerable"] and not wrong_branch and field == case["field"]
                         and value == case["expected_value"] and scope == case["expected_scope"]
                         and stance == case["expected_stance"])
    return {
        **case, "cluster": cluster, "accepted": accepted, "correct_tuple": correct_tuple,
        "correct_claim": correct_tuple and citation_supported,
        "citation_supported": citation_supported if accepted else None,
        "wrong_branch": wrong_branch, "reviewer_minutes": minutes,
        "predicted_facility_id": facility_id, "predicted_field": field, "predicted_value": value,
        "predicted_scope": scope, "predicted_stance": stance,
    }


def _interval(numerator, denominator, clusters, *, method, confidence, resamples, seed):
    trials = int(sum(denominator))
    if trials == 0 or method == "none":
        return None
    events = int(sum(numerator))
    if method == "wilson":
        z_squared = NormalDist().inv_cdf((1 - confidence) / 2) ** 2
        observed = events / trials
        center = (observed + z_squared / (2 * trials)) / (1 + z_squared / trials)
        half_width = sqrt(z_squared * (observed * (1 - observed) / trials + z_squared / (4 * trials**2)))
        half_width /= 1 + z_squared / trials
        return [max(0.0, center - half_width), min(1.0, center + half_width)]

    grouped = {}
    for event, eligible, cluster in zip(numerator, denominator, clusters):
        totals = grouped.setdefault(cluster, [0, 0])
        totals[0] += event
        totals[1] += eligible
    # A single contributing cluster or a constant outcome produces an uninformative interval.
    usable = sum(count > 0 for _, count in grouped.values())
    if usable < 2 or events in (0, trials):
        return None
    values = np.array(list(grouped.values()))
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(resamples):
        successes, sample_size = values[rng.integers(len(values), size=len(values))].sum(axis=0)
        if sample_size:
            estimates.append(successes / sample_size)
    if not estimates:
        return None
    tail = (1 - confidence) / 2
    return np.quantile(estimates, [tail, 1 - tail]).tolist()


def _summarize(rows, interval_options):
    accepted = [row["accepted"] for row in rows]
    correct_claim = [row["correct_claim"] for row in rows]
    all_cases = [True] * len(rows)
    metrics = {
        "accepted_claim_precision": (correct_claim, accepted),
        "answerable_case_recall": (correct_claim, [row["answerable"] for row in rows]),
        "abstention_rate": ([not value for value in accepted], all_cases),
        "correct_tuple_rate": ([row["correct_tuple"] for row in rows], all_cases),
        "unsupported_citation_rate": ([row["citation_supported"] is False for row in rows], accepted),
        "wrong_branch_rate": ([row["wrong_branch"] for row in rows], accepted),
    }
    clusters = [row["cluster"] for row in rows]
    result = {"cases": len(rows), "clusters": len(set(clusters)), "accepted": sum(accepted),
              "answerable": sum(row["answerable"] for row in rows)}
    for name, (numerator, denominator) in metrics.items():
        events, trials = sum(numerator), sum(denominator)
        result[name] = {"numerator": events, "denominator": trials, "value": events / trials if trials else None,
                        "interval": _interval(numerator, denominator, clusters, **interval_options)}
    correct = [row for row in rows if row["correct_claim"]]
    new_facts = {tuple(row[key] for key in ("facility_id", "field", "expected_value", "expected_scope", "expected_stance"))
                 for row in correct if row["originally_missing"]}
    tracked_facts = len(new_facts) if all(row["originally_missing"] is not None for row in correct) else None
    times = [row["reviewer_minutes"] for row in rows]
    result["accepted_new_facts"] = tracked_facts
    result["reviewer_minutes_per_accepted_new_fact"] = (sum(times) / tracked_facts
                                                       if tracked_facts and all(time is not None for time in times) else None)
    return result


def _fingerprint(manifest):
    encoded = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def recovery_case_fingerprint(cases):
    """Hash the normalized adjudicated reference before making predictions."""
    return _fingerprint(_cases(cases))


def evaluate_recovery(cases, decisions, *, split=None, expected_case_fingerprint=None,
                      interval_method="none", confidence=.95, resamples=2000, seed=42):
    """Return metrics for explicit accepted/abstained decisions on adjudicated cases.

    Case rows require case_id, facility_id, field, answerable, expected_value
    and expected_scope. An optional expected_stance defaults to supports for
    answerable cases. Record document_family and passage_digest when known so
    linked cases stay in one split and dependence can be reflected in intervals.
    Accepted decisions supply facility_id, field, value, scope, citation_supported
    and optional stance. The citation judgement comes from evidence review. It
    measures whether the cited passage supports the claim the system returned.

    A correct claim matches the answerable reference tuple and has a supported
    citation. Correct tuple rate uses all evaluated cases, including abstentions.
    A zero denominator yields None. Time per new fact requires a time on every
    decision, plus originally_missing on each correctly accepted claim.

    Interval methods are opt-in. Wilson assumes independent cases. A cluster
    bootstrap assumes independent connected groups and reports uncertainty for
    this diagnostic design. Freeze the complete case fingerprint before scoring.
    """
    method = _choice(interval_method, {"none", "wilson", "cluster_bootstrap"}, "interval_method")
    if not isinstance(confidence, Real) or not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one.")
    if method == "cluster_bootstrap" and (not isinstance(resamples, Integral) or resamples < 2):
        raise ValueError("resamples must be an integer of at least two.")
    manifest = _cases(cases)
    fingerprint = _fingerprint(manifest)
    if expected_case_fingerprint is not None and fingerprint != expected_case_fingerprint:
        raise ValueError("Cases differ from the frozen reference fingerprint.")
    selected, clusters = _select(manifest, _clusters(manifest), split)
    if method == "wilson" and len(set(clusters)) != len(clusters):
        raise ValueError("Wilson intervals require independent cases. Choose cluster_bootstrap for linked cases.")
    decisions = _records(decisions, _DECISION_KEYS, "decision")
    if set(decisions) != {case["case_id"] for case in selected}:
        raise ValueError("Provide exactly one decision for every case in the selected split, including abstentions.")

    options = dict(method=method, confidence=confidence, resamples=resamples, seed=seed)
    per_case = [_score_case(case, decisions[case["case_id"]], cluster) for case, cluster in zip(selected, clusters)]
    result = {
        "case_fingerprint": fingerprint, "split": selected[0]["split"], "interval_method": method,
        "confidence": confidence if method != "none" else None,
        "overall": _summarize(per_case, options), "per_case": per_case,
    }
    for dimension in ("field", "source_type", "language"):
        cohorts = {}
        for row in per_case:
            cohorts.setdefault(row[dimension] or "unspecified", []).append(row)
        result[f"by_{dimension}"] = {name: _summarize(rows, options) for name, rows in sorted(cohorts.items())}
    return result
