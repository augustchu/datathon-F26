"""Exercise the deployed entry point with a changing, simulated Databricks table."""

import os
from pathlib import Path
import re
from unittest import TestCase
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import classic_data


APP = Path(__file__).resolve().parents[1] / "classic_dashboard.py"
MANDI = "Netaji Subhash Chandra Bose Zonal Hospital"
KULLU = "Regional Hospital Kullu"


def facility(source_id, name, pin, lat, lon, **fields):
    return {"source_id": source_id, "facility_id": f"fdr:{source_id}", "facility_name": name,
            "pin": pin, "latitude": lat, "longitude": lon, "state": "Himachal Pradesh", **fields}


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def ranked_names(app):
    return [item.value.split(". ", 1)[1] for item in app.markdown if re.match(r"### \d+\. ", item.value)]


class DashboardFlowTest(TestCase):
    def test_route_adapter_keeps_reachable_options_and_falls_back_when_routing_fails(self):
        candidates = [{"row": {"lat": 31.7, "lon": 76.9}}, {"row": {"lat": 31.9, "lon": 77.1}}]
        origin = (31.833, 77.018)
        with patch.object(classic_data, "_cached_travel_times", return_value={"0": None, "1": 45}):
            routed = classic_data.route_classic_results(candidates, origin, use_traveltime=True)
        self.assertTrue(routed.uses_routes)
        self.assertEqual(routed.results, [{**candidates[1], "route_minutes": 45.0}])
        with patch.object(classic_data, "_cached_travel_times", side_effect=TimeoutError):
            fallback = classic_data.route_classic_results(candidates, origin, use_traveltime=True)
        self.assertFalse(fallback.uses_routes)
        self.assertEqual(fallback.results, candidates)

    def test_ranking_map_load_opt_in_and_databricks_refresh(self):
        version = 32
        records = pd.DataFrame([
            facility("c259a369-d940-4fdc-a51b-1784d5966941", MANDI, "175001", 31.71145, 76.92702),
            facility("9e64d63e-9421-4b29-ac72-dc73447a1db4", KULLU, "175101", 31.95667, 77.11154),
            facility("routing-hold", "Incorrectly geocoded branch", "175001", 31.833, 77.018,
                     service_text="procedure: haemodialysis; dialysis", branch_routing_hold=True),
            facility("wrong-service", "Unrelated branch", "175101", 31.833, 77.018,
                     service_text="description: dialysis is unavailable"),
        ])

        def query(statement, _, **options):
            if statement.startswith("DESCRIBE HISTORY"):
                return pd.DataFrame([{"version": version, "timestamp": f"2026-11-22 12:{version}:00"}])
            self.assertTrue(statement.endswith(f"VERSION AS OF {version}"), statement)
            return records.assign(snapshot_prepared_at=f"2026-11-22 12:{version}:00")

        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "dashboard-flow"}), \
             patch.object(classic_data, "_query_snapshot", side_effect=query):
            app = AppTest.from_file(str(APP), default_timeout=20).run()
            widget(app, "selectbox", "Service").select("Dialysis")
            widget(app, "selectbox", "Location type").select("Coordinates (lat/lon)")
            widget(app, "toggle", "Use TravelTime driving estimates").set_value(False)
            app.run()
            widget(app, "text_input", "Latitude").set_value("31.833")
            widget(app, "text_input", "Longitude").set_value("77.018").run()
            widget(app, "button", "Find facilities").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(ranked_names(app), [MANDI, KULLU])

            widget(app, "toggle", "Consider projected load").set_value(True).run()
            self.assertFalse(app.exception)
            self.assertEqual(ranked_names(app), [KULLU, MANDI])
            self.assertTrue(any("Compared projected monthly load for 2" in item.value for item in app.caption))
            map_html = app.get("iframe")[0].proto.srcdoc
            self.assertLess(map_html.index(f"1. {KULLU}"), map_html.index(f"2. {MANDI}"))

            widget(app, "selectbox", "How urgent is this need?").select("Urgent").run()
            self.assertFalse(app.exception)
            self.assertTrue(widget(app, "toggle", "Consider projected load").proto.disabled)
            self.assertEqual(ranked_names(app), [MANDI, KULLU])

            version = 33
            records = records.loc[records["facility_name"].ne(MANDI)]
            widget(app, "button", "Refresh from Databricks").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(ranked_names(app), [KULLU])
            self.assertTrue(any("Prepared snapshot 2026-11-22 12:33:00" in item.value for item in app.caption))

    def test_deployed_app_explains_missing_warehouse_instead_of_defaulting_to_demo(self):
        with patch.dict(os.environ, {"DATABRICKS_APP_NAME": "testapp"}, clear=True):
            app = AppTest.from_file(str(APP), default_timeout=20).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Databricks snapshot could not be loaded" in item.value for item in app.error))
        self.assertEqual(widget(app, "radio", "Facility data").value, "Prepared Databricks snapshot")
