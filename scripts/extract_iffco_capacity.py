"""Extract dated hospital counts from a saved IFFCO Tokio quality table."""

import argparse
import csv
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import quote

from bs4 import BeautifulSoup


SOURCE_URL = 'https://www.iffcotokio.co.in/health-insurance/network-hospital-quality-parameters'
HEADERS = ('HOSPITAL NAME', 'Rohini Id.', 'Address', 'INFORMATION AS AT (Date)',
           'Total Bed strength of the Hospital', 'No. of Doctors')
FIELDS = ('source', 'source_url', 'source_date', 'source_period', 'source_record_id', 'name',
          'state', 'city', 'pin', 'address', 'beds', 'doctors', 'bed_scope',
          'table_row', 'rohini_id')
PIN = re.compile(r'\b[1-9][0-9]{5}\b')


def cells(row):
    return tuple(' '.join(cell.get_text(' ', strip=True).split())
                 for cell in row.find_all(['th', 'td'], recursive=False))


def positive_integer(value):
    token = value.replace(',', '')
    return int(token) if re.fullmatch(r'[1-9][0-9]*', token) else None


def address_keys(address):
    pins = set(PIN.findall(address))
    pin = pins.pop() if len(pins) == 1 else ''
    # State and locality are usable when the address publishes this specific layout.
    if pin:
        match = re.search(r',\s*([^,]+?)\s*-\s*' + pin + r'\s*,\s*([a-zA-Z ]+)$', address)
        if match:
            return pin, match[1].strip(), match[2].strip()
    return pin, '', ''


def parse(html):
    soup = BeautifulSoup(html, 'html.parser')
    tables = [table.select('tr') for table in soup.select('table')]
    tables = [rows for rows in tables if rows and cells(rows[0])[:len(HEADERS)] == HEADERS]
    if len(tables) != 1:
        raise ValueError(f'Expected one hospital disclosure table, found {len(tables)}')
    rows = tables[0]
    width = len(cells(rows[0]))
    records = []
    for number, row in enumerate(rows[1:], start=2):
        values = cells(row)
        if len(values) != width:
            raise ValueError(f'Unexpected disclosure table width at row {number}')
        name, rohini, address, reported_on, beds, doctors = values[:len(HEADERS)]
        beds, doctors = positive_integer(beds), positive_integer(doctors)
        if not name or not (beds or doctors):
            continue
        try:
            source_date = datetime.strptime(reported_on, '%m/%d/%Y').date().isoformat()
        except ValueError as exc:
            raise ValueError(f'Invalid reporting date on row {number}: {reported_on!r}') from exc
        pin, city, state = address_keys(address)
        records.append({'source': 'IFFCO Tokio', 'source_url': SOURCE_URL + '#:~:text=' + quote(name, safe=''),
                        'source_date': source_date, 'source_period': source_date, 'source_record_id': f'{rohini}:{number}',
                        'name': name, 'state': state, 'city': city, 'pin': pin, 'address': address,
                        'beds': beds, 'doctors': doctors, 'bed_scope': 'whole_facility',
                        'table_row': number, 'rohini_id': rohini})
    return len(rows) - 1, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html', type=Path, help='Saved provider disclosure page.')
    parser.add_argument('output', type=Path, help='Destination CSV for the branch matcher.')
    args = parser.parse_args()
    row_count, records = parse(args.html.read_bytes())
    with args.output.open('w', encoding='utf-8', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    print(f'{row_count} source rows, {len(records)} with a positive bed or doctor count.')


if __name__ == '__main__':
    main()
