"""Extract the requested facility fields from two central-government lists.

Uses cached public PDFs with pdfplumber. ESIC's server timed out during collection;
its complete web-reader PDF text snapshot is a disclosed fallback input. The
fallback includes all 206 lines, not search snippets. Run with --download to retry
downloads; existing files are otherwise used for reproducible offline extraction.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import unquote

import pdfplumber
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/government_sources"
RAW = OUT / "raw/central"
AUDIT = OUT / "audit"
URLS = {
    "bis_cghs": "https://www.bis.gov.in/wp-content/uploads/2026/11/Circular-and-227-cghs-cashless-consolidated-list-10-Nov-2026.pdf",
    "esic": "https://dmn.esic.gov.in/attachments/sstfile/Empanelment_of_CGHS_Empanelled_HCOs_1772002059.pdf",
}
FIELDS = ["facility_name", "phone_numbers", "email_addresses", "location", "services", "validity_dates", "source_links"]
LIST_FIELDS = set(FIELDS) - {"facility_name", "location"}
MAIN_INACTIVE = {3,14,18,22,38,43,55,56,76,85,98,101,109,114,120,124,136,151,180}
INACTIVE_NOTE = "Source note: exited CGHS / suspended by CGHS or BIS / not interested in renewal for BIS cashless facility (source groups these possibilities)."


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\x02", "-")).strip()


def unique(items):
    return list(dict.fromkeys(x for x in items if x))


def phones(text):
    # Repair a source line wrap after a short area code, preserving number ranges.
    text = re.sub(r"(\b\d{2,4}\s*-?)\s*\n\s*(?=\d{7,10}\b)", r"\1 ", text)
    result = []
    for line in text.splitlines():
        for candidate in re.findall(r"\+?\d[\d\t ()/.-]*\d", line):
            candidate = clean(candidate).rstrip(".- ")
            if len(re.sub(r"\D", "", candidate)) >= 7:
                result.append(candidate)
    return unique(result)


def emails(text, hyperlinks=()):
    # Wraps inside address tokens occur frequently in narrow PDF email columns.
    chunks, current = [], ""
    complete = re.compile(r"^[^@\s]+@[A-Za-z0-9.-]+\.(?:com|org|net|in|edu|gov|io|biz|info|health|care|hospital|clinic)$", re.I)
    for line in re.split(r"\n|;|,", text):
        line = line.strip()
        if not line:
            continue
        if line.count("@") > 1:
            chunks.append(current)
            chunks.extend(re.split(r"\s+(?=[A-Za-z0-9._%+-]+@)", line))
            current = ""
        elif (("@" in line and "@" in current and not line.startswith("@")) or
              (complete.fullmatch(current) and "@" not in line and not line.startswith("."))):
            chunks.append(current)
            current = line
        else:
            current += line
    chunks.append(current)
    result = []
    pattern = r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    for chunk in chunks:
        result.extend(re.findall(pattern, re.sub(r"\s+", "", chunk)))
    for link in hyperlinks:
        result.extend(re.findall(pattern, unquote(link.removeprefix("mailto:")).split("?")[0]))
    return unique(result)


def map_header(row):
    labels = {}
    for i, value in enumerate(row):
        value = clean(value).lower()
        if value.startswith("sl"): labels["serial"] = i
        elif "name" in value and ("hospital" in value or "lab" in value): labels["name"] = i
        elif value == "address": labels["location"] = i
        elif "contact" in value: labels["phones"] = i
        elif "mail" in value: labels["emails"] = i
        elif "nabh" in value: labels["accreditation"] = i
        elif "treatment" in value or "procedure" in value: labels["services"] = i
        elif "validity" in value: labels["validity"] = i
    if not {"serial","name","location","phones","emails","services","validity"} <= labels.keys():
        return None
    sorted_positions = sorted(labels.values()) + [len(row)]
    return {key: (start, sorted_positions[sorted_positions.index(start)+1]) for key,start in labels.items()}


def bis_extract(path):
    records, current, mapping, fragments = [], None, None, []
    table_rows = 0
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            for table in page.find_tables():
                rows = table.extract()
                if not rows or len(rows[0]) < 7:
                    continue
                for row_index, row in enumerate(rows):
                    detected = map_header(row)
                    if detected:
                        mapping = detected
                        continue
                    if mapping is None or len(row) < 7:
                        continue
                    parts = {key: "\n".join(v for v in row[start:end] if v)
                             for key, (start,end) in mapping.items()}
                    serial = clean(parts.get("serial"))
                    if re.fullmatch(r"\d+[.)]?", serial):
                        current = {"serial": int(serial.rstrip(".)")), "main": page_number <= 59,
                                   "pages": [], "links": [],
                                   "parts": {key: [] for key in mapping}}
                        records.append(current)
                        table_rows += 1
                    elif serial:
                        raise ValueError(f"Unexpected serial cell on page {page_number}: {serial!r}")
                    if current is None or not any(parts.values()):
                        continue
                    current["pages"].append(page_number)
                    for key,value in parts.items():
                        if value:
                            current["parts"].setdefault(key, []).append(value)
                    # Mailto annotations repair wraps, but only within this row's email cells.
                    start, end = mapping["emails"]
                    email_boxes = [b for b in table.rows[row_index].cells[start:end] if b]
                    for link in page.hyperlinks:
                        if not str(link.get("uri", "")).lower().startswith("mailto:"):
                            continue
                        x, y = (link.get("x0",0)+link.get("x1",0))/2, (link.get("top",0)+link.get("bottom",0))/2
                        if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in email_boxes):
                            current["links"].append(link["uri"])
    output, warnings = [], []
    for item in records:
        parts = {key: "\n".join(value) for key,value in item["parts"].items()}
        name, validity = clean(parts.get("name")), [clean(parts.get("validity"))]
        status_match = re.search(r"\b(?:Exit from|Suspended|Suspension|Not interested)\b", name, re.I)
        if status_match:
            validity.append(name[status_match.start():])
            name = name[:status_match.start()].rstrip(" (")
        if item["main"] and item["serial"] in MAIN_INACTIVE:
            validity.append(INACTIVE_NOTE)
        scope = clean(parts.get("services"))
        # Repeated payment eligibility prose is not a facility service.
        scope = re.sub(r"\(?The cashless facilities shall be ONLY for hospitalization.*?be on CASH BASIS\.\s*\)?", "", scope, flags=re.I).strip()
        record = {"facility_name": name, "phone_numbers": phones(parts.get("phones", "")),
                  "email_addresses": emails(parts.get("emails", ""), item["links"]),
                  "location": clean(parts.get("location")), "services": [scope] if scope else [],
                  "validity_dates": unique(validity),
                  "source_links": [f"{URLS['bis_cghs']}#page={n}" for n in unique(item["pages"])]}
        # PDF page29 has an extra fragmented copy of a wrapped email in a split
        # subcell. Keep the complete address, not its repeated trailing fragment.
        if (item["main"] and item["serial"] == 122 and
                "vivekanandpratisthanparishad@gmail.com" in record["email_addresses"] and
                "had@gmail.com" in record["email_addresses"]):
            record["email_addresses"].remove("had@gmail.com")
            warnings.append({"facility_name": name,
                             "issue": "Omitted ambiguous repeated trailing email fragment had@gmail.com; complete wrapped address retained",
                             "source_text": parts["emails"]})
        if "@" in parts.get("emails", "") and not record["email_addresses"]:
            warnings.append({"facility_name": name, "issue": "Email text could not be parsed", "source_text": parts["emails"]})
        output.append(record)
        fragments.append({"serial": item["serial"], "main": item["main"], "pages": unique(item["pages"]), "source_columns": parts})
    assert table_rows == 294, f"Source layout changed: expected 294 entries, saw {table_rows}"
    assert [r["serial"] for r in records if r["main"]] == list(range(1,228))
    assert all(r["facility_name"] and r["location"] for r in output)
    return output, {"numbered_entries": table_rows, "main_entries": 227, "branch_appendix_entries": 67,
                    "inactive_note_entries": sum(INACTIVE_NOTE in r["validity_dates"] for r in output),
                    "warnings": warnings, "source_row_fragments": fragments}


def esic_extract_web_snapshot(path):
    snapshot = path.read_text()
    lines = []
    for match in re.finditer(r"^L(\d+)@P(\d+)(?:-\d+)?: (.*)$", snapshot, re.M):
        n, page, value = int(match[1]), int(match[2])+1, match[3]
        if n >= 69:
            value = re.sub(r"111-U/17/11/23/SSTManual/Med\.II I/3536053/2026", "", value)
            if clean(value): lines.append((page, value))
    blocks, current = [], None
    for page,text in lines:
        if re.fullmatch(r"[1-6]", text):
            current = {"serial": int(text), "lines": [], "pages": [page]}
            blocks.append(current)
        elif current is not None:
            current["lines"].append(text)
            current["pages"].append(page)
    assert [b["serial"] for b in blocks] == list(range(1,7))
    output = []
    first_scope = re.compile(r"^(?:Cardiology|Nephrology(?: Including)?|Bone Marrow Transplant|Non-Interventional|2 D Echo)$")
    for block in blocks:
        ls = block["lines"]
        # Row 1's scope begins "Cardiology including"; all other markers are full lines.
        service_start = next(i for i,s in enumerate(ls) if first_scope.match(s) or s == "Cardiology including")
        contact_start = next(i for i,s in enumerate(ls) if re.match(r"(?:Mr\.|Ms\.|Dr\.)", s))
        date_start = next(i for i,s in enumerate(ls) if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", s))
        address = clean(" ".join(ls[:service_start]))
        name, location = address.split(",",1)
        scope = clean(" ".join(ls[service_start:contact_start] + ls[date_start+3:]))
        output.append({"facility_name": name.strip(), "phone_numbers": phones("\n".join(ls[contact_start:date_start])),
                       "email_addresses": [], "location": location.strip(), "services": [scope],
                       "validity_dates": [clean(" ".join(ls[date_start:date_start+3]))],
                       "source_links": [f"{URLS['esic']}#page={n}" for n in unique(block["pages"])]})
    return output


def write_output(name, rows):
    for row in rows:
        assert list(row) == FIELDS and all(isinstance(row[k],list) for k in LIST_FIELDS)
    (OUT / f"{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in rows))
    with (OUT / f"{name}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: json.dumps(v,ensure_ascii=False) if k in LIST_FIELDS else v for k,v in r.items()} for r in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    AUDIT.mkdir(parents=True, exist_ok=True)
    failures = []
    if args.download:
        for name,url in URLS.items():
            try:
                response = requests.get(url, timeout=(10,45))
                response.raise_for_status()
                if not response.content.startswith(b"%PDF"): raise ValueError("Not a PDF response")
                (RAW / f"{name}.pdf").write_bytes(response.content)
            except requests.RequestException as exc:
                failures.append({"source": name, "error": type(exc).__name__})
    bis, detail = bis_extract(RAW / "bis_cghs.pdf")
    esic = esic_extract_web_snapshot(RAW / "esic_web_reader_snapshot.txt")
    write_output("bis_cghs", bis)
    write_output("esic", esic)
    source_files = [RAW / "bis_cghs.pdf", RAW / "esic_web_reader_snapshot.txt"]
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "counts": {"bis_cghs": len(bis), "esic": len(esic)},
              "source_urls": URLS, "download_failures": failures,
              "collection_access_notes": ["Initial default-sandbox download failed DNS resolution; approved network escalation used for public PDFs.",
                                          "ESIC requests to dmn.esic.gov.in, www.esic.gov.in and esic.gov.in timed out; web-reader full PDF text was available.",
                                          "ESIC page screenshots failed with cached PDF image unavailable."],
              "visual_spot_checks": [{"source":"bis_cghs", "pdf_page":3,
                                      "checked":"Hospital row boundaries, service scope, contact/validity columns, Cygnus exit note"},
                                     {"source":"bis_cghs", "pdf_page":29,
                                      "checked":"Aarvy suspension note; Vivekanand email cell visually has font rendering artifacts, so ambiguous repeated fragment excluded after text/table inspection"}],
              "retrieval_methods": {"bis_cghs": "Direct PDF download; pdfplumber tables",
                                    "esic": "Complete web-reader cached PDF text, all206 source lines; direct official-host downloads timed out"},
              "source_files": [{"path":str(p.relative_to(ROOT)), "sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in source_files],
              "notes": ["Services retain source scope paragraphs rather than a normalized taxonomy.",
                        "Validity and inactive/exit qualifiers are preserved; entries are not asserted currently active.",
                        "Phones retain source formatting/ranges; incomplete local numbers are not expanded.",
                        "BIS branch appendix rows are separate source entries, not cross-source entity matches.",
                        "ESIC contains two separate scope entries for Action Cancer Hospital; both retained.",
                        "ESIC text was inspected but image rendering failed (cached PDF image unavailable)."],
              "bis_details": detail}
    (AUDIT / "central_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()
