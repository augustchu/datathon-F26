#!/usr/bin/env python3
"""Extract the Kerala DHS July 2026 facility-level sanctioned inpatient bed register."""

import argparse
import csv
import re
from pathlib import Path

import pdfplumber


SOURCE_URL = (
    "https://dhs.kerala.gov.in/wp-content/uploads/2026/08/"
    "Sanctioned_IP_Beds_DHS_Kerala_July_2026-final.pdf"
)
SOURCE_PERIOD = "2026-07"
EXPECTED_FACILITIES = 1289
EXPECTED_BEDS = 38525
HEADER = (
    "Sl. No.",
    "District",
    "Category",
    "Name of the Institution",
    "Complete Address With Pin Code",
    "Sanctioned Bed",
)
DISTRICTS = {
    "TVM": "Thiruvananthapuram",
    "KLM": "Kollam",
    "PTA": "Pathanamthitta",
    "ALP": "Alappuzha",
    "KTM": "Kottayam",
    "IDK": "Idukki",
    "EKM": "Ernakulam",
    "TSR": "Thrissur",
    "PLKD": "Palakkad",
    "MLP": "Malappuram",
    "KKD": "Kozhikode",
    "WYD": "Wayanad",
    "KNR": "Kannur",
    "KSD": "Kasaragod",
}
PIN_PATTERN = re.compile(r"(?<!\d)[1-9]\d{2} ?\d{3}(?!\d)")


def extract_facilities(pdf_path: Path) -> list[dict]:
    facilities = []
    with pdfplumber.open(pdf_path) as document:
        # The cover and statewide summary precede the facility tables.
        for page in document.pages[2:]:
            table = page.extract_table({"snap_tolerance": 2, "join_tolerance": 2})
            if not table or tuple(" ".join(cell.split()) for cell in table[0]) != HEADER:
                raise ValueError(f"Unexpected facility table on PDF page {page.page_number}")

            for row_number, row in enumerate(table[1:], start=1):
                serial, district, category, name, address, beds = (
                    " ".join((cell or "").split()) for cell in row
                )
                if not serial.isdecimal() or district not in DISTRICTS or not name:
                    raise ValueError(f"Invalid facility row on PDF page {page.page_number}: {row}")
                if beds and not beds.isdecimal():
                    raise ValueError(f"Invalid bed count on PDF page {page.page_number}: {row}")

                pins = {pin.replace(" ", "") for pin in PIN_PATTERN.findall(address)}
                facilities.append({
                    "source": "Kerala DHS",
                    "source_url": f"{SOURCE_URL}#page={page.page_number}",
                    "source_date": "",
                    "source_period": SOURCE_PERIOD,
                    "source_record_id": f"p{page.page_number}:r{row_number}",
                    "source_serial": serial,
                    "name": name,
                    "state": "Kerala",
                    "district": DISTRICTS[district],
                    "city": "",
                    "pin": next(iter(pins)) if len(pins) == 1 else "",
                    "address": address,
                    "category": category,
                    "beds": int(beds) if beds else "",
                    "bed_scope": "sanctioned",
                    "doctors": "",
                })

    count = len(facilities)
    beds_total = sum(row["beds"] for row in facilities if row["beds"] != "")
    if (count, beds_total) != (EXPECTED_FACILITIES, EXPECTED_BEDS):
        raise ValueError(f"Extraction disagrees with the published summary: {count} rows, {beds_total} beds")
    return facilities


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True, help="Saved copy of the July 2026 PDF")
    parser.add_argument("--output", type=Path, required=True, help="CSV output path")
    args = parser.parse_args()

    rows = extract_facilities(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
