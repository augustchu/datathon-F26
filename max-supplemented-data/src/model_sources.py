"""Train and evaluate a small source selector on reviewed claim disagreements."""

import argparse
from collections import defaultdict
from datetime import date
import json
from pathlib import Path

import numpy as np
import xgboost as xgb


FIELDS = ('capacity', 'numberDoctors')
PUBLISHERS = ('FDR', 'IFFCO Tokio', 'sbi_general_archived_quality_disclosure')
AS_OF = date(2026, 11, 21)
FEATURES = ['is_doctor_count', 'is_fdr', 'is_iffco', 'is_sbi', 'observation_dated',
            'age_years', 'same_value_publishers', 'competing_publishers']
MIN_REVIEWED = 8
PARAMS = dict(objective='binary:logistic', max_depth=2, eta=0.15, min_child_weight=0.5,
              reg_lambda=3, seed=217, tree_method='hist', nthread=1, base_score=0.5)
ROUNDS = 24


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def read_reviews(folder):
    reviews = {}
    for path in sorted(folder.rglob('*.json')):
        for row in read_json(path):
            case_id = row['case_id']
            if case_id in reviews or row['status'] not in ('unresolved', 'supported'):
                raise ValueError(f'Invalid or duplicate review for {case_id}')
            if (row['verified_value'] is None) != (row['status'] == 'unresolved'):
                raise ValueError(f'Review status and value disagree for {case_id}')
            if not row.get('evidence_url') or not row.get('rationale'):
                raise ValueError(f'Review lacks a source or rationale for {case_id}')
            reviews[case_id] = row
    return reviews


def feature_vector(field, claim, candidates):
    observed = claim.get('date') or ''
    age = max(0, (AS_OF - date.fromisoformat(observed)).days / 365.25) if observed else 0
    peer_sources = {c['source'] for c in candidates if c['source'] != claim['source']}
    supporters = {c['source'] for c in candidates
                  if c['source'] in peer_sources and c['value'] == claim['value']}
    return [field == 'numberDoctors', *(claim['source'] == source for source in PUBLISHERS),
            bool(observed), age, len(supporters), len(peer_sources)]


def matrix(cases):
    rows, labels, weights = [], [], []
    for case in cases:
        candidates = case['candidates']
        for claim in candidates:
            rows.append(feature_vector(case['field'], claim, candidates))
            labels.append(int(claim['value'] == case['verified_value']))
            weights.append(1 / len(candidates))
    return xgb.DMatrix(np.asarray(rows, dtype=np.float32), label=labels, weight=weights,
                       feature_names=FEATURES, nthread=1)


def fit(cases):
    return xgb.train(PARAMS, matrix(cases), num_boost_round=ROUNDS)


def score_candidates(model, field, candidates):
    rows = np.asarray([feature_vector(field, candidate, candidates) for candidate in candidates], dtype=np.float32)
    return [float(score) for score in model.predict(xgb.DMatrix(rows, feature_names=FEATURES, nthread=1))]


def publisher_results(cases):
    totals = defaultdict(lambda: {'matched': 0, 'reviewed': 0})
    for case in cases:
        values = defaultdict(set)
        for claim in case['candidates']:
            values[claim['source']].add(claim['value'])
        for source, reported in values.items():
            totals[source]['reviewed'] += 1
            totals[source]['matched'] += int(case['verified_value'] in reported)
    return {source: {**info, 'smoothed_hit_rate': (info['matched'] + 1) / (info['reviewed'] + 2)}
            for source, info in totals.items()}


def insurer_comparison(sample, reviews, field):
    insurers = set(PUBLISHERS[1:])
    result = {'sampled_pairs': 0, 'supported_pairs': 0, 'sampled_pairs_with_different_values': 0,
              'supported_pairs_with_different_values': 0, 'iffco_only_correct': 0, 'sbi_only_correct': 0,
              'both_correct': 0, 'neither_correct': 0}
    for case in sample:
        if case['field'] != field:
            continue
        reports = defaultdict(set)
        for claim in case['candidates']:
            if claim['source'] in insurers:
                reports[claim['source']].add(claim['value'])
        if set(reports) != insurers:
            continue
        result['sampled_pairs'] += 1
        iffco, sbi = (reports[source] for source in PUBLISHERS[1:])
        differ = iffco != sbi
        result['sampled_pairs_with_different_values'] += differ
        review = reviews[case['case_id']]
        if review['status'] != 'supported':
            continue
        result['supported_pairs'] += 1
        result['supported_pairs_with_different_values'] += differ
        hit_i, hit_s = review['verified_value'] in iffco, review['verified_value'] in sbi
        if hit_i and hit_s:
            result['both_correct'] += 1
        elif hit_i:
            result['iffco_only_correct'] += 1
        elif hit_s:
            result['sbi_only_correct'] += 1
        else:
            result['neither_correct'] += 1
    return result


def best_index(scores, candidates, priors):
    def tie_break(index):
        claim = candidates[index]
        return (scores[index], priors.get(claim['source'], 0.5), claim.get('date') or '',
                -PUBLISHERS.index(claim['source']) if claim['source'] in PUBLISHERS else -len(PUBLISHERS))
    return max(range(len(candidates)), key=tie_break)


def group_evaluation(cases):
    scores = defaultdict(list)
    predictions = []
    for held_out in cases:
        training = [case for case in cases if case['identity'] != held_out['identity']]
        if not any(claim['value'] == case['verified_value'] for case in training for claim in case['candidates']):
            return None, []
        priors = {source: row['smoothed_hit_rate'] for source, row in publisher_results(training).items()}
        candidates = held_out['candidates']
        xgb_scores = score_candidates(fit(training), held_out['field'], candidates)
        baseline_scores = [priors.get(claim['source'], 0.5) for claim in candidates]
        newest = [float(date.fromisoformat(claim['date']).toordinal()) if claim['date'] else 0 for claim in candidates]
        actual = np.asarray([int(c['value'] == held_out['verified_value']) for c in candidates])
        record = {'case_id': held_out['case_id'], 'field': held_out['field'],
                  'has_a_correct_candidate': bool(actual.any())}
        for method, values in (('xgboost', xgb_scores), ('pooled_publisher', baseline_scores), ('newest', newest)):
            pick = best_index(values, candidates, priors)
            hit = int(actual[pick])
            brier = float(np.mean((np.asarray(values) - actual) ** 2)) if method != 'newest' else None
            scores[method].append((hit, brier, bool(actual.any())))
            record[method] = {'selected_source': candidates[pick]['source'], 'hit': bool(hit)}
        predictions.append(record)

    metrics = {}
    for method, values in scores.items():
        matched_cases = [hit for hit, _, exists in values if exists]
        brier = [error for _, error, _ in values if error is not None]
        metrics[method] = {'hits': sum(hit for hit, _, _ in values), 'reviewed_cases': len(values),
                           'hits_when_candidate_exists': sum(matched_cases),
                           'cases_with_correct_candidate': len(matched_cases),
                           'mean_candidate_brier': sum(brier) / len(brier) if brier else None}
    return metrics, predictions


def train(sample_path, review_dir, output_dir):
    sample = read_json(sample_path)
    reviews = read_reviews(review_dir)
    if {case['case_id'] for case in sample} != reviews.keys():
        raise ValueError('The reviewed case IDs must match the frozen sample exactly')
    labeled, attendance = [], defaultdict(lambda: {'sampled': 0, 'supported': 0, 'matching_candidate': 0})
    ledger = []
    for case in sample:
        review = reviews[case['case_id']]
        field = case['field']
        attendance[field]['sampled'] += 1
        if review['status'] == 'supported':
            attendance[field]['supported'] += 1
            if field in FIELDS and not isinstance(review['verified_value'], int):
                raise ValueError(f'Expected an exact integer count for {case["case_id"]}')
            verified = review['verified_value']
            match_sources = sorted({claim['source'] for claim in case['candidates']
                                    if (verified == claim['value'] if field != 'website' else
                                        verified in claim['value'].removeprefix('https://').removeprefix('www.').split(', '))})
            attendance[field]['matching_candidate'] += bool(match_sources)
            if field in FIELDS:
                labeled.append({**case, 'verified_value': verified, 'matching_sources': match_sources})
        else:
            match_sources = []
        ledger.append({'case_id': case['case_id'], 'name': case['name'], 'pin': case['pin'], 'field': field,
                       **review, 'matching_sources': match_sources})

    output_dir.mkdir(parents=True, exist_ok=True)
    report = {'field_review_coverage': dict(attendance), 'pooled_priority_fields': list(FIELDS),
              'pooled_publisher_scores': publisher_results(labeled), 'fields': {},
              'iffco_sbi_head_to_head': {field: insurer_comparison(sample, reviews, field) for field in FIELDS},
              'minimum_supported_for_model_default': MIN_REVIEWED, 'parameters': PARAMS,
              'rounds': ROUNDS, 'xgboost_version': xgb.__version__}
    for field in FIELDS:
        cases = [case for case in labeled if case['field'] == field]
        reg = {'supported_cases': len(cases), 'default': 'pooled_publisher', 'xgboost_trained': False}
        if len({case['identity'] for case in cases}) >= 3 and any(case['matching_sources'] for case in cases):
            fit(cases).save_model(output_dir / f'{field}_selector.json')
            metrics, predictions = group_evaluation(cases)
            reg.update({'xgboost_trained': True, 'group_held_out': metrics, 'out_of_fold': predictions})
            if metrics:
                boosted = metrics['xgboost']
                baseline = metrics['pooled_publisher']
                if (len(cases) >= MIN_REVIEWED and boosted['hits'] > max(baseline['hits'], metrics['newest']['hits'])
                        and boosted['mean_candidate_brier'] < baseline['mean_candidate_brier']):
                    reg['default'] = 'xgboost'
        reg['reason'] = ('Improved held-out top choice and Brier score above the minimum case count.'
                         if reg['default'] == 'xgboost' else
                         'The verified cases did not support making XGBoost the default.')
        report['fields'][field] = reg
    write_json(output_dir / 'model_evaluation.json', report)
    write_json(output_dir / 'review_ledger.json', ledger)
    print(json.dumps({field: {k: v for k, v in result.items() if k != 'out_of_fold'}
                      for field, result in report['fields'].items()}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', type=Path, required=True)
    parser.add_argument('--reviews', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    train(args.sample, args.reviews, args.output_dir)
