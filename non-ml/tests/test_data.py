"""Examples of source and linkage errors that would change recommendations."""

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from facility_tool.data import extract_claims, link_entities, load_records


class FacilityDataTests(unittest.TestCase):
    def test_same_name_is_not_enough_to_transfer_evidence(self):
        shared = {"name": "Union Hospital", "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan"}
        fdr = pd.DataFrame([
            dict(shared, unique_id="a", address_line1="10 Pine Lane", address_zipOrPostcode=302001,
                 latitude=26.9141, longitude=75.7901),
            dict(shared, unique_id="b", address_line1="10 Pine Lane", address_zipOrPostcode=302001,
                 description="This branch offers dialysis.", source_urls='["https://example.org/union"]'),
            dict(shared, unique_id="c", address_line1="89 Maple Lane", address_zipOrPostcode=302001,
                 latitude=26.9741, longitude=75.8101, procedure="MRI"),
            dict(shared, unique_id="d", address_line1="8 Hill Street", address_zipOrPostcode=302018,
                 latitude=26.8731, longitude=75.7861, procedure="Burn care"),
        ])
        records = load_records("no-public-input-needed", fdr, include_public=False)
        entities, links = link_entities(records)
        links = links.set_index("record_id")
        self.assertEqual(links.loc["fdr:b", "facility_id"], "fdr:a")
        self.assertEqual(links.loc["fdr:c", "link_status"], "ambiguous")
        self.assertEqual(links.loc["fdr:d", "facility_id"], "fdr:d")
        self.assertEqual(entities.set_index("facility_id").loc["fdr:a", "location_record_id"], "fdr:a")
        claims = extract_claims(records, links.reset_index(), {"dialysis": [], "MRI": [], "burn care": []}, "2026-11-21")
        self.assertEqual(set(zip(claims["facility_id"], claims["service"])), {("fdr:a", "dialysis"), ("fdr:d", "burn care")})

    def test_ambiguous_link_and_equipment_cannot_upgrade_service(self):
        fdr = pd.DataFrame([{
            "unique_id": "x", "name": "Sample Care", "equipment": "MRI scanner",
            "description": "No MRI, CT scan or dialysis. We refer patients elsewhere for haemodialysis.",
            "specialties": "Nephrology",
            "source_urls": '["https://example.org/1", "https://example.org/2"]',
        }])
        records = load_records("no-public-input-needed", fdr, include_public=False)
        _, links = link_entities(records)
        aliases = {"MRI": [], "CT scan": [], "dialysis": ["haemodialysis"]}
        claims = extract_claims(records, links, aliases, "2026-11-21")
        self.assertEqual(set(claims["service"]), set(aliases))
        self.assertNotIn("supports", set(claims["stance"]))
        self.assertIn("contradicts", set(claims.loc[claims["service"].eq("MRI"), "stance"]))
        self.assertEqual(set(claims["citation_quality"]), {"candidate_links"})
        self.assertEqual(set(claims["source_url"]), {""})

    def test_dates_and_blood_directory_types_keep_scope(self):
        def government(name, validity):
            return dict(facility_name=name, location="100 North Road, Delhi 110001", phone_numbers=[],
                        email_addresses=[], services=["Dialysis"], validity_dates=validity,
                        source_links=["https://example.org/list.pdf#page=5"])

        gov_rows = [
            government("Current", ["21.11.2025 To 21.11.2026"]),
            government("Future", ["22.11.2026 to 22.11.2028"]),
            government("Expired", ["20.11.2025 to 20.11.2026"]),
            government("Suspended", ["Suspended by CGHS", "21.11.2025 to 21.11.2027"]),
            government("Undated", []),
        ]
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for group, names in {"government_sources": ("bis_cghs", "esic", "cmchis", "kerala_sha", "rghs"),
                                 "supplemental": ("nqas", "nabh", "eraktkosh")}.items():
                directory = base / "data" / group
                directory.mkdir(parents=True)
                for name in names:
                    (directory / f"{name}.jsonl").touch()
            (base / "data/government_sources/bis_cghs.jsonl").write_text("\n".join(map(json.dumps, gov_rows)), encoding="utf-8")
            blood = {"facility_name": "Sample Blood Service", "facility_type": "BSU", "source_url": "https://example.org/directory"}
            (base / "data/supplemental/eraktkosh.jsonl").write_text(json.dumps(blood), encoding="utf-8")
            records = load_records(base)
        self.assertEqual(records.attrs["source_counts"]["rghs"], 0)
        _, links = link_entities(records)
        claims = extract_claims(records, links, {"dialysis": [], "blood bank": [], "blood storage unit": ["BSU"]}, "2026-11-21")
        claims = claims.merge(records[["record_id", "name"]], on="record_id")
        scope = claims.loc[claims["service"].eq("dialysis")].set_index("name")
        self.assertEqual(scope["validity"].to_dict(), {"Current": "active", "Future": "future", "Expired": "expired",
                                                        "Suspended": "inactive", "Undated": "unknown"})
        self.assertEqual(set(scope["excerpt"]), {"Dialysis"})
        self.assertEqual(claims.loc[claims["source"].eq("eraktkosh"), "service"].tolist(), ["blood storage unit"])


if __name__ == "__main__":
    unittest.main()
