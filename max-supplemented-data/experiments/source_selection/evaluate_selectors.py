"""Compare the two frozen capacity source selectors, with group-held-out checks."""

import argparse
from collections import Counter, defaultdict
import hashlib
from itertools import combinations
import math
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

import xgboost as xgb

from build_dataset import read_checkpoint
from model_sources import PUBLISHERS, best_index, fit, publisher_results, read_json, score_candidates, write_json

FIELD = 'capacity'
CHECKPOINT_NAMES = ('iteration_01', 'iteration_02')
EXCLUDED_METHODS = {'branch_identity_hold', 'review_unresolved', 'original_count_hold'}


def source_stratum(candidates):
    outside = {item['source'] for item in candidates} - {'FDR'}
    if len(outside) == 2:
        outside_values = {item['value'] for item in candidates if item['source'] in outside}
        return 'dual_disagree' if len(outside_values) > 1 else 'dual_agree'
    if 'IFFCO Tokio' in outside:
        return 'iffco_only'
    if 'sbi_general_archived_quality_disclosure' in outside:
        return 'sbi_only'
    return 'fdr_only' if candidates else 'no_candidate'


def candidates_for_decision(record):
    candidates = list(record['eligible_external_claims'])
    original = record['original_reported']
    if original is not None:
        candidates.append({'source': 'FDR', 'value': original, 'date': ''})
    return [item for item in candidates if item['source'] in PUBLISHERS]


def dataset_cases(run_dir):
    locations = {}
    for row in read_checkpoint(run_dir / 'dataset/facilities.jsonl'):
        identity = row.get('facility_id')
        if identity:
            locations[identity] = row['prepared_location']
    cases = {}
    for row in read_checkpoint(run_dir / 'dataset/count_decisions.jsonl'):
        if row['field'] != FIELD:
            continue
        location = locations.get(row['facility_id'], {})
        candidates = candidates_for_decision(row)
        cases[row['facility_id']] = {**row, 'identity': row['facility_id'], 'candidates': candidates,
                                     'name': location.get('name'), 'pin': str(location.get('pin') or ''),
                                     'stratum': source_stratum(candidates)}
    return cases


def load_run(run_dir):
    model = xgb.Booster(params={'nthread': 1})
    model.load_model(run_dir / 'model/capacity_selector.json')
    sample = read_json(run_dir / 'sample.json')
    ledger = {item['case_id']: item for item in read_json(run_dir / 'model/review_ledger.json')}
    supported = []
    for case in sample:
        reviewed = ledger[case['case_id']]
        if case['field'] == FIELD and reviewed['status'] == 'supported':
            supported.append({**case, 'verified_value': reviewed['verified_value'],
                              'stratum': source_stratum(case['candidates'])})
    report = read_json(run_dir / 'model/model_evaluation.json')
    return {'model': model, 'sample': sample, 'supported': supported, 'report': report}


def priors_for(cases):
    return {source: row['smoothed_hit_rate'] for source, row in publisher_results(cases).items()}


def predictions(case, models, priors):
    candidates = case['candidates']
    probabilities = {name: score_candidates(model, FIELD, candidates) for name, model in models.items()}
    probabilities['publisher_fallback'] = [priors.get(item['source'], 0.5) for item in candidates]
    pick = {method: candidates[best_index(values, candidates, priors)] for method, values in probabilities.items()}
    newest = max(range(len(candidates)), key=lambda index: (
        candidates[index].get('date') or '', priors.get(candidates[index]['source'], 0.5)))
    pick['newest'] = candidates[newest]
    return pick, probabilities


def scored_case(case, models, priors):
    pick, probabilities = predictions(case, models, priors)
    expected = case['verified_value']
    labels = [item['value'] == expected for item in case['candidates']]
    available = any(labels)
    scores = {}
    for method, item in pick.items():
        values = probabilities.get(method)
        brier = mean((value - label) ** 2 for value, label in zip(values, labels)) if values else None
        log_loss = None
        if values:
            correct = [value if label else (1 - value) for value, label in zip(values, labels)]
            log_loss = -mean(math.log(max(value, 1e-12)) for value in correct)
        scores[method] = {'source': item['source'], 'exact': item['value'] == expected,
                          'absolute_error': abs(item['value'] - expected), 'brier': brier, 'log_loss': log_loss}
    reference_band = 'under_100' if expected < 100 else '100_to_299' if expected < 300 else '300_and_above'
    return {'case_id': case['case_id'], 'stratum': case['stratum'], 'available': available,
            'reference_band': reference_band,
            'scores': scores}


def summarize(cases):
    available = sum(case['available'] for case in cases)
    methods = sorted({method for case in cases for method in case['scores']})
    result = {'cases_with_evidence': len(cases), 'cases_with_exact_candidate': available, 'methods': {}}
    for method in methods:
        rows = [case['scores'][method] for case in cases]
        hits = sum(row['exact'] for row in rows)
        result['methods'][method] = {
            'exact': hits, 'n': len(rows), 'exact_rate': hits / len(rows),
            'exact_rate_when_candidate_exists': hits / available if available else None,
            'mae_beds': mean(row['absolute_error'] for row in rows),
            'worst_absolute_error_beds': max(row['absolute_error'] for row in rows),
            'brier': mean(row['brier'] for row in rows) if rows[0]['brier'] is not None else None,
            'log_loss': mean(row['log_loss'] for row in rows) if rows[0]['log_loss'] is not None else None,
            'selected_sources': dict(Counter(row['source'] for row in rows)),
        }
    return result


def with_slices(cases):
    def by_field(field):
        groups = defaultdict(list)
        for case in cases:
            groups[case[field]].append(case)
        return {name: summarize(rows) for name, rows in groups.items()}

    return {**summarize(cases), 'by_candidate_availability': by_field('stratum'),
            'by_reference_bed_band': by_field('reference_band'),
            'by_candidate_ceiling': {label: summarize([case for case in cases if case['available'] == eligible])
                                     for eligible, label in ((True, 'exact_candidate'), (False, 'no_exact_candidate'))}}


def cross_fit(runs, held_out_cases, modeled_runs):
    results = []
    for case in held_out_cases:
        training = {name: [row for row in runs[name]['supported'] if row['identity'] != case['identity']]
                    for name in modeled_runs}
        models = {name: fit(rows) for name, rows in training.items()}
        # Use the same prior for both model tie-breaks on each common fold.
        result = scored_case(case, models, priors_for(training[modeled_runs[-1]]))
        results.append(result)
    return results


def frozen_review(evidence, cases, previously_sampled):
    categories = ('dual_disagree', 'iffco_only', 'sbi_only')
    chosen = []
    for stratum in categories:
        eligible = [case for case in cases.values()
                    if case['identity'] not in previously_sampled
                    and case['method'] not in (EXCLUDED_METHODS | {'direct_review'})
                    and case['name'] and case['pin'] and case['stratum'] == stratum
                    and len({item['value'] for item in case['candidates']}) > 1]
        salt = evidence['design']['salt']
        ordered = sorted(eligible, key=lambda case: hashlib.sha256(f"{salt}:{case['identity']}".encode()).hexdigest())
        chosen.extend(ordered[:evidence['design']['per_stratum']])
    frozen = [(case['name'], case['pin'], case['stratum']) for case in chosen]
    documented = [(case['name'], case['pin'], case['stratum']) for case in evidence['challenge']
                  if case['stratum'] != 'prior_review_anchor']
    if frozen != documented:
        raise ValueError('Frozen review manifest does not match the candidate snapshot')

    by_name = defaultdict(list)
    for case in cases.values():
        by_name[(case['name'], case['pin'])].append(case)
    labeled = []
    for record in evidence['challenge']:
        matches = by_name[(record['name'], record['pin'])]
        if len(matches) != 1 or matches[0]['identity'] in previously_sampled:
            raise ValueError(f"Ambiguous or previously sampled branch for {record['case_id']}")
        if record['supported_beds'] is not None:
            labeled.append({**matches[0], 'verified_value': record['supported_beds'], 'case_id': record['case_id'],
                            'stratum': record['stratum']})
    return labeled


def population_diagnostics(cases, models, priors, reviewed):
    selection_counts = defaultdict(Counter)
    by_stratum = defaultdict(lambda: {'n': 0, 'checkpoint_value_disagreements': 0,
                                      'checkpoint_source_disagreements': 0, 'population_methods': defaultdict(Counter)})
    method_pair_differences = Counter()
    opportunities = 0
    skip_counts = Counter()
    for identity, case in cases.items():
        candidates = case['candidates']
        if identity in reviewed or case['method'] in EXCLUDED_METHODS or case['method'] == 'direct_review':
            skip_counts['held_or_reviewed'] += 1
            continue
        if len({candidate['value'] for candidate in candidates}) < 2:
            skip_counts['no_conflicting_numeric_candidates'] += 1
            continue
        pick, _ = predictions(case, models, priors)
        opportunities += 1
        group = by_stratum[case['stratum']]
        group['n'] += 1
        group['checkpoint_value_disagreements'] += pick[CHECKPOINT_NAMES[0]]['value'] != pick[CHECKPOINT_NAMES[1]]['value']
        group['checkpoint_source_disagreements'] += pick[CHECKPOINT_NAMES[0]]['source'] != pick[CHECKPOINT_NAMES[1]]['source']
        ordered = sorted(pick)
        for left, right in combinations(ordered, 2):
            method_pair_differences[f'{left}__vs__{right}'] += pick[left]['value'] != pick[right]['value']
        for method, claim in pick.items():
            selection_counts[method][claim['source']] += 1
            group['population_methods'][method][claim['source']] += 1
    return {'branch_capacity_rows': len(cases), 'unlabeled_conflicting_cases_scored': opportunities,
            'skip_counts': dict(skip_counts), 'selected_sources': dict(selection_counts),
            'value_disagreements': dict(method_pair_differences), 'by_candidate_availability': dict(by_stratum)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local', type=Path, default=ROOT / 'local')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('results.json'))
    parser.add_argument('--evidence', type=Path, default=Path(__file__).with_name('evidence.json'))
    args = parser.parse_args()

    runs = {name: load_run(args.local / name.replace('_', '-')) for name in CHECKPOINT_NAMES}
    first, second = (runs[name] for name in CHECKPOINT_NAMES)
    cases = dataset_cases(args.local / 'iteration-02')
    all_sampled = {row['identity'] for run in runs.values() for row in run['sample']}
    evidence = read_json(args.evidence)
    independent = frozen_review(evidence, cases, all_sampled)
    models = {name: run['model'] for name, run in runs.items()}
    shared_priors = priors_for(second['supported'])
    independent_rows = [scored_case(case, models, shared_priors) for case in independent]
    common_rows = cross_fit(runs, first['supported'], CHECKPOINT_NAMES)
    individual = {}
    for name, run in runs.items():
        individual[name] = with_slices(cross_fit(runs, run['supported'], (name,)))
    extension_supported = [case for case in second['supported']
                           if case['identity'] not in {row['identity'] for row in first['sample']}]
    extension_first_model = [scored_case(case, {CHECKPOINT_NAMES[0]: first['model']}, priors_for(first['supported']))
                             for case in extension_supported]

    reviewed_for_diagnostics = all_sampled | {case['identity'] for case in independent}
    metrics = {
        'independent_holdout': {**with_slices(independent_rows), 'reviewed': len(evidence['challenge']),
                                'unresolved': len(evidence['challenge']) - len(independent_rows)},
        'paired_common_cross_fit': with_slices(common_rows),
        'native_cross_fit': individual,
        'extension_holdout_iteration_01_only': with_slices(extension_first_model),
        'dataset_diagnostics': population_diagnostics(cases, models, shared_priors, reviewed_for_diagnostics),
        'checkpoint_evidence': {name: {'training_supported_cases': len(run['supported']),
                                       'original_grouped_oof': run['report']['fields'][FIELD]['group_held_out'],
                                       'artifact_sha256': hashlib.sha256((args.local / name.replace('_', '-') /
                                                                        'model/capacity_selector.json').read_bytes()).hexdigest()}
                                for name, run in runs.items()},
        'shared_tie_break_publisher_priors': shared_priors,
        'xgboost_version': xgb.__version__,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, metrics)
    print(f'Saved aggregate metrics to {args.output}')


if __name__ == '__main__':
    main()
