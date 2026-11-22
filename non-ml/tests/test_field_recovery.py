import unittest

import pandas as pd

from facility_tool.data import CLAIM_COLUMNS
from facility_tool.field_recovery import merge_recovered_service_claims, prepare_field_recovery
from facility_tool.recommend import prepare_service_candidates
from facility_tool.verification import verify_services


AS_OF = '2026-11-21'
ALIASES = {'mri': ['MRI', 'magnetic resonance imaging'], 'dialysis': ['dialysis']}


def branch(facility_id, pin='302004', address='11 Example Road', *, eligible=True):
    return dict(facility_id=facility_id, name='Example Hospital', pin=pin, address=address,
                city='Jaipur', location_routing_eligible=eligible)


def document(key, claim, **changes):
    anchor = 'Example Hospital, 11 Example Road, Jaipur 302004.'
    return {
        'document_id': key, 'facility_name': 'Example Hospital', 'pin': '302004',
        'branch_anchor': anchor, 'source_url': f'https://{key}.invalid/publication',
        'publisher_family': key, 'publisher': f'Publisher {key}', 'retrieved_on': '2026-11-15',
        'record_type': 'Branch profile', 'passages': [{'text': f'{anchor}\n{claim}', 'page': 2}],
        **changes,
    }


def review(claim_id, **changes):
    return {'claim_id': claim_id, 'decision': 'approved', 'reviewer': 'test-reviewer',
            'reviewed_on': AS_OF, 'reason': 'Checked the saved passage and branch anchor.', **changes}


def recover(*, documents=(), reviews=(), branches=None, structured=None, records=None, links=None):
    if branches is None:
        branches = [branch('target'), branch('other', '302007', '24 Other Road')]
    if structured is None:
        structured = pd.DataFrame([dict(facility_id='target', capacity=None, numberDoctors=None)])
    if records is None:
        records = pd.DataFrame(columns=['record_id', 'source', 'service_texts'])
    if links is None:
        links = pd.DataFrame(columns=['record_id', 'facility_id', 'link_status'])
    return prepare_field_recovery(records, links, pd.DataFrame(branches), structured, ALIASES,
                                  as_of=AS_OF, documents=documents, reviews=reviews)


def approve_all(*, independent=False, **inputs):
    pending = recover(**inputs)
    approvals = [review(key, independence_checked=independent) for key in pending['recovery_claims']['claim_id']]
    return recover(**inputs, reviews=approvals)


class FieldRecoveryTests(unittest.TestCase):
    def test_saved_span_is_verbatim_and_approval_is_bound_to_evidence(self):
        original = document('beds', 'The hospital has 80 total beds.')
        inputs = recover(documents=[original])
        candidate = inputs['recovery_claims'].iloc[0]
        passage = original['passages'][0]['text']
        span = passage[candidate['char_start']:candidate['char_end']]
        self.assertEqual(candidate['excerpt'], span)
        self.assertEqual(span, 'The hospital has 80 total beds.')
        self.assertEqual(candidate['page'], 2)
        self.assertEqual(candidate['raw_value'], '80')
        self.assertEqual(candidate['normalized_value'], 80)
        self.assertEqual(candidate['source_record_id'], 'beds')
        self.assertEqual(candidate['publication_scope'], 'Branch profile')
        self.assertEqual(candidate['retrieved_on'], '2026-11-15')
        self.assertEqual(candidate['document_date'], '')
        self.assertEqual(candidate['linkage_status'], 'linked')
        self.assertEqual(candidate['facility_id'], 'target')
        self.assertEqual(inputs['recovery_decisions'].iloc[0]['review_status'], 'extracted_unreviewed')
        self.assertTrue(inputs['recovered_field_updates'].empty)

        approvals = [review(candidate['claim_id'])]
        approved = recover(documents=[original], reviews=approvals)
        released = approved['recovered_field_updates'].iloc[0]
        self.assertEqual((released['value'], released['method']), (80, 'source_recovered'))
        self.assertIn(original['source_url'], released['provenance'])
        self.assertIn(span, released['detail'])
        decision = approved['recovery_decisions'].iloc[0]
        self.assertEqual((decision['reviewer'], decision['reviewed_on']), ('test-reviewer', AS_OF))

        changed = document('beds', 'The hospital maintains 80 total beds.')
        after_edit = recover(documents=[changed], reviews=approvals)
        self.assertTrue(after_edit['recovered_field_updates'].empty)
        self.assertEqual(set(after_edit['recovery_decisions']['release_status']), {'awaiting_review', 'unmatched_review'})

    def test_saved_anchor_and_one_branch_are_required(self):
        valid = document('branch', 'The hospital has 80 total beds.')
        match = approve_all(documents=[valid])['recovered_field_updates']
        self.assertEqual(match['facility_id'].tolist(), ['target'])
        duplicate_pin = [branch('target'), branch('other', '302004', '24 Other Road')]
        ambiguous = approve_all(documents=[valid], branches=duplicate_pin)
        self.assertEqual(ambiguous['recovery_decisions']['release_status'].tolist(), ['ambiguous_branch'])
        self.assertTrue(ambiguous['recovered_field_updates'].empty)

        misplaced = {**valid, 'branch_anchor': 'Example Hospital, 24 Other Road, Jaipur 302004.'}
        absent_anchor = approve_all(documents=[misplaced])
        self.assertEqual(absent_anchor['recovery_decisions']['release_status'].tolist(), ['unverified_branch_anchor'])
        self.assertTrue(absent_anchor['recovered_field_updates'].empty)

    def test_an_anchor_on_one_page_cannot_release_another_branch_on_a_later_page(self):
        valid = document('mixed', '')
        mixed = {**valid, 'passages': [
            {'text': valid['branch_anchor'], 'page': 1},
            {'text': 'Example Hospital, 24 Other Road, Jaipur 302007. The hospital has 175 beds.', 'page': 2},
        ]}
        result = approve_all(documents=[mixed])
        self.assertEqual(result['recovery_decisions']['release_status'].tolist(), ['passage_branch_unresolved'])
        self.assertTrue(result['recovered_field_updates'].empty)

    def test_a_differently_named_facility_with_another_pin_makes_the_passage_unresolved(self):
        competing = {**branch('other', '302007', '24 Other Road'), 'name': 'Horizon Medical Center'}
        shared_page = document('mixed', 'Horizon Medical Center at 24 Other Road, Jaipur 302007 provides MRI.')
        result = approve_all(documents=[shared_page], branches=[branch('target'), competing])
        self.assertEqual(result['recovery_decisions']['release_status'].tolist(), ['passage_branch_unresolved'])
        self.assertTrue(result['recovered_service_claims'].empty)

    def test_expiration_location_hold_and_future_review_prevent_release(self):
        expired = approve_all(documents=[document('expired', 'The hospital has 80 total beds.', valid_through='2026-11-20')])
        self.assertEqual(expired['recovery_decisions']['release_status'].tolist(), ['expired_scope'])
        self.assertTrue(expired['recovered_field_updates'].empty)
        original = document('held', 'The hospital has 80 total beds.')
        held = approve_all(documents=[original], branches=[branch('target', eligible=False)])
        self.assertEqual(held['recovery_decisions']['release_status'].tolist(), ['location_held'])
        self.assertTrue(held['recovered_field_updates'].empty)
        candidate = recover(documents=[original])['recovery_claims'].iloc[0]['claim_id']
        future = recover(documents=[original], reviews=[review(candidate, reviewed_on='2026-11-22')])
        self.assertEqual(future['recovery_decisions']['release_status'].tolist(), ['review_date_in_future'])
        self.assertTrue(future['recovered_field_updates'].empty)

    def test_rejection_reason_is_retained_in_the_audit(self):
        doc = document('rejected', 'The hospital has 80 total beds.')
        candidate = recover(documents=[doc])['recovery_claims'].iloc[0]['claim_id']
        outcome = recover(documents=[doc], reviews=[review(candidate, decision='rejected', reason='Branch is uncertain.')])
        audit = outcome['recovery_decisions'].iloc[0]
        self.assertEqual(audit['review_status'], 'rejected')
        self.assertEqual(audit['review_reason'], 'Branch is uncertain.')
        self.assertTrue(outcome['recovered_field_updates'].empty)

    def test_reported_exact_and_ambiguous_structured_counts_retain_authority(self):
        original = document('beds', 'The hospital has 80 total beds.')
        for existing, status in [(80, 'already_reported'), (73, 'structured_value_disagrees'),
                                 ('70 to 90', 'structured_value_needs_review'), (0, 'structured_value_disagrees')]:
            with self.subTest(existing=existing):
                raw = pd.DataFrame([dict(facility_id='target', capacity=existing)])
                result = approve_all(documents=[original], structured=raw)
                self.assertEqual(result['recovery_decisions']['release_status'].tolist(), [status])
                self.assertTrue(result['recovered_field_updates'].empty)
                self.assertEqual(raw.iloc[0]['capacity'], existing)

    def test_doctor_count_retains_scope_and_date(self):
        undated = approve_all(documents=[document('staff', 'The hospital has 18 full-time doctors.')])
        self.assertEqual(undated['recovery_decisions']['release_status'].tolist(), ['doctor_count_date_missing'])
        self.assertTrue(undated['recovered_field_updates'].empty)
        dated = approve_all(documents=[document(
            'staff', 'As of 2026-09-05, the hospital had 18 full-time doctors. The hospital has a total of 30 doctors.',
            document_date='2026-09-05',
        )])
        updates = dated['recovered_field_updates'].set_index('field')
        self.assertEqual(updates.loc['numberDoctors:full_time_doctors', 'value'], 18)
        self.assertEqual(updates.loc['numberDoctors', 'value'], 30)
        self.assertIn('dated 2026-09-05', updates.loc['numberDoctors:full_time_doctors', 'detail'])

    def test_competing_numeric_claims_are_preserved_as_conflict(self):
        result = approve_all(documents=[document('first', 'The hospital has 80 total beds.'),
                                        document('second', 'The hospital maintains 85 total beds.')])
        combined = result['recovered_field_updates']
        self.assertEqual(combined['method'].tolist(), ['conflicting'])
        self.assertTrue(combined['value'].isna().all())
        self.assertIn('80 total beds', combined.iloc[0]['detail'])
        self.assertIn('85 total beds', combined.iloc[0]['detail'])
        self.assertEqual(set(result['recovery_decisions']['release_status']), {'conflicting'})

    def test_fdr_numeric_recovery_preserves_source_id_and_needs_review(self):
        records = pd.DataFrame([{
            'record_id': 'fdr-1', 'source_record_id': 'row-1', 'source': 'fdr',
            'source_urls': ['https://example.invalid/listing', 'https://example.invalid/another-page'],
            'service_texts': [['description', 'The hospital has 80 total beds. The hospital offers MRI.']],
        }])
        links = pd.DataFrame([dict(record_id='fdr-1', facility_id='target', link_status='anchor')])
        initial = recover(records=records, links=links)
        candidates = initial['recovery_claims']
        self.assertEqual(set(candidates['source_url']), {''})
        self.assertTrue(initial['recovered_field_updates'].empty)
        approved = recover(records=records, links=links, reviews=[review(key) for key in candidates['claim_id']])
        updates = approved['recovered_field_updates']
        self.assertEqual(updates['value'].tolist(), [80])
        self.assertIn('FDR source record row-1', updates.iloc[0]['provenance'])
        self.assertTrue(approved['recovered_service_claims'].empty)
        service = approved['recovery_decisions'].loc[lambda rows: rows['field'].eq('service')]
        self.assertEqual(service['release_status'].tolist(), ['service_needs_saved_public_citation'])

    def test_service_conflict_flows_to_existing_verifier(self):
        result = approve_all(documents=[document('first', 'The hospital offers MRI.'),
                                        document('second', 'The hospital does not offer MRI.')])
        recovered = result['recovered_service_claims']
        self.assertEqual(set(recovered['stance']), {'supports', 'contradicts'})
        self.assertTrue(result['recovered_field_updates'].empty)
        base = pd.DataFrame(columns=CLAIM_COLUMNS)
        decisions = verify_services(merge_recovered_service_claims(base, recovered, AS_OF), AS_OF)
        self.assertEqual(decisions['verification_status'].tolist(), ['conflicting'])

    def test_undated_document_stays_eligible_at_existing_lower_evidence_tier(self):
        entity = {**branch('target'), 'district': 'Jaipur', 'state': 'Rajasthan', 'lat': 26.9,
                  'lon': 75.8, 'location_quality': 'source_coordinates_unverified', 'location_hold_reason': ''}
        result = approve_all(documents=[document('mri-branch', 'The hospital offers MRI.')], branches=[entity])
        prepared = prepare_service_candidates(pd.DataFrame([entity]), result['recovered_service_claims'], 'mri', as_of=AS_OF)
        candidate = prepared.iloc[0]
        self.assertEqual(candidate['evidence_hold_reason'], '')
        self.assertEqual(candidate['evidence_tier'], 1)
        self.assertEqual(candidate['claim']['validity'], 'unknown')

    def test_equipment_updates_do_not_establish_a_service(self):
        result = approve_all(documents=[document('inventory', 'The hospital is equipped with a 3 Tesla MRI scanner.')])
        equipment = result['recovered_field_updates'].iloc[0]
        self.assertEqual((equipment['field'], equipment['value']), ('equipment:mri_scanner', 'present'))
        self.assertIn('3 Tesla', equipment['detail'])
        self.assertTrue(result['recovered_service_claims'].empty)

    def test_matching_text_or_same_publisher_does_not_add_corroboration(self):
        first = document('first', 'The hospital offers MRI.', publisher_family='Example Foundation')
        copy = document('copy', 'The hospital offers MRI.')
        same_family = document('affiliate', 'Magnetic resonance imaging is available.', publisher_family='example_foundation')
        for other in (copy, same_family):
            with self.subTest(other=other['document_id']):
                result = approve_all(documents=[first, other], independent=True)
                self.assertEqual(len(result['recovered_service_claims']), 1)
                self.assertIn('duplicate_evidence', set(result['recovery_decisions']['release_status']))

        base = approve_all(documents=[first])['recovered_service_claims'].assign(source='reviewed')
        for non_independent in (copy, same_family):
            with self.subTest(existing_and_recovered=non_independent['document_id']):
                additional = approve_all(documents=[non_independent], independent=True)['recovered_service_claims']
                self.assertEqual(len(merge_recovered_service_claims(base, additional, AS_OF)), 1)
        verified = document('separate', 'Magnetic resonance imaging is available.')
        ordinary = approve_all(documents=[verified])['recovered_service_claims']
        self.assertEqual(len(merge_recovered_service_claims(base, ordinary, AS_OF)), 1)
        independent = approve_all(documents=[verified], independent=True)['recovered_service_claims']
        combined = merge_recovered_service_claims(base, independent, AS_OF)
        self.assertEqual(verify_services(combined, AS_OF)['verification_status'].tolist(), ['corroborated'])


if __name__ == '__main__':
    unittest.main()
