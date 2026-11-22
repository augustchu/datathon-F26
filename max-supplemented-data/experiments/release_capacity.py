"""Add selected-model estimates to the winning filled-in dataset, preserving evidence."""

import argparse
from collections import Counter
import csv
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def read_predictions(path, run_id):
    predictions = {}
    with path.open(encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            identity = row['facility_id']
            if not identity or identity in predictions or row['run_id'] != run_id:
                raise ValueError('Predictions must have unique facility IDs and reference the selected model.')
            point, lower, upper = (float(row[key]) for key in ('capacity_predicted', 'capacity_lower', 'capacity_upper'))
            if not all(math.isfinite(value) for value in (point, lower, upper)) or not 0 <= lower <= point <= upper:
                raise ValueError('Prediction interval must be finite, nonnegative and contain its point estimate.')
            predictions[identity] = {'value': max(1, math.floor(point + 0.5)),
                                     'point_estimate': point, 'lower': math.floor(lower),
                                     'upper': max(1, math.ceil(upper)), 'method': 'xgboost_estimate',
                                     'source': run_id, 'source_url': None,
                                     'interval_basis': 'held_out_calibration_absolute_residuals'}
    return predictions


def read_quality_holds(path):
    audit = json.loads(path.read_text(encoding='utf-8'))
    holds = audit['capacity_quality_holds']
    indexed = {item['facility_id']: item for item in holds}
    if len(indexed) != len(holds) or any(not identity for identity in indexed):
        raise ValueError('Capacity quality holds require unique facility IDs.')
    return indexed


def matches_held_count(raw, held):
    try:
        return Decimal(str(raw).strip()) == Decimal(str(held))
    except InvalidOperation:
        return False


def add_predictions(dataset_dir, output_dir, predictions, run_id, iteration, quality_holds):
    if output_dir.exists():
        raise FileExistsError(f'Choose a new release directory: {output_dir}')
    if predictions.keys() & quality_holds.keys():
        raise ValueError('Capacity quality holds must stay outside model imputation.')
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    initial_rows = 0
    used_predictions = set()
    matched_holds = set()
    applied_holds = set()
    methods = Counter()
    with TemporaryDirectory(prefix='.release-', dir=output_dir.parent) as staging:
        build = Path(staging) / 'release'
        build.mkdir()
        with ((dataset_dir / 'facilities.jsonl').open(encoding='utf-8') as json_in,
              (dataset_dir / 'facility_values.csv').open(encoding='utf-8', newline='') as csv_in,
              (build / 'facilities.jsonl').open('w', encoding='utf-8') as json_out,
              (build / 'facility_values.csv').open('w', encoding='utf-8', newline='') as csv_out):
            reader = csv.DictReader(csv_in)
            extra = ['capacity_prediction_lower', 'capacity_prediction_upper', 'capacity_model_id']
            writer = csv.DictWriter(csv_out, fieldnames=[*reader.fieldnames, *extra])
            writer.writeheader()
            for incoming, flat in zip(json_in, reader, strict=True):
                row = json.loads(incoming)
                if row['source_row_index'] != initial_rows or int(flat['row_index']) != initial_rows:
                    raise ValueError('JSONL and CSV source-row order disagree.')
                initial_rows += 1
                identity = row['facility_id']
                published = row['count_fields']['capacity']
                value = published['value']
                if ((identity or '') != flat['facility_id']
                        or ('' if value is None else str(value)) != flat['capacity']
                        or published['method'] != flat['capacity_status']):
                    raise ValueError(f'JSONL and CSV capacity decisions disagree at row {initial_rows - 1}.')
                final = published
                hold = quality_holds.get(identity)
                if hold and matches_held_count(row['original'].get('capacity'), hold['raw_observed']):
                    if str(row['prepared_location'].get('pin')) != hold['pin']:
                        raise ValueError(f'A capacity quality hold has a different branch PIN: {identity}')
                    matched_holds.add(identity)
                if hold and value == hold['raw_observed']:
                    if str(row['prepared_location'].get('pin')) != hold['pin']:
                        raise ValueError(f'A held capacity value has a different branch PIN: {identity}')
                    applied_holds.add(identity)
                    final = {'value': None, 'method': 'target_quality_hold', 'source': 'public_target_audit',
                             'source_url': hold['source_url'], 'identity_source': hold['identity_source'],
                             'reason': hold['reason']}
                estimate = predictions.get(identity)
                if estimate:
                    if (published['value'] is not None or published['method'] != 'missing'
                            or row['prepared_location']['location_routing_eligible'] is not True):
                        raise ValueError(f'An estimate targets an ineligible or populated capacity row: {identity}')
                    used_predictions.add(identity)
                    final = estimate
                    flat['capacity_prediction_lower'] = estimate['lower']
                    flat['capacity_prediction_upper'] = estimate['upper']
                    flat['capacity_model_id'] = run_id
                if final is not published:
                    flat['capacity'] = final['value']
                    flat['capacity_status'] = final['method']
                    flat['capacity_source'] = final['source']
                    flat['capacity_source_url'] = final['source_url']
                row['final_capacity'] = final
                methods[final['method']] += 1
                json_out.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                writer.writerow(flat)
        if not initial_rows or set(predictions) != used_predictions:
            raise ValueError('Every modeled facility must map to at least one original row.')
        if quality_holds.keys() != matched_holds:
            raise ValueError('Every capacity quality hold must match its documented original value and branch.')
        input_summary = json.loads((dataset_dir / 'dataset_summary.json').read_text(encoding='utf-8'))
        if initial_rows != input_summary['fdr_rows_preserved']:
            raise ValueError('Release row count differs from the input checkpoint.')
        result = {'selected_run': run_id, 'source_iteration': iteration, 'original_rows_preserved': initial_rows,
                  'distinct_facilities_imputed': len(used_predictions), 'source_rows_imputed': methods['xgboost_estimate'],
                  'final_methods_by_original_row': dict(methods), 'preserved_original_values': True,
                  'distinct_facilities_withheld_for_target_quality': len(applied_holds),
                  'capacity_quality_holds': list(quality_holds.values()),
                  'identity_holds_preserved': True}
        (build / 'release_audit.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        os.rename(build, output_dir)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('dataset-dir', 'predictions', 'input-audit', 'output-dir'):
        parser.add_argument(f'--{key}', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--iteration', required=True, choices=('iteration-01', 'iteration-02'))
    args = parser.parse_args()
    result = add_predictions(args.dataset_dir, args.output_dir, read_predictions(args.predictions, args.run_id),
                             args.run_id, args.iteration, read_quality_holds(args.input_audit))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
