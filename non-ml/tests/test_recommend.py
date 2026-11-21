import unittest

import pandas as pd

from facility_tool.recommend import compare_modes, simulate_spread


SERVICE = "dialysis"
AS_OF = "2026-11-21T12:00:00Z"


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
            pd.DataFrame(self.entities),
            pd.DataFrame(self.claims).assign(service=service),
            28, 77, service, as_of=AS_OF, spread_demand=True,
            pressure_inputs=pd.DataFrame([pressure("first", 100, 100), pressure("near", 1, 100)]).assign(
                service=service, unit="obstetric admissions"
            ),
            measured_load=pd.DataFrame([load("first", 1), load("near", 8)]).assign(
                service=service, unit="obstetric admission slots"
            ),
        )
        self.assertEqual(set(self.first_rows(result)["facility_id"]), {"first"})
        self.assertEqual(set(result["summary"]["load_status"]), {"non_emergency_only"})

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
