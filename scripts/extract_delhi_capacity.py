#!/usr/bin/env python3
"""Extract approved bed strength from Delhi DGHS's July 2025 nursing-home register."""

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path

import pdfplumber


SOURCE_URL = "https://dgehs.delhi.gov.in/sites/default/files/inline-files/msnh_27.pdf"
SOURCE_DATE = "2025-07-18"
EXPECTED_ROWS = 1184
PIN_PATTERN = re.compile(r"[1-9]\d{5}")
TABLE_COLUMNS = [53.22, 80.7, 189.3, 318.78, 362.1, 408.78, 446.1, 507.54]


def registration_end(value: str) -> str:
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
        try:
            return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
        except ValueError:
            return ""
    return ""


def extract_facilities(pdf_path: Path) -> list[dict]:
    facilities = []
    with pdfplumber.open(pdf_path) as document:
        for page in document.pages:
            table = page.find_table({
                "vertical_strategy": "explicit",
                "explicit_vertical_lines": TABLE_COLUMNS,
            })
            if table is None:
                raise ValueError(f"Missing facility table on PDF page {page.page_number}")

            for row_number, (cells, geometry) in enumerate(zip(table.extract(), table.rows), start=1):
                if cells[0] == "S.No.":
                    continue
                if len(cells) == 6:
                    cells.insert(0, None)
                reported_serial, name, address, raw_pin, beds, registration, validity = (
                    " ".join((cell or "").split()) for cell in cells
                )
                if not reported_serial and not cells[0]:
                    # The last two pages omit the left border on some labeled rows.
                    _, top, _, bottom = geometry.bbox
                    serial_text = page.crop((TABLE_COLUMNS[0], top, TABLE_COLUMNS[1], bottom)).extract_text()
                    reported_serial = (serial_text or "").strip()
                if not (name and address and beds.isdecimal() and registration and validity):
                    raise ValueError(f"Unexpected facility row on PDF page {page.page_number}: {cells}")

                # On the final page, the last line of the Yatharth name overlaps the next cell.
                if page.page_number == 42 and registration == "2016" and name.startswith("Services Pvt Ltd) "):
                    if facilities[-1]["source_record_id"] != "2015":
                        raise ValueError("Unexpected preceding row for the known final-page name overflow")
                    facilities[-1]["name"] += " Services Pvt Ltd)"
                    name = name.removeprefix("Services Pvt Ltd) ")

                facilities.append({
                    "source": "Delhi DGHS Nursing Home Cell",
                    "source_url": f"{SOURCE_URL}#page={page.page_number}",
                    "source_date": SOURCE_DATE,
                    "source_period": "2025-07",
                    "source_record_id": registration,
                    "source_serial": reported_serial,
                    "source_row_on_page": row_number,
                    "name": name.lstrip("*").strip(),
                    "state": "Delhi",
                    "city": "",
                    "pin": raw_pin if PIN_PATTERN.fullmatch(raw_pin) else "",
                    "source_pin": raw_pin,
                    "address": address,
                    "beds": int(beds),
                    "bed_scope": "approved_beds",
                    "doctors": "",
                    "registration_valid_until": registration_end(validity),
                    "registration_validity_raw": validity,
                    "source_starred": name.startswith("*"),
                })

    if len(facilities) != EXPECTED_ROWS or len({row["source_record_id"] for row in facilities}) != EXPECTED_ROWS:
        raise ValueError(f"Expected {EXPECTED_ROWS} unique registrations, got {len(facilities)} rows")
    return facilities


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True, help="Saved copy of the 18 July 2025 PDF")
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
