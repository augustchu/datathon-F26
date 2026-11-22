#!/usr/bin/env python3
"""Extract facility establishment-year claims from Puducherry's public statistics."""

import argparse
import csv
import re
from pathlib import Path
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup


BASE_URL = "https://health.py.gov.in/"
STATISTICS_URL = urljoin(BASE_URL, "statistics-glance")
HOSPITALS_URL = urljoin(BASE_URL, "hospitals")
OFFICIAL_DIRECTORY_NAMES = {
    "GH, Pondy": "Indira Gandhi Govt. General Hospital & Post Graduate Institute",
    "GHCD, Gorimedu": "Govt. Hospital for Chest Diseases",
    "ESI Hospital, Gorimedu": "Employees State Insurance Hospital",
    "MGGL Hospital": "Mahatma Gandhi Leprosy Hospital",
    "GH, Karaikal": "General Hospital, Karaikal",
    "GH, Mahe": "General Hospital, Mahe",
    "GH, Yanam": "General Hospital, Yanam",
}
CHC_NAMES = {"Karikalampakkam", "Mannadipet", "Thirunallar", "Palloor"}


def _rows(table):
    return [
        [" ".join(cell.get_text(" ", strip=True).split()) for cell in tr.find_all(["th", "td"], recursive=False)]
        for tr in table.find_all("tr")
    ]


def _hospital_links(directory_path: Path) -> dict[str, str]:
    document = BeautifulSoup(directory_path.read_text(encoding="utf-8-sig"), "html.parser")
    links = {}
    for table in document.find_all("table"):
        for link in table.select("td a[href]"):
            name = " ".join(link.get_text(" ", strip=True).split())
            links[name] = urljoin(HOSPITALS_URL, link["href"])
    return links


def extract_years(statistics_path: Path, directory_path: Path) -> list[dict]:
    document = BeautifulSoup(statistics_path.read_text(encoding="utf-8-sig"), "html.parser")
    hospital_links = _hospital_links(directory_path)
    if missing_links := set(OFFICIAL_DIRECTORY_NAMES.values()).difference(hospital_links):
        raise ValueError(f"Missing hospital names in official directory: {sorted(missing_links)}")

    records = []
    for table_number, table in enumerate(document.find_all("table"), start=1):
        rows = _rows(table)
        year_rows = [row[1:] for row in rows if row and row[0] == "Year Established"]
        if not year_rows:
            continue
        names = rows[0][1:]
        years, = year_rows
        if rows[0][0] != "Institution" or len(names) != len(years):
            raise ValueError(f"Misaligned institution and year columns in table {table_number}")

        is_hospital_table = names[0] == "GH, Pondy"
        is_chc_table = set(names) == CHC_NAMES
        for column, (published_name, year) in enumerate(zip(names, years, strict=True), start=1):
            if not re.fullmatch(r"(?:18|19|20)\d{2}", year):
                raise ValueError(f"Unexpected establishment year for {published_name}: {year}")

            if is_hospital_table:
                name = OFFICIAL_DIRECTORY_NAMES.get(published_name, published_name)
                city = published_name.split(",", 1)[-1].strip() if "," in published_name else ""
                if city in {"Pondy", "Gorimedu"}:
                    city = "Puducherry"
                scope = "published_hospital_institution"
            else:
                kind = "Community Health Centre" if is_chc_table else "Primary Health Centre"
                name = f"{kind} {published_name}"
                city = published_name
                scope = "published_health_centre_institution"

            records.append({
                "source": "Puducherry DHFWS Statistics - A Glance",
                "source_url": f"{STATISTICS_URL}#:~:text={quote(published_name, safe='')}",
                "source_date": "",
                "source_record_id": f"table{table_number}:institution{column}",
                "name": name,
                "state": "Puducherry",
                "city": city,
                "pin": "",
                "address": "",
                "field": "yearEstablished",
                "value": year,
                "scope": scope,
                "excerpt": f"Institution | {published_name} ; Year Established | {year}",
                "identity_url": hospital_links.get(name, ""),
            })

    if len(records) != 52:
        raise ValueError(f"Expected 52 establishment-year claims in the published snapshot, got {len(records)}")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--statistics", type=Path, required=True)
    parser.add_argument("--hospital-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = extract_years(args.statistics, args.hospital_directory)
    with args.output.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


if __name__ == "__main__":
    main()
