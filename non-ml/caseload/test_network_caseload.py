import csv
import gzip
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from build_network_caseload import estimates, programme_capacity, write_estimates
from baseline_coverage import read_json
from facility_tool.network_load import read_network_pressure, replay_network_load
from plan_network_load import read_forecast_evidence


class NetworkCensusTests(unittest.TestCase):
    def test_full_scenario_even_for_unverified_and_unlinked_branches(self):
        source = {"url": "https://example.org/report"}
        profile = {"MRI": {"unit": "scans", "scope": "Hospital MRI examinations", "benchmark_rows": [
            {"facility": "A", "facility_id": "a", "region": "State", "typical_month": 10,
             "source": source, "period_start": "2025-01", "period_end": "2025-03"},
            {"facility": "B", "facility_id": "b", "region": "State", "typical_month": 20,
             "source": source, "period_start": "2025-01", "period_end": "2025-03"},
        ]}}
        metadata = {"state": "State", "documented_services": "", "services_evidence_status": "",
                    "routing_eligible": True}
        branches = [("a", metadata), ("unknown", metadata), ("release-row:3", dict(metadata, routing_eligible=False))]
        rows = list(estimates(branches, profile, {"caseloads": []}, "2026-11", {}))
        self.assertEqual([r["caseload"] for r in rows], [10, 15, 15])
        self.assertEqual([r["caseload_predicted"] for r in rows], [False, True, True])
        self.assertTrue(all(row["capacity_predicted"] for row in rows))
        self.assertEqual(rows[0]["capacity"], 20)
        self.assertEqual(rows[0]["capacity_peer_count"], 1)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "estimates.csv.gz"
            total, _ = write_estimates(path, iter(rows), "a" * 64)
            self.assertEqual(total, 3)
            with gzip.open(path, "rt", newline="") as stream:
                saved = list(csv.DictReader(stream))
            self.assertEqual(len(saved), 3)
            self.assertEqual(len(read_forecast_evidence(path, "mri", "a" * 64)), 3)
            with self.assertRaisesRegex(ValueError, "different facility release"):
                read_forecast_evidence(path, "mri", "b" * 64)
            pressure = read_network_pressure(path, "mri")
            self.assertEqual(pressure["facility_id"].tolist(), ["a", "unknown"])

    def test_programme_throughput_uses_resource_links_only(self):
        overrides = programme_capacity(read_json("load_evidence.json"), read_json("tn_maternity_model_data.json"))
        self.assertEqual(len(overrides), 7)
        self.assertEqual(overrides["dialysis", "fdr:f14b8790-5fd5-4da8-9599-db45874396b5"]["capacity"], 271.333333)
        self.assertEqual(overrides["maternity_delivery", "fdr:f447fa95-f439-4dab-9de2-3205136588bf"]["capacity_peer_count"], 56)

    def test_batch_balancing_uses_background_and_excludes_far_facilities(self):
        candidates = pd.DataFrame([
            dict(facility_id=ident, name=ident, service="mri", scope="MRI", eligible=True,
                 evidence_tier="active", distance_km=km)
            for ident, km in [("nearest", 1), ("nearby", 2), ("too_far", 20)]
        ])
        pressure = pd.DataFrame([
            dict(facility_id=ident, service="mri", demand=cases, capacity=100, unit="scans",
                 period="typical month", basis="MRI", source_url="https://example.org/report", capacity_predicted=True,
                 caseload_predicted=ident == "nearby")
            for ident, cases in [("nearest", 90), ("nearby", 10), ("too_far", 0)]
        ])
        result = replay_network_load(candidates, pressure, 100).set_index("facility_id")
        self.assertEqual(result["baseline_suggestions"].tolist(), [100, 0, 0])
        self.assertEqual(result["balanced_suggestions"].sum(), 100)
        self.assertGreater(result.loc["nearby", "balanced_suggestions"], 0)
        self.assertEqual(result.loc["too_far", "balanced_suggestions"], 0)
        self.assertTrue(result.loc["nearby", "caseload_predicted"])
        self.assertTrue(result.loc["nearby", "capacity_predicted"])
