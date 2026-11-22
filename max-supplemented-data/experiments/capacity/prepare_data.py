"""Produce privacy-local, aligned XGBoost inputs from the frozen FDR exports."""

import argparse
from collections import Counter
import csv
from datetime import date
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'non-ml' / 'src'), str(ROOT / 'ML' / 'models with population density')]
from density_data import NFHS_FEATURES
from facility_tool.count_models import CATEGORY_NAMES
from facility_tool.geography import place_key, state_key


VERSIONS = ('01', '02', '03', '04')
SPLITS = ('train', 'calibration', 'test')
NUMERIC = ('distinct_social_media_presence_count', 'number_of_facts_about_the_organization',
           'post_metrics_post_count', 'engagement_metrics_n_followers', 'engagement_metrics_n_likes',
           'engagement_metrics_n_engagements')
DATES = ('recency_of_page_update', 'post_metrics_most_recent_social_media_post_date')
CATEGORICAL = (*CATEGORY_NAMES, 'affiliated_staff_presence', 'custom_logo_presence')
FACILITY = ('numberDoctors', 'latitude', 'longitude', *NUMERIC, *CATEGORICAL,
            *(field + '_day' for field in DATES))
LABEL_METHODS = {'fdr_reported', 'fdr_reported_contested', 'dated_external_report', 'direct_review',
                 'approved_source_recovery', 'explicit_fdr_description', 'latest_year_mean', 'recent_two_year_mean'}
TARGET_DEFECTS = {
    ('Harsh Hospital', '854301'): {
        'name': 'Harsh Hospital, Purnea', 'pin': '854301', 'held_value': 200000,
        'source': 'https://harshhospital.com/', 'identity_source': 'https://harshhospital.com/contact-us',
        'reason': 'The hospital page describes 200000+ Happy Patients and 50+ Patient Bed separately. The latter is a lower bound.',
    },
}
CENSUS_SPOT_CHECK = {
    'dibang valley': 8004, 'lower dibang valley': 54080, 'west siang': 112274,
}
CENSUS_SPOT_CHECK_SOURCE = ('https://ecostatistics.arunachal.gov.in/vali_admin/images/state_publication/'
                            '347899FINAL%20-Statitistical%20Abstract%202023-compressed.pdf')


def file_hash(path):
    hasher = sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1048576), b''):
            hasher.update(data)
    return hasher.hexdigest()


def value_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def json_lines(path):
    with path.open(encoding='utf-8') as stream:
        for i, line in enumerate(stream):
            row = json.loads(line)
            if i or set(row) != {'columns'}:
                yield row


def facility_key(value):
    return str(value or '').strip()


def index(rows, label):
    output = {}
    for row in rows:
        key = facility_key(row['facility_id'])
        if not key or key in output:
            raise ValueError(f'{label} contains a missing or duplicate facility ID.')
        output[key] = row
    return output


def number(value, minimum=None, maximum=None):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return None
    return value


def count(value):
    value = number(value, 0, 2**53)
    return int(value) if value is not None and value.is_integer() else None


def area_key(state, district):
    return state_key(state), place_key(district)


def raw_nfhs_order(path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        artifact = json.load(stream)
    si, di = (artifact['columns'].index(field) for field in ('state_ut', 'district_name'))
    return [(row[si], row[di]) if isinstance(row, list) else (row['state_ut'], row['district_name'])
            for row in artifact['rows']]


def population_input(args, raw_nfhs):
    path = args.population
    if path is None:
        return [], {'status': 'no_population_supplied'}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        columns, rows = set(reader.fieldnames or ()), list(reader)
    receipt = {'path': path.name, 'sha256': file_hash(path), 'source_rows': len(rows),
               'source_revision': args.population_source_revision,
               'grain': 'district population estimate, without a land-area denominator'}
    if columns >= {'state', 'district', 'population'}:
        receipt['status'] = 'keyed_population'
        triples = [(row['state'], row['district'], row['population']) for row in rows]
    elif columns == {'population'} and args.population_follows_nfhs_order:
        raw_keys = [(row['state_ut'], row['district_name']) for row in raw_nfhs]
        if len(rows) != len(raw_keys):
            raise ValueError('Population and unsorted NFHS row counts differ.')
        reference = args.nfhs_order_reference
        if reference is None or raw_nfhs_order(reference) != raw_keys:
            raise ValueError('The cached raw NFHS response must match the JSONL checkpoint key order exactly.')
        receipt.update(status='user_confirmed_nfhs_row_order', nfhs_raw_key_order_sha256=value_hash(raw_keys),
                       nfhs_order_reference_sha256=file_hash(reference), raw_cache_order_exact_match=True,
                       linkage_basis=('The user confirmed the CSV follows NFHS order. Both cached copies of the NFHS '
                                      'export agree on the key order. The population CSV omits geographic keys.'))
        triples = [(*key, row['population']) for key, row in zip(raw_keys, rows, strict=True)]
    else:
        raise ValueError('Provide state,district,population or explicitly select --population-follows-nfhs-order.')

    keyed, seen = [], set()
    for state, district, raw in triples:
        key = area_key(state, district)
        if not all(key) or key in seen:
            raise ValueError('Population district keys must be complete and unique after normalization.')
        seen.add(key)
        value = number(raw, 1)
        if raw is not None and str(raw).strip() and value is None:
            raise ValueError('Population must contain positive finite values or blanks.')
        keyed.append({'state': state, 'district': district, 'population': value})
    receipt['nonmissing_source_rows'] = sum(row['population'] is not None for row in keyed)
    return keyed, receipt


def survey_input(raw_nfhs):
    missing = set(NFHS_FEATURES) - set(raw_nfhs[0])
    if missing:
        raise ValueError(f'NFHS source is missing selected fields: {sorted(missing)}')
    keys = [area_key(row['state_ut'], row['district_name']) for row in raw_nfhs]
    counts = Counter(keys)
    lookup = {
        key: {field: number(row.get(field), 0 if field.endswith('_pct') else 1,
                            100 if field.endswith('_pct') else None) for field in NFHS_FEATURES}
        for key, row in zip(keys, raw_nfhs, strict=True) if all(key) and counts[key] == 1
    }
    return lookup, sum(size > 1 for size in counts.values())


def original_branch_rows(iterations):
    paths = [path / 'dataset' / 'facilities.jsonl' for path in iterations]
    raw, count_rows, unlinked = {}, 0, 0
    for count_rows, (first, *others) in enumerate(zip(*(json_lines(path) for path in paths), strict=True), 1):
        fields = ('source_row_index', 'facility_id', 'original')
        if first['source_row_index'] != count_rows - 1 or any(any(row[k] != first[k] for k in fields)
                                                            for row in others):
            raise ValueError('All supplements must retain the same original rows and branch assignments.')
        key = facility_key(first['facility_id'])
        if key:
            raw.setdefault(key, first['original'])
        else:
            unlinked += 1
    return raw, count_rows, unlinked


def reviewed_ids(iterations, entities):
    reviewed = {
        facility_key(case['identity'])
        for path in (iteration / 'sample.json' for iteration in iterations)
        for case in json.loads(path.read_text(encoding='utf-8'))
        if case.get('field') in {'capacity', 'numberDoctors'}
    }
    prior_path = ROOT / 'data' / 'recovery' / 'capacity-evidence' / 'reviews.json'
    prior = json.loads(prior_path.read_text(encoding='utf-8'))
    names_and_pins = {(str(case['name']).strip(), str(case['pin']).strip()) for case in prior}
    reviewed.update(key for key, row in entities.items()
                    if (str(row.get('name', '')).strip(), str(row.get('pin', '')).strip()) in names_and_pins)
    return reviewed


def exact_branch_matches(pairs, entities, linked):
    matches = {pair: [] for pair in pairs}
    for key in linked:
        row = entities[key]
        identity = (str(row.get('name', '')).strip(), str(row.get('pin', '')).strip())
        if identity in matches:
            matches[identity].append(key)
    if any(len(keys) != 1 for keys in matches.values()):
        raise ValueError('A target review case failed unique name and PIN linkage.')
    return {pair: keys[0] for pair, keys in matches.items()}


def independent_review_ids(path, entities, linked):
    cases = json.loads(path.read_text(encoding='utf-8'))['challenge']
    pairs = {(case['name'].strip(), case['pin'].strip()) for case in cases}
    if len(pairs) != len(cases):
        raise ValueError('The independent review duplicates a name and PIN pair.')
    return set(exact_branch_matches(pairs, entities, linked).values())


def date_feature(value):
    try:
        return (date.fromisoformat(str(value).strip()[:10]) - date(1970, 1, 1)).days
    except ValueError:
        return None


def facility_features(raw, doctor, entity):
    result = {field: number(raw.get(field), 0) for field in NUMERIC}
    for field, categories in CATEGORY_NAMES.items():
        result[field] = categories.get(str(raw.get(field, '')).strip().casefold())
    for field in ('affiliated_staff_presence', 'custom_logo_presence'):
        value = str(raw.get(field, '')).strip().casefold()
        result[field] = value if value in {'true', 'false'} else None
    result.update({field + '_day': date_feature(raw.get(field)) for field in DATES})
    result['numberDoctors'] = count(doctor.get('value')) if doctor.get('status') == 'reported' else None
    result['latitude'] = number(entity['lat'], 6, 38) if entity['location_routing_eligible'] else None
    result['longitude'] = number(entity['lon'], 67, 98) if entity['location_routing_eligible'] else None
    return result


def assemble(args):
    snapshot = args.snapshot
    versions = {version: path for version in VERSIONS
                if (path := getattr(args, f'iteration_{version}', None)) is not None}
    iterations = tuple(versions.values())
    raw, source_rows, unlinked = original_branch_rows(iterations)
    capacity, doctors, splits, entities, contexts = [
        index(json_lines(snapshot / f'{file}.jsonl'), file) for file in
        ('capacity_target_audit', 'number_doctors_target_audit', 'capacity_split', 'entities', 'district_context')
    ]
    decisions = {}
    for version, folder in versions.items():
        releases = list(json_lines(folder / 'dataset' / 'count_decisions.jsonl'))
        decisions[version] = {
            field: index((row for row in releases if row['field'] == field), f'{version} {field}')
            for field in ('capacity', 'numberDoctors')
        }
    for lookup in (capacity, doctors, splits, *(field for version in decisions.values() for field in version.values())):
        if set(lookup) != set(raw):
            raise ValueError(f'Facility IDs differ across the original and release tables: raw {len(raw)}, table {len(lookup)}.')
    if not set(raw) <= entities.keys() or not set(raw) <= contexts.keys():
        raise ValueError('Every linked source facility needs location and district audit entries.')
    if any(capacity[key]['split'] != splits[key]['split'] or capacity[key]['status'] != splits[key]['status']
           or doctors[key]['split'] != splits[key]['split'] or splits[key]['split'] not in SPLITS for key in raw):
        raise ValueError('Audited facility split or label status disagrees across the snapshot tables.')

    reviewed = reviewed_ids(iterations, entities)
    independent = independent_review_ids(args.independent_review, entities, raw)
    defects = {key: TARGET_DEFECTS[pair] for pair, key in exact_branch_matches(TARGET_DEFECTS, entities, raw).items()}
    raw_nfhs = list(json_lines(snapshot / 'raw_nfhs.jsonl'))
    socioeconomic, ambiguous_nfhs_keys = survey_input(raw_nfhs)
    keyed_population, population_metadata = population_input(args, raw_nfhs)
    population = {area_key(row['state'], row['district']): row['population'] for row in keyed_population}
    if population_metadata['status'] == 'user_confirmed_nfhs_row_order':
        population_metadata['spot_check_values'] = [
            {**row, 'historical_census_2011_population': CENSUS_SPOT_CHECK[place_key(row['district'])]}
            for row in keyed_population if area_key(row['state'], row['district'])[0] == 'arunachal pradesh'
            and place_key(row['district']) in CENSUS_SPOT_CHECK
        ]
        population_metadata['spot_check_source'] = CENSUS_SPOT_CHECK_SOURCE
        population_metadata['spot_check_interpretation'] = (
            'Large differences from the 2011 Census are flagged for review. Observation dates, estimation method '
            'and boundary equivalence for the published population estimates are undocumented.'
        )

    features = {'facility': list(FACILITY), 'socioeconomic': list(NFHS_FEATURES),
                'population': ['log_population'] if population else [],
                'population_status': population_metadata['status'],
                'iteration_feature_aliases': {version: {'numberDoctors': f'numberDoctors_iteration_{version}'}
                                              for version in versions},
                'source_policies': {f'iteration_{version}': json.loads((folder / 'dataset/dataset_summary.json')
                                    .read_text()).get('count_policy', 'saved_selector') for version, folder in versions.items()}}
    features['feature_types'] = {field: 'categorical' if field in CATEGORICAL else 'numeric'
                                 for group in ('facility', 'socioeconomic', 'population') for field in features[group]}
    fields = ('capacity', 'numberDoctors')
    cohorts, rows = {split: Counter() for split in SPLITS}, []
    for key, original in raw.items():
        entity, context, audit = entities[key], contexts[key], capacity[key]
        route = entity['location_routing_eligible'] is True
        split = splits[key]['split']
        truth = count(audit.get('value')) if audit['status'] == 'reported' else None
        defect = defects.get(key)
        if defect and (truth != defect['held_value'] or str(entity.get('pin')) != defect['pin']):
            raise ValueError('A documented target exclusion requires readjudication in this snapshot.')
        bed_decisions = [decisions[version]['capacity'][key] for version in versions]
        conflict = any(decision['method'] in {'fdr_reported_contested', 'review_unresolved'}
                       for decision in bed_decisions)
        new_bed_review = any('release_review' in decision for decision in bed_decisions)
        source_disagreement = conflict or any(decision.get('source_value_disagreement') is True
                                              for decision in bed_decisions)
        usable = route and audit['status'] in {'reported', 'missing'} and not defect
        training = usable and key not in independent
        evaluation = training and truth is not None and key not in reviewed and not conflict and not new_bed_review
        geography = (context['state_key'], context['district_key']) if route else ('', '')
        if context.get('postal_resolution') in {'state_conflict', 'district_conflict', 'ambiguous'}:
            geography = ('', '')
        socio = socioeconomic.get(geography, {})
        pop = population.get(geography)
        row = {
            'facility_id': entity['facility_id'], 'split': split, 'routing_eligible': route,
            'state': entity.get('state') or context.get('postal_state'),
            'district': entity.get('district') or context.get('postal_district'),
            'district_join_key': '|'.join(geography) if all(geography) else None,
            'capacity_observed': truth, 'capacity_observed_status': audit['status'],
            'training_eligible': training, 'evaluation_eligible': evaluation,
            'source_selection_review_exposed': key in reviewed, 'capacity_source_disagreement': source_disagreement,
            'capacity_target_defect': bool(defect),
            'nfhs_matched': any(value is not None for value in socio.values()), 'population_matched': pop is not None,
            **facility_features(original, doctors[key], entity),
            **{field: socio.get(field) for field in NFHS_FEATURES},
        }
        if population:
            row['log_population'] = math.log1p(pop) if pop is not None else None
        for version in versions:
            for field in fields:
                decision = decisions[version][field][key]
                selected, method = count(decision.get('value')), decision['method']
                if selected is not None and method not in LABEL_METHODS:
                    raise ValueError(f'Unexpected published-value method in iteration {version}: {method}')
                row[f'{field}_iteration_{version}'] = selected
                row[f'{field}_method_iteration_{version}'] = method
            selected = row[f'capacity_iteration_{version}']
            row[f'imputation_eligible_iteration_{version}'] = (
                usable and audit['status'] == 'missing' and selected is None
                and row[f'capacity_method_iteration_{version}'] == 'missing'
            )
            cohorts[split][f'usable_training_labels_iteration_{version}'] += training and selected is not None
            cohorts[split][f'eligible_missing_iteration_{version}'] += row[f'imputation_eligible_iteration_{version}']
        cohorts[split].update({
            'linked_branches': 1, 'original_reported': truth is not None,
            'held_original_reported': not route and truth is not None,
            'review_exposed_original_reported': key in reviewed and truth is not None,
            'independent_review_held': key in independent,
            'source_conflict_original_reported': conflict and truth is not None,
            'source_disagreement_original_reported': source_disagreement and truth is not None,
            'explicit_target_defect': bool(defect), 'evaluation_labels': evaluation,
            'nfhs_matched': row['nfhs_matched'], 'population_matched': row['population_matched'],
            'evaluation_nfhs_matched': evaluation and row['nfhs_matched'],
            'evaluation_population_matched': evaluation and row['population_matched'],
        })
        rows.append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / 'facilities.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    if keyed_population:
        output = args.output_dir / 'population_keyed.csv'
        with output.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=('state', 'district', 'population'))
            writer.writeheader()
            writer.writerows(keyed_population)
        population_metadata['derived_keyed_sha256'] = file_hash(output)
    receipts = {
        'source_rows_per_iteration': source_rows, 'canonical_facilities': len(rows),
        'unlinked_source_rows_removed': unlinked, 'duplicate_linked_rows_collapsed': source_rows - len(rows) - unlinked,
        'counts_by_split': cohorts,
        'independent_review_count': len(independent),
        'independent_review_manifest_sha256': file_hash(args.independent_review),
        'capacity_quality_holds': [
            {'facility_id': key, 'name': defect['name'], 'pin': defect['pin'], 'raw_observed': defect['held_value'],
             'source_url': defect['source'], 'identity_source': defect['identity_source'], 'reason': defect['reason']}
            for key, defect in defects.items()
        ],
        'holdout_id_sha256_by_split': {
            split: value_hash(sorted(row['facility_id'] for row in rows
                                     if row['split'] == split and row['evaluation_eligible'])) for split in SPLITS
        },
        'capacity_target_audit_sha256': file_hash(snapshot / 'capacity_target_audit.jsonl'),
        'capacity_split_sha256': file_hash(snapshot / 'capacity_split.jsonl'),
        'nfhs': {'source_rows': len(raw_nfhs), 'usable_district_keys': len(socioeconomic),
                 'duplicate_normalized_keys_excluded': ambiguous_nfhs_keys, 'survey_period': '2019–21',
                 'snapshot_sha256': file_hash(snapshot / 'raw_nfhs.jsonl'),
                 'join': 'exact audited state and district pair; suppressed values and small-sample markers excluded'},
        'population': population_metadata,
    }
    for name, data in (('feature_groups.json', features), ('input_audit.json', receipts)):
        (args.output_dir / name).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n',
                                            encoding='utf-8')
    print(json.dumps(receipts, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('snapshot', 'iteration-01', 'iteration-02', 'output-dir'):
        parser.add_argument(f'--{option}', type=Path, required=True)
    for option in ('iteration-03', 'iteration-04'):
        parser.add_argument(f'--{option}', type=Path)
    parser.add_argument('--population', type=Path)
    parser.add_argument('--population-source-revision')
    parser.add_argument('--population-follows-nfhs-order', action='store_true')
    parser.add_argument('--nfhs-order-reference', type=Path)
    parser.add_argument('--independent-review', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'source_selection' / 'evidence.json')
    assemble(parser.parse_args())
