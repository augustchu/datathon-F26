"""Attach manually checked public service statements to specific prepared branches."""

from collections import defaultdict
from datetime import date
from hashlib import sha256
from urllib.parse import urlparse

import pandas as pd

from .data import CLAIM_COLUMNS
from .geography import pin_key, place_key


REVIEW_FIELDS = ('publisher', 'publisher_key', 'reviewed_on')
LINK_FIELDS = ('facility_name', 'service', 'source_url', 'review_status', 'facility_id')


def attach_reviewed_claims(entries, entities, aliases, as_of):
    """Require a unique exact facility name plus branch-level location evidence."""
    date_limit = date.fromisoformat(as_of)
    by_name = defaultdict(list)
    for row in entities.to_dict(orient='records'):
        by_name[place_key(row['name'])].append(row)
    result, links = [], []
    keys = set()
    for entry in entries:
        name, service, url = (entry[key] for key in ('facility_name', 'service', 'source_url'))
        pin, address = pin_key(entry.get('pin')), place_key(entry.get('address_contains'))
        city = place_key(entry.get('city'))
        reviewed = date.fromisoformat(entry['reviewed_on'])
        parsed_url = urlparse(url)
        if (
            service not in aliases or not place_key(name) or not (pin or address)
            or parsed_url.scheme not in ('http', 'https') or not parsed_url.hostname
            or any(not str(entry[key]).strip() for key in ('excerpt', 'scope', 'publisher', 'publisher_key'))
            or entry['stance'] not in ('supports', 'contradicts')
        ):
            raise ValueError(f'Invalid reviewed service claim for {name!r}.')
        key = (place_key(name), pin, address, service, url)
        if key in keys:
            raise ValueError(f'Duplicate reviewed service claim for {name!r}.')
        keys.add(key)
        candidates = [
            row for row in by_name[place_key(name)]
            if (not city or place_key(row['city']) == city)
            and (not pin or pin_key(row['pin']) == pin)
            and (not address or f" {address} " in f" {place_key(row['address'])} ")
        ]
        status = 'review_date_in_future' if reviewed > date_limit else (
            'matched' if len(candidates) == 1 else 'ambiguous' if candidates else 'unmatched'
        )
        facility_id = candidates[0]['facility_id'] if status == 'matched' else ''
        links.append(dict(facility_name=name, service=service, source_url=url, review_status=status, facility_id=facility_id))
        if not facility_id:
            continue
        first, last = entry.get('valid_from'), entry.get('valid_through')
        start = date.fromisoformat(first) if first else None
        end = date.fromisoformat(last) if last else None
        if start and end and start > end:
            raise ValueError(f'Reviewed validity dates for {name!r} are reversed.')
        validity = (
            'future' if start and date_limit < start else 'expired' if end and date_limit > end
            else 'active' if start and end else 'unknown'
        )
        record_id = 'reviewed:' + sha256('\n'.join(key).encode()).hexdigest()[:20]
        result.append({
            'facility_id': facility_id, 'record_id': record_id, 'service': service,
            'stance': entry['stance'], 'excerpt': entry['excerpt'], 'source_url': url, 'source_urls': [url],
            'source': 'reviewed', 'scope': entry['scope'], 'validity': validity,
            'validity_text': [f'{first} to {last}'] if first and last else [],
            'validity_start': first or '', 'validity_end': last or '',
            'evidence_field': 'reviewed_service_text', 'citation_quality': 'direct',
            'match_reason': 'Reviewed source matched an exact facility name and branch address or PIN',
            **{field: entry[field] for field in REVIEW_FIELDS},
        })
    return pd.DataFrame(result, columns=[*CLAIM_COLUMNS, *REVIEW_FIELDS]), pd.DataFrame(links, columns=LINK_FIELDS)
