"""Freeze a stratified facility–field sample from the documentary comparison."""

import argparse
from collections import defaultdict
import csv
from hashlib import sha256
import json
from pathlib import Path


SEED = 'external-disagreement-pilot-v1'
QUOTAS = {
    'capacity': {'IFFCO': 6, 'SBI': 6, 'both': 4},
    'numberDoctors': {'IFFCO': 5, 'SBI': 5, 'both': 4},
    'website': {'IFFCO': 5},
    'yearEstablished': {'two government reports': 1},
}
PAST_REVIEWS = {('Holy Family Hospital', '400050'), ('Shri Balaji Metro Hospital, Raigarh', '496001')}


def read(path):
    with path.open(encoding='utf-8', newline='') as handle:
        yield from csv.DictReader(handle)


def score(facility_id, field):
    return sha256(f'{SEED}\0{facility_id}\0{field}'.encode()).hexdigest()


def count_cases(path):
    groups = defaultdict(list)
    for row in read(path):
        if row['scope'] == 'whole_facility_reported_total':
            groups[(row['facility_id'], row['field'])].append(row)
    for (identity, field), rows in groups.items():
        first = rows[0]
        if first['fdr_status'] != 'reported' or (first['fdr_name'], first['fdr_pin']) in PAST_REVIEWS:
            continue
        if all(row['comparison'] != 'external_conflict' for row in rows):
            continue
        publishers = {row['source'] for row in rows}
        stratum = 'both' if len(publishers) > 1 else 'IFFCO' if 'IFFCO Tokio' in publishers else 'SBI'
        candidates = [{'source': 'FDR', 'value': int(float(first['fdr_value'])), 'date': '', 'url': ''}]
        candidates.extend({'source': row['source'], 'value': int(float(row['external_value'])),
                           'date': row['source_date'], 'url': row['source_url']} for row in rows)
        yield {'identity': identity, 'field': field, 'stratum': stratum, 'name': first['fdr_name'],
               'pin': first['fdr_pin'], 'state': first['fdr_state'], 'city': first['fdr_city'],
               'address': first['fdr_address'], 'candidates': candidates}


def other_cases(path):
    years = defaultdict(list)
    for row in read(path):
        if row['field'] == 'website' and row['comparison'] == 'different_published_contact':
            yield {'identity': row['facility_id'], 'field': 'website', 'stratum': 'IFFCO', 'name': row['fdr_name'],
                   'pin': row['fdr_pin'], 'state': row['fdr_state'], 'city': row['fdr_city'],
                   'address': row['address'], 'candidates': [
                       {'source': 'FDR', 'value': row['fdr_value'], 'date': '', 'url': ''},
                       {'source': row['source'], 'value': row['value'], 'date': row['source_date'], 'url': row['source_url']}]}
        if row['review_reason'] == 'different_published_establishment_years':
            years[(row['name'], row['city'], row['state'])].append(row)
    for (name, city, state), rows in years.items():
        address = next(row['address'] for row in rows if row['address'])
        pin = next(row['pin'] for row in rows if row['pin'])
        yield {'identity': f'public:{state}:{city}:{name}', 'field': 'yearEstablished',
               'stratum': 'two government reports', 'name': name, 'pin': pin, 'state': state, 'city': city,
               'address': address, 'candidates': [{'source': row['source'], 'value': int(row['value']),
                                                  'date': row['source_date'], 'url': row['source_url']} for row in rows]}


def freeze(counts, other, extend=False):
    grouped = defaultdict(dict)
    for case in [*count_cases(counts), *other_cases(other)]:
        grouped[(case['field'], case['stratum'])][case['identity']] = case
    selected = []
    for field, strata in QUOTAS.items():
        for stratum, quota in strata.items():
            population = grouped[(field, stratum)]
            if len(population) < quota:
                raise ValueError(f'Insufficient candidates for {field} / {stratum}')
            for identity in sorted(population, key=lambda value: score(value, field))[:quota]:
                case = population[identity]
                selected.append({'case_id': f'R{len(selected) + 1:02d}', **case})
    if extend:
        for field in ('capacity', 'numberDoctors'):
            for stratum, n in QUOTAS[field].items():
                population = grouped[(field, stratum)]
                remaining = sorted(population, key=lambda value: score(value, field))[n:n + 2]
                if len(remaining) != 2:
                    raise ValueError(f'Cannot extend {field} / {stratum}')
                for identity in remaining:
                    selected.append({'case_id': f'R{len(selected) + 1:02d}', **population[identity]})
    return selected


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('counts', 'other-review', 'output'):
        parser.add_argument(f'--{option}', type=Path, required=True)
    parser.add_argument('--extend', action='store_true', help='Append 12 more cases without changing the first 36.')
    args = parser.parse_args()
    selected = freeze(args.counts, args.other_review, args.extend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(selected, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Frozen {len(selected)} cases across {len(QUOTAS)} fields.')
