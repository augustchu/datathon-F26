#!/usr/bin/env python3
"""Extract the branch and count columns of SBI General's archived quality disclosure."""

import argparse
import csv
import re
import subprocess
import tempfile
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree


NAMESPACE = "{http://www.w3.org/1999/xhtml}"
SOURCE_COLUMNS = ("source_record_id", "name", "address", "city", "state", "pin", "source_date", "beds", "doctors")
OUTPUT_COLUMNS = ("source", "source_url", "source_date", "source_record_id", "name", "state", "city", "pin", "address", "beds", "doctors")
REPORTS = {
    "2021": {
        "url": "https://content.sbigeneral.in/uploads/59df4f4ec5cd48ecb9fee1c702387553.pdf",
        "pages": 172,
        "rows": 15769,
        "columns": (51.92, 65.12, 120.08, 187.76, 209.72, 234.68, 259.64, 283.88, 305.84, 331.04),
    },
    "2022": {
        "url": "https://content.sbigeneral.in/uploads/f22596609234455e8b24df7859027ce6.pdf",
        "pages": 237,
        "rows": 17529,
        "columns": (51.32, 65.02, 114.68, 155.00, 184.52, 206.60, 222.57, 251.60, 281.48, 302.12),
    },
}
HORIZONTAL_RULE = re.compile(r"M (51\.503906) ([\d.]+) L (77[34]\.\d+) \2 L \3 ([\d.]+)")


def exact_page_bounds(pdf, folder, page_number):
    """Read the wide horizontal table rules of the 2022 release."""
    svg = folder / f"page-{page_number}.svg"
    subprocess.run(["pdftocairo", "-svg", "-f", str(page_number), "-l", str(page_number), str(pdf), str(svg)], check=True)
    drawing = svg.read_text(encoding="utf-8")
    svg.unlink()
    data_top = 77.9 if page_number == 1 else 54.2
    # Cairo's logical drawing is very slightly scaled relative to Poppler's
    # PDF word coordinates. Convert the midpoint of each rectangular rule.
    bounds = {
        (float(top) + float(bottom)) / (2 * 0.99886)
        for _, top, _, bottom in HORIZONTAL_RULE.findall(drawing)
        if float(top) >= data_top
    }
    return sorted(bounds)


def page_rows(page, page_number, columns, bounds=None):
    words = []
    for word in page.findall(f"{NAMESPACE}word"):
        x_left = float(word.get("xMin"))
        x_right = float(word.get("xMax"))
        column = bisect_right(columns, (x_left + x_right) / 2) - 1
        if 0 <= column < len(SOURCE_COLUMNS):
            y_top = float(word.get("yMin"))
            words.append((column, y_top, x_left, word.text or ""))

    if bounds is None:
        top = 81.12 if page_number == 1 else 54.12
        # The 2021 serials are vertically centered. The center and top rule
        # determine the bottom rule even if adjacent cells have wrapped text.
        row_centers = sorted(y + 1.08 for column, y, _, value in words if column == 0 and y >= top and value.isdecimal())
        bounds = [top]
        for center in row_centers:
            bounds.append(2 * center - bounds[-1])
    cells = [[[] for _ in SOURCE_COLUMNS] for _ in range(len(bounds) - 1)]
    for column, y, x, value in words:
        row_index = bisect_right(bounds, y + 1.08) - 1
        if 0 <= row_index < len(cells):
            cells[row_index][column].append((round(y, 1), x, value))

    for row in cells:
        values = [" ".join(word for _, _, word in sorted(cell)) for cell in row]
        yield dict(zip(SOURCE_COLUMNS, values, strict=True))


def positive_integer(value):
    if re.fullmatch(r"[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+", value):
        return int(value.replace(",", "")) or ""
    return ""


def iso_date(value):
    for format_string in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, format_string).date().isoformat()
        except ValueError:
            continue
    return ""


def extract(pdf, output, edition):
    report = REPORTS[edition]
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        layout = folder / "layout.xhtml"
        with ThreadPoolExecutor(max_workers=5) as pool:
            extract_words = pool.submit(subprocess.run, ["pdftotext", "-bbox", str(pdf), str(layout)], check=True)
            page_bounds = {}
            if edition == "2022":
                page_bounds = {
                    page: pool.submit(exact_page_bounds, pdf, folder, page)
                    for page in range(1, report["pages"] + 1)
                }
            extract_words.result()
            rows = []
            page_number = 0
            for _, page in ElementTree.iterparse(layout, events=("end",)):
                if page.tag != f"{NAMESPACE}page":
                    continue
                page_number += 1
                bounds = page_bounds[page_number].result() if page_bounds else None
                for claim in page_rows(page, page_number, report["columns"], bounds):
                    claim["source"] = "sbi_general_archived_quality_disclosure"
                    claim["source_url"] = f"{report['url']}#page={page_number}"
                    claim["source_date"] = iso_date(claim["source_date"])
                    claim["beds"] = positive_integer(claim["beds"])
                    claim["doctors"] = positive_integer(claim["doctors"])
                    rows.append(claim)
                page.clear()

    serials = [row["source_record_id"] for row in rows]
    if page_number != report["pages"] or serials != [str(n) for n in range(1, report["rows"] + 1)]:
        raise ValueError("Page count or record sequence differs from the archived SBI report")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows):,} source rows across {page_number} pages")
    print(f"{sum(bool(row['beds']) for row in rows):,} positive bed claims")
    print(f"{sum(bool(row['doctors']) for row in rows):,} positive doctor claims")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Archived SBI General PDF")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--edition", choices=REPORTS, default="2021")
    args = parser.parse_args()
    extract(args.pdf, args.output, args.edition)
