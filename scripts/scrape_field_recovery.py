"""Snapshot the exact public pages in the field-recovery pilot manifest."""

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import requests


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'data/recovery/public_page_manifest.json'
MAX_BYTES = 512 * 1024
MAX_SECONDS = 20
SOURCE_KEYS = ('document_id', 'facility_name', 'pin', 'address_contains', 'city',
               'publisher', 'publisher_family', 'source_url', 'publication_scope')


def write_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        tempfile = Path(handle.name)
        try:
            handle.write(payload)
            handle.close()
            tempfile.replace(path)
        finally:
            tempfile.unlink(missing_ok=True)


def write_json(path, payload):
    write_bytes(path, (json.dumps(payload, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))


def fetch(url):
    """Fetch exactly one known HTTPS HTML page and preserve its bytes."""
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f'Expected a public HTTPS URL in the pilot manifest: {url!r}')
    start = monotonic()
    with requests.get(url, stream=True, timeout=(5, 8), allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200 or 'text/html' not in response.headers.get('Content-Type', '').lower():
            raise ValueError(f'Expected a direct HTTP 200 HTML page: {url}')
        result = bytearray()
        for chunk in response.iter_content(chunk_size=16 * 1024):
            result.extend(chunk)
            if len(result) > MAX_BYTES or monotonic() - start > MAX_SECONDS:
                raise ValueError(f'Public page exceeded the pilot size or time bound: {url}')
    if not result:
        raise ValueError(f'Empty response from {url}')
    return bytes(result)


def read_snapshot(entry, directory, retrieved_on):
    page_path = directory / f"{entry['document_id']}.html"
    metadata_path = page_path.with_suffix('.json')
    if retrieved_on:
        html = fetch(entry['source_url'])
        metadata = {'source_url': entry['source_url'], 'retrieved_on': retrieved_on,
                    'html_sha256': sha256(html).hexdigest(), 'html_bytes': len(html)}
        # A challenge page or changed selector should never replace a usable cache.
        parse(entry, html, metadata)
        write_bytes(page_path, html)
        write_json(metadata_path, metadata)
    else:
        if page_path.stat().st_size > MAX_BYTES:
            raise ValueError(f'Cached HTML exceeded the pilot size bound: {page_path}')
        html = page_path.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if (metadata['source_url'] != entry['source_url'] or metadata['html_sha256'] != sha256(html).hexdigest()
                or metadata['html_bytes'] != len(html)):
            raise ValueError(f'Cached HTML changed or no longer belongs to the declared source: {page_path}')
        date.fromisoformat(metadata['retrieved_on'])
    return html, metadata


def select_excerpt(soup, rule, source_url):
    selector, needle = rule['selector'], rule['contains']
    # Only normalize text-node whitespace. Keep punctuation and publication wording.
    matches = [' '.join(node.get_text(' ', strip=True).split()) for node in soup.select(selector)]
    matches = [text for text in matches if needle in text]
    if len(matches) != 1:
        raise ValueError(f'{source_url}: expected one {selector!r} match containing {needle!r}; found {len(matches)}.')
    text = matches[0]
    start, end = rule.get('from'), rule.get('through')
    if start:
        offset = text.find(start)
        if offset < 0:
            raise ValueError(f'{source_url}: start marker {start!r} missing from {selector!r}.')
        text = text[offset:]
    if end:
        offset = text.find(end)
        if offset < 0:
            raise ValueError(f'{source_url}: end marker {end!r} missing from {selector!r}.')
        text = text[:offset + len(end)]
    if needle not in text:
        raise ValueError(f'{source_url}: passage clipping removed {needle!r}.')
    return text


def parse(entry, html, metadata):
    soup = BeautifulSoup(html, 'html.parser')
    anchor = '\n'.join(select_excerpt(soup, rule, entry['source_url']) for rule in entry['anchor'])
    values = [select_excerpt(soup, rule, entry['source_url']) for rule in entry['passages']]
    # A reviewed single-branch page can preserve its identity block beside the claims.
    # Blank lines separate the exact source fragments and keep span offsets reproducible.
    passages = [anchor + '\n\n' + '\n'.join(values)] if entry.get('join_with_anchor') else [anchor, *values]
    return {**{key: entry[key] for key in SOURCE_KEYS if key in entry},
            'retrieved_on': metadata['retrieved_on'], 'branch_anchor': anchor,
            'passages': [{'text': text} for text in dict.fromkeys(passages)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, required=True, help='Private directory for raw HTML and pinned hashes.')
    parser.add_argument('--documents', type=Path, required=True, help='Destination for the selected recovery documents JSON.')
    parser.add_argument('--audit', type=Path, required=True, help='Destination for the source hash and clipping audit.')
    parser.add_argument('--fetch', action='store_true', help='Fetch the fixed HTTPS URLs. Default reads the existing cache.')
    parser.add_argument('--retrieved-on', help='ISO date of the current fetch. Required only with --fetch.')
    args = parser.parse_args()
    if args.fetch != bool(args.retrieved_on):
        parser.error('Use --fetch and --retrieved-on together when retrieving a fresh snapshot.')
    if args.documents.resolve() == args.audit.resolve():
        parser.error('Choose separate output paths for documents and the audit.')
    retrieved_on = date.fromisoformat(args.retrieved_on).isoformat() if args.fetch else None
    sources = json.loads(MANIFEST.read_text(encoding='utf-8'))
    documents, audit = [], []
    for entry in sources:
        html, metadata = read_snapshot(entry, args.cache_dir, retrieved_on)
        documents.append(parse(entry, html, metadata))
        audit.append({'document_id': entry['document_id'], **metadata,
                      'join_with_anchor': entry.get('join_with_anchor', False),
                      'anchor_selection': entry['anchor'], 'passage_selection': entry['passages']})
    write_json(args.documents, documents)
    write_json(args.audit, audit)
    print(f'Prepared {len(documents)} fixed public page snapshots. Raw HTML stays in {args.cache_dir}.')


if __name__ == '__main__':
    main()
