"""Extract dated facility staffing, infrastructure and website leads from saved IFFCO HTML."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from extract_iffco_capacity import HEADERS, SOURCE_URL, address_keys, cells, positive_integer


COLUMNS = ('source', 'source_url', 'source_date', 'source_record_id', 'name', 'state', 'city',
           'pin', 'address', 'field', 'value', 'scope', 'excerpt')
COUNT_FIELDS = (
    (6, 'Total No. of Full time doctors', 'full_time_doctors', 'full_time_mci_approved_on_roll'),
    (7, 'No. of consultants', 'consultants', 'hospital_reported_consultants'),
    (8, 'No. of Surgeons or Interventionists', 'surgeons_interventionists', 'surgeons_or_interventionists'),
    (9, 'Total No. of Qualified Nurses', 'qualified_nurses', 'hospital_qualified_nurses'),
    (10, 'Total No. of Intensive care Unit beds', 'icu_beds', 'intensive_care_unit_beds'),
    (11, 'No. of Doctors (with Qualification', 'icu_doctors', 'icu_exclusive_mbbs_md_doctors'),
    (12, 'Number of qualified Nurses available exclusively', 'icu_nurses', 'icu_exclusive_nurses_all_shifts'),
)
ACCREDITATION = re.compile(r'\b(?:nabh|nqas|state[ -]level certificate|(?:pre[ -])?entry[ -]level)\b', re.I)
NEGATIVE_ACCREDITATION = re.compile(
    r'^(?:no(?:\s|$)(?!\.)|not\b|non[ -]|nil\b|none\b)|'
    r'\b(?:not accredited|no accredit\w*|non[ -]nabh|not applicable|appl(?:ied|ication)|'
    r'(?:under|in)[ -]?process|pending|expired|rejected)\b', re.I,
)
URL_HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}', re.I)
SOCIAL_OR_DIRECTORY_HOSTS = {
    'facebook.com', 'fb.com', 'google.com', 'google.co.in', 'goo.gl', 'instagram.com',
    'justdial.com', 'maps.app.goo.gl', 'practo.com', 'wa.me', 'youtube.com',
}


def accreditation_lead(value):
    if ACCREDITATION.search(value) and not NEGATIVE_ACCREDITATION.search(value):
        return value
    return ''


def website_lead(value):
    if not value or any(char.isspace() for char in value):
        return ''
    candidate = value if '://' in value else f'https://{value}'
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        if parts.port is not None:
            return ''
    except ValueError:
        return ''
    if (parts.scheme not in {'http', 'https'} or not host or not URL_HOST.fullmatch(host)
            or parts.username is not None or parts.password is not None):
        return ''
    if any(host == domain or host.endswith(f'.{domain}') for domain in SOCIAL_OR_DIRECTORY_HOSTS):
        return ''
    return candidate


def source_rows(html):
    soup = BeautifulSoup(html, 'html.parser')
    tables = [table.select('tr') for table in soup.select('table')]
    matches = [rows for rows in tables if rows and cells(rows[0])[:len(HEADERS)] == HEADERS]
    if len(matches) != 1:
        raise ValueError(f'Expected one IFFCO hospital disclosure table, found {len(matches)}')
    rows = matches[0]
    headers = cells(rows[0])
    prefixes = (*((index, text) for index, text, _, _ in COUNT_FIELDS),
                (13, 'Accreditation received by Hospital'), (23, 'Hospital web address'))
    if len(headers) != 24 or not all(headers[index].startswith(text) for index, text in prefixes):
        raise ValueError('The IFFCO disclosure detail columns have changed')
    for number, row in enumerate(rows[1:], start=2):
        values = cells(row)
        if len(values) != len(headers):
            raise ValueError(f'Unexpected IFFCO disclosure table width at row {number}')
        name, rohini, address, reported_on = values[:4]
        if not name:
            continue
        source_date = datetime.strptime(reported_on, '%m/%d/%Y').date().isoformat()
        pin, city, state = address_keys(address)
        source = {
            'source': 'IFFCO Tokio', 'source_url': SOURCE_URL + '#:~:text=' + quote(name, safe=''),
            'source_date': source_date, 'source_record_id': f'{rohini}:{number}', 'name': name,
            'state': state, 'city': city, 'pin': pin, 'address': address,
        }
        candidates = [(index, field, scope, positive_integer(values[index]))
                      for index, _, field, scope in COUNT_FIELDS]
        candidates.extend([
            (13, 'accreditation', 'historical_insurer_disclosure', accreditation_lead(values[13])),
            (23, 'website', 'hospital_website_reported_by_insurer', website_lead(values[23])),
        ])
        for index, field, scope, value in candidates:
            if value:
                yield {**source, 'field': field, 'value': value, 'scope': scope,
                       'excerpt': f'{headers[index]} | {values[index]}'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html', type=Path, help='Saved IFFCO Tokio HTML disclosure')
    parser.add_argument('output', type=Path, help='Destination CSV for attributed source claims')
    args = parser.parse_args()
    rows = list(source_rows(args.html.read_bytes()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    for field, count in sorted(Counter(row['field'] for row in rows).items()):
        print(f'{field}: {count:,}')


if __name__ == '__main__':
    main()
