#!/usr/bin/env python3
"""Extract published claims from the district page for General Hospital, Yanam."""

import argparse
import csv
import re
from pathlib import Path

from bs4 import BeautifulSoup


SOURCE_URL = "https://yanam.gov.in/departments/health/"
NAME = "General Hospital, Yanam"
IDENTITY_URL = "https://yanam.gov.in/public-utility/government-general-hospital/"


def text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def extract_health_page(html: str) -> list[dict]:
    article = BeautifulSoup(html, "html.parser").find("main")
    if article is None or not any("Govt. General Hospital, Yanam" in text(tag) for tag in article.find_all("h2")):
        raise ValueError("Expected the district's Government General Hospital page")
    paragraphs = [text(element) for element in article.find_all("p")]
    address_line = next(line for line in paragraphs if line.startswith("Office Address:"))
    pin = re.search(r"\b[1-9]\d{5}\b", address_line).group()
    address = address_line.removeprefix("Office Address:").strip()
    history = next(line for line in paragraphs if "The Government General Hospital, Yanam was established" in line)
    year = re.search(r"was established in (\d{4})", history).group(1)
    contacts = next(line for line in paragraphs if line.startswith("Email : gh[dot]yanam"))
    raw_email = re.search(r"Email\s*:\s*(\S+)", contacts).group(1)
    email = raw_email.replace("[dot]", ".").replace("[at]", "@")
    phone = re.search(r"\+91\d{10}\b", contacts).group()

    claims = []

    def add(field, value, scope, excerpt):
        claims.append({
            "source": "Yanam district administration",
            "source_url": SOURCE_URL,
            "source_date": "",
            "source_record_id": f"yanam-health-{len(claims) + 1}",
            "name": NAME,
            "state": "Puducherry",
            "city": "Yanam",
            "pin": pin,
            "address": address,
            "field": field,
            "value": value,
            "scope": scope,
            "excerpt": excerpt,
            "identity_url": IDENTITY_URL,
        })

    add("yearEstablished", year, "published_hospital_institution", history.split(" and it", 1)[0])
    add("phone", phone, "published branch office contact", f"Ph. | {phone}")
    add("email", email, "published branch office contact", f"Email | {raw_email}")

    tables = [
        [[text(cell) for cell in row.find_all(["th", "td"], recursive=False)] for row in table.find_all("tr")]
        for table in article.find_all("table")
    ]
    services = next(rows for rows in tables if rows[0] == ["Services", "Specialties"])
    for service, specialty in services[1:]:
        if service and not service.endswith(" Beds"):
            add("capability", service, "branch-published service listing", f"Services | {service}")
        if specialty:
            add("specialties", specialty, "branch-published specialty listing", f"Specialties | {specialty}")

    bed_table = next(rows for rows in tables if rows[0][-2:] == ["AS per Census book", "Actual"])
    _, _, census, actual = next(row for row in bed_table if row[1] == "Total Beds")
    for field, value, scope in (("censusBeds", census, "AS per Census book"), ("actualBeds", actual, "Actual")):
        if not value.isdecimal():
            raise ValueError(f"Unexpected {scope} bed count: {value!r}")
        add(field, value, scope, f"Total Beds | {scope} | {value}")
    return claims


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-html", type=Path, required=True)
    parser.add_argument("--years", type=Path, required=True, help="Puducherry statistics year evidence CSV")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    claims = extract_health_page(args.health_html.read_text(encoding="utf-8-sig"))
    with args.years.open(encoding="utf-8", newline="") as input_file:
        existing = [row for row in csv.DictReader(input_file) if row["name"] == NAME and row["field"] == "yearEstablished"]
    if len(existing) != 1:
        raise ValueError(f"Expected one year claim for {NAME} from Puducherry statistics")
    claims.extend(existing)

    with args.output.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(claims[0]))
        writer.writeheader()
        writer.writerows(claims)


if __name__ == "__main__":
    main()
