import unittest

import pandas as pd

from facility_tool.quality import build_quality_profiles


AS_OF = '2026-11-21'
NQAS = 'https://qps.nhsrcindia.org/quality-certification/national-level-certificate'
NABH = 'https://nabh.co/find-a-healthcare-organisation/'
CERTIFICATE = 'https://portal.nabh.co/Documents/AccreditedList/Hospitals/H-2024-0001.pdf'


def directory(record, source, status, *, expires='', url=None):
    url = url or (NQAS if source == 'nqas' else NABH)
    return dict(record_id=record, source=source, source_urls=[url], raw=dict(
        certification_status=status, validity_date_iso=expires, source_url=url,
        retrieved_at_utc='2026-11-21T12:00:00Z',
    ))


def link(record_id, facility_id, status='linked'):
    return dict(record_id=record_id, facility_id=facility_id, link_status=status, link_reason='Exact branch link')


def reviewed(record, facility='a', service='dialysis', **changes):
    return dict(dict(
        record_id=record, facility_id=facility, service=service,
        source='reviewed', publisher_key='nabh', excerpt='  Nephrology including dialysis.  ',
        scope='NABH accredited scope, certificate H-2024-0001 for Example Branch',
        source_url=CERTIFICATE, reviewed_on=AS_OF, validity_start='2024-09-01',
        validity_end='2028-08-31', citation_quality='direct', stance='supports', validity='active',
        evidence_field='reviewed_service_text', match_reason='Reviewed against exact branch address',
    ), **changes)


def verification(facility, service, status='single_direct_source'):
    return dict(facility_id=facility, service=service, verification_status=status, verification_as_of=AS_OF)


def quality(records, links, claims, pairs):
    assessments, profiles = build_quality_profiles(
        pd.DataFrame(records), pd.DataFrame(links), pd.DataFrame(claims), pd.DataFrame(pairs), AS_OF,
    )
    return assessments.set_index('record_id'), profiles.set_index(['facility_id', 'service'])


class QualityProfilesTests(unittest.TestCase):
    def test_full_branch_certification_and_reviewed_certificate_keep_distinct_scopes(self):
        assessment, profiles = quality(
            [directory('nqas:one', 'nqas', 'Certified', expires=AS_OF),
             directory('nqas:ambiguous', 'nqas', 'Certified', expires='2029-01-01'),
             directory('nabh:list', 'nabh', 'Accredited on 6th Edition')],
            [link('nqas:one', 'a'), link('nqas:ambiguous', 'b', 'ambiguous'), link('nabh:list', 'b')],
            [reviewed('reviewed:one')],
            [verification('a', 'dialysis', 'corroborated'), verification('a', 'mri'), verification('b', 'dialysis')],
        )
        first = profiles.loc['a', 'dialysis']
        self.assertTrue(first['quality_corroborated'])
        self.assertTrue(first['quality_assessed'])
        self.assertEqual(first['quality_assessment_status'], 'nqas_certified_and_nabh_service_scope')
        self.assertEqual(len(first['quality_evidence']), 2)
        self.assertEqual(first['quality_evidence'][1]['excerpt'], '  Nephrology including dialysis.  ')
        self.assertEqual(first['quality_evidence'][1]['source_url'], CERTIFICATE)
        self.assertEqual(first['quality_evidence'][1]['validity_end'], '2028-08-31')
        self.assertEqual(profiles.loc[('a', 'mri'), 'quality_assessment_programmes'], ['nqas'])
        self.assertFalse(profiles.loc[('b', 'dialysis'), 'quality_assessed'])
        self.assertEqual(profiles.loc[('b', 'dialysis'), 'quality_assessment_status'], 'assessment_status_only')
        self.assertEqual(assessment.loc['nabh:list', 'assessment_status'], 'directory_status_only')
        self.assertEqual(assessment.loc['nqas:ambiguous', 'facility_id'], '')

    def test_conditional_expired_unconfirmed_and_incomplete_assessments_never_confer_credit(self):
        assessment, profiles = quality(
            [directory('conditional', 'nqas', 'Conditionality', expires='2027-01-01'),
             directory('expired', 'nqas', 'Certified', expires='2025-01-01'),
             directory('source-only', 'nqas', 'Certified', expires='2029-01-01')],
            [link('conditional', 'a'), link('expired', 'b'), link('source-only', 'c', 'source_only')],
            [reviewed('reviewed:missing-date', validity_end=''),
             reviewed('reviewed:outside-publisher', facility='b', source_url='https://nabh.co.example.org/certificate.pdf'),
             reviewed('reviewed:newer', facility='c', reviewed_on='2026-11-22')],
            [verification('a', 'dialysis'), verification('b', 'dialysis'), verification('c', 'dialysis'),
             verification('d', 'dialysis')],
        )
        self.assertFalse(profiles['quality_assessed'].any())
        self.assertEqual(assessment['assessment_status'].to_dict(), {
            'conditional': 'conditional', 'expired': 'expired', 'source-only': 'nqas_certified',
            'reviewed:missing-date': 'incomplete', 'reviewed:outside-publisher': 'incomplete', 'reviewed:newer': 'future',
        })
        self.assertEqual(profiles.loc[('d', 'dialysis'), 'quality_assessment_status'], 'unknown')

    def test_negative_and_disagreeing_current_credentials_prevent_credit(self):
        assessment, profiles = quality(
            [directory('withdrawn', 'nabh', 'Forced Withdrawal of Certification w.e.f. 15th January 2025'),
             directory('current-a', 'nqas', 'Certified', expires='2028-10-01'),
             directory('current-b', 'nqas', 'Certified', expires='2029-01-01')],
            [link('withdrawn', 'a'), link('current-a', 'b'), link('current-b', 'b')],
            [reviewed('reviewed:positive')],
            [verification('a', 'dialysis'), verification('b', 'dialysis')],
        )
        self.assertFalse(profiles['quality_assessed'].any())
        self.assertTrue(profiles['quality_assessment_status'].eq('conflicting_assessment_evidence').all())
        self.assertEqual(assessment.loc['withdrawn', 'assessment_status'], 'negative')
        self.assertTrue(assessment.loc['reviewed:positive', 'eligible_for_quality'])


if __name__ == '__main__':
    unittest.main()
