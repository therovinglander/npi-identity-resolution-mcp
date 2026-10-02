#!/usr/bin/env python3
"""
ingest_nppes.py — Load an NPPES weekly dissemination file into MongoDB Atlas.
"""

import argparse
import csv
import datetime as dt
import os
import sys

ENTITY_TYPE_MAP = {"1": "individual", "2": "organization"}
TAXONOMY_SLOTS = 15


def parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%m/%d/%Y")
    except ValueError:
        return None


def clean(value):
    value = (value or "").strip()
    return value or None


def transform_row(row):
    npi = clean(row.get("NPI"))
    if not npi:
        return None

    entity_code = clean(row.get("Entity Type Code"))

    if entity_code is None:
        deact_date = parse_date(row.get("NPI Deactivation Date"))
        if deact_date:
            return {
                "npi": npi,
                "status": "deactivated",
                "deactivation_date": deact_date,
                "deactivation_reason_code": clean(row.get("NPI Deactivation Reason Code")),
                "last_update_date": parse_date(row.get("Last Update Date")),
            }
        return None

    entity_type = ENTITY_TYPE_MAP.get(entity_code, entity_code)

    taxonomies = []
    for i in range(1, TAXONOMY_SLOTS + 1):
        code = clean(row.get(f"Healthcare Provider Taxonomy Code_{i}"))
        if not code:
            continue
        taxonomies.append({
            "code": code,
            "license_number": clean(row.get(f"Provider License Number_{i}")),
            "license_state": clean(row.get(f"Provider License Number State Code_{i}")),
            "primary": clean(row.get(f"Healthcare Provider Primary Taxonomy Switch_{i}")) == "Y",
        })

    doc = {
        "npi": npi,
        "status": "active",
        "entity_type_code": entity_code,
        "entity_type": entity_type,
        "ein": clean(row.get("Employer Identification Number (EIN)")),
        "organization_name": clean(row.get("Provider Organization Name (Legal Business Name)")),
        "last_name": clean(row.get("Provider Last Name (Legal Name)")),
        "first_name": clean(row.get("Provider First Name")),
        "middle_name": clean(row.get("Provider Middle Name")),
        "name_prefix": clean(row.get("Provider Name Prefix Text")),
        "name_suffix": clean(row.get("Provider Name Suffix Text")),
        "credential": clean(row.get("Provider Credential Text")),
        "sex": clean(row.get("Provider Sex Code")),
        "mailing_address": {
            "line1": clean(row.get("Provider First Line Business Mailing Address")),
            "line2": clean(row.get("Provider Second Line Business Mailing Address")),
            "city": clean(row.get("Provider Business Mailing Address City Name")),
            "state": clean(row.get("Provider Business Mailing Address State Name")),
            "postal_code": clean(row.get("Provider Business Mailing Address Postal Code")),
            "country": clean(row.get("Provider Business Mailing Address Country Code (If outside U.S.)")),
            "phone": clean(row.get("Provider Business Mailing Address Telephone Number")),
        },
        "practice_address": {
            "line1": clean(row.get("Provider First Line Business Practice Location Address")),
            "line2": clean(row.get("Provider Second Line Business Practice Location Address")),
            "city": clean(row.get("Provider Business Practice Location Address City Name")),
            "state": clean(row.get("Provider Business Practice Location Address State Name")),
            "postal_code": clean(row.get("Provider Business Practice Location Address Postal Code")),
            "country": clean(row.get("Provider Business Practice Location Address Country Code (If outside U.S.)")),
            "phone": clean(row.get("Provider Business Practice Location Address Telephone Number")),
        },
        "taxonomies": taxonomies,
        "enumeration_date": parse_date(row.get("Provider Enumeration Date")),
        "last_update_date": parse_date(row.get("Last Update Date")),
        "deactivation_reason_code": clean(row.get("NPI Deactivation Reason Code")),
        "deactivation_date": parse_date(row.get("NPI Deactivation Date")),
        "reactivation_date": parse_date(row.get("Provider Reactivation Date") or row.get("NPI Reactivation Date")),
    }
    return doc


def iter_documents(path, limit=None):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        n = 0
        for row in reader:
            doc = transform_row(row)
            if doc is None:
                continue
            yield doc
            n += 1
            if limit and n >= limit:
                return


def run(args):
    if args.dry_run:
        print(f"--- DRY RUN: previewing up to {args.limit or 5} transformed documents ---")
        import json
        shown = 0
        for doc in iter_documents(args.file, limit=args.limit or 5):
            def default(o):
                return o.isoformat() if isinstance(o, dt.datetime) else str(o)
            print(json.dumps(doc, indent=2, default=default))
            shown += 1
        print(f"--- {shown} document(s) shown, no DB write performed ---")
        return

    try:
        from pymongo import MongoClient, UpdateOne
    except ImportError:
        sys.exit(
            "pymongo is not installed. Run: python3 -m pip install pymongo\n"
            "(or re-run with --dry-run to preview the transform without a DB connection)"
        )

    uri = args.mongo_uri or os.environ.get("MONGODB_URI")
    if not uri:
        sys.exit("No Mongo URI given. Set MONGODB_URI env var or pass --mongo-uri.")

    client = MongoClient(uri)
    db = client[args.db]
    coll = db[args.collection]

    coll.create_index("npi", unique=True)

    batch = []
    total = 0
    upserted = 0
    modified = 0

    def flush(batch):
        nonlocal upserted, modified
        if not batch:
            return
        result = coll.bulk_write(batch, ordered=False)
        upserted += result.upserted_count
        modified += result.modified_count

    for doc in iter_documents(args.file, limit=args.limit):
        batch.append(UpdateOne({"npi": doc["npi"]}, {"$set": doc}, upsert=True))
        total += 1
        if len(batch) >= args.batch_size:
            flush(batch)
            batch = []
            print(f"...{total} rows processed", file=sys.stderr)
    flush(batch)

    print(f"Done. Rows processed: {total} | new docs upserted: {upserted} | existing docs modified: {modified}")


def main():
    p = argparse.ArgumentParser(description="Ingest an NPPES npidata_pfile CSV into MongoDB.")
    p.add_argument("--file", required=True, help="Path to npidata_pfile_*.csv")
    p.add_argument("--mongo-uri", default=None, help="Mongo connection string (default: $MONGODB_URI)")
    p.add_argument("--db", default="npi_identity", help="Database name (default: npi_identity)")
    p.add_argument("--collection", default="providers", help="Collection name (default: providers)")
    p.add_argument("--batch-size", type=int, default=1000, help="Bulk write batch size")
    p.add_argument("--limit", type=int, default=None, help="Only process the first N usable rows (testing)")
    p.add_argument("--dry-run", action="store_true", help="Print transformed docs, skip DB entirely")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main(x)
