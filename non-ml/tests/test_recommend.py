import json
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from facility_tool.recommend import PREPARED_COLUMNS, compare_modes, compare_prepared_modes, prepare_service_candidates, simulate_spread
from facility_tool.travel import SOURCE as TRAVELTIME_SOURCE
from facility_tool.workspace import Store


SERVICE = "dialysis"
AS_OF = "2026-11-21T12:00:00Z"
QUALITY_POLICY = {"corroboration_share": 0.6, "max_credit_km": 3, "max_credit_minutes": 5}
EXPERIENCE_POLICY = dict(QUALITY_POLICY, max_experience_credit_km=3, max_experience_credit_minutes=2)


def entity(branch, lon, quality="source_coordinates_unverified"):
    return dict(
        facility_id=branch, name=branch, address="Example address", city="Example city",
        district="Example district", state="Example state", pin="100001",
        lat=28.0, lon=lon, location_quality=quality,
    )


def claim(branch, stance="supports", validity="active", citation_quality="direct", scope="Published scope"):
    return dict(
        facility_id=branch, record_id=f"claim-{branch}-{stance}", service=SERVICE, stance=stance,
        excerpt="Dialysis service" if stance == "supports" else "Refers patients elsewhere for dialysis",
        source_url="https://example.org/facility", source="fixture", scope=scope, validity=validity,
        citation_quality=citation_quality,
    )


def update(branch, field, method, value, unit="", provenance="Synthetic fixture"):
    return dict(facility_id=branch, field=field, method=method, value=value, unit=unit, provenance=provenance)


def load(branch, available, *, unit="dialysis slots", as_of="2026-11-21T11:00:00Z", basis="measured"):
    return dict(
        facility_id=branch, service=SERVICE, unit=unit, as_of=as_of, available_units=available,
        source_url="https://example.org/measured-slots", basis=basis,
    )


def pressure(branch, demand, capacity, *, unit="dialysis visits", period="same day", basis="synthetic scenario"):
    return dict(
        facility_id=branch, service=SERVICE, demand=demand, capacity=capacity, unit=unit,
        period=period, basis=basis, source_url="https://example.org/pressure-scenario",
    )


def routes(**times):
    return pd.DataFrame([
        dict(facility_id=branch, travel_minutes=minutes, travel_source="Synthetic driving time")
        for branch, minutes in times.items()
    ])


def driving_routes(**estimates):
    return pd.DataFrame([
        dict(facility_id=branch, route_distance_km=kilometers, travel_minutes=minutes,
             travel_mode="driving", travel_source="Synthetic road route", travel_source_url="https://example.org/route")
        for branch, (kilometers, minutes) in estimates.items()
    ])


def provider_route(branch, kilometers=None, minutes=None, *, status="estimated", mode="driving"):
    return dict(facility_id=branch, route_distance_km=kilometers, travel_minutes=minutes,
                travel_mode=mode, travel_source=TRAVELTIME_SOURCE, travel_status=status)


def rated(site, value, key="Example survey, overall experience, 1–5, 2026 Q3", status="eligible"):
    return dict(site, patient_experience_value=value, patient_comparison_key=key, patient_experience_status=status)


def query(entities, claims, updates=None, measurements=None, **kwargs):
    return compare_modes(
        pd.DataFrame(entities), pd.DataFrame(claims), 28.0, 77.0, SERVICE,
        updates=pd.DataFrame(updates) if updates is not None else None,
        measured_load=pd.DataFrame(measurements) if measurements is not None else None,
        as_of=AS_OF, **kwargs,
    )


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.entities = [entity("first", 77.01), entity("near", 77.03), entity("far", 77.18)]
        self.claims = [claim(row["facility_id"]) for row in self.entities]

    @staticmethod
    def first_rows(result):
        rows = result["rows"]
        return rows.loc[rows["rank"].eq(1) & rows["kind"].eq("recommendation")]

    def test_modes_only_overlay_estimates_and_keep_service_leads_separate(self):
        entities = self.entities + [entity("inferred", 77.001), entity("contradiction", 77.005)]
        claims = self.claims + [claim("contradiction"), claim("contradiction", "contradicts")]
        updates = [
            update("first", "capacity", "ml", 150, "beds"),
            update("first", "capacity", "regression", 100, "beds"),
            update("near", "capacity", "reported", 90, "beds"),
            update("near", "capacity", "source_recovered", 110, "beds"),
            update("near", "capacity", "ml", 130, "beds"),
            update("inferred", f"service:{SERVICE}", "ml", 0.91, "probability"),
            update("contradiction", f"service:{SERVICE}", "ml", 0.99, "probability"),
        ]
        output = query(entities, claims, updates)
        first = self.first_rows(output).set_index("mode")
        self.assertEqual(set(first["facility_id"]), {"first"})
        self.assertEqual(first.loc["confirmed", "capacity_method"], "unknown")
        self.assertEqual(first.loc["ml", "capacity"], 150)
        self.assertEqual(first.loc["regression", "capacity"], 100)
        leads = output["rows"].loc[output["rows"]["kind"].eq("lead")]
        self.assertEqual(list(leads[["mode", "facility_id"]].itertuples(index=False, name=None)), [("ml", "inferred")])
        trace = output["trace"]
        self.assertTrue(trace.loc[trace["facility_id"].eq("near"), "capacity_method"].eq("conflicting").all())
        self.assertTrue(trace.loc[trace["facility_id"].eq("contradiction"), "decision"].eq("excluded").all())

    def test_unavailable_modes_and_unusable_service_evidence_are_visible(self):
        entities = [entity("candidate citation", 77.01), entity("coarse", 77.01, "pin_centroid"), entity("expired", 77.01)]
        claims = [claim("candidate citation", citation_quality="candidate_links"), claim("coarse"), claim("expired", validity="expired")]
        output = query(entities, claims)
        self.assertTrue(output["rows"].empty)
        self.assertEqual(
            output["summary"].set_index("mode").loc["ml", "mode_status"],
            "no_model_outputs_supplied",
        )
        self.assertEqual(set(output["trace"]["facility_id"]), {row["facility_id"] for row in entities})

    def test_absent_optional_checkpoints_leave_geographic_ranking_available(self):
        output = query(
            self.entities, self.claims, updates=[], measurements=[], pressure_inputs=pd.DataFrame(),
            travel_times=pd.DataFrame(), spread_demand=True,
        )
        self.assertEqual(set(self.first_rows(output)["facility_id"]), {"first"})
        self.assertEqual(set(output["summary"]["load_status"]), {"no_measured_load"})

    def test_prepared_routing_uses_audited_flags_and_retains_location_sources(self):
        held = dict(
            self.entities[0], location_routing_eligible=False, location_hold_reason="Pin conflicts with coordinates",
            location_source_urls=["https://example.org/location"], location_review_flags=["pin_conflict"],
        )
        ready = dict(self.entities[1], location_routing_eligible=True, location_hold_reason="")
        prepared = prepare_service_candidates(pd.DataFrame([held, ready]), pd.DataFrame(self.claims), SERVICE, as_of=AS_OF)
        result = compare_prepared_modes(prepared, 28.0, 77.0, SERVICE, as_of=AS_OF)
        self.assertEqual(set(self.first_rows(result)["facility_id"]), {"near"})
        review = result["trace"].loc[result["trace"]["facility_id"].eq("first")].iloc[0]
        self.assertEqual(review["review_reason"], "Pin conflicts with coordinates")
        self.assertEqual(review["location_source_urls"], ["https://example.org/location"])
        self.assertEqual(review["location_review_flags"], ["pin_conflict"])
        self.assertTrue(pd.isna(review["distance_km"]))

    def test_prepared_comparison_round_trips_through_json_checkpoint(self):
        entities = pd.DataFrame([
            dict(row, location_routing_eligible=True, location_hold_reason="")
            for row in [*self.entities, entity("lead", 77.001)]
        ])
        claims = pd.DataFrame([
            dict(row, validity_start="2026-01-01", validity_end="2026-11-22")
            for row in self.claims
        ])
        updates = pd.DataFrame([
            dict(update("first", "capacity", "ml", 120, "beds"), lower=pd.NA, upper=float("nan")),
            update("lead", f"service:{SERVICE}", "regression", 0.8, "probability"),
        ])
        prepared = prepare_service_candidates(entities, claims, SERVICE, updates=updates, as_of=AS_OF)
        expected = compare_modes(entities, claims, 28.0, 77.0, SERVICE, updates=updates, as_of=AS_OF)
        with TemporaryDirectory() as directory:
            store = Store(".", "demo", "2026-11-21", output_dir=directory)
            store.write("service_candidates", prepared)
            reloaded = store.read("service_candidates")
            actual = compare_prepared_modes(reloaded, 28.0, 77.0, SERVICE, as_of=AS_OF)
            later = compare_prepared_modes(reloaded, 28.0, 77.0, SERVICE, as_of="2026-11-25", lead_threshold=1)
        for name in expected:
            pd.testing.assert_frame_equal(expected[name], actual[name])
        self.assertEqual(len(later["rows"].loc[later["rows"]["kind"].eq("lead")]), 0)
        self.assertEqual(set(later["rows"]["evidence_as_of"]), {"2026-11-21"})
        self.assertEqual(set(later["rows"]["validity"]), {"active"})
        fields = self.first_rows(actual).set_index("mode").loc["ml", "field_updates_json"]
        self.assertIsNone(json.loads(fields)["capacity"]["lower"])
        self.assertIsNone(json.loads(fields)["capacity"]["upper"])

    def test_empty_prepared_checkpoint_keeps_results_schema(self):
        result = compare_prepared_modes(pd.DataFrame(columns=PREPARED_COLUMNS), 28.0, 77.0, SERVICE, as_of=AS_OF)
        self.assertTrue(result["rows"].empty)
        self.assertEqual(list(result["rows"].columns), list(result["trace"].columns))
        self.assertEqual(set(result["summary"]["n_eligible"]), {0})

    def test_query_date_refreshes_a_claims_parsed_validity(self):
        stale = dict(claim("first"), validity_start="2026-04-01", validity_end="2026-11-20")
        suspended = dict(claim("near", validity="inactive"), validity_start="2026-04-01", validity_end="2027-04-01")
        output = query(self.entities, [stale, suspended])
        self.assertTrue(output["rows"].empty)
        self.assertEqual(set(output["trace"]["validity"]), {"expired", "inactive"})

    def test_opt_in_load_uses_fresh_comparable_service_measurements_and_hard_cap(self):
        measurements = [load("first", 1), load("near", 8), load("far", 30)]
        normal = query(self.entities, self.claims, measurements=measurements)
        opted = query(self.entities, self.claims, measurements=measurements, spread_demand=True, max_extra_km=5)
        self.assertEqual(set(self.first_rows(normal)["facility_id"]), {"first"})
        first = self.first_rows(opted)
        self.assertEqual(set(first["facility_id"]), {"near"})
        self.assertTrue(first["extra_km_vs_baseline"].le(5).all())
        self.assertTrue(first["load_available_units"].eq(8).all())
        self.assertTrue(first["load_source_url"].str.startswith("https://").all())

    def test_load_cannot_promote_weaker_evidence_or_a_different_scope(self):
        claims = [claim("first"), claim("near", validity="unknown"), claim("far", scope="Restricted scheme")]
        out = query(
            self.entities, claims, measurements=[load("first", 1), load("near", 9), load("far", 9)],
            spread_demand=True, max_extra_km=30,
        )
        self.assertEqual(set(self.first_rows(out)["facility_id"]), {"first"})

    def test_load_rejects_stale_observations_incompatible_units_and_scenarios(self):
        for near in (
            load("near", 10, as_of="2026-11-20T01:00:00Z"),
            load("near", 10, as_of="2026-11-21T03:00:00Z"),
            load("near", 10, unit="beds"),
            load("near", 10, unit="total facility beds"),
            load("near", 10, basis="synthetic"),
        ):
            with self.subTest(near=near):
                out = query(self.entities, self.claims, measurements=[load("first", 1), near], spread_demand=True)
                self.assertEqual(set(self.first_rows(out)["facility_id"]), {"first"})
                self.assertEqual(set(out["summary"]["load_status"]), {"no_fresh_comparable_measurements"})

    def test_weighted_score_combines_stated_pressure_and_minutes(self):
        route_times = routes(first=20, near=22, far=80)
        pressures = pd.DataFrame([pressure("first", 100, 100), pressure("near", 10, 100), pressure("far", 0, 100)])
        off = query(self.entities, self.claims, pressure_inputs=pressures, travel_times=route_times)
        self.assertEqual(set(self.first_rows(off)["facility_id"]), {"first"})
        self.assertEqual(set(self.first_rows(off)["travel_minutes"]), {20})
        ranked = query(
            self.entities, self.claims, pressure_inputs=pressures, travel_times=route_times,
            spread_demand=True, max_extra_minutes=10, travel_weight=0.5,
        )
        best = self.first_rows(ranked)
        self.assertEqual(set(best["facility_id"]), {"near"})
        self.assertEqual(set(best["weighted_travel_metric"]), {"route_minutes"})
        self.assertEqual(set(best["extra_minutes_vs_baseline"]), {2})
        self.assertEqual(set(best["pressure_ratio"]), {0.1})
        self.assertEqual(set(best["pressure_grade"]), {"demand_below_stated_capacity"})
        self.assertAlmostEqual(best.iloc[0]["weighted_score"], 0.5 * (0.2 + 0.1 / 1.1))
        self.assertTrue(ranked["trace"].loc[ranked["trace"]["facility_id"].eq("far"), "weighted_score"].isna().all())

    def test_full_route_times_set_normal_order_but_partial_times_do_not(self):
        full = query(self.entities, self.claims, travel_times=routes(first=20, near=10, far=80))
        partial = query(self.entities, self.claims, travel_times=routes(first=20, near=10))
        self.assertEqual(set(self.first_rows(full)["facility_id"]), {"near"})
        self.assertEqual(set(self.first_rows(partial)["facility_id"]), {"first"})
        self.assertEqual(set(self.first_rows(partial)["travel_minutes"]), {20})
        self.assertEqual(set(partial["summary"]["ranking_travel_metric"]), {"straight_line_km"})

    def test_requested_driving_distance_can_rank_differently_from_time(self):
        matrix = driving_routes(first=(6, 30), near=(4, 45), far=(22, 55))
        by_distance = query(self.entities, self.claims, travel_times=matrix, route_sort="distance")
        by_time = query(self.entities, self.claims, travel_times=matrix, route_sort="time")
        first = self.first_rows(by_distance)
        self.assertEqual(set(first["facility_id"]), {"near"})
        self.assertEqual(set(self.first_rows(by_time)["facility_id"]), {"first"})
        self.assertEqual(set(first["ranking_travel_metric"]), {"route_distance_km"})
        self.assertEqual(set(first["route_distance_km"]), {4})
        self.assertEqual(set(first["travel_minutes"]), {45})
        self.assertEqual(set(first["travel_mode"]), {"driving"})
        self.assertEqual(set(first["travel_source_url"]), {"https://example.org/route"})
        self.assertEqual(set(first["extra_route_km_vs_baseline"]), {0})

    def test_incomplete_or_mixed_mode_route_distance_falls_back_without_dropping_estimates(self):
        incomplete = driving_routes(first=(6, 30), near=(4, 15), far=(None, 55))
        mixed = driving_routes(first=(6, 30), near=(4, 15), far=(25, 55))
        mixed.loc[mixed["facility_id"].eq("far"), "travel_mode"] = "cycling"
        for matrix in (incomplete, mixed):
            with self.subTest(matrix=matrix):
                result = query(self.entities, self.claims, travel_times=matrix, route_sort="distance")
                first = self.first_rows(result)
                self.assertEqual(set(first["facility_id"]), {"first"})
                self.assertEqual(set(first["ranking_travel_metric"]), {"straight_line_km"})
                self.assertEqual(set(first["route_distance_km"]), {6})
                self.assertTrue(first["ranking_travel_reason"].str.contains("unavailable for the full set").all())

    def test_required_route_excludes_failures_without_a_straight_line_fallback(self):
        entities = self.entities + [entity("distant", 77.9), entity("failed", 77.002),
                                   entity("wrong-mode", 77.004), entity("held", 77.003)]
        claims = [claim(row["facility_id"], validity="expired" if row["facility_id"] == "held" else "active")
                  for row in entities]
        matrix = pd.DataFrame([
            provider_route("first", minutes=5),
            provider_route("near", status="not_routed_within_limit"),
            provider_route("far", 140, 110),
            provider_route("distant", 103, 90),
            provider_route("failed", status="network_error"),
            provider_route("wrong-mode", 0.5, 2, mode="cycling"),
            provider_route("held", 0.4, 2),
        ])
        result = query(entities, claims, travel_times=matrix, route_sort="distance",
                       required_travel_mode="driving", max_radius_km=None)
        recommendations = result["rows"].loc[result["rows"]["mode"].eq("confirmed")]
        self.assertEqual(recommendations["facility_id"].tolist(), ["distant", "far"])
        self.assertGreater(recommendations.iloc[0]["distance_km"], 50)
        self.assertEqual(set(recommendations["ranking_travel_metric"]), {"route_distance_km"})
        summary = result["summary"].iloc[0]
        self.assertEqual((summary.n_documented_candidates, summary.n_eligible, summary.n_route_unreachable,
                          summary.n_route_unavailable, summary.routing_comparison_status), (6, 2, 1, 3, "partial"))
        trace = result["trace"].loc[result["trace"]["mode"].eq("confirmed")].set_index("facility_id")
        self.assertEqual(trace.loc["first", "travel_status"], "missing_requested_metric")
        self.assertEqual(trace.loc["near", "travel_status"], "not_routed_within_limit")
        self.assertEqual(trace.loc["failed", "travel_status"], "network_error")
        self.assertEqual(trace.loc["wrong-mode", "travel_status"], "mismatched_transport_mode")
        self.assertIn("expired", trace.loc["held", "review_reason"])

    def test_required_route_top_five_ties_use_measured_time_then_id(self):
        entities = [entity(f"facility-{i}", 77 + i / 4) for i in range(7)]
        claims = [claim(row["facility_id"]) for row in entities]
        # TravelTime distances tie. The near facility takes longer than several farther options.
        matrix = pd.DataFrame([provider_route(f"facility-{i}", 210, 125 - i // 2) for i in range(7)])
        result = query(entities, claims, travel_times=matrix, route_sort="distance",
                       required_travel_mode="driving", max_radius_km=None)
        recommendations = result["rows"].loc[result["rows"]["mode"].eq("confirmed")]
        self.assertEqual(recommendations["facility_id"].tolist(),
                         ["facility-6", "facility-4", "facility-5", "facility-2", "facility-3"])
        self.assertEqual(recommendations["rank"].tolist(), [1, 2, 3, 4, 5])
        self.assertEqual(set(result["summary"]["routing_comparison_status"]), {"complete"})

    def test_required_route_holds_load_reranking_when_detour_time_is_unknown(self):
        matrix = pd.DataFrame([provider_route("first", 20, 20), provider_route("near", 22),
                               provider_route("far", 23, 40)])
        pressures = pd.DataFrame([pressure("first", 100, 100), pressure("near", 0, 100), pressure("far", 0, 100)])
        result = query(self.entities, self.claims, travel_times=matrix, route_sort="distance",
                       required_travel_mode="driving", max_radius_km=None, pressure_inputs=pressures,
                       spread_demand=True, max_extra_minutes=10, travel_weight=0)
        self.assertEqual(set(self.first_rows(result)["facility_id"]), {"first"})
        self.assertEqual(set(result["summary"]["weighted_status"]), {"no_comparable_alternative_within_detour"})

    def test_load_detour_enforces_both_driving_distance_and_minutes(self):
        matrix = driving_routes(first=(20, 20), near=(32, 21), far=(22, 60))
        pressures = pd.DataFrame([pressure("first", 100, 100), pressure("near", 0, 100), pressure("far", 0, 100)])
        measurements = [load("first", 1), load("near", 10), load("far", 10)]
        for route_sort in ("time", "distance"):
            common = dict(
                spread_demand=True, route_sort=route_sort, travel_times=matrix,
                max_extra_km=5, max_extra_minutes=10, travel_weight=0,
            )
            with self.subTest(route_sort=route_sort):
                weighted = query(self.entities, self.claims, pressure_inputs=pressures, **common)
                measured = query(self.entities, self.claims, measurements=measurements, **common)
                self.assertEqual(set(self.first_rows(weighted)["facility_id"]), {"first"})
                self.assertEqual(set(self.first_rows(measured)["facility_id"]), {"first"})
                self.assertEqual(set(weighted["summary"]["weighted_status"]), {"no_comparable_alternative_within_detour"})
                self.assertEqual(set(measured["summary"]["load_status"]), {"no_fresh_comparable_measurements"})

    def test_time_ranking_still_caps_straight_line_detour_without_route_km(self):
        result = query(
            self.entities, self.claims, measurements=[load("first", 1), load("near", 4), load("far", 10)],
            travel_times=routes(first=20, near=21, far=22), route_sort="time",
            spread_demand=True, max_extra_km=5, max_extra_minutes=10,
        )
        self.assertEqual(set(self.first_rows(result)["facility_id"]), {"near"})
        self.assertEqual(set(self.first_rows(result)["ranking_travel_metric"]), {"route_minutes"})

    def test_documented_quality_can_promote_a_nearby_facility_with_explicit_credit(self):
        entities = [
            self.entities[0],
            dict(self.entities[1], quality_corroborated=True, quality_assessed=True),
            self.entities[2],
        ]
        ordinary = query(entities, self.claims)
        quality_ranked = query(entities, self.claims, quality_policy=QUALITY_POLICY)
        self.assertEqual(set(self.first_rows(ordinary)["facility_id"]), {"first"})
        winner = self.first_rows(quality_ranked).iloc[0]
        self.assertEqual(winner["facility_id"], "near")
        self.assertEqual(winner["travel_only_rank"], 2)
        self.assertEqual(winner["baseline_rank"], 1)
        self.assertEqual(winner["ranking_policy"], "documented_quality_and_travel")
        self.assertEqual(winner["quality_credit_unit"], "km")
        self.assertAlmostEqual(winner["corroboration_credit"], 1.8)
        self.assertAlmostEqual(winner["assessment_credit"], 1.2)
        self.assertAlmostEqual(winner["balanced_travel_score"], winner["distance_km"] - 3)

    def test_documented_quality_never_crosses_evidence_gates_or_credit_limit(self):
        entities = [
            self.entities[0],
            dict(self.entities[1], quality_corroborated=True, quality_assessed=True),
            dict(self.entities[2], quality_corroborated=True, quality_assessed=True),
        ]
        only_far_eligible = query(entities, [claim("first"), claim("near", "weak"), claim("far")], quality_policy=QUALITY_POLICY)
        self.assertEqual(set(self.first_rows(only_far_eligible)["facility_id"]), {"first"})
        near = only_far_eligible["trace"].loc[only_far_eligible["trace"]["facility_id"].eq("near")]
        self.assertTrue(near["rank"].isna().all())
        lower_tier = query(
            entities, [claim("first"), claim("near", validity="unknown"), claim("far", "weak")],
            quality_policy=QUALITY_POLICY,
        )
        self.assertEqual(set(self.first_rows(lower_tier)["facility_id"]), {"first"})
        self.assertTrue(lower_tier["trace"]["quality_credit"].dropna().le(3).all())

    def test_route_credit_uses_minutes_and_breaks_equal_scores_by_travel(self):
        entities = [self.entities[0], dict(self.entities[1], quality_corroborated=True, quality_assessed=True)]
        claims = self.claims[:2]
        tie = query(
            entities, claims, travel_times=routes(first=20, near=25),
            route_sort="time", quality_policy=QUALITY_POLICY,
        )
        earlier = query(
            entities, claims, travel_times=routes(first=20, near=24),
            route_sort="time", quality_policy=QUALITY_POLICY,
        )
        self.assertEqual(set(self.first_rows(tie)["facility_id"]), {"first"})
        winner = self.first_rows(earlier).iloc[0]
        self.assertEqual(winner["facility_id"], "near")
        self.assertEqual(winner["quality_credit_unit"], "minutes")
        self.assertEqual(winner["quality_credit"], 5)

    def test_unknown_quality_flags_add_no_credit_and_preserve_travel_order(self):
        entities = [dict(row, quality_corroborated="true", quality_assessed=None) for row in self.entities]
        result = query(entities, self.claims, quality_policy=QUALITY_POLICY)
        self.assertEqual(set(self.first_rows(result)["facility_id"]), {"first"})
        self.assertEqual(set(result["trace"]["quality_credit"]), {0})
        self.assertEqual(set(result["summary"]["ranking_policy"]), {"travel_only"})
        self.assertTrue(result["trace"][["quality_corroborated", "quality_assessed"]].isna().all().all())

    def test_opted_in_load_remains_final_after_quality_ranking(self):
        entities = [
            self.entities[0], dict(self.entities[1], quality_corroborated=True, quality_assessed=True), self.entities[2],
        ]
        balanced = query(
            entities, self.claims, measurements=[load("first", 8), load("near", 2), load("far", 100)],
            quality_policy=QUALITY_POLICY, spread_demand=True, max_extra_km=5,
        )
        winner = self.first_rows(balanced).iloc[0]
        self.assertEqual(winner["facility_id"], "first")
        self.assertEqual(winner["baseline_rank"], 2)
        self.assertEqual(winner["travel_only_rank"], 1)
        self.assertEqual(winner["load_status"], "measured_availability_rerank")

    def test_comparable_experience_contributes_a_separate_bounded_credit(self):
        entities = [rated(self.entities[0], 0), rated(self.entities[1], 0.9)]
        result = query(entities, self.claims[:2], quality_policy=EXPERIENCE_POLICY)
        winner = self.first_rows(result).iloc[0]
        self.assertEqual(winner["facility_id"], "near")
        self.assertEqual(winner["travel_only_rank"], 2)
        self.assertEqual(winner["patient_ranking_status"], "applied")
        self.assertEqual(winner["ranking_policy"], "patient_experience_and_travel")
        self.assertEqual(winner["patient_tier_eligible"], 2)
        self.assertAlmostEqual(winner["patient_experience_credit"], 2.7)
        self.assertEqual(winner["quality_credit"], 0)
        self.assertAlmostEqual(winner["balanced_travel_score"], winner["distance_km"] - 2.7)
        self.assertEqual(set(result["summary"]["quality_total_max_credit"]), {6})
        closest = query(entities, self.claims[:2])
        self.assertEqual(set(self.first_rows(closest)["facility_id"]), {"first"})
        self.assertEqual(set(closest["trace"]["patient_experience_credit"]), {0})

    def test_experience_coverage_uses_full_eligible_radius_before_top_k(self):
        entities = [rated(self.entities[0], 0), rated(self.entities[1], 1), self.entities[2]]
        incomplete = query(entities, self.claims, quality_policy=EXPERIENCE_POLICY, top_k=1)
        complete = query(entities, self.claims, quality_policy=EXPERIENCE_POLICY, top_k=1, max_radius_km=5)
        first = self.first_rows(incomplete).iloc[0]
        self.assertEqual(first["facility_id"], "first")
        self.assertEqual(first["patient_ranking_status"], "held_incomplete_coverage")
        self.assertEqual((first["patient_tier_eligible"], first["patient_tier_candidates"]), (2, 3))
        self.assertEqual(set(incomplete["trace"]["patient_experience_credit"]), {0})
        self.assertEqual(set(self.first_rows(complete)["facility_id"]), {"near"})
        self.assertEqual(set(self.first_rows(complete)["patient_tier_candidates"]), {2})

    def test_experience_holds_incomparable_windows_and_undersized_tiers(self):
        entities = [rated(self.entities[0], 0), rated(self.entities[1], 1, key="Example survey, overall, 1–5, 2025 Q3")]
        incomparable = query(entities, self.claims[:2], quality_policy=EXPERIENCE_POLICY)
        self.assertEqual(set(self.first_rows(incomparable)["facility_id"]), {"first"})
        self.assertEqual(set(incomparable["trace"]["patient_ranking_status"]), {"held_incomparable"})
        self.assertEqual(set(incomparable["trace"]["patient_experience_credit"]), {0})
        sparse = query(entities, [claim("first"), claim("near", validity="unknown")], quality_policy=EXPERIENCE_POLICY)
        self.assertEqual(set(sparse["trace"]["patient_ranking_status"]), {"held_insufficient_candidates"})
        self.assertEqual(set(sparse["trace"]["patient_experience_credit"]), {0})
        self.assertEqual(set(sparse["summary"]["patient_held_tiers"]), {2})

    def test_patient_experience_uses_route_minutes_and_load_still_runs_last(self):
        entities = [
            rated(self.entities[0], 0),
            rated(dict(self.entities[1], quality_corroborated=True), 1),
        ]
        common = dict(travel_times=routes(first=20, near=21), quality_policy=EXPERIENCE_POLICY)
        ordinary = query(entities, self.claims[:2], **common)
        winner = self.first_rows(ordinary).iloc[0]
        self.assertEqual(winner["facility_id"], "near")
        self.assertEqual(winner["quality_credit_unit"], "minutes")
        self.assertEqual(winner["patient_experience_credit"], 2)
        self.assertEqual(winner["quality_credit"], 3)
        self.assertEqual(winner["total_quality_experience_credit"], 5)
        balanced = query(
            entities, self.claims[:2], measurements=[load("first", 7), load("near", 1)],
            spread_demand=True, **common,
        )
        self.assertEqual(set(self.first_rows(balanced)["facility_id"]), {"first"})
        self.assertEqual(set(balanced["summary"]["load_status"]), {"measured_availability_rerank"})

    def test_mixed_routes_use_one_score_basis_and_honor_known_time_cap(self):
        pressures = pd.DataFrame([pressure("first", 100, 100), pressure("near", 0, 100), pressure("far", 10, 100)])
        result = query(
            self.entities, self.claims, pressure_inputs=pressures, travel_times=routes(first=20, near=40),
            spread_demand=True, max_extra_km=25, max_extra_minutes=10, travel_weight=0.0,
        )
        self.assertEqual(set(self.first_rows(result)["facility_id"]), {"far"})
        self.assertEqual(set(result["trace"]["weighted_travel_metric"].dropna()), {"straight_line_km"})
        self.assertTrue(result["trace"].loc[result["trace"]["facility_id"].eq("near"), "weighted_score"].isna().all())

    def test_incomparable_pressure_falls_back_to_fresh_measured_availability(self):
        for unmatched in (
            pressure("near", 1, 100, period="other day"),
            pressure("near", 1, 100, basis="incompatible estimator"),
            pressure("near", 1, 100, unit="beds"),
            pressure("near", 1, 0),
        ):
            with self.subTest(unmatched=unmatched):
                result = query(
                    self.entities, self.claims, measurements=[load("first", 1), load("near", 7)],
                    pressure_inputs=pd.DataFrame([pressure("first", 30, 100), unmatched]),
                    spread_demand=True,
                )
                self.assertEqual(set(self.first_rows(result)["facility_id"]), {"near"})
                self.assertEqual(set(result["summary"]["weighted_status"]), {"no_comparable_pressure_inputs"})
                self.assertEqual(set(result["summary"]["load_status"]), {"measured_availability_rerank"})

    def test_explicit_emergency_request_stays_on_normal_order(self):
        service = "emergency_c_section"
        result = compare_modes(
            pd.DataFrame([rated(self.entities[0], 0),
                          rated(dict(self.entities[1], quality_corroborated=True, quality_assessed=True), 1),
                          rated(self.entities[2], 1)]),
            pd.DataFrame(self.claims).assign(service=service),
            28, 77, service, as_of=AS_OF, spread_demand=True, quality_policy=EXPERIENCE_POLICY,
            pressure_inputs=pd.DataFrame([pressure("first", 100, 100), pressure("near", 1, 100)]).assign(
                service=service, unit="obstetric admissions"
            ),
            measured_load=pd.DataFrame([load("first", 1), load("near", 8)]).assign(
                service=service, unit="obstetric admission slots"
            ),
        )
        self.assertEqual(set(self.first_rows(result)["facility_id"]), {"first"})
        self.assertEqual(set(result["summary"]["load_status"]), {"non_emergency_only"})
        self.assertEqual(set(result["summary"]["ranking_policy"]), {"emergency_travel_only"})
        self.assertEqual(set(result["trace"]["quality_credit"]), {0})
        self.assertEqual(set(result["trace"]["patient_experience_credit"]), {0})

    def test_simulation_retains_total_and_distance_cap(self):
        trace = query(self.entities, self.claims)["trace"]
        candidates = trace.loc[trace["mode"].eq("confirmed")]
        assumptions = pd.DataFrame([
            dict(facility_id=branch, service=SERVICE, unit="dialysis slots per shift", capacity=size)
            for branch, size in (("first", 2), ("near", 6), ("far", 100))
        ])
        out = simulate_spread(candidates, assumptions, SERVICE, requests=60, max_extra_km=5).set_index("facility_id")
        self.assertEqual(out["baseline_suggestions"].sum(), 60)
        self.assertEqual(out["balanced_suggestions"].sum(), 60)
        self.assertEqual(out.loc["far", "balanced_suggestions"], 0)
        self.assertGreater(out.loc["near", "balanced_suggestions"], out.loc["first", "balanced_suggestions"])
        self.assertEqual(set(out["status"]), {"simulated_suggestions_only"})


if __name__ == "__main__":
    unittest.main()
