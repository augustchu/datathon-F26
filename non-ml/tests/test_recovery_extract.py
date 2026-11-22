import unittest

from facility_tool.recovery_extract import extract_candidates


ALIASES = {
    'dialysis': ['dialysis', 'haemodialysis'],
    'mri': ['MRI', 'magnetic resonance imaging'],
    'nicu': ['NICU', 'neonatal intensive care unit'],
    'nicu_level_3': ['Level III NICU', 'NICU Level 3'],
    'emergency_c_section': ['emergency C-section'],
}


class RecoveryExtractionTests(unittest.TestCase):
    def test_exact_source_offsets_survive_unicode_and_separators(self):
        text = '  Élan Hospital is a 1,250-bed multispecialty hospital. \n  We offer dialysis;  Other wording.'
        beds, service = extract_candidates(text, ALIASES)
        self.assertEqual(beds['value'], 1250)
        self.assertEqual(beds['raw_value'], '1,250')
        self.assertEqual(text[beds['start']:beds['end']], 'Élan Hospital is a 1,250-bed multispecialty hospital.')
        self.assertEqual(text[service['start']:service['end']], 'We offer dialysis')

    def test_rejects_approximate_subunit_historical_and_network_beds(self):
        for text in (
            'The hospital has at least 250 beds.',
            'This is a 100–150-bed hospital.',
            'The hospital has 250.5 beds.',
            'The hospital has 250 beds only in the ICU.',
            'The hospital has 250 beds in the pediatric surgical department.',
            'The hospital has 250 beds across three branches.',
            'The hospital previously had 250 beds.',
            'The hospital had 250 beds.',
            'The hospital plans to add 250 beds.',
            'A nearby specialist hospital is a 250-bed facility.',
            'The hospital has 250 beds at the Pune branch.',
            'As of 31/2/2026, the hospital has 250 beds.',
        ):
            with self.subTest(text=text):
                self.assertEqual(extract_candidates(text, ALIASES), [])

    def test_keeps_bed_basis_explicit_date_and_zero(self):
        claims = extract_candidates(
            'As of 3/1/2026, this facility had 50 staffed beds. The facility has 80 licensed beds. '
            'The hospital has 0 operating beds. Its ICU has 10 beds.', ALIASES,
        )
        self.assertEqual([(row['value'], row['scope'], row['claim_date']) for row in claims], [
            (50, 'staffed_beds', '2026-01-03'), (80, 'licensed_beds', ''), (0, 'operating_beds', ''),
        ])

    def test_features_capacity_requires_a_whole_facility_subject(self):
        text = ('Our state-of-the-art hospital features a 35-bedded capacity, including a modern '
                'Neonatal Intensive Care Unit (NICU) and Pediatric Intensive Care Unit (PICU), ensuring '
                'that your child receives the best possible care in critical situations.')
        claims = extract_candidates(text, ALIASES)
        self.assertEqual([(row['field'], row['value'], row['scope']) for row in claims], [
            ('capacity', 35, 'whole_facility_beds'),
        ])
        self.assertEqual(text[claims[0]['start']:claims[0]['end']], text)
        for unsafe in (
            'Our NICU features a 35-bed capacity.',
            'Our other branch hospital features a 35-bed capacity.',
            'Our hospital features a 35-bedded capacity at the Pune branch.',
            'Our hospital features a 35-bedded capacity for the NICU.',
            'Our hospital features a 35-bedded capacity reserved for cardiac surgery.',
        ):
            with self.subTest(text=unsafe):
                self.assertEqual(extract_candidates(unsafe, ALIASES), [])

    def test_preserves_doctor_definitions_and_rejects_mixed_staff_counts(self):
        claims = extract_candidates(
            'As of 2025-08-02, the hospital had 18 full-time physicians. The hospital has 5 visiting doctors. '
            'The hospital has a total of 30 doctors. The facility has 22 doctors. '
            'The hospital has 40 doctors and nurses.', ALIASES,
        )
        self.assertEqual([(row['value'], row['scope'], row['claim_date']) for row in claims], [
            (18, 'full_time_doctors', '2025-08-02'), (5, 'visiting_doctors', ''),
            (30, 'whole_facility_doctors', ''), (22, 'doctors_unspecified', ''),
        ])

    def test_equipment_presence_and_exact_service_are_different_claims(self):
        claims = extract_candidates(
            'This facility is currently equipped with a 3 Tesla MRI scanner. MRI scans are available. '
            'The hospital plans to install a CT scanner. We provide Level III NICU.', ALIASES,
        )
        self.assertEqual([(row['field'], row['value'], row['stance']) for row in claims], [
            ('equipment', 'mri_scanner', 'supports'), ('service', 'mri', 'supports'),
            ('service', 'nicu_level_3', 'supports'),
        ])
        self.assertEqual(claims[0]['subtype'], '3 Tesla')

    def test_service_abstention_and_explicit_contradiction(self):
        claims = extract_candidates(
            'We do not currently offer dialysis. We perform emergency C-section. '
            'We offer dialysis machines. No referral is required for dialysis. '
            'Our other city branch offers dialysis. We offer dialysis at the Pune branch. '
            'Dialysis is available at the Pune branch. Dialysis is provided through an affiliate. '
            'Our nephrology service and maternity ward are open. Patients are referred for dialysis.', ALIASES,
        )
        self.assertEqual([(row['value'], row['stance']) for row in claims], [
            ('dialysis', 'contradicts'), ('emergency_c_section', 'supports'),
        ])

    def test_owned_unit_with_an_explicit_care_statement_supports_named_service(self):
        text = ('Our Neonatal Intensive Care Unit (NICU) is dedicated to providing specialized care '
                'for premature and critically ill newborns.')
        claims = extract_candidates(text, ALIASES)
        self.assertEqual([(claim['value'], claim['stance'], claim['rule']) for claim in claims], [
            ('nicu', 'supports', 'explicit_owned_unit_service'),
        ])
        self.assertEqual(text[claims[0]['start']:claims[0]['end']], text)
        for unsafe in (
            'Our other branch’s NICU is dedicated to providing specialized care for premature newborns.',
            'At the Pune branch, our NICU is dedicated to providing specialized care for premature newborns.',
            'In a future phase, our NICU is dedicated to providing specialized care for premature newborns.',
            'Our planned NICU will be dedicated to providing specialized care for premature newborns.',
            'For referrals, our NICU is dedicated to providing specialized care for premature newborns.',
            'Our Neonatology specialty is dedicated to providing specialized care for premature newborns.',
        ):
            with self.subTest(text=unsafe):
                self.assertEqual(extract_candidates(unsafe, ALIASES), [])


if __name__ == '__main__':
    unittest.main()
