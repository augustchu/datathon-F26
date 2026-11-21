import json
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

import pandas as pd

from facility_tool.travel import ENDPOINT, SOURCE, estimate_travel


ORIGIN = (28.0, 77.0)
SETTINGS = dict(enabled=True, app_id="test-app", api_key="test-key", departure_time="2026-11-21T17:30:00+05:30")


def facilities():
    return pd.DataFrame([
        dict(facility_id="local-id-b", lat=28.0, lon=77.01, name="Example B"),
        dict(facility_id="local-id-a", lat=28.0, lon=77.01, name="Example A"),
        dict(facility_id="local-id-c", lat=28.0, lon=77.02),
        dict(facility_id="local-id-distant", lat=28.0, lon=77.03),
        dict(facility_id="local-id-centroid", lat=28.0, lon=77.0, location_quality="district_centroid"),
        dict(facility_id="local-id-conflict", lat=28.0, lon=77.0, location_conflict=True),
        dict(facility_id="local-id-invalid", lat=0.0, lon=0.0),
    ])


class TravelTests(unittest.TestCase):
    @patch("facility_tool.travel._post_json")
    def test_disabled_and_missing_credentials_never_call_provider(self, post):
        with patch.dict(os.environ, {}, clear=True):
            disabled = estimate_travel(ORIGIN, facilities())
            unconfigured = estimate_travel(ORIGIN, facilities(), enabled=True, app_id="test-app")
        self.assertTrue(disabled["travel_status"].eq("disabled").all())
        self.assertTrue(unconfigured["travel_status"].eq("missing_credentials").all())
        self.assertTrue(unconfigured["travel_minutes"].isna().all())
        self.assertTrue(unconfigured["route_distance_km"].isna().all())
        post.assert_not_called()

    @patch("facility_tool.travel._post_json")
    def test_one_matrix_keeps_partial_coverage_visible_and_stable(self, post):
        post.return_value = {"results": [{
            "search_id": "from-origin",
            "locations": [{"id": "destination-1", "properties": [{"travel_time": 735, "distance": 1570}]}],
            "unreachable": ["destination-2"],
        }]}
        original = facilities()
        found = estimate_travel(ORIGIN, original, mode="public_transport", max_destinations=3, **SETTINGS)
        self.assertEqual(found["facility_id"].tolist(), original["facility_id"].tolist())
        post.assert_called_once()
        payload = post.call_args.args[0]
        self.assertEqual(payload["locations"][0], {"id": "origin", "coords": {"lat": 28.0, "lng": 77.0}})
        self.assertEqual(payload["locations"][1]["coords"], {"lat": 28.0, "lng": 77.01})
        self.assertEqual(payload["departure_searches"], [{
            "id": "from-origin",
            "departure_location_id": "origin",
            "arrival_location_ids": ["destination-1", "destination-2", "destination-3"],
            "departure_time": "2026-11-21T12:00:00Z",
            "transportation": {"type": "public_transport"},
            "travel_time": 5400,
            "properties": ["travel_time"],
        }])
        self.assertNotIn("local-id", json.dumps(payload))
        self.assertNotIn("Example", json.dumps(payload))
        by_id = found.set_index("facility_id")
        self.assertEqual(by_id.at["local-id-a", "travel_minutes"], 12.25)
        self.assertEqual(by_id.at["local-id-a", "travel_source"], SOURCE)
        self.assertTrue(by_id["route_distance_km"].isna().all())
        self.assertEqual(by_id["travel_status"].to_dict(), {
            "local-id-a": "estimated",
            "local-id-b": "not_routed_within_limit",
            "local-id-c": "missing_result",
            "local-id-distant": "outside_request_limit",
            "local-id-centroid": "invalid_destination",
            "local-id-conflict": "invalid_destination",
            "local-id-invalid": "invalid_destination",
        })
        self.assertTrue(by_id.loc[by_id["travel_status"].ne("estimated"), "travel_minutes"].isna().all())
        reordered = estimate_travel(ORIGIN, original.iloc[::-1], mode="public_transport", max_destinations=3, **SETTINGS)
        self.assertEqual(post.call_args.args[0], payload)
        pd.testing.assert_frame_equal(by_id.sort_index(), reordered.set_index("facility_id").sort_index())

    @patch("facility_tool.travel.build_opener")
    def test_transport_uses_fixed_endpoint_and_header_authentication(self, build):
        opener = build.return_value
        response = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        response.read.return_value = json.dumps({"results": [{
            "search_id": "from-origin",
            "locations": [{"id": "destination-1", "properties": [{"travel_time": 15}]}],
            "unreachable": [],
        }]}).encode("utf-8")
        with patch.dict(os.environ, {"TRAVELTIME_APP_ID": "test-app", "TRAVELTIME_API_KEY": "test-key"}, clear=True):
            found = estimate_travel(ORIGIN, facilities().iloc[:1], enabled=True, timeout=7)
        request = opener.open.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 7})
        self.assertEqual(headers["x-application-id"], "test-app")
        self.assertEqual(headers["x-api-key"], "test-key")
        self.assertEqual(json.loads(request.data)["departure_searches"][0]["properties"], ["travel_time", "distance"])
        self.assertEqual(found["travel_minutes"].tolist(), [0.25])
        self.assertIsNone(build.call_args.args[0].redirect_request(None, None, 302, "Redirect", {}, "https://example.com"))

    @patch("facility_tool.travel._post_json")
    def test_provider_error_uses_generic_status_and_never_exposes_error_text(self, post):
        post.side_effect = HTTPError(ENDPOINT, 429, "body with test-key", None, None)
        found = estimate_travel(ORIGIN, facilities(), max_destinations=1, **SETTINGS)
        self.assertIn("rate_limited", set(found["travel_status"]))
        self.assertNotIn("test-key", found.to_string())
        self.assertTrue(found["travel_minutes"].isna().all())
        post.side_effect = None
        post.return_value = {"results": [{
            "search_id": "from-origin",
            "locations": [{"id": "destination-1", "properties": [{"travel_time": 99999}]}],
            "unreachable": [],
        }]}
        found = estimate_travel(ORIGIN, facilities(), max_destinations=1, **SETTINGS)
        self.assertIn("missing_result", set(found["travel_status"]))
        self.assertTrue(found["travel_minutes"].isna().all())

    @patch("facility_tool.travel._post_json")
    def test_distances_and_times_stay_together_for_the_chosen_route(self, post):
        post.return_value = {"results": [{
            "search_id": "from-origin",
            "locations": [
                {"id": "destination-1", "properties": [
                    {"travel_time": 600, "distance": 1250},
                    {"travel_time": 540, "distance": 2325},
                ]},
                {"id": "destination-2", "properties": [
                    {"travel_time": 420},
                    {"travel_time": 450, "distance": 1750},
                    {"distance": 1200},
                ]},
                {"id": "destination-3", "properties": [
                    {"distance": 2700},
                    {"travel_time": 99999, "distance": 2300},
                ]},
            ],
            "unreachable": [],
        }]}
        for mode in ('driving', 'walking', 'cycling'):
            with self.subTest(mode=mode):
                found = estimate_travel(ORIGIN, facilities(), mode=mode, max_destinations=3, **SETTINGS).set_index('facility_id')
                search = post.call_args.args[0]['departure_searches'][0]
                self.assertEqual(search['transportation'], {'type': mode})
                self.assertEqual(search['properties'], ['travel_time', 'distance'])
                self.assertEqual(found.at['local-id-a', 'travel_minutes'], 9)
                self.assertEqual(found.at['local-id-a', 'route_distance_km'], 2.325)
                self.assertEqual(found.at['local-id-b', 'travel_minutes'], 7)
                self.assertTrue(pd.isna(found.at['local-id-b', 'route_distance_km']))
                self.assertTrue(pd.isna(found.at['local-id-c', 'travel_minutes']))
                self.assertEqual(found.at['local-id-c', 'route_distance_km'], 2.7)
                self.assertEqual(found.at['local-id-c', 'travel_source'], SOURCE)


if __name__ == "__main__":
    unittest.main()
