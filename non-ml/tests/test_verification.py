import unittest

import pandas as pd

from facility_tool.verification import verify_services


AS_OF = '2026-11-21'


def claim(record, url, *, service='dialysis', facility='a', source='fdr', stance='supports', validity='unknown', **fields):
    return dict(
        dict(facility_id=facility, service=service, record_id=record, excerpt='  Listed dialysis availability.  ',
             source_url=url, scope='Published service listing', source=source, stance=stance, validity=validity,
             citation_quality='direct'),
        **fields,
    )


class ServiceVerificationTests(unittest.TestCase):
    def test_same_publisher_pages_and_org_namespaces_count_once(self):
        fdr_url = 'https://www.department-a.gov.in/facilities/branch'
        permit_url = 'https://listings.department-a.nic.in/service/permits'
        department_b = 'https://scheme.department-b.gov.in/published-coverage'
        rows = [claim('fdr:one', fdr_url), claim('government:one', permit_url, source='government')]
        one = verify_services(pd.DataFrame(rows), AS_OF).iloc[0]
        self.assertEqual(one['verification_status'], 'single_direct_source')
        self.assertEqual(one['supporting_publisher_count'], 1)
        self.assertEqual(one['supporting_publishers'], ['department-a.gov.in'])
        self.assertEqual(one['supporting_record_ids'], ['fdr:one', 'government:one'])

        rows.extend([
            claim('government:two', department_b, source='government', scope='Coverage under fictional Scheme B'),
            claim('government:other-branch', department_b, facility='b'),
            claim('government:other-service', department_b, service='blood_storage_unit'),
        ])
        results = verify_services(pd.DataFrame(rows), AS_OF).set_index(['facility_id', 'service'])
        row = results.loc[('a', 'dialysis')]
        self.assertEqual(row['verification_status'], 'corroborated')
        self.assertEqual(row['supporting_publishers'], ['department-a.gov.in', 'department-b.gov.in'])
        self.assertEqual(row['verification_as_of'], AS_OF)
        self.assertIn('Coverage under fictional Scheme B', row['evidence_scopes'])
        self.assertEqual(row['evidence'][0]['excerpt'], '  Listed dialysis availability.  ')
        self.assertEqual(results.loc[('b', 'dialysis'), 'verification_status'], 'single_direct_source')
        self.assertEqual(results.loc[('a', 'blood_storage_unit'), 'verification_status'], 'single_direct_source')

    def test_date_scope_and_strength_gate_support_and_hold_unattributed_disputes(self):
        rows = [
            claim('valid', 'https://one.example.org/available', validity='future',
                  validity_start='2026-01-01', validity_end=AS_OF),
            claim('ineligible-url', 'https://one.example.org/available',
                  source_urls=['https://one.example.org/available', 'https://two.example.net/facility']),
            claim('past', 'https://two.example.net/removed', stance='contradicts', validity='active',
                  validity_start='2025-01-01', validity_end='2026-10-01'),
            claim('future', 'https://two.example.net/arrangements', stance='contradicts', validity='active',
                  validity_start='2026-12-01', validity_end='2027-10-01'),
            claim('suspended', 'https://two.example.net/suspended', validity='inactive',
                  validity_start='2026-01-01', validity_end='2027-01-01'),
            claim('unknown-date', 'https://two.example.net/arrangements', validity_text=['through a disputed end date']),
            claim('equipment', 'https://two.example.net/facility', evidence_field='equipment'),
            claim('referral', 'https://two.example.net/facility', stance='weak'),
        ]
        row = verify_services(pd.DataFrame(rows), AS_OF).iloc[0]
        self.assertEqual(row['verification_status'], 'single_direct_source')
        self.assertEqual(row['supporting_record_ids'], ['valid'])
        self.assertEqual(row['expired_record_ids'], ['past'])
        self.assertEqual(row['future_record_ids'], ['future'])
        self.assertEqual(row['inactive_record_ids'], ['suspended'])
        self.assertEqual(row['insufficient_record_ids'], ['equipment', 'ineligible-url', 'referral', 'unknown-date'])

        unresolved = ['https://one.example.org/available', 'https://two.example.net/facility']
        rows.append(claim('denial', '', stance='contradicts', excerpt='No dialysis service.',
                          citation_quality='candidate_links', source_urls=unresolved))
        row = verify_services(pd.DataFrame(rows), AS_OF).iloc[0]
        self.assertEqual(row['verification_status'], 'conflicting')
        self.assertEqual(row['contradicting_record_ids'], ['denial'])
        self.assertEqual(row['unattributed_contradiction_record_ids'], ['denial'])
        self.assertFalse(row['contradicting_urls'])
        self.assertIn('could not be traced', row['scope_caveat'])
        self.assertEqual(row['evidence'][-1]['candidate_source_urls'], unresolved)

    def test_resolved_scheme_record_can_support_and_multipublisher_record_cannot(self):
        page = 'https://chtn.cmchistn.com/api/v1/tpa-hospitals-v2/?search=fictional'
        listing = 'https://www.cmchistn.com/hospitalList.php'
        rows = [
            claim('cmchis:1', page, source='cmchis', source_urls=[listing, page]),
            claim('repeated-record', 'https://first-source.example.org/show'),
            claim('repeated-record', 'https://second-source.example.net/show'),
        ]
        verified = verify_services(pd.DataFrame(rows), AS_OF).iloc[0]
        self.assertEqual(verified['verification_status'], 'single_direct_source')
        self.assertEqual(verified['supporting_publishers'], ['cmchistn.com'])
        self.assertEqual(verified['supporting_urls'], [page])
        self.assertEqual(verified['insufficient_record_ids'], ['repeated-record'])


if __name__ == '__main__':
    unittest.main()
