#!/usr/bin/env python3
"""Export a bounded, explicitly partial NABH public-directory extract.

The directory's own public JavaScript calls get_hospitals and returns HTML.
BeautifulSoup parses that returned HTML; no authentication or CAPTCHA is used.
Default: 20 evenly spaced result pages. --all-pages enables an explicit full run.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

DIRECTORY_URL = "https://nabh.co/find-a-healthcare-organisation/"
AJAX_URL = "https://nabh.co/wp-admin/admin-ajax.php"
USER_AGENT = "DatathonResearch/1.0 (public facility directory research; respectful rate limit)"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def certificate_link(columns: dict) -> tuple[str, bool]:
    """Return a real public link, rejecting anchors and other placeholders."""
    directory = urlsplit(DIRECTORY_URL)
    for column in (columns[1], columns[4]):
        for anchor in column.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith("#"):
                continue
            url = urljoin(DIRECTORY_URL, href)
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            if (parsed.netloc, parsed.path.rstrip("/")) == (directory.netloc, directory.path.rstrip("/")):
                continue
            query_keys = {key.lower() for key, _ in parse_qsl(parsed.query)}
            temporary = bool(query_keys & {
                "x-amz-signature", "x-goog-signature", "signature", "sig",
                "x-amz-expires", "expires", "awsaccesskeyid",
            })
            return url, temporary
    return "", False


def parse_page(payload: dict, page: int, fetched_at: str, snapshot: str) -> list[dict]:
    soup = BeautifulSoup(payload["html"], "html.parser")
    coordinates = {}
    for item in payload.get("mapData", []):
        key = (clean(item.get("name", "")), clean(item.get("address", "")))
        coordinates.setdefault(key, []).append(item)
    rows = []
    for position, card in enumerate(soup.select(".organisation-list"), 1):
        columns = {n: card.select_one(f".hs-col-{n}") for n in range(1, 6)}
        if any(value is None for value in columns.values()):
            raise ValueError(f"Missing expected listing columns: page {page}, row {position}")
        name = clean(columns[1].get_text(" ", strip=True))
        address = clean(columns[2].get_text(" ", strip=True))
        accreditation = clean(columns[4].get_text(" ", strip=True))
        status = clean(columns[5].get_text(" ", strip=True))
        if not name:
            raise ValueError(f"Empty facility name: page {page}, row {position}")
        certificate_url, certificate_temporary = certificate_link(columns)
        website = next((a["href"] for a in columns[3].find_all("a", href=True)
                        if a["href"].startswith(("http://", "https://"))), "")
        phone = next((a["href"][4:] for a in columns[3].find_all("a", href=True)
                      if a["href"].startswith("tel:")), "")
        matches = coordinates.get((name, address), [])
        coord = matches[0] if len(matches) == 1 else {}
        key = "|".join([name, address, accreditation])
        rows.append({
            "source_record_id": hashlib.sha256(key.encode()).hexdigest()[:24],
            "facility_name": name,
            "address": address,
            "accreditation_number": accreditation,
            "status_text": status,
            "certificate_url": certificate_url,
            "certificate_link_missing": not bool(certificate_url),
            "certificate_url_temporary": certificate_temporary,
            "website": website,
            "facility_phone": clean(phone),
            "latitude": coord.get("lat"),
            "longitude": coord.get("lng"),
            "coordinate_match": "exact_name_and_address" if coord else "missing_or_ambiguous",
            "source_url": DIRECTORY_URL,
            "source_endpoint": AJAX_URL,
            "source_page": page,
            "source_row_on_page": position,
            "retrieved_at_utc": fetched_at,
            "raw_snapshot": snapshot,
            "evidence_text": clean(card.get_text(" ", strip=True)),
        })
    return rows


def write_exports(out: Path, rows: list[dict], manifest: dict) -> None:
    manifest.update({"row_count": len(rows),
                     "unique_record_count": len({r["source_record_id"] for r in rows}),
                     "coordinate_count": sum(r["latitude"] is not None for r in rows),
                     "certificate_link_missing_count": sum(r["certificate_link_missing"] for r in rows),
                     "certificate_url_temporary_count": sum(r["certificate_url_temporary"] for r in rows),
                     "parser_version": 2})
    if rows:
        with (out / "facilities.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        with (out / "facilities.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows: handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


def rebuild_from_snapshots(out: Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text())
    previous = [json.loads(line) for line in (out / "facilities.jsonl").read_text().splitlines()]
    requests_by_snapshot = {request["snapshot"]: request for request in manifest["requests"]}
    rows = []
    for page in manifest["selected_pages"]:
        snapshot = f"raw/page-{page:04}.json"
        request = requests_by_snapshot[snapshot]
        body = (out / snapshot).read_bytes()
        if hashlib.sha256(body).hexdigest() != request["sha256"]:
            raise ValueError(f"Raw snapshot hash mismatch: {snapshot}")
        rows.extend(parse_page(json.loads(body), page, request["retrieved_at_utc"], snapshot))
    if [row["source_record_id"] for row in previous] != [row["source_record_id"] for row in rows]:
        raise ValueError("Offline reparse changed source identity/order; exports not overwritten")
    manifest["reprocessed_at_utc"] = now()
    limitation = "Missing or fragment-only certificate links are blank; signed URLs are flagged as temporary."
    if limitation not in manifest["limitations"]:
        manifest["limitations"].append(limitation)
    write_exports(out, rows, manifest)
    print(json.dumps({key: manifest[key] for key in ["row_count", "unique_record_count", "certificate_link_missing_count", "certificate_url_temporary_count"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data/external/nabh")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--all-pages", action="store_true")
    parser.add_argument("--from-snapshots", action="store_true", help="Reparse saved responses without network access")
    args = parser.parse_args()
    if args.max_pages < 1 or args.delay < 0.5:
        parser.error("max-pages must be >=1 and delay >=0.5 seconds")
    out = args.output_dir
    if args.from_snapshots:
        rebuild_from_snapshots(out)
        return
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    manifest = {"source": "NABH public healthcare organisation directory", "source_url": DIRECTORY_URL,
                "started_at_utc": now(), "status": "running", "requested_max_pages": args.max_pages,
                "selection_method": "evenly spaced pages across advertised result range",
                "requests": [], "errors": [], "limitations": [
                    "Partial directory extract unless complete_directory is true.",
                    "Listings may include accreditation, certification, empanelment, and renewal; statuses are preserved verbatim.",
                    "NABH warns that website migration can cause incomplete or inconsistent records.",
                    "Directory presence is not evidence of current service availability.",
                    "No certificate contents or service scope inferred; URLs are retained for further verification."]}
    rows = []

    def fetch(method: str, url: str, filename: str, **kwargs):
        response = session.request(method, url, timeout=45, **kwargs)
        stamp = now()
        path = raw / filename
        path.write_bytes(response.content)
        manifest["requests"].append({"method": method, "url": url, "request_data": kwargs.get("data"),
                                     "status_code": response.status_code, "retrieved_at_utc": stamp,
                                     "snapshot": str(path.relative_to(out)), "bytes": len(response.content),
                                     "sha256": hashlib.sha256(response.content).hexdigest()})
        response.raise_for_status()
        return response, stamp, str(path.relative_to(out))

    try:
        robots, _, _ = fetch("GET", "https://nabh.co/robots.txt", "robots.txt")
        rules = RobotFileParser()
        rules.parse(robots.text.splitlines())
        # urllib applies the first matching path; sort so the site's explicit
        # /wp-admin/admin-ajax.php Allow wins over the broader /wp-admin/ rule.
        # Most-specific path wins, with Allow winning equal-length ties.
        for entry in rules.entries + ([rules.default_entry] if rules.default_entry else []):
            entry.rulelines.sort(key=lambda rule: (len(rule.path), rule.allowance), reverse=True)
        if not all(rules.can_fetch(USER_AGENT, u) for u in (DIRECTORY_URL, AJAX_URL)):
            raise RuntimeError("robots.txt disallows a required URL; stopped")
        # Verify the read-only action still exists in the public directory form.
        response = session.get(DIRECTORY_URL, timeout=45)
        response.raise_for_status()
        form = BeautifulSoup(response.text, "html.parser").find("form", id="hospitalSearchForm")
        action = form.find("input", attrs={"name": "action"}) if form else None
        if not action or action.get("value") != "get_hospitals":
            raise RuntimeError("Public directory search form changed; refusing to guess endpoint inputs")
        data = {"action": "get_hospitals", "q": "", "lat": "", "long": "", "selectedSpecText": "", "page": 1}
        first, first_stamp, first_snapshot = fetch("POST", AJAX_URL, "page-0001.json", data=data)
        first_payload = first.json()
        total_pages = int(first_payload["pagination"]["total_pages"])
        total_results = int(first_payload["pagination"]["total_results"])
        n = min(args.max_pages, total_pages)
        pages = list(range(1, total_pages + 1)) if args.all_pages else sorted({1 + i * total_pages // n for i in range(n)})
        manifest.update({"advertised_total_pages": total_pages, "advertised_total_results": total_results,
                         "selected_pages": pages, "complete_directory": False})
        for page in pages:
            if page == 1:
                payload, stamp, snapshot = first_payload, first_stamp, first_snapshot
            else:
                time.sleep(args.delay)
                response, stamp, snapshot = fetch("POST", AJAX_URL, f"page-{page:04}.json", data={**data, "page": page})
                payload = response.json()
            if int(payload["pagination"]["current_page"]) != page:
                raise ValueError(f"Requested page {page} but received another page")
            parsed = parse_page(payload, page, stamp, snapshot)
            expected = min(25, total_results - (page - 1) * 25)
            if len(parsed) != expected:
                raise ValueError(f"Page {page}: parsed {len(parsed)} rows, expected {expected}")
            rows.extend(parsed)
            print(f"page {page}/{total_pages}: {len(parsed)} rows; total {len(rows)}", flush=True)
        manifest["status"] = "success"
        manifest["complete_directory"] = len(pages) == total_pages and len(rows) == total_results
    except (requests.RequestException, ValueError, KeyError, RuntimeError) as exc:
        manifest["status"] = "partial" if rows else "blocked"
        manifest["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        manifest["finished_at_utc"] = now()
        write_exports(out, rows, manifest)
        print(json.dumps({k: manifest[k] for k in ["status", "row_count", "unique_record_count"]}), flush=True)


if __name__ == "__main__":
    main()
