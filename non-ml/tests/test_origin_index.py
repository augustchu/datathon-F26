"""Prepared place resolution uses audited coordinates and survives checkpoint IO."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from facility_tool.origin_index import build_origin_index, resolve_prepared_origin
from facility_tool.workspace import Store


def entity(city, district, state, lat, lon, *, eligible=True):
    return dict(city=city, district=district, state=state, lat=lat, lon=lon,
                location_routing_eligible=eligible)


def postal(pin, district, state, lat, lon):
    return dict(pin=pin, district=district, state=state, lat=lat, lon=lon)


class OriginIndexTests(unittest.TestCase):
    def test_city_and_postal_district_precedence_and_eligible_points(self):
        entities = pd.DataFrame([
            entity('Bela', 'Agra', 'Uttar Pradesh', 27.0, 78.0),
            entity('Bela', 'Agra', 'Uttar Pradesh', 27.2, 78.2),
            entity('Bela', 'Agra', 'Uttar Pradesh', 28.0, 79.0, eligible=False),
            entity('Sitapur', 'Other', 'Uttar Pradesh', 27.5, 80.7, eligible=None),
        ])
        areas = pd.DataFrame([
            postal('282001', 'Bela', 'Uttar Pradesh', 27.4, 78.4),
            postal('282002', 'Agra', 'Uttar Pradesh', 27.6, 78.6),
            postal('282003', 'Agra', 'Uttar Pradesh', 27.8, 78.8),
        ])
        index = build_origin_index(entities, areas)
        city = resolve_prepared_origin('BELA, Uttar Pradesh', index)
        self.assertAlmostEqual(city.lat, 27.1)
        self.assertAlmostEqual(city.lon, 78.1)
        self.assertIn('city origin', city.basis)
        district = resolve_prepared_origin('Agra', index)
        self.assertAlmostEqual(district.lat, 27.7)
        self.assertAlmostEqual(district.lon, 78.7)
        self.assertIn('post-office', district.basis)
        with self.assertRaisesRegex(ValueError, 'no usable coordinates'):
            resolve_prepared_origin('Sitapur', index)

    def test_ambiguous_pin_and_place_with_state_aliases(self):
        entities = pd.DataFrame([
            entity('Rampur', 'Rampur', 'Uttar Pradesh', 28.8, 79.0),
            entity('Rampur', 'Shimla', 'Himachal Pradesh', 31.4, 77.6, eligible=False),
        ])
        areas = pd.DataFrame([
            postal('123456', 'Puri', 'Odisha', 19.8, 85.8),
            postal('123456', 'Bilaspur', 'Chhattisgarh', 22.1, 82.2),
        ])
        index = build_origin_index(entities, areas)
        with self.assertRaisesRegex(ValueError, 'multiple areas.*Bilaspur.*Puri'):
            resolve_prepared_origin('123456', index)
        chosen = resolve_prepared_origin('123456, ORISSA', index)
        self.assertEqual((chosen.label, chosen.lat, chosen.lon), ('123456, Puri, Odisha', 19.8, 85.8))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            resolve_prepared_origin('Rampur', index)
        chosen = resolve_prepared_origin('Rampur, Uttar Pradesh', index)
        self.assertEqual((chosen.lat, chosen.lon), (28.8, 79.0))

    def test_checkpoint_round_trip_and_coordinate_queries(self):
        index = build_origin_index(pd.DataFrame(), pd.DataFrame([
            postal('560001', 'Bengaluru Urban', 'Karnataka', 12.97, 77.59),
        ]))
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp), 'demo', '2026-11-21', tmp)
            store.write('origin_index', index)
            saved = store.read('origin_index')
        self.assertEqual(saved['name_key'].iloc[0], '560001')
        with patch('facility_tool.origin_index.median', side_effect=AssertionError('unexpected query aggregation')):
            place = resolve_prepared_origin('560001', saved)
            coordinates = resolve_prepared_origin('12.123456, 77.987654', saved)
        self.assertEqual((place.lat, place.lon), (12.97, 77.59))
        self.assertEqual((coordinates.lat, coordinates.lon), (12.123456, 77.987654))
        self.assertEqual(coordinates.basis, 'User-supplied coordinates')
        with self.assertRaisesRegex(ValueError, 'India coordinate range'):
            resolve_prepared_origin('77.59, 12.97', saved)

    def test_state_qualified_ambiguous_city_can_use_a_named_postal_district(self):
        entities = pd.DataFrame([
            entity('Jaipur', 'District A', 'Rajasthan', 26.90, 75.78),
            entity('Jaipur', 'District B', 'Rajasthan', 26.94, 75.89),
        ])
        areas = pd.DataFrame([postal('302001', 'Jaipur', 'Rajasthan', 26.91, 75.79)])
        index = build_origin_index(entities, areas)
        origin = resolve_prepared_origin('Jaipur, Rajasthan', index)
        self.assertEqual((origin.lat, origin.lon), (26.91, 75.79))
        self.assertIn('district origin from post-office', origin.basis)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            resolve_prepared_origin('Jaipur', index)


if __name__ == '__main__':
    unittest.main()
