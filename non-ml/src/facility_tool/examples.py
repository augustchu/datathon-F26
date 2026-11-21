"""Fictional facility records and hand-written estimates for the local walkthrough."""

from datetime import date

import pandas as pd


def demo_inputs():
    rows = []
    for i in range(40):
        facility_type = 'Hospital' if i % 3 else 'Clinic'
        operator = 'Public' if i % 2 else 'Private'
        service = ('Onsite haemodialysis and dialysis sessions are offered.' if i % 6 == 0
                   else 'An MRI service is listed.' if i % 7 == 0
                   else 'General outpatient care.')
        rows.append({
            'unique_id': f'demo-{i:02d}', 'name': f'Fictional Jaipur Facility {i:02d}',
            'phone_numbers': '[]' if i % 4 == 0 else '["0000000000"]' if i % 5 == 0 else '["+91 90000 00001"]',
            'officialPhone': None, 'address_line1': f'{i + 11} Example Road',
            'address_city': 'Jaipur', 'address_stateOrRegion': 'Rajasthan',
            'address_zipOrPostcode': '302001', 'countries': 'India',
            'facilityTypeId': facility_type, 'operatorTypeId': operator, 'description': service,
            'numberDoctors': None if i % 9 == 0 else str(4 + (i * 3) % 46),
            'capacity': None if i % 6 == 0 else '80–100 beds' if i == 5 else '12 ICU beds' if i == 11 else str(20 + (i * 11) % 180),
            'specialties': 'Nephrology' if i == 2 else None,
            'procedure': None, 'equipment': 'dialysis machine' if i == 2 else None,
            'capability': None, 'latitude': 26.914 + i * 0.0021,
            'longitude': 75.788 + ((i * 7) % 31) * 0.0018,
            'source_urls': [f'https://example.invalid/fictional-facility/{i:02d}'],
        })
    pins = pd.DataFrame([
        {'pincode': '302001', 'officename': 'Fictional Jaipur Post A', 'district': 'Jaipur',
         'statename': 'Rajasthan', 'latitude': 26.9124, 'longitude': 75.7873},
        {'pincode': '302001', 'officename': 'Fictional Jaipur Post B', 'district': 'Jaipur',
         'statename': 'Rajasthan', 'latitude': 26.9176, 'longitude': 75.7923},
        {'pincode': '302099', 'officename': 'Fictional Boundary Post A', 'district': 'Jaipur',
         'statename': 'Rajasthan', 'latitude': 26.92, 'longitude': 75.80},
        {'pincode': '302099', 'officename': 'Fictional Boundary Post B', 'district': 'Ajmer',
         'statename': 'Rajasthan', 'latitude': 26.44, 'longitude': 74.63},
    ])
    nfhs = pd.DataFrame([{'district_name': 'Jaipur', 'state_ut': 'Rajasthan',
                          'hh_improved_water_pct': '(91.0)', 'hh_electricity_pct': '*'}])
    return {'facilities': pd.DataFrame(rows), 'pins': pins, 'nfhs': nfhs}


def demo_overlays(entities, as_of):
    """Use fixed toy numbers to display mode differences without fitting a model."""
    date.fromisoformat(as_of)
    ids = entities.set_index('name')['facility_id'].to_dict()
    updates = []
    for idx, regression_value, ml_value in [(0, 72, 95), (6, 110, 125), (24, 85, 140)]:
        facility = ids[f'Fictional Jaipur Facility {idx:02d}']
        for method, value in [('regression', regression_value), ('ml', ml_value)]:
            updates.append({'facility_id': facility, 'field': 'capacity', 'method': method,
                            'value': value, 'unit': 'beds', 'lower': value - 25, 'upper': value + 25,
                            'provenance': 'Hand-written demo value', 'detail': 'Fictional estimate and interval; no model was fitted.'})
    for idx, method, score in [(2, 'ml', 0.74), (3, 'regression', 0.66)]:
        updates.append({'facility_id': ids[f'Fictional Jaipur Facility {idx:02d}'],
                        'field': 'service:dialysis', 'method': method, 'value': score, 'unit': 'score',
                        'lower': None, 'upper': None, 'provenance': 'Hand-written demo value',
                        'detail': 'Fictional lead score; does not establish service availability.'})
    load = pd.DataFrame([
        {'facility_id': ids[f'Fictional Jaipur Facility {idx:02d}'], 'service': 'dialysis',
         'unit': 'appointment slots', 'available_units': slots, 'as_of': f'{as_of}T00:00:00Z',
         'source_url': f'https://example.invalid/fictional-slots/{idx:02d}'}
        for idx, slots in [(0, 1), (6, 8), (12, 4)]
    ])
    return pd.DataFrame(updates), load


def demo_pressure(entities):
    """An illustrative service-day scenario, independent of the count estimates."""
    ids = entities.set_index('name')['facility_id'].to_dict()
    return pd.DataFrame([
        {'facility_id': ids[f'Fictional Jaipur Facility {idx:02d}'], 'service': 'dialysis',
         'unit': 'sessions per day', 'period': 'Fictional weekday', 'basis': 'scenario',
         'demand': demand, 'capacity': capacity, 'source_url': f'https://example.invalid/fictional-pressure/{idx:02d}'}
        for idx, demand, capacity in [(0, 55, 40), (6, 46, 50), (12, 30, 35), (18, 10, 50),
                                      (24, 120, 50), (30, 58, 55), (36, 50, 30)]
    ])
