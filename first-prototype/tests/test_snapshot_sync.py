"""Regressions for warehouse sync and the routing screen on older snapshots."""

from datetime import datetime
import os
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

import classic_data
from pipeline import load_audited_location_holds, prepare_facilities


def adapt_facilities(raw):
    return raw.assign(facility_id=raw.index.map(str), address="", city=raw["address_city"])


class SnapshotSyncTest(TestCase):
    def setUp(self):
        for cache in (classic_data._check_snapshot, classic_data._load_snapshot):
            cache.clear()
            self.addCleanup(cache.clear)
        self.enterContext(patch.dict(os.environ, {
            "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/snapshot-test",
            "FACILITY_SNAPSHOT_TABLE": "test.default.facilities",
            "SNAPSHOT_METADATA_TABLE": "test.default.metadata",
        }))

    def test_refresh_loads_a_new_delta_version_and_uses_its_timestamp(self):
        version = 1
        queries = []

        def query(statement, path, **kwargs):
            self.assertEqual(path, "/sql/1.0/warehouses/snapshot-test")
            queries.append(statement)
            if statement.startswith("DESCRIBE HISTORY"):
                return pd.DataFrame([{"version": version, "timestamp": f"2026-11-2{version} 12:00:00"}])
            self.assertTrue(statement.endswith(f"VERSION AS OF {version}"))
            return pd.DataFrame([{"facility_id": "fdr:one", "facility_name": f"Hospital {version}",
                                  "latitude": 24, "longitude": 76, "snapshot_prepared_at": f"2026-11-2{version}"}])

        with patch.object(classic_data, "_query_snapshot", side_effect=query):
            first = classic_data.load_classic_snapshot(adapt_facilities)
            again = classic_data.load_classic_snapshot(adapt_facilities)
            self.assertEqual(again.revision, first.revision)
            self.assertEqual(len(queries), 2)

            classic_data._check_snapshot.clear()
            classic_data.load_classic_snapshot(adapt_facilities)
            self.assertEqual(len(queries), 3)

            version = 2
            current = classic_data.load_classic_snapshot(adapt_facilities, force_refresh=True)
            self.assertEqual(len(queries), 5)
            self.assertEqual(current.facilities["name"].tolist(), ["Hospital 2"])
            self.assertEqual(current.prepared_at, "2026-11-22")
            self.assertNotEqual(first.revision, current.revision)
            self.assertIsNotNone(datetime.fromisoformat(current.checked_at).tzinfo)

    def test_current_reads_cover_views_without_version_history(self):
        rows = pd.DataFrame([
            {"facility_id": "fdr:one", "facility_name": "First", "latitude": 24, "longitude": 76},
            {"facility_id": "fdr:two", "facility_name": "Second", "latitude": 25, "longitude": 77},
        ])

        def query(statement, _, **kwargs):
            if statement.startswith("DESCRIBE HISTORY"):
                self.assertTrue(kwargs["allow_server_error"])
                return None
            if statement.endswith("`metadata`"):
                return pd.DataFrame([{"prepared_at": "2026-11-21"}])
            self.assertEqual(statement, "SELECT * FROM `test`.`default`.`facilities`")
            return rows

        with patch.object(classic_data, "_query_snapshot", side_effect=query):
            first = classic_data.load_classic_snapshot(adapt_facilities)
            rows = rows.iloc[::-1]
            reordered = classic_data.load_classic_snapshot(adapt_facilities, force_refresh=True)
            rows = rows.assign(facility_name=["Updated", "First"])
            current = classic_data.load_classic_snapshot(adapt_facilities, force_refresh=True)

        self.assertEqual(first.revision, reordered.revision)
        self.assertNotEqual(first.revision, current.revision)
        self.assertEqual(current.prepared_at, "2026-11-21")
        self.assertEqual(current.facilities["name"].tolist(), ["Updated", "First"])


class SnapshotLocationTest(TestCase):
    def test_old_snapshot_filters_audited_points_without_holding_corrected_points(self):
        branch, ((latitude, longitude), *_) = next(iter(load_audited_location_holds().items()))
        rows = pd.DataFrame([
            {"facility_id": branch, "facility_name": "Held", "latitude": latitude, "longitude": longitude},
            {"facility_id": branch, "facility_name": "Moved", "latitude": latitude + 0.01, "longitude": longitude},
            {"facility_id": "fdr:unique", "facility_name": "Eligible", "latitude": 24, "longitude": 76},
            {"facility_id": "fdr:flagged", "facility_name": "Flagged", "latitude": 24, "longitude": 76,
             "location_search_eligible": "FALSE"},
            {"facility_id": "fdr:outside", "facility_name": "Unmatched", "latitude": 24, "longitude": 76,
             "region_match_status": "outside_or_missing"},
            {"facility_id": "fdr:missing", "facility_name": "No point", "latitude": 0, "longitude": 0},
        ])

        facilities = classic_data.snapshot_to_classic(rows, adapt_facilities)

        self.assertEqual(facilities["name"].tolist(), ["Moved", "Eligible"])
        self.assertEqual(facilities["equity_facility_id"].tolist(), ["", "fdr:unique"])

    def test_new_snapshot_preserves_explicit_source_routing_hold(self):
        source = pd.DataFrame([{"unique_id": "flagged", "name": "Flagged", "latitude": 24, "longitude": 76,
                                "routing_eligible": "FALSE"}])

        prepared = prepare_facilities(source).iloc[0]

        self.assertTrue(prepared["branch_routing_hold"])
        self.assertFalse(prepared["location_search_eligible"])
