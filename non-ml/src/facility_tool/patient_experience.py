"""Prepare comparable patient survey aggregates and optional public review links."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from math import isfinite
import re
from urllib.parse import urlencode, urlsplit

import pandas as pd

from .geography import pin_key


PATIENT_EXPERIENCE_COLUMNS = (
    'facility_id', 'patient_experience_status', 'patient_experience_value', 'patient_comparison_key',
    'patient_rating_source', 'patient_rating_source_url', 'patient_rating_metric',
    'patient_rating_period_start', 'patient_rating_period_end', 'patient_rating',
    'patient_rating_min', 'patient_rating_max', 'patient_rating_response_count',
    'patient_reviews_search_url',
)
RATING_COLUMNS = (
    'facility_id', 'source', 'source_url', 'metric', 'period_start', 'period_end',
    'rating', 'rating_min', 'rating_max', 'response_count',
)
ISO_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
MAP_SEARCH = 'https://www.google.com/maps/search/'


def _label(value):
    return value.strip() if isinstance(value, str) else ''


def _day(value, field):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f'{field} must be a valid YYYY-MM-DD date.')


def _whole_number(value, field, minimum=0):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{field} must be a whole number of at least {minimum}.') from None
    if not parsed.is_finite() or parsed != parsed.to_integral_value() or not minimum <= parsed <= 2**63 - 1:
        raise ValueError(f'{field} must be a whole number of at least {minimum}, within the supported integer range.')
    return int(parsed)


def _number(value, field):
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{field} must be a finite number.') from None
    if pd.api.types.is_bool(value) or not isfinite(parsed):
        raise ValueError(f'{field} must be a finite number.')
    return parsed


def _published_url(value):
    url = _label(value)
    try:
        parsed = urlsplit(url)
        valid = bool(parsed.scheme == 'https' and parsed.hostname and not (parsed.username or parsed.password))
        # Trigger port validation without changing the source URL.
        parsed.port
    except ValueError:
        valid = False
    if not valid or any(ch.isspace() for ch in url):
        raise ValueError('source_url must be an HTTPS link without embedded credentials.')
    return url


def _reviews_search_url(entity):
    name, address, city, state = (_label(entity.get(key)) for key in ('name', 'address', 'city', 'state'))
    pin = pin_key(entity.get('pin'))
    if not name or not (address or city or pin):
        return ''
    parts = [part for part in dict.fromkeys((name, address, city, state, pin, 'India')) if part]
    url = f'{MAP_SEARCH}?{urlencode({"api": 1, "query": ", ".join(parts)})}'
    return url if len(url) <= 2048 else ''


def _rating_profile(row, cutoff, threshold, max_age, prior_count):
    source, metric = _label(row['source']), _label(row['metric'])
    if not source or not metric:
        raise ValueError('source and metric are required to compare patient ratings.')
    url = _published_url(row['source_url'])
    start = _day(row['period_start'], 'period_start')
    end = _day(row['period_end'], 'period_end')
    if not start <= end <= cutoff:
        raise ValueError('The rating period must begin on or before its end and may not extend past as_of.')

    rating, lower, upper = (_number(row[key], key) for key in ('rating', 'rating_min', 'rating_max'))
    width = upper - lower
    if not isfinite(width) or not (lower <= rating <= upper and lower < upper):
        raise ValueError('The rating must be within an increasing finite rating scale.')
    count = _whole_number(row['response_count'], 'response_count')
    status = 'stale' if (cutoff - end).days > max_age else 'small_sample' if count < threshold else 'eligible'
    value = None
    if status == 'eligible':
        normalized = (rating - lower) / width
        weight = count / (count + prior_count)
        value = weight * normalized + (1 - weight) / 2
    comparison = json.dumps([source, metric, lower or 0.0, upper or 0.0, start.isoformat(), end.isoformat()],
                            ensure_ascii=False, separators=(',', ':'))
    return dict(
        patient_experience_status=status, patient_experience_value=value, patient_comparison_key=comparison,
        patient_rating_source=source, patient_rating_source_url=url, patient_rating_metric=metric,
        patient_rating_period_start=start.isoformat(), patient_rating_period_end=end.isoformat(),
        patient_rating=rating, patient_rating_min=lower, patient_rating_max=upper,
        patient_rating_response_count=count,
    )


def _facility_ids(frame, label):
    if frame.columns.has_duplicates:
        raise ValueError(f'{label} requires unique column names.')
    if 'facility_id' not in frame:
        raise ValueError(f'{label} requires a facility_id column.')
    ids = frame['facility_id']
    if not ids.map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError(f'{label} requires exact nonblank facility_id values.')
    if ids.duplicated().any():
        raise ValueError(f'{label} has duplicate facility_id values. Resolve duplicate aggregates before use.')
    return set(ids)


def prepare_patient_experience(entities, ratings, as_of, *, min_responses=30, max_age_days=365, prior_responses=20):
    """Join published or authorized aggregates by exact branch identifier.

    Eligible ratings use a shared source, metric, scale and reporting window
    to support comparison. Normalize the reported mean to 0–1 and shrink it
    toward the scale center with an editable prior response count. Keep raw
    aggregate fields visible for small samples and stale survey windows.
    Google Maps URLs are plain public branch searches constructed offline.
    """
    cutoff = _day(as_of, 'as_of')
    threshold = _whole_number(min_responses, 'min_responses', minimum=1)
    max_age = _whole_number(max_age_days, 'max_age_days')
    prior_count = _whole_number(prior_responses, 'prior_responses')
    entity_ids = _facility_ids(entities, 'Entities')
    ratings_by_id = {}
    if ratings is not None and not ratings.empty:
        missing = set(RATING_COLUMNS).difference(ratings.columns)
        if missing:
            raise ValueError(f'Patient ratings are missing columns: {", ".join(sorted(missing))}.')
        if _facility_ids(ratings, 'Patient ratings') - entity_ids:
            raise ValueError('Patient ratings contain facility_id values absent from the prepared entities.')
        for position, row in enumerate(ratings[list(RATING_COLUMNS)].to_dict(orient='records'), start=1):
            try:
                ratings_by_id[row['facility_id']] = _rating_profile(row, cutoff, threshold, max_age, prior_count)
            except ValueError as error:
                raise ValueError(f'Patient rating row {position}: {error}') from None

    rows = []
    for entity in entities.to_dict(orient='records'):
        ident = entity['facility_id']
        profile = ratings_by_id.get(ident, dict(patient_experience_status='unknown', patient_comparison_key=''))
        rows.append(dict(profile, facility_id=ident, patient_reviews_search_url=_reviews_search_url(entity)))
    result = pd.DataFrame(rows, columns=PATIENT_EXPERIENCE_COLUMNS)
    # Keep response counts integral even when some branches have no aggregate.
    result['patient_rating_response_count'] = pd.array(
        [row.get('patient_rating_response_count') for row in rows], dtype='Int64',
    )
    return result
