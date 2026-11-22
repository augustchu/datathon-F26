"""Build scoped monthly caseload and throughput scenarios from observed site data."""

import json
from collections import Counter
from math import ceil, isfinite
from pathlib import Path
from statistics import mean, median

from build import month_index, month_label, monthly_counts
from expand_coverage import GRAINS, read_series


ROOT = Path(__file__).resolve().parent

PROFILES = {
    "Dialysis": {
        "unit": "sessions",
        "scope": "Completed PMNDP haemodialysis sessions at participating Himachal Pradesh centres",
        "limit": "A state programme cohort. Private or non-programme sessions and peritoneal dialysis are outside the reported scope.",
        "cohort_file": "hp_dialysis_model_data.json",
        "region": "Himachal Pradesh",
    },
    "Emergency C-section": {
        "unit": "operations",
        "scope": "Emergency caesarean sections performed at public-hospital MCH wings in the NHSRC Uttar Pradesh study",
        "limit": "Two programme wings report monthly means with an unclear averaging denominator. All-urgency caesareans and studies with eligibility restrictions are excluded.",
        "original": ("mirzapur_emergency_cs", "chandauli_emergency_cs"),
    },
    "Maternity / delivery": {
        "unit": "deliveries",
        "scope": "All reported deliveries at Tamil Nadu Directorate of Medical and Rural Health Services CEmONC centres",
        "limit": "A state programme cohort of DMS centres. Medical college, community health centre and private hospital delivery cohorts have separate distributions.",
        "cohort_file": "tn_maternity_model_data.json",
        "region": "Tamil Nadu",
        "cohort_links": {
            "tn-dms:the-nilgiris:coonoor": "govt_lawley_deliveries",
            "tn-dms:thanjavur:pattukottai": "pattukkottai_deliveries",
        },
    },
    "NICU": {
        "unit": "admissions",
        "scope": "Reported admissions to the named neonatal intensive care unit",
        "limit": "Neonatal outpatient, SNCU and generic neonatal ward counts are excluded. The Bhubaneswar report scopes its count to Level III.",
        "original": ("bathinda_nicu",),
        "expanded": ("aiims_bhubaneswar_nicu_calendar",),
    },
    "Pediatric care": {
        "unit": "visits",
        "scope": "General pediatric medicine outpatient encounters including follow-ups",
        "limit": "Pediatric admissions, surgery, and separately reported subspecialty clinic encounters are outside the scope.",
        "expanded": ("himalayan_expanded_pediatric", "sgrd_pediatric", "lok_nayak_pediatrics"),
    },
    "Cardiac surgery": {
        "unit": "operations",
        "scope": "Hospital cardiac operations as published in a total cardiac surgery row",
        "limit": "Catheterization, scheme-specific cardiac surgeries and cardiac operating theatre counts with a different reporting definition are excluded.",
        "original": ("unmehta_cardiac",),
        "expanded": ("whitefield_cardiac_surgery_annual",),
        "original_links": {"unmehta_cardiac": "unmehta_mri_monthly"},
    },
    "Cardiology consultation": {
        "unit": "visits",
        "scope": "General cardiology outpatient consultations including recorded follow-ups",
        "limit": "Pediatric cardiology-only clinic counts and separately reported cardiac surgery outpatient visits are excluded.",
        "expanded": ("himalayan_expanded_cardiology", "whitefield_cardiology_annual"),
    },
    "MRI": {
        "unit": "scans",
        "scope": "Hospital radiology MRI examinations",
        "limit": "The reports give total MRI workload, with limited information about indication, equipment or case mix. Some sites have only an older report or single month.",
        "original": ("smimer_mri", "bilaspur_mri", "bathinda_mri"),
        "expanded": (
            "whitefield_mri_annual", "unmehta_mri_monthly", "aiims_bhubaneswar_mri_annual",
            "sgrd_mri", "zydus_dahod_mri", "himalayan_expanded_mri", "scb_mri_annual",
        ),
    },
    "CT scan": {
        "unit": "scans",
        "scope": "Hospital radiology examinations reported under the CT scan line",
        "limit": "Separately tabulated CT angiography is excluded. Reports with an unspecified split cannot support angiography-specific or strictly non-angiography estimates.",
        "original": ("smimer_ct", "bilaspur_ct", "bathinda_ct"),
        "expanded": (
            "whitefield_ct_annual", "unmehta_ct_monthly", "sgrd_ct", "zydus_dahod_ct",
            "himalayan_expanded_ct", "aiims_bhubaneswar_ct_annual",
        ),
    },
    "Burn care": {
        "unit": "cases",
        "scope": "Burn cases classified in the hospital Trauma and Emergency service table",
        "limit": "One reporting hospital. Burn outpatient and inpatient unit volumes are outside the selected definition.",
        "original": ("bilaspur_burn",),
    },
    "Orthopedic surgery": {
        "unit": "operations",
        "scope": "Major orthopedic operations",
        "limit": "Reports that combine major and minor operations, or do not specify which operations were counted, are excluded.",
        "original": ("tiruppur_ortho",),
        "expanded": ("himalayan_expanded_ortho", "sgrd_ortho"),
    },
    "Fracture care": {
        "unit": "admissions",
        "scope": "Fracture patients eligible for the UPUMS prospective cohort and admitted under orthopedics via emergency",
        "limit": "One study at one hospital. Fractures seen without eligible admission are outside its definition. The authors report 24 months of exposure.",
        "original": ("saifai_fractures",),
    },
    "Dental care": {
        "unit": "visits",
        "scope": "Hospital dentistry outpatient department encounters",
        "limit": "College-wide central dental registrations and separately reported procedures are outside the scope.",
        "original": ("gt_dental",),
        "expanded": ("sgrd_dental",),
    },
    "Oral surgery": {
        "unit": "operations",
        "scope": "Major and minor oral and maxillofacial operations, excluding separately tabulated extraction and implant categories",
        "limit": "Two dental institutions. Major-only surgery series and separately tabulated procedures are excluded.",
        "expanded": ("its_muradnagar_oral", "vishnu_dental_oral_surgery"),
    },
    "Cataract surgery": {
        "unit": "operations",
        "scope": "Cataract operations performed at named branches of the Aravind Eye Care System",
        "limit": "Branches of one eye-care provider. Transfer to a general hospital or to another provider has no external validation.",
        "original": (
            "aravind_madurai_cataract", "aravind_dindigul_cataract", "aravind_tirunelveli_cataract",
            "aravind_coimbatore_cataract", "aravind_kovilpatti_cataract", "aravind_pondicherry_cataract",
            "aravind_tirupati_cataract",
        ),
    },
    "Stroke care": {
        "unit": "cases",
        "scope": "Stroke cases classified in the hospital Trauma and Emergency service table",
        "limit": "One reporting hospital. Ischemic-only study admissions and thrombectomy procedure counts are outside the selected definition.",
        "original": ("bilaspur_stroke",),
    },
    "Chemotherapy": {
        "unit": "administrations",
        "scope": "Hospital day-care chemotherapy administrations across the published day-care departments",
        "limit": "One reporting hospital. Department-only series, counts with unverified procedure units, inpatient chemotherapy and radiotherapy treatments are excluded.",
        "expanded": ("aiims_bbsr_chemo_daycare_total",),
    },
    "Trauma / emergency": {
        "unit": "visits",
        "scope": "All-cause hospital emergency department casualty attendances",
        "limit": "Trauma-specific cases and separate emergency outpatient subtotals or inpatient counts are outside the scope.",
        "original": ("smimer_casualty",),
        "expanded": ("kem_mumbai_casualty", "sanjay_gandhi_memorial_casualty", "lok_nayak_casualty", "zydus_dahod_emergency"),
    },
}

UNIT_EQUIVALENTS = {
    ("Oral surgery", "vishnu_dental_oral_surgery"): "procedures",
    ("Trauma / emergency", "kem_mumbai_casualty"): "casualty attendances",
    ("Trauma / emergency", "sanjay_gandhi_memorial_casualty"): "casualty attendances",
    ("Dialysis", "hp_dialysis_model_data.json"): "PMNDP haemodialysis sessions",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def indexed(rows, field):
    result = {row[field]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"Duplicate caseload source key: {field}")
    return result


def linked_replay_rows(replay):
    """Use the previously reviewed exact branch links, including limited history series."""
    linked = {}
    for row in replay["caseloads"]:
        if row["status"] == "withheld" and row.get("reason") not in {
            "insufficient_matched_horizon_tests", "insufficient_recent_observations",
        }:
            continue
        if not row["facility_id"]:
            continue
        key = row["series_id"]
        site = row["site"]
        if row["facility_id"] != site["facility_id"] or (key in linked and linked[key] != site):
            raise ValueError(f"Conflicting reviewed site link: {key}")
        linked[key] = site
    return linked


def check_scope(service, spec, source_id, row):
    expected_unit = UNIT_EQUIVALENTS.get((service, source_id), spec["unit"])
    if row["service"] != service or row["unit"] != expected_unit:
        raise ValueError(f"Review changed care option or unit for {source_id}: {row['service']}, {row['unit']}")


def original_benchmark(service, spec, source, estimate, cutoff, site):
    key = source["id"]
    check_scope(service, spec, key, source)
    if any(source[field] != estimate[field] for field in ("facility", "region", "scope", "unit")):
        raise ValueError(f"Rebuild typical caseloads after revising observation {key}")
    first, last = estimate["first_month"], estimate["last_month"]
    if not first or month_index(last) >= cutoff:
        raise ValueError(f"Missing closed-period typical caseload for {key}")
    citation = {**estimate["source"], "locator": source["locator"]}
    observations = [
        {"period_start": month_label(month), "period_end": month_label(month), "reported_count": count, "months": 1}
        for month, count in sorted(monthly_counts(source.get("monthly", [])).items())
        if month_index(first) <= month <= month_index(last)
    ]
    if estimate["method"] != "median_of_reported_months":
        period = source["period"]
        observations = [{
            "period_start": first, "period_end": last, "months": estimate["period_months"],
            **({"reported_count": period["total"]} if "total" in period else {"reported_monthly_average": period["monthly_average"]}),
        }]
    if site and site["state"] != source["region"]:
        raise ValueError(f"Changed region for reviewed original series {key}")
    return {
        "series_id": key, "facility": source["facility"], "facility_id": site["facility_id"] if site else None,
        "region": source["region"], "locality": source["locality"], "site": site,
        "scope": source["scope"], "source_unit": source["unit"], "source": citation,
        "typical_month": estimate["typical_month"], "period_start": first, "period_end": last,
        "method": estimate["method"], "source_observations": observations,
        "source_notes": source.get("notes"),
    }


def expanded_benchmark(service, spec, series, manifest, cutoff, reviewed_site):
    key = series["id"]
    check_scope(service, spec, key, series)
    site = manifest["sites"][series["site"]]
    if reviewed_site and reviewed_site != site:
        raise ValueError(f"Review changed branch identity for {key}")
    months = GRAINS[series["grain"]][0]
    sources = manifest["sources"]
    observed = {m: value for m, value in read_series(series, sources).items() if m + months <= cutoff}
    if not observed:
        raise ValueError(f"Missing closed-period observations for {key}")
    last = max(observed)
    selected = sorted(m for m in observed if m > last - 12) if months == 1 else [last]
    history = [
        {"period_start": month_label(m), "period_end": month_label(m + months - 1),
         "reported_count": observed[m][0], "months": months, "source": sources[observed[m][1]]}
        for m in selected
    ]
    if months == 12:
        method = "latest_reported_annual_total_divided_by_12"
    elif len(selected) == 1:
        method = "one_reported_month_snapshot"
    else:
        method = "median_of_reported_months"
    return {
        "series_id": key, "facility": site["facility"],
        "facility_id": reviewed_site["facility_id"] if reviewed_site else None,
        "region": site["state"], "site": reviewed_site, "scope": series["scope"], "source_unit": series["unit"],
        "source": history[-1]["source"], "typical_month": median(observed[m][0] / months for m in selected),
        "period_start": history[0]["period_start"], "period_end": history[-1]["period_end"],
        "method": method, "source_observations": history,
    }


def cohort_benchmarks(service, spec, dataset, cutoff, sites):
    check_scope(service, spec, spec["cohort_file"], dataset)
    latest = {}
    for raw in dataset["observations"]:
        row = dict(zip(dataset["columns"], raw, strict=True))
        year = int(row["period"].removeprefix("FY").split("–")[0])
        start = month_index(f"{year}-04")
        key = row["facility_id"]
        if row["months"] != 12 or year < 2000 or row["period"] != f"FY{year}–{(year + 1) % 100:02d}":
            raise ValueError(f"Review fiscal-year exposure for {key}")
        if start + 12 <= cutoff and (key not in latest or start > latest[key][0]):
            latest[key] = (start, row)
    rows = []
    for key, (start, row) in sorted(latest.items()):
        source = dataset["sources"][row["source"]]
        hp_link = dataset.get("facility_links", {}).get(key)
        replay_id = spec.get("cohort_links", {}).get(key)
        site = sites.get(replay_id)
        if site and site["state"] != spec["region"]:
            raise ValueError(f"Changed region for reviewed programme site {key}")
        exposure = {"period_start": month_label(start), "period_end": month_label(start + 11),
                    "months": 12, "reported_count": row["total"]}
        rows.append({
            "series_id": key, "replay_series_id": replay_id, "facility": row["facility"],
            "facility_id": hp_link["fdr_id"] if hp_link else site["facility_id"] if site else None,
            "region": spec["region"], "district": row["district"], "site": site,
            "scope": spec["scope"], "source_unit": dataset["unit"], "source": source,
            "typical_month": row["total"] / 12, "period_start": exposure["period_start"],
            "period_end": exposure["period_end"], "method": "latest_reported_annual_total_divided_by_12",
            "source_observations": [exposure], "programme_link_evidence": hp_link,
            "reported_resource": {"value": row[dataset["capacity_field"]], "field": dataset["capacity_field"],
                                  "facility_type": row.get("facility_type")},
        })
    return rows


def nearest_rank_p90(values):
    """Return the observed nearest-rank p90, with no distribution extrapolation."""
    ordered = sorted(values)
    return ordered[ceil(0.9 * len(ordered)) - 1] if ordered else None


def leave_one_site_out(rows):
    """Measure cross-sectional transfer error with each reporting site excluded in turn."""
    count = len(rows)
    result = {"method": "leave_one_site_out_peer_median", "source_sites": count,
              "scored_sites": 0, "peer_count_per_fold": None, "mae": None, "rows": []}
    if count < 2:
        return result
    for position, row in enumerate(rows):
        prediction = median(peer["typical_month"] for index, peer in enumerate(rows) if index != position)
        amount = row["typical_month"]
        result["rows"].append({
            "series_id": row["series_id"], "facility": row["facility"], "observed_monthly_equivalent": amount,
            "prediction": prediction, "absolute_error": abs(amount - prediction),
        })
    result.update(scored_sites=count, peer_count_per_fold=count - 1,
                  mae=mean(row["absolute_error"] for row in result["rows"]))
    return result


def load_profiles(root=ROOT):
    """Load one benchmark per selected site using only observed, closed-period source data."""
    root = Path(root)
    observations = read_json(root / "observations.json")
    typical = read_json(root / "typical_caseload.json")
    replay = read_json(root / "care_options_replay.json")
    if set(observations["targets"]) != set(PROFILES) or set(typical["targets"]) != set(PROFILES):
        raise ValueError("The caseload profiles must match the current Care needed list")
    if observations["as_of_month"] != typical["as_of_month"]:
        raise ValueError("The input caseload artifacts use different cutoff months")
    cutoff = month_index(observations["as_of_month"])
    estimates = indexed(typical["estimates"], "id")
    primary = indexed(observations["series"], "id")
    sites = linked_replay_rows(replay)
    manifest_paths = [*sorted((root / "expansion").glob("*.json")), *sorted(root.glob("care_replay_*.json"))]
    expansion = {}
    for path in manifest_paths:
        manifest = read_json(path)
        for series in manifest.get("series", ()):
            key = series["id"]
            if key in expansion:
                raise ValueError(f"Duplicate expansion series: {key}")
            expansion[key] = (series, manifest)
    modelling_results = {
        cohort["service"]: cohort for cohort in read_json(root / "xgboost_results.json")["cohorts"]
    }

    result = {}
    for service, spec in PROFILES.items():
        rows = [
            original_benchmark(service, spec, primary[key], estimates[key], cutoff,
                               sites.get(spec.get("original_links", {}).get(key)))
            for key in spec.get("original", ())
        ]
        rows.extend(expanded_benchmark(service, spec, *expansion[key], cutoff, sites.get(key))
                    for key in spec.get("expanded", ()))
        cohort_file = spec.get("cohort_file")
        if cohort_file:
            rows.extend(cohort_benchmarks(service, spec, read_json(root / cohort_file), cutoff, sites))
        linked = [row["facility_id"] for row in rows if row["facility_id"]]
        if len(linked) != len(set(linked)):
            raise ValueError(f"Repeated site in {service} planning cohort")
        values = [row["typical_month"] for row in rows]
        if not values or any(type(value) not in (int, float) or not isfinite(value) or value < 0 for value in values):
            raise ValueError(f"Missing or invalid typical monthly workloads for {service}")
        diagnostics = leave_one_site_out(rows)
        if cohort_file:
            prior = modelling_results[service]
            diagnostics["prior_resource_baselines"] = {
                "cohort": prior["cohort"], "features": prior["features"], "unit": prior["unit"],
                "evaluation": prior["evaluation"],
            }
        result[service] = {
            "unit": spec["unit"], "scope": spec["scope"], "scope_limit": spec["limit"],
            "as_of_month": observations["as_of_month"], "benchmark_rows": rows,
            "replay_series_ids": [*spec.get("expanded", ()), *spec.get("cohort_links", {}).values()],
            "peer_median_month": median(values), "observed_p90_month": nearest_rank_p90(values),
            "capacity_scenario_method": "nearest_rank_observed_peer_p90",
            "region_counts": dict(sorted(Counter(row["region"] for row in rows).items())),
            "singleton_anchor": len(rows) == 1,
            "diagnostics": diagnostics,
        }
    return result


def profile_predictions(profiles):
    """Return aggregate monthly workload and throughput scenario values by care option."""
    return {
        service: {key: profile[key] for key in (
            "unit", "scope", "scope_limit", "peer_median_month", "observed_p90_month", "singleton_anchor",
        )}
        for service, profile in profiles.items()
    }
