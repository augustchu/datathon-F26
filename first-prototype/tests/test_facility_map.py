import unittest

import pandas as pd

from facility_map import build_map


def facility(name, **overrides):
    data = dict(facility_name=name, latitude=26.9, longitude=75.8,
                city="Jaipur", district="Jaipur", state="Rajasthan", pin="302004",
                phone="+91 (141) 555-0100", beds_for_scenario=30,
                capacity_release_status="xgboost_estimate", travel_time_minutes=0, distance_km=2.4)
    return {**data, **overrides}


class FacilityMapTests(unittest.TestCase):
    def test_ranked_rows_keep_card_order_and_travel_labels(self):
        rows = pd.DataFrame([facility("Zebra Hospital"), facility("Alpha Hospital")], index=[7, 2])
        markup = build_map((26.8, 75.7), rows, origin_label="Jaipur", using_routes=True)

        self.assertIn("Your location: Jaipur", markup)
        self.assertLess(markup.index("1. Zebra Hospital"), markup.index("2. Alpha Hospital"))
        self.assertIn("Estimated drive:</b> 0 min", markup)
        self.assertIn("Straight-line distance:</b> 2.4 km", markup)
        self.assertIn("Estimated: 30 beds", markup)
        self.assertIn('href="tel:+911415550100"', markup)
        self.assertIn("destination=26.900000,75.800000", markup)

    def test_map_escapes_markup_and_javascript_from_facility_data(self):
        label = "</script><img src=x>${invalid}`\\"
        rows = pd.DataFrame([facility(label, phone=label, city=label)])
        markup = build_map((26.8, 75.7), rows, origin_label=label)

        self.assertNotIn(label, markup)
        self.assertNotIn("${invalid}", markup)
        self.assertIn("&lt;/script&gt;&lt;img src=x&gt;", markup)
        self.assertNotIn('href="tel:', markup)
        self.assertNotIn("Estimated drive:", markup)

    def test_overview_and_origin_only_are_supported(self):
        self.assertNotIn("Your location", build_map())
        markup = build_map((26.8, 75.7), pd.DataFrame(), origin_label="Entered coordinates")
        self.assertIn("Your location: Entered coordinates", markup)
        self.assertNotIn("Directions", markup)


if __name__ == "__main__":
    unittest.main()
