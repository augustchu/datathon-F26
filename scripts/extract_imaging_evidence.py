#!/usr/bin/env python3
"""Extract dated or scope-qualified imaging claims from saved official HTML."""

import argparse
import csv
import re
from pathlib import Path

from bs4 import BeautifulSoup


DMER_URL = "https://dmer.maharashtra.gov.in/english/list-of-hospitals-with-ct-mri-facilities/"
NHM_URL = "https://nhm.maharashtra.gov.in/en/scheme/iphs-2/"
PPP_CAPTION = "CT Scan service on PPP basis at SDH."
CT_CENTER_CAPTIONS = (
    "Government Owned CT Scan Centers (Operational Partner) at SDH & GH.",
    "Government Owned CT Scan Centers (Operational Partner) at DH.",
)
COMMENCED_SERVICES = re.compile(r"commenced services at (\d+) SDH health facilities")
OUTPUT_COLUMNS = (
    "source", "source_url", "source_record_id", "source_date", "name", "state", "city", "pin",
    "address", "field", "value", "scope", "excerpt",
)


def cell_text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def table_rows(table):
    for row in table.select("tr"):
        yield [cell_text(cell) for cell in row.find_all(["th", "td"], recursive=False)]


def extract_dmer(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table#hospitalTable")
    if table is None:
        raise ValueError("The DMER hospital imaging table is missing")
    rows = iter(table_rows(table))
    labels = next(rows)
    if labels != ["Hospital Name", "CT Scan Facility", "MRI Scan"]:
        raise ValueError("Unexpected DMER imaging column headings")

    for row_number, (name, ct, mri) in enumerate(rows, 1):
        city = name.rpartition(",")[2].strip() if "," in name else ""
        group = bool(re.search(r"\bgroup\b", name, re.IGNORECASE))
        for label, field, count in zip(labels[1:], ("ct_scanners", "mri_scanners"), (ct, mri), strict=True):
            if not count.isdecimal():
                raise ValueError(f"Invalid DMER count on row {row_number}: {count!r}")
            if int(count) == 0:
                continue
            yield {
                "source": "maharashtra_dmer",
                "source_url": f"{DMER_URL}#hospitalTable",
                "source_record_id": f"dmer-{row_number}-{field}",
                "name": name,
                "city": city,
                "field": field,
                "value": count,
                "scope": "hospital group imaging inventory" if group else "hospital imaging inventory",
                "excerpt": f"{name} | {label} | {count}",
            }


def extract_nhm(html):
    soup = BeautifulSoup(html, "html.parser")
    commencement = next(
        (text for element in soup.select("li") if COMMENCED_SERVICES.search(text := cell_text(element))),
        "",
    )
    count_match = COMMENCED_SERVICES.search(commencement)
    captions = (PPP_CAPTION, *CT_CENTER_CAPTIONS)
    seen_captions = set()
    for table in soup.select("table"):
        if table.caption is None or (caption := cell_text(table.caption)) not in captions:
            continue
        if caption in seen_captions:
            raise ValueError(f"Duplicate NHM imaging table {caption}")
        seen_captions.add(caption)
        rows = list(table_rows(table))[1:]
        is_service = caption == PPP_CAPTION
        if is_service and (count_match is None or len(rows) != int(count_match.group(1))):
            raise ValueError("The named NHM CT service rows do not reconcile with the commencement statement")

        for serial, name, *_ in rows:
            city = re.sub(r"^(Sub District|District|General) Hospital[ ,]*", "", name).rstrip(".")
            excerpt = f"{caption} | {serial} | {name}"
            if is_service:
                excerpt = f"{excerpt} | {commencement}"
            yield {
                "source": "maharashtra_nhm",
                "source_url": NHM_URL,
                "source_record_id": f"nhm-ct-{captions.index(caption) + 1}-{serial.rstrip('.')}",
                "name": name,
                "city": city,
                "field": "service_ct" if is_service else "ct_center_listed",
                "value": "true",
                "scope": "public-private CT service listed as commenced" if is_service else "government-owned CT center listing",
                "excerpt": excerpt,
            }

    if seen_captions != set(captions):
        raise ValueError("One or more NHM imaging tables are missing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dmer-html", type=Path, required=True)
    parser.add_argument("--nhm-html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    claims = [
        *extract_dmer(args.dmer_html.read_text(encoding="utf-8")),
        *extract_nhm(args.nhm_html.read_text(encoding="utf-8")),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows({"state": "Maharashtra", **claim} for claim in claims)
    print(f"Wrote {len(claims)} imaging claims to {args.output}")


if __name__ == "__main__":
    main()
