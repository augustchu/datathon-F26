"""Build historical service workload and resource evidence for load planning."""

import argparse
import hashlib
import json
from math import ceil
from pathlib import Path

from baseline_coverage import ROOT, read_json, unpack


INPUT_FILES = (
    "expanded_coverage.json",
    "baseline_coverage.json",
    "hp_dialysis_model_data.json",
    "tn_maternity_model_data.json",
)
HP_PERIODS = {"FY2023–24": "2024-03", "FY2024–25": "2025-03"}
TN_PROJECT_SERIES = {
    "tn-dms:the-nilgiris:coonoor": "govt_lawley_deliveries",
    "tn-dms:thanjavur:pattukottai": "pattukkottai_deliveries",
}
CAPACITY_BASIS = "historical planning with predicted monthly dialysis throughput"
CAPACITY_METHOD = "prior_march_leave_site_out_nearest_rank_p90_per_functional_machine"


def register_source(source, sources):
    if not source:
        return None
    for key, existing in sources.items():
        if existing == source:
            return key
    key = f"source_{len(sources) + 1:02d}"
    sources[key] = source
    return key


def fiscal_window(period):
    start = int(period[2:6])
    end = start + 1
    if period != f"FY{start:04d}–{end % 100:02d}":
        raise ValueError(f"Invalid fiscal year {period}")
    return f"{start:04d}-04", f"{end:04d}-03"


def workload_rows(expanded, previous, sources):
    reported, estimated = [], []
    for target in expanded["targets"]:
        if target["status"] == "withheld":
            continue
        site = target["site"]
        row = {
            **{key: target[key] for key in (
                "series_id", "service", "scope", "unit", "grain", "period_start", "period_end", "typical_month",
            )},
            "facility_id": site["facility_id"],
            "facility": site["facility"],
            "state": site["state"],
            "identity_url": site["identity_url"],
        }
        if target["status"] == "reported":
            reported.append({
                **row, "reported_count": target["reported_count"],
                "source_id": register_source(target["source"], sources),
            })
        elif target["status"] == "baseline_estimate":
            estimated.append({
                **row, "method": target["method"], "gap_kind": target["gap_kind"],
                "gap_source_id": register_source(target.get("gap_source"), sources),
                "prior_periods": [
                    {
                        "period_start": prior["period_start"], "reported_count": prior["reported_count"],
                        "source_id": register_source(prior["source"], sources),
                    }
                    for prior in target["prior_periods"]
                ],
            })
        else:
            raise ValueError(f"Unknown caseload status {target['status']}")

    for target in previous["estimates"]:
        start, end = fiscal_window(target["target_period"])
        prior_start, _ = fiscal_window(target["prior_period"])
        estimated.append({
            "series_id": target["programme_site_id"],
            "facility_id": target["facility_id"],
            "facility": target["facility"],
            "state": "Himachal Pradesh",
            "identity_url": target["facility_link"]["source_url"],
            "service": target["service"],
            "scope": "Reported programme haemodialysis sessions at a PMNDP site",
            "unit": target["unit"],
            "grain": "fiscal_year",
            "period_start": start,
            "period_end": end,
            "typical_month": target["estimated_typical_month"],
            "method": target["method"],
            "gap_kind": "no_matching_total_recovered",
            "gap_source_id": None,
            "prior_periods": [{
                "period_start": prior_start, "reported_count": target["prior_reported_total"],
                "source_id": register_source(target["prior_source"], sources),
            }],
        })

    keys = [(row["facility_id"], row["service"], row["scope"], row["period_start"], row["period_end"])
            for row in reported + estimated]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate branch, scope and period in caseload evidence")
    return reported, estimated


def intensity_rows(cohort, sources, windows, count_field, resource_field, links):
    result = []
    seen = set()
    seen_project_sites = set()
    for row in unpack(cohort, "observations"):
        start, end = windows[row["period"]]
        site = row["facility_id"]
        count, resource = row[count_field], row[resource_field]
        if type(count) is not int or type(resource) is not int or count < 0 or resource <= 0:
            raise ValueError(f"Invalid count or resource at {site} in {row['period']}")
        if (site, start, end) in seen:
            raise ValueError(f"Repeated programme site and period: {site} in {row['period']}")
        seen.add((site, start, end))
        link = links.get(site, {})
        if project_id := link.get("fdr_id"):
            key = (project_id, start, end)
            if key in seen_project_sites:
                raise ValueError(f"Multiple programme rows resolve to {project_id} in {row['period']}")
            seen_project_sites.add(key)
        result.append({
            "programme_site_id": site,
            "facility": row["facility"],
            "facility_id": project_id,
            "identity_url": link.get("source_url"),
            "district": row["district"],
            "period_start": start,
            "period_end": end,
            "reported_count": count,
            "reported_resource_count": resource,
            "count_per_resource": round(count / resource, 6),
            "source_id": register_source(cohort["sources"][row["source"]], sources),
        })
    return result


def maternity_links(maternity, reported, sources):
    """Bridge the two previously reviewed Table 1 sites without fuzzy matching."""
    by_series = {row["series_id"]: row for row in reported}
    links = {}
    for row in unpack(maternity, "observations"):
        programme_site = row["facility_id"]
        if programme_site not in TN_PROJECT_SERIES:
            continue
        linked = by_series[TN_PROJECT_SERIES[programme_site]]
        period = fiscal_window(row["period"])
        source = maternity["sources"][row["source"]]
        linked_source = sources[linked["source_id"]]
        if (linked["service"] != maternity["service"] or linked["unit"] != maternity["unit"]
                or linked["reported_count"] != row["total"]
                or (linked["period_start"], linked["period_end"]) != period
                or linked_source["url"] != source["url"] or "Table 1" not in linked_source["locator"]):
            raise ValueError(f"Previously reviewed CEmONC link requires review: {programme_site}")
        links[programme_site] = {"fdr_id": linked["facility_id"], "source_url": linked["identity_url"]}
    return links


def build_dialysis_model(rows, sources):
    prior = [row for row in rows if row["period_start"] == "2024-03"]
    later = [row for row in rows if row["period_start"] == "2025-03"]
    diagnostics, pressure_inputs = [], []
    for row in later:
        training_sites = [site for site in prior if site["programme_site_id"] != row["programme_site_id"]]
        rates = sorted(
            site["reported_count"] / site["reported_resource_count"]
            for site in training_sites
        )
        if not rates:
            raise ValueError(f"No prior comparison sites for {row['programme_site_id']}")
        p90 = rates[ceil(0.9 * len(rates)) - 1]
        predicted = round(p90 * row["reported_resource_count"], 6)
        if predicted <= 0:
            raise ValueError(f"Nonpositive throughput benchmark for {row['programme_site_id']}")
        prior_source_ids = sorted({site["source_id"] for site in training_sites})
        demand_source_id = row["source_id"]
        provenance = {
            "capacity_predicted": True,
            "capacity_method": CAPACITY_METHOD,
            "capacity_training_source_ids": prior_source_ids,
            "capacity_resource_source_id": demand_source_id,
            "demand_source_id": demand_source_id,
        }
        diagnostics.append({
            "programme_site_id": row["programme_site_id"], "facility_id": row["facility_id"],
            "reported_count": row["reported_count"], "reported_resource_count": row["reported_resource_count"],
            "training_site_count": len(rates), "source_rate_p90": round(p90, 6),
            "predicted_session_capacity": predicted, "observed_exceeds_prediction": row["reported_count"] > predicted,
            **provenance,
        })
        if row["facility_id"]:
            pressure_inputs.append({
                "facility_id": row["facility_id"], "service": "dialysis", "period": "2025-03",
                "demand": row["reported_count"], "capacity": predicted, "unit": "dialysis sessions",
                "basis": CAPACITY_BASIS, "source_url": sources[demand_source_id]["url"], **provenance,
                "capacity_training_sources": [sources[source_id] for source_id in prior_source_ids],
                "capacity_resource_source": sources[demand_source_id],
            })

    model = {
        "training_period": "2024-03",
        "evaluation_period": "2025-03",
        "method": CAPACITY_METHOD,
        "description": (
            "Sort the other programme sites' March 2024 sessions per reported functional machine and take "
            "the observation at rank ceil(0.90 times n), using rank 1 for the smallest value. Multiply by "
            "the destination's March 2025 reported functional machines. Training includes all prior sites for "
            "a destination without a March 2024 record."
        ),
        "interpretation": (
            "A historical throughput benchmark for planning. It uses completed sessions as a proxy for "
            "served workload. Actual staffed capacity and demand that did not receive service are unknown."
        ),
        "sites_evaluated": len(diagnostics),
        "sites_where_observed_exceeds_prediction": sum(x["observed_exceeds_prediction"] for x in diagnostics),
        "linked_project_sites_evaluated": len(pressure_inputs),
        "linked_project_sites_where_observed_exceeds_prediction": sum(
            x["observed_exceeds_prediction"] for x in diagnostics if x["facility_id"]
        ),
        "rows": diagnostics,
    }
    return model, pressure_inputs


def build_evidence(expanded, previous, dialysis, maternity):
    sources = {}
    reported, estimated = workload_rows(expanded, previous, sources)
    hp = intensity_rows(dialysis, sources, {key: (month, month) for key, month in HP_PERIODS.items()},
                        "march_sessions", "machines", dialysis["facility_links"])
    tn = intensity_rows(maternity, sources, {"FY2025–26": fiscal_window("FY2025–26")},
                        "total", "beds", maternity_links(maternity, reported, sources))
    model, pressure_inputs = build_dialysis_model(hp, sources)
    return {
        "schema_version": 1,
        "purpose": "Historical load planning only",
        "as_of_month": expanded["as_of_month"],
        "summary": {
            "reported_caseload_rows": len(reported),
            "estimated_caseload_rows": len(estimated),
            "reported_caseload_facilities": len({row["facility_id"] for row in reported}),
            "estimated_caseload_facilities": len({row["facility_id"] for row in estimated}),
            "all_caseload_facilities": len({row["facility_id"] for row in reported + estimated}),
            "withheld_expansion_rows": sum(row["status"] == "withheld" for row in expanded["targets"]),
            "hp_pmndp_programme_rows": len(hp),
            "hp_pmndp_linked_project_rows": sum(row["facility_id"] is not None for row in hp),
            "tn_cemonc_programme_rows": len(tn),
            "tn_cemonc_linked_project_rows": sum(row["facility_id"] is not None for row in tn),
            "historical_predicted_capacity_pressure_rows": len(pressure_inputs),
            "live_availability_rows": 0,
        },
        "sources": sources,
        "reported_caseload": reported,
        "estimated_caseload": estimated,
        "historical_intensity": {
            "hp_pmndp": {
                "scope": dialysis["cohort"], "unit": dialysis["unit"], "resource_unit": "functional dialysis machines",
                "interpretation": "March sessions per functional machine reported in the same March census.",
                "rows": hp,
            },
            "tn_cemonc": {
                "scope": maternity["cohort"], "unit": maternity["unit"], "resource_unit": "reported CEmONC beds",
                "interpretation": "Fiscal-year deliveries per reported programme bed. Staffed bed-days are unknown.",
                "rows": tn,
            },
        },
        "dialysis_capacity_model": model,
        "pressure_inputs": pressure_inputs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "load_evidence.json")
    args = parser.parse_args(argv)
    evidence = build_evidence(*(read_json(filename) for filename in INPUT_FILES))
    evidence["input_sha256"] = {
        filename: hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() for filename in INPUT_FILES
    }
    args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
