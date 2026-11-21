import unittest

import pandas as pd

from facility_tool.reviewed_claims import attach_reviewed_claims


class ReviewedClaimTests(unittest.TestCase):
    def test_shared_name_needs_branch_anchor_and_dates_stay_scoped(self):
        sites = pd.DataFrame([
            {'name': 'Example Hospital', 'city': 'Jaipur', 'address': '38 Other Road', 'pin': '302001', 'facility_id': 'other'},
            {'name': 'Example Hospital', 'city': 'Jaipur', 'address': '38 Branch Road', 'pin': '302004', 'facility_id': 'correct'},
        ])
        entry = {
            'facility_name': 'Example Hospital', 'pin': '302004', 'service': 'dialysis',
            'source_url': 'https://example.invalid/branch', 'publisher': 'Example Society',
            'publisher_key': 'example-society', 'excerpt': 'Branch dialysis program',
            'scope': 'Example accreditation', 'reviewed_on': '2026-11-21', 'stance': 'supports',
            'valid_from': '2023-01-01', 'valid_through': '2025-12-31',
        }
        claims, audit = attach_reviewed_claims([entry], sites, {'dialysis': []}, '2026-11-21')
        self.assertEqual(audit.iloc[0]['review_status'], 'matched')
        self.assertEqual(claims.iloc[0]['facility_id'], 'correct')
        self.assertEqual(claims.iloc[0]['validity'], 'expired')
        with self.assertRaises(ValueError):
            attach_reviewed_claims([{**entry, 'pin': ''}], sites, {'dialysis': []}, '2026-11-21')
        held, review = attach_reviewed_claims([{**entry, 'address_contains': 'Other Road'}],
                                              sites, {'dialysis': []}, '2026-11-21')
        self.assertTrue(held.empty)
        self.assertEqual(review.iloc[0]['review_status'], 'unmatched')


if __name__ == '__main__':
    unittest.main()
