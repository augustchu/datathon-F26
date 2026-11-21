"""Sidecar values with source or model provenance. Original rows stay intact."""

import json
from pathlib import Path

import pandas as pd


COLUMNS = ('facility_id', 'field', 'method', 'value', 'unit', 'lower', 'upper', 'provenance', 'detail')


def empty_updates():
    return pd.DataFrame(columns=COLUMNS)


def contact_evidence(records, links):
    """Return cited contacts per record, without choosing between different numbers."""
    rows = []
    for row in records.merge(links[['record_id', 'facility_id', 'link_status']], on='record_id', validate='1:1').itertuples(index=False):
        if not row.facility_id or not row.source_urls or row.link_status == 'ambiguous':
            continue
        for field, values in [('phone_numbers', row.phones), ('email_addresses', row.emails)]:
            for value in values:
                rows.append({'facility_id': row.facility_id, 'field': field, 'value': value, 'source': row.source,
                             'record_id': row.record_id, 'link_status': row.link_status,
                             'source_urls': row.source_urls, 'source_url': row.source_urls[0],
                             'url_note': 'Record lists multiple source URLs' if len(row.source_urls) > 1 else 'Record source URL'})
    return pd.DataFrame(rows, columns=['facility_id', 'field', 'value', 'source', 'record_id', 'link_status', 'source_urls', 'source_url', 'url_note'])


def load_estimates(path, valid_facility_ids, *, services):
    """Import existing, explicitly selected model outputs, never as confirmed data."""
    if not path:
        return empty_updates()
    location = Path(path)
    if location.suffix.casefold() == '.csv':
        frame = pd.read_csv(location, dtype={'facility_id': str, 'field': str, 'method': str, 'provenance': str})
    elif location.suffix.casefold() == '.jsonl':
        with location.open(encoding='utf-8') as stream:
            frame = pd.DataFrame([json.loads(line) for line in stream if line.strip()])
    else:
        raise ValueError('Model outputs must be CSV or standard JSONL with one object per line.')
    return validate_estimates(frame, valid_facility_ids, services=services)


def validate_estimates(frame, valid_facility_ids, *, services):
    """Check a candidate overlay before exposing it in a recommendation."""
    missing = set(COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f'Model output is missing fields: {sorted(missing)}')
    frame = frame[list(COLUMNS)].copy()
    for name in ('facility_id', 'field', 'method', 'provenance'):
        frame[name] = frame[name].astype('string').str.strip()
        if frame[name].fillna('').eq('').any():
            raise ValueError(f'Model output needs nonempty {name}.')
    if not frame['method'].isin(['ml', 'regression']).all():
        raise ValueError('An imported estimate must use ml or regression as its method.')
    fields = {'capacity', 'numberDoctors', *(f'service:{service}' for service in services)}
    if not frame['field'].isin(fields).all():
        raise ValueError('Estimates must be bed or doctor counts or scores for a configured service.')
    if not frame['facility_id'].isin(set(valid_facility_ids)).all():
        raise ValueError('Model output includes facility IDs outside this snapshot.')
    if frame.duplicated(['facility_id', 'field', 'method']).any():
        raise ValueError('Choose one estimate per facility, field and method before importing.')
    for field in ('value', 'lower', 'upper'):
        clean = pd.to_numeric(frame[field], errors='coerce')
        required = frame[field].notna() | (field == 'value')
        if (clean.isna() & required).any() or clean.dropna().isin([float('inf'), -float('inf')]).any():
            raise ValueError(f'{field} must be finite and numeric for counts and service scores.')
        frame[field] = clean
    _check_intervals(frame)
    return frame


def _check_intervals(frame):
    for row in frame.itertuples(index=False):
        low, high = row.lower, row.upper
        if pd.isna(low) != pd.isna(high):
            raise ValueError('Provide both interval bounds or neither.')
        if row.value < 0 or (not pd.isna(low) and not 0 <= low <= row.value <= high):
            raise ValueError('Counts and scores must be nonnegative and within their declared bounds.')
        if row.field.startswith('service:') and (row.value > 1 or not pd.isna(high) and high > 1):
            raise ValueError('Service lead scores and intervals must lie between 0 and 1.')
        if row.field in ('capacity', 'numberDoctors') and row.unit != {'capacity': 'beds', 'numberDoctors': 'doctors'}[row.field]:
            raise ValueError('Count estimates must declare beds or doctors as the matching unit.')
