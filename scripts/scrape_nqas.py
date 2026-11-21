"""Scrape public NHSRC NQAS tables, following only published pagination links.

Run: python3 scripts/scrape_nqas.py [--max-pages 221] [--workers 3]
Raw responses, row provenance, and a coverage manifest are preserved.
"""
import argparse
import csv
import gzip
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://qps.nhsrcindia.org/quality-certification/national-level-certificate"
ROOT = Path(__file__).resolve().parents[1] / "data/external/nqas"
KEYS = {"Sr No.": "source_serial", "Facility Name": "facility_name",
        "Certified": "certification_status", "Validity Till Date": "validity_date_raw",
        "No. of department assessed": "departments_assessed"}


def now():
    return datetime.now(timezone.utc).isoformat()


def page_number(url):
    return int(parse_qs(urlparse(url).query).get("page", [",0"])[0].split(",")[-1] or 0)


def fetch(url):
    started = now()
    try:
        response = requests.get(url, timeout=(15, 60), headers={"User-Agent": "DatathonResearch/1.0 (public facility data validation)"})
        # Do not work around access denials; record them as gaps.
        response.raise_for_status()
        raw = response.content
        soup = BeautifulSoup(raw, "html.parser")
        page = page_number(url)
        raw_path = ROOT / "raw" / f"page_{page:04d}.html.gz"
        raw_path.write_bytes(gzip.compress(raw))
        records = []
        for table in soup.select("table"):
            caption = table.find("caption")
            state = caption.get_text(" ", strip=True) if caption else ""
            headers = [th.get_text(" ", strip=True) for th in table.select("thead th")]
            if "Facility Name" not in headers:
                continue
            for tr in table.select("tbody tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td", recursive=False)]
                if len(cells) != len(headers):
                    raise ValueError(f"Column mismatch on page {page}: {len(headers)} vs {len(cells)}")
                fields = dict(zip(headers, cells))
                record = {KEYS.get(key, key): value for key, value in fields.items()}
                record.update(state=state, source_url=response.url, source_page=page,
                              scraped_at_utc=started, source="NHSRC NQAS",
                              source_fields=fields, raw_snapshot=str(raw_path.relative_to(ROOT)))
                record["record_id"] = hashlib.sha256(json.dumps([state, fields], sort_keys=True).encode()).hexdigest()[:20]
                record["validity_date_iso"] = ""
                record["date_parse_error"] = False
                if record.get("validity_date_raw"):
                    try:
                        record["validity_date_iso"] = datetime.strptime(record["validity_date_raw"], "%d/%m/%Y").date().isoformat()
                    except ValueError:
                        record["date_parse_error"] = True
                records.append(record)
        text = soup.get_text(" ", strip=True)
        total = re.search(r"Total Number of facility\s*:\s*([\d,]+)", text)
        updated = re.search(r"Updated till-\s*:\s*([\d-]+)", text)
        links = set()
        for anchor in soup.select("nav.pager a[href]"):
            target = urljoin(response.url, anchor["href"])
            if urlparse(target).netloc == urlparse(BASE).netloc and urlparse(target).path == urlparse(BASE).path:
                links.add(target)
        return {"url": response.url, "page": page, "fetched_at_utc": started,
                "status": response.status_code, "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "records": records, "links": sorted(links),
                "advertised_count": int(total.group(1).replace(",", "")) if total else None,
                "source_updated_through": updated.group(1) if updated else None}
    except Exception as error:
        return {"url": url, "page": page_number(url), "fetched_at_utc": started, "error": str(error)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=221)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--resume", action="store_true", help="Reuse saved successful pages and retry failed requests")
    args = parser.parse_args()
    if args.max_pages < 1 or not 1 <= args.workers <= 3:
        parser.error("max-pages must be positive and workers between 1 and 3")
    (ROOT / "raw").mkdir(parents=True, exist_ok=True)
    pending, visited, results = {0: BASE}, set(), []
    started = now()
    previous_failures = []
    if args.resume:
        previous = json.loads((ROOT / "manifest.json").read_text())
        if previous.get("pages_remaining_discovered"):
            raise RuntimeError("Resume currently supports retrying a completed traversal only")
        grouped = {}
        for line in (ROOT / "facilities.jsonl").read_text().splitlines():
            row = json.loads(line)
            grouped.setdefault(row["source_page"], []).append(row)
        pending = {}
        previous_failures = previous.get("failures", [])
        for result in previous["pages"]:
            if "error" in result:
                # Never retry an authentication denial or rate-limit response.
                if any(code in result["error"] for code in ["401 Client", "403 Client", "429 Client"]):
                    results.append(result)
                    visited.add(result["page"])
                else:
                    pending[result["page"]] = result["url"]
            else:
                snapshot = ROOT / "raw" / f"page_{result['page']:04d}.html.gz"
                if hashlib.sha256(gzip.decompress(snapshot.read_bytes())).hexdigest() != result["raw_sha256"]:
                    raise RuntimeError(f"Snapshot hash mismatch: {snapshot}")
                result["records"] = grouped.get(result["page"], [])
                results.append(result)
                visited.add(result["page"])
    robots = {"url": "https://qps.nhsrcindia.org/robots.txt", "checked_at_utc": now()}
    try:
        response = requests.get(robots["url"], timeout=(15, 30))
        robots["http_status"] = response.status_code
        (ROOT / "robots.txt").write_bytes(response.content)
        if response.status_code == 200:
            from urllib.robotparser import RobotFileParser
            policy = RobotFileParser()
            policy.parse(response.text.splitlines())
            if not policy.can_fetch("DatathonResearch", BASE):
                raise RuntimeError("robots.txt disallows this directory")
    except requests.RequestException as error:
        robots["error"] = str(error)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while pending and len(visited) < args.max_pages:
            pages = sorted(pending)[:min(args.workers, args.max_pages - len(visited))]
            urls = [pending.pop(page) for page in pages]
            visited.update(pages)
            batch = list(pool.map(fetch, urls))
            results.extend(batch)
            for result in batch:
                for url in result.get("links", []):
                    page = page_number(url)
                    if page not in visited:
                        pending[page] = url
            print(json.dumps({"pages_attempted": len(visited), "rows": sum(len(r.get("records", [])) for r in results), "errors": sum("error" in r for r in results)}), flush=True)
            # Low bounded concurrency, with a pause between batches.
            time.sleep(0.7)
    results.sort(key=lambda r: r["page"])
    records = [row for result in results for row in result.get("records", [])]
    (ROOT / "facilities.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
    columns = ["record_id", "source_serial", "facility_name", "state", "certification_status", "departments_assessed", "validity_date_raw", "validity_date_iso", "date_parse_error", "source", "source_page", "source_url", "scraped_at_utc", "raw_snapshot", "source_fields"]
    with (ROOT / "facilities.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow({**row, "source_fields": json.dumps(row["source_fields"], ensure_ascii=False)})
    totals = sorted({r["advertised_count"] for r in results if r.get("advertised_count") is not None})
    unique_ids = len({row["record_id"] for row in records})
    manifest = {"source_url": BASE, "started_at_utc": started, "finished_at_utc": now(),
                "source_updated_through": sorted({r["source_updated_through"] for r in results if r.get("source_updated_through")}),
                "advertised_counts": totals, "rows": len(records), "unique_record_ids": unique_ids,
                "page_limit": args.max_pages, "pages_fetched": sum("error" not in r for r in results),
                "pages_remaining_discovered": sorted(pending), "failures": [r for r in results if "error" in r],
                "coverage": "complete_displayed_listing" if not pending and not any("error" in r for r in results) and totals == [len(records)] else "partial_or_count_mismatch",
                "robots_check": robots,
                "previous_transient_failures": previous_failures,
                "identity_note": "unique_record_ids counts unique source rows including serial number, not distinct real-world facilities",
                "interpretation": "Directory assertions only. Certification validity and status are preserved without inferring current clinical service availability.",
                "pages": [{k: v for k, v in r.items() if k not in ("records", "links")} for r in results]}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k:v for k,v in manifest.items() if k != "pages"}, indent=2))


if __name__ == "__main__":
    main()
