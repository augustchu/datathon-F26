#!/usr/bin/env python3
"""Freeze an SRS-without-replacement, two-phase validation audit.

The sampling frame is the saved supplemental JSONL rows, not all real facilities.
Each source gets a separate 95% interval. Exact hypergeometric tail inversion
selects a conservative sample whose endpoints are always within 0.05 of x/n.
Run: python3 scripts/design_validation_audit.py
Import exact_interval() or summarize_categories() to analyze completed labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

import numpy as np
import scipy
from scipy.stats import hypergeom, norm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/validation_audit"
SEED = 20261121
ALPHA = 0.05
MARGIN = 0.05
EXPECTED_POPULATIONS = {"nqas": 22015, "nabh": 500, "eraktkosh": 6524}
BROWSER_FIELDS = {
    "nqas": ["facility_name", "state", "certification_status", "validity_date_text"],
    "nabh": ["facility_name", "address", "accreditation_number", "certification_status"],
    "eraktkosh": ["facility_name", "address", "state", "facility_type"],
}


def exact_bounds_all(population_size: int, sample_size: int, alpha: float = ALPHA):
    """Return exact equal-tailed confidence bounds on integer population K.

    scipy parameters are (M=population_size, n=K, N=sample_size).
    L(x)=min K with P_K[X>=x]>=alpha/2;
    U(x)=max K with P_K[X<=x]>=alpha/2.
    K is restricted to the feasible support x..population_size-sample_size+x.
    This inverts tests for K; scipy.hypergeom.interval() does NOT do that.
    """
    if not (1 <= sample_size <= population_size) or not (0 < alpha < 1):
        raise ValueError("Require 1 <= n <= N and 0 < alpha < 1")
    x = np.arange(sample_size + 1, dtype=np.int64)
    low, high = x.copy(), population_size - sample_size + x
    while np.any(low < high):
        middle = (low + high) // 2
        accepted = hypergeom.sf(x - 1, population_size, middle, sample_size) >= alpha / 2
        high = np.where(accepted, middle, high)
        low = np.where(accepted, low, middle + 1)
    lower = low.copy()
    low, high = x.copy(), population_size - sample_size + x
    while np.any(low < high):
        middle = (low + high + 1) // 2
        accepted = hypergeom.cdf(x, population_size, middle, sample_size) >= alpha / 2
        low = np.where(accepted, middle, low)
        high = np.where(accepted, high, middle - 1)
    return lower, low


def exact_interval(population_size: int, sample_size: int, successes: int, alpha: float = ALPHA):
    if not isinstance(successes, int) or not 0 <= successes <= sample_size:
        raise ValueError("successes must be an integer in 0..n")
    lower, upper = exact_bounds_all(population_size, sample_size, alpha)
    return {"lower_count": int(lower[successes]), "upper_count": int(upper[successes]),
            "lower": float(lower[successes] / population_size),
            "upper": float(upper[successes] / population_size),
            "estimate": successes / sample_size, "confidence_level": 1 - alpha}


def precision(population_size, sample_size, alpha=ALPHA):
    lower, upper = exact_bounds_all(population_size, sample_size, alpha)
    estimate = np.arange(sample_size + 1) / sample_size
    widths = (upper - lower) / population_size
    deviations = np.maximum(estimate - lower / population_size, upper / population_size - estimate)
    return {"sample_size": sample_size, "maximum_interval_width": float(widths.max()),
            "maximum_endpoint_distance_from_estimate": float(deviations.max()),
            "widest_interval_success_counts": np.where(widths == widths.max())[0].tolist(),
            "worst_endpoint_distance_success_counts": np.where(deviations == deviations.max())[0].tolist()}


def design_size(population_size):
    z = float(norm.ppf(1 - ALPHA / 2))
    # Conservative normal planning approximation with p(1-p)=1/4 and FPC.
    n_normal = math.ceil(population_size * z*z * .25 /
                         (MARGIN*MARGIN * (population_size - 1) + z*z * .25))
    n = n_normal
    first_width = None
    while n <= population_size:
        result = precision(population_size, n)
        if first_width is None and result["maximum_interval_width"] <= 2 * MARGIN + 1e-12:
            first_width = result.copy()
        if result["maximum_endpoint_distance_from_estimate"] <= MARGIN + 1e-12:
            return {"normal_fpc_planning_n": n_normal,
                    "normal_fpc_formula": "ceil(N*z^2*0.25 / (e^2*(N-1) + z^2*0.25))",
                    "exact_width_target": first_width,
                    "selected_exact_precision": result,
                    "preceding_sample_size_precision": precision(population_size, n-1) if n > 1 else None}
        n += 1
    raise AssertionError("A census must satisfy precision")


def validate_exact_method(population_size, sample_size):
    """Check all x endpoints and actual coverage for every possible K."""
    lower, upper = exact_bounds_all(population_size, sample_size)
    x = np.arange(sample_size + 1)
    assert lower[0] == 0 and upper[-1] == population_size
    assert np.all(lower <= upper)
    assert np.all(lower == population_size - upper[::-1])
    assert np.all(np.diff(lower) >= 0) and np.all(np.diff(upper) >= 0)
    assert np.all(hypergeom.sf(x - 1, population_size, lower, sample_size) >= ALPHA / 2)
    assert np.all(hypergeom.cdf(x, population_size, upper, sample_size) >= ALPHA / 2)
    possible_K = np.arange(population_size + 1)
    # Acceptance ranges in x from the monotonic bounds.
    first_x = np.searchsorted(upper, possible_K, side="left")
    last_x = np.searchsorted(lower, possible_K, side="right") - 1
    coverage = 1 - hypergeom.cdf(first_x - 1, population_size, possible_K, sample_size) \
                 - hypergeom.sf(last_x, population_size, possible_K, sample_size)
    assert float(coverage.min()) >= 1 - ALPHA - 1e-12
    census_lower, census_upper = exact_bounds_all(20, 20)
    assert np.array_equal(census_lower, np.arange(21))
    assert np.array_equal(census_upper, np.arange(21))
    return {"all_success_counts_checked": sample_size + 1,
            "all_population_success_counts_checked": population_size + 1,
            "minimum_actual_coverage": float(coverage.min()),
            "minimum_coverage_population_success_count": int(np.argmin(coverage)),
            "boundary_symmetry_monotonicity_and_census_checks": "passed"}


def validate_fraction_reference():
    """Compare integer endpoints to exact rational tails on small populations."""
    checked = 0
    for N in range(2, 16):
        for n in range(1, N + 1):
            lower, upper = exact_bounds_all(N, n)
            denominator = math.comb(N, n)
            for x in range(n + 1):
                accepted = []
                for K in range(x, N - n + x + 1):
                    masses = {
                        k: Fraction(math.comb(K, k) * math.comb(N-K, n-k), denominator)
                        for k in range(max(0, n-(N-K)), min(K, n) + 1)
                    }
                    low_tail = sum((p for k,p in masses.items() if k <= x), Fraction())
                    high_tail = sum((p for k,p in masses.items() if k >= x), Fraction())
                    if low_tail >= Fraction(1,40) and high_tail >= Fraction(1,40):
                        accepted.append(K)
                assert (int(lower[x]), int(upper[x])) == (min(accepted), max(accepted))
                checked += 1
    return {"integer_endpoint_cases_checked_against_exact_fraction_arithmetic": checked,
            "population_sizes": "2..15", "result": "passed"}


def summarize_categories(population_size, sample_size, confirmed, contradicted, unresolved):
    """Analyze fully attempted categorical outcomes without calling unknown false.

    'confirmed' is extraction-agreement in phase 1 or independent corroboration
    in phase 2. Its rate is an audit outcome, not necessarily real-world validity.
    Assumption for validity bounds: confirmations are true and contradictions
    are false; unresolved cases can be either. Misclassification is not covered.
    """
    if any(not isinstance(v, int) or v < 0 for v in (confirmed, contradicted, unresolved)):
        raise ValueError("Counts must be nonnegative integers")
    if confirmed + contradicted + unresolved != sample_size:
        raise ValueError("Every frozen sampled row must have one completed audit category")
    known_ci = exact_interval(population_size, sample_size, confirmed)
    possible_ci = exact_interval(population_size, sample_size, confirmed + unresolved)
    return {
        "counts": {"confirmed": confirmed, "contradicted": contradicted, "unresolved": unresolved},
        "confirmed_outcome_rate_ci": known_ci,
        "sample_validity_identification_bounds": [confirmed / sample_size, (confirmed + unresolved) / sample_size],
        "population_validity_confidence_region": [known_ci["lower"], possible_ci["upper"]],
        "population_validity_confidence_region_coverage": "at least 95% under fixed labels and valid adjudication; lower and upper one-sided error at most 2.5% each",
        "unknown_handling": "Unresolved cases are not invalid cases. The confirmed-outcome indicator measures recorded confirmation only.",
        "precision_limit": "The ±5-point sampling target applies to a completely observed binary outcome. Unresolved validity can widen the identification/confidence region beyond ±5 points.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    design = {
        "version": 1, "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed, "confidence_level_per_source": .95, "target_endpoint_margin": MARGIN,
        "inference_scope": "Separate per-source inference for fixed saved supplemental ROW populations, including retained duplicates. Not all Indian facilities, not the entire NABH directory, not simultaneous 95% coverage across sources/phases/metrics.",
        "sampling": "Simple random sampling without replacement within each source. Python random.Random with a SHA-256-derived source-specific seed; selected population row indices and file hashes are frozen. Same rows used in both phases.",
        "sample_unit": "A row in the saved supplemental JSONL, identified by source and one-based population row index. Source record IDs alone are not unique for NABH.",
        "precision_rule": "For every x=0..n, both exact confidence limits must lie within 0.05 of x/n. This is stronger than merely requiring total interval width <=0.10. Intervals may be asymmetric.",
        "exact_interval_method": "Invert two one-sided finite-population hypergeometric tests at alpha/2=0.025: L=min K with P_K(X>=x)>=0.025; U=max K with P_K(X<=x)>=0.025. Return [L/N,U/N].",
        "normal_approximation": "Conservative p=0.5 FPC normal planning sizes are recorded for comparison and are superseded by the larger exact-precision sample sizes.",
        "phase_order": ["1_source_extraction", "2_independent_corroboration"],
        "phase1": {
            "estimand": "Proportion of frozen rows whose specified core identity/claim fields agree with the attributable source record, under the predeclared comparison rules.",
            "categories": ["matched", "mismatched", "unresolved"],
            "matched_rule": "Every listed browser-comparable field must match the same facility/source record. Only whitespace/HTML presentation normalization is allowed; name, address, status, dates and type must retain meaning.",
            "mismatched_rule": "A demonstrable extraction or transformation error against attributable source evidence. A subsequent source update alone is not an extraction error.",
            "unresolved_rule": "Outage, inaccessible page, ambiguous entity/branch, missing comparable field, uncertain source change or insufficient evidence. Preserve reason and attempts; never replace the row or label unknown false.",
            "additional_offline_checks": "Check derived ISO dates, expiry flags, evidence-field projection, source identifiers and coordinate NULL handling against frozen raw exports. Browser core-field agreement does not independently verify numeric coordinates or real-world service availability.",
            "changed_source_rule": "If live content differs from the saved snapshot, record both dates/evidence and separate source change from parsing failure; unresolved until attributable archived evidence settles the extraction question.",
        },
        "phase2": {
            "estimand": "Proportion of the same frozen rows with genuinely independent corroboration of their declared identity and claim scope under a fixed verification protocol.",
            "categories": ["corroborated", "contradicted", "unresolved"],
            "corroborated_rule": "Match the same facility and branch plus every claim designated for verification, using attributable independent evidence with source/date/excerpts recorded. Identity-only evidence cannot verify a service or accreditation claim.",
            "contradicted_rule": "Credible independent evidence explicitly conflicts with a designated claim; report which claim and dates. Absence from a directory alone is not contradiction.",
            "unresolved_rule": "No suitable independent evidence, inaccessible sources, ambiguous identity, incompatible dates, mixed claims, or uncertain source independence. Retain as unresolved.",
            "independence_rule": "The original directory, its PDFs/underlying API, mirrors, syndicated copies and sources evidently copying it are not independent corroboration. If provenance independence cannot be established, do not assert it.",
            "reporting": "Report corroborated/contradicted/unresolved counts; exact CI for the recorded corroboration rate; sample identification bounds C/n..(C+U)/n and a population validity confidence region [L(C)/N,U(C+U)/N]. Unknowns are not false. Unresolved cases may prevent ±5-point precision for underlying validity.",
        },
        "completion_rules": [
            "Freeze the complete sample before new outcomes are inspected. No optional stopping, resampling, replacements, convenient substitutions or deletion of outages.",
            "Attempt every sampled row under a fixed protocol. Keep pending distinct from completed-unresolved; do not publish a final interval from a convenient completed subset.",
            "Use the same frozen sample in phase 2, including phase-1 mismatches and unresolved rows. Analyze paired phase outcomes; do not treat the phases as independent samples.",
            "Prior pilot checks count only when their row is in the frozen sample and their recorded evidence satisfies this protocol. They do not determine inclusion or sample size.",
            "Document adjudication criteria and source-search effort before independent validation begins. Sampling confidence does not cover reviewer misclassification or source error.",
        ],
        "references": [
            {"title": "Penn State STAT 506: Confidence Intervals and Sample Size", "url": "https://online.stat.psu.edu/stat506/Lesson02", "role": "Conservative p=0.5 sample-size planning, finite population correction, and hypergeometric sampling without replacement."},
            {"title": "SciPy hypergeom documentation", "url": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.hypergeom.html", "role": "Hypergeometric parameterization and cdf/sf used for test inversion; its interval method is not used as a parameter confidence interval."},
        ],
        "software": {"scipy": scipy.__version__, "numpy": np.__version__, "sampling_library": "Python standard-library random"},
        "exact_arithmetic_reference_test": validate_fraction_reference(),
        "sources": {},
    }
    for source, expected_N in EXPECTED_POPULATIONS.items():
        path = ROOT / "data/supplemental" / f"{source}.jsonl"
        raw = path.read_bytes()
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if len(rows) != expected_N:
            raise ValueError(f"{source}: frame changed; expected {expected_N}, got {len(rows)}. Reconsider design before freezing a new audit.")
        size = design_size(len(rows))
        n = size["selected_exact_precision"]["sample_size"]
        source_seed = int.from_bytes(hashlib.sha256(f"{args.seed}:{source}".encode()).digest(), "big")
        indices = random.Random(source_seed).sample(range(len(rows)), n)
        sample = [{"audit_id": f"{source}:{index+1:06d}", "source": source,
                   "sample_draw_order": order+1, "population_row_index_1based": index+1,
                   "source_record_id": rows[index]["source_record_id"],
                   "browser_comparable_fields": BROWSER_FIELDS[source],
                   "expected": rows[index], "phase1_status": "pending", "phase2_status": "pending"}
                  for order,index in enumerate(indices)]
        assert len({row["audit_id"] for row in sample}) == n
        frame_sha = hashlib.sha256(raw).hexdigest()
        payload = {"source": source, "population_size": len(rows), "sample_size": n,
                   "population_file": f"data/supplemental/{source}.jsonl", "population_sha256": frame_sha,
                   "seed": args.seed, "source_seed_sha256_hex": f"{source_seed:064x}",
                   "sampling_design": "SRS without replacement, fixed paired sample for both phases", "rows": sample}
        json_path = OUT / f"{source}_sample.json"
        jsonl_path = OUT / f"{source}_sample.jsonl"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        jsonl_path.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in sample),encoding="utf-8")
        entry = {"population_size": len(rows), "population_sha256": frame_sha,
                 "population_file": payload["population_file"], "sample_size": n,
                 "browser_comparable_fields": BROWSER_FIELDS[source], "precision_calculation": size,
                 "exact_coverage_validation": validate_exact_method(len(rows), n),
                 "source_seed_sha256_hex": payload["source_seed_sha256_hex"],
                 "sample_population_indices_1based_in_draw_order": [index+1 for index in indices],
                 "sample_files_sha256": {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (json_path,jsonl_path)}}
        design["sources"][source] = entry
        print(json.dumps({"source":source,"N":len(rows),"n":n,"max_endpoint_distance":size["selected_exact_precision"]["maximum_endpoint_distance_from_estimate"],"min_exact_coverage":entry["exact_coverage_validation"]["minimum_actual_coverage"]}),flush=True)
    design["total_distinct_sampled_rows"] = sum(s["sample_size"] for s in design["sources"].values())
    design["total_phase_row_assessments"] = 2 * design["total_distinct_sampled_rows"]
    design_path = OUT / "design.json"
    design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
