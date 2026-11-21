#!/usr/bin/env python3
"""Collect public eRaktKosh blood-centre directory data; record HFR access limits.

BeautifulSoup reads the public landing page and locates its app asset. The
directory is a React app, so facility rows come from the same public JSON
endpoints used by its state-search UI. No login, credentials, CAPTCHA or browser
cookies are used. Stop on HTTP access denials. API results are directory claims,
not independently verified clinical service or real-time stock availability.
"""
import argparse
import csv
import hashlib
import json
import math
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.eraktkosh.mohfw.gov.in/eraktkoshPortal/"
UI = BASE + "#/publicPages/bloodBankDirectory"
HFR = "https://facility.abdm.gov.in/"


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def bounded_request(session, method, url, *, deadline=45, **kwargs):
    """Bound total transfer time as well as requests' per-read timeout on POSIX."""
    if not hasattr(signal, "SIGALRM"):
        return session.request(method, url, timeout=(10, 20), **kwargs)
    def alarm(_signum, _frame):
        raise requests.Timeout("Whole-request deadline exceeded (" + str(deadline) + " seconds)")
    previous = signal.signal(signal.SIGALRM, alarm)
    signal.alarm(deadline)
    try:
        return session.request(method, url, timeout=(10, 20), **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def coordinate_quality(latitude, longitude):
    if latitude is None or longitude is None or latitude == "" or longitude == "":
        return "missing"
    try:
        lat, lon = float(latitude), float(longitude)
    except (ValueError, TypeError):
        return "non_numeric"
    if not math.isfinite(lat) or not math.isfinite(lon):
        return "non_finite"
    if lat == 0 or lon == 0:
        return "zero_placeholder"
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return "out_of_range"
    return "plausible_unverified"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", action="append", help="Optional exact state name; default all UI states")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--skip-hfr", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Reuse prior successful raw responses and retry remaining states")
    args = parser.parse_args()
    output = ROOT / "data/external/eraktkosh"
    output.mkdir(parents=True, exist_ok=True)
    raw = output / "raw"
    raw.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "DatathonPublicDirectoryResearch/1.0 (requests; no authentication)"
    manifest = {"source": "eRaktKosh", "source_url": UI, "started_at_utc": now(),
                "method": "BeautifulSoup HTML discovery + public directory JSON used by the page",
                "requests": [], "records": 0, "complete": False,
                "identifier_namespaces": {"state_code": "eRaktKosh UI state identifier, not assumed to be LGD",
                                          "district_code": "eRaktKosh response distId; crosswalk not validated",
                                          "hospital_code": "eRaktKosh hospitalCode"},
                "meaning": "Participating blood banks and BSUs; not blood-stock data or clinical verification"}
    previous = {}
    if args.resume and (output / "manifest.json").exists():
        previous = json.loads((output / "manifest.json").read_text())
        manifest["requests"] = previous.get("requests", []).copy()
        manifest["original_started_at_utc"] = previous.get("original_started_at_utc", previous.get("started_at_utc"))
        manifest["resumed_at_utc"] = now()
    cached_traces = {r.get("raw_file"): r for r in manifest["requests"] if r.get("http_status") == 200}

    def fetch(url, filename, method="GET", **kwargs):
        time.sleep(max(args.delay, 0))
        item = {"url": url, "method": method, "fetched_at_utc": now()}
        try:
            response = bounded_request(session, method, url, **kwargs)
            item.update({"url": response.url, "http_status": response.status_code,
                         "bytes": len(response.content), "sha256": hashlib.sha256(response.content).hexdigest(),
                         "raw_file": str(filename.relative_to(output))})
            filename.write_bytes(response.content)
            manifest["requests"].append(item)
            response.raise_for_status()
            return response, item
        except requests.RequestException as error:
            item["error"] = str(error)
            if item not in manifest["requests"]:
                manifest["requests"].append(item)
            save_json(output / "manifest.json", manifest)
            raise

    if not args.skip_hfr:
        directory = ROOT / "data/external/hfr"
        directory.mkdir(parents=True, exist_ok=True)
        attempt = {"source_url": HFR, "fetched_at_utc": now(), "records": 0}
        try:
            response = session.get(HFR, timeout=30)
            (directory / "landing.html").write_bytes(response.content)
            soup = BeautifulSoup(response.text, "html.parser")
            attempt.update({"http_status": response.status_code, "title": soup.title.get_text() if soup.title else None,
                            "status": "blocked_no_bypass" if response.status_code in (401,403) else "landing_only_no_directory_rows_extracted"})
        except requests.RequestException as error:
            attempt.update({"status": "request_failed", "error": str(error)})
        save_json(directory / "access_attempt.json", attempt)

    robots_url = "https://www.eraktkosh.mohfw.gov.in/robots.txt"
    robots_response = bounded_request(session, "GET", robots_url)
    (raw / "robots.txt").write_bytes(robots_response.content)
    manifest["robots"] = {"url": robots_url, "http_status": robots_response.status_code, "fetched_at_utc": now()}
    if robots_response.status_code == 200:
        robots = RobotFileParser()
        robots.parse(robots_response.text.splitlines())
        paths = [BASE, BASE + "eraktkosh/master/all", BASE + "eraktkosh/bloodbank/nearest"]
        if not all(robots.can_fetch(session.headers["User-Agent"], u) for u in paths):
            manifest["error"] = "Public crawl rules disallow one of the required paths."
            save_json(output / "manifest.json", manifest)
            return
    elif robots_response.status_code not in (404,410):
        manifest["error"] = "Unable to establish crawl rules: HTTP " + str(robots_response.status_code)
        save_json(output / "manifest.json", manifest)
        return

    if args.resume and previous.get("endpoint_discovery") and "raw/master.json" in cached_traces:
        manifest["endpoint_discovery"] = previous["endpoint_discovery"]
        master_data = json.loads((raw / "master.json").read_text())
    else:
        response, _ = fetch(BASE, raw / "landing.html")
        soup = BeautifulSoup(response.text, "html.parser")
        script_urls = [urljoin(BASE, tag["src"]) for tag in soup.select("script[src]")]
        main_url = next((u for u in script_urls if "/static/js/main." in u), None)
        if not main_url:
            raise RuntimeError("Public app asset not found; inspect changed page instead of guessing endpoints")
        # Inspect only published application code to confirm the public lookup paths.
        asset = session.get(main_url, timeout=60)
        asset.raise_for_status()
        required_paths = ["/eraktkosh/master/all", "/eraktkosh/bloodbank/nearest", "/publicPages/bloodBankDirectory"]
        if not all(p in asset.text for p in required_paths):
            raise RuntimeError("Expected public directory routes changed; no endpoints called")
        manifest["endpoint_discovery"] = {"asset_url": main_url, "sha256": hashlib.sha256(asset.content).hexdigest(),
                                          "public_paths": required_paths}
        master, _ = fetch(BASE + "eraktkosh/master/all", raw / "master.json", method="POST", json={"hospitalCode": 100})
        master_data = master.json()
    states = master_data["statesWithDistricts"]
    if args.state:
        requested = set(args.state)
        states = [state for state in states if state["stateName"] in requested]
        if {s["stateName"] for s in states} != requested:
            raise ValueError("State names must exactly match names returned by the UI master-data endpoint")
    manifest["scope"] = {"state_count": len(states), "states": [s["stateName"] for s in states],
                         "coverage": "all state options returned by public UI" if not args.state else "selected states"}
    records = []
    manifest["state_results"] = []
    manifest["failed_states"] = []
    for state in states:
        state_code = str(state["stateCode"])
        try:
            raw_file = raw / ("state_" + state_code + ".json")
            cache_key = str(raw_file.relative_to(output))
            if args.resume and cache_key in cached_traces and raw_file.exists():
                trace = cached_traces[cache_key]
                if hashlib.sha256(raw_file.read_bytes()).hexdigest() != trace["sha256"]:
                    raise ValueError("Cached raw response checksum mismatch")
                entries = json.loads(raw_file.read_text())
            else:
                for attempt in range(2):
                    try:
                        response, trace = fetch(BASE + "eraktkosh/bloodbank/nearest", raw_file,
                                               params={"stateCode": state_code, "districtCode": -1})
                        entries = response.json()
                        break
                    except requests.RequestException as error:
                        denied = error.response is not None and error.response.status_code in (401, 403, 429)
                        if denied or attempt == 1:
                            raise
                        time.sleep(2)
            if not isinstance(entries, list):
                raise ValueError("Expected a JSON array of directory entries")
            for index, entry in enumerate(entries):
                quality = coordinate_quality(entry.get("latitude"), entry.get("longitude"))
                records.append({"source": "eraktkosh", "state_code": state_code, "state_name": state["stateName"],
                                "source_row_index": index, "hospital_code": entry.get("hospitalCode"),
                                "facility_name": entry.get("name"), "address": entry.get("address"),
                                "category": entry.get("hospitalType"), "facility_type": entry.get("type"),
                                "district_code": entry.get("distId"), "phone": entry.get("phone"), "email": entry.get("email"),
                                "latitude": entry.get("latitude"), "longitude": entry.get("longitude"),
                                "coordinate_valid": quality == "plausible_unverified", "coordinate_quality": quality,
                                "location_verified": False,
                                "source_url": UI, "data_url": trace["url"], "fetched_at_utc": trace["fetched_at_utc"],
                                "raw_file": trace["raw_file"]})
            manifest["state_results"].append({"state_code": state_code, "state_name": state["stateName"], "records": len(entries),
                                              "distinct_hospital_codes": len({e.get("hospitalCode") for e in entries}),
                                              "success": True, "raw_file": cache_key, "fetched_at_utc": trace["fetched_at_utc"]})
            print(state["stateName"], len(entries), flush=True)
        except (requests.RequestException, ValueError) as error:
            failure = {"state_code": state_code, "state_name": state["stateName"], "success": False, "error": str(error)}
            manifest["failed_states"].append(failure)
            manifest["state_results"].append(failure)
            print("FAILED", state["stateName"], str(error), flush=True)
            if isinstance(error, requests.RequestException) and error.response is not None and error.response.status_code in (401,403,429):
                manifest["error"] = "Access denied or rate-limited; remaining requests stopped."
                break
        manifest["records"] = len(records)
        save_json(output / "manifest.json", manifest)
    (output / "facilities.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records))
    if records:
        with (output / "facilities.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    complete_states = sum(s["success"] for s in manifest["state_results"])
    manifest.update({"records": len(records), "complete": complete_states == len(states),
                     "states_expected": len(states), "states_completed": complete_states,
                     "states_failed": len(manifest["failed_states"]), "finished_at_utc": now(),
                     "coordinate_quality_counts": {q: sum(r["coordinate_quality"] == q for r in records)
                                                   for q in sorted({r["coordinate_quality"] for r in records})},
                     "coordinate_valid_definition": "Numeric, finite, nonzero latitude/longitude within world coordinate ranges only; not verified against addresses or geography",
                     "completeness_note": "Completeness means every state option returned an array. No independent national facility total is exposed by these responses; UI counts require browser verification."})
    save_json(output / "manifest.json", manifest)
    print(json.dumps({"records": len(records), "complete": manifest["complete"]}))


if __name__ == "__main__":
    main()
