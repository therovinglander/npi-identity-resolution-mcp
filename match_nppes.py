#!/usr/bin/env python3
"""
match_nppes.py - Find likely-duplicate providers in MongoDB and score them.

PHASE 2 of the identity-resolution build: read providers already loaded by
ingest_nppes.py, propose candidate matches, score each pair with an
explainable (not black-box ML) formula, and write results to a
`match_candidates` collection for human review.

Usage:
    export MONGODB_URI="mongodb+srv://andrewdubinsky1_db_user:8BHHkKRyQpG0n7XV@cluster0.j2smsls.mongodb.net"
    python3 match_nppes.py --db production --dry-run --limit 20
    python3 match_nppes.py --db production
"""

import argparse
import difflib
import os
import sys

# ---------------------------------------------------------------------------
# SCORING WEIGHTS
# Each field gets a slice of 100% (must sum to 1.0). These are a DEFENSIBLE
# STARTING POINT, not a trained/calibrated model — see README for why.
# Address weighted highest: two different "Jennifer Smith"s rarely share a
# street address, but two different "Williams" families share a last name
# constantly.
# ---------------------------------------------------------------------------
WEIGHTS = {
    "last_name": 0.25,
    "first_name": 0.20,
    "address": 0.30,
    "taxonomy": 0.15,
    "zip": 0.10,
}

def normalize(s):
    """Lowercase + strip, so 'SMITH' and 'Smith ' compare equal. None-safe."""
    if not s:
        return ""
    return s.strip().lower()


def string_similarity(a, b):
    """
    0.0-1.0 similarity score using Python's built-in difflib.
    SequenceMatcher finds the longest matching blocks between two strings
    and ratios that against total length -- the same idea "diff" tools use
    to find common chunks of text. Ships in every Python install.
    """
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()
def score_pair(doc_a, doc_b):
    """
    Compare two provider documents field by field, return:
      - confidence: 0.0-1.0 overall score (weighted sum of field scores)
      - evidence: dict showing the per-field score that produced it
                  (this is the "explainable, not black-box" requirement --
                  a human reviewing a proposed merge should be able to see
                  WHY the score is what it is, not just trust a number)
    """
    evidence = {}

    evidence["last_name"] = string_similarity(doc_a.get("last_name"), doc_b.get("last_name"))
    evidence["first_name"] = string_similarity(doc_a.get("first_name"), doc_b.get("first_name"))

    # EARLY REJECTION GATE: if first names are too dissimilar, this is almost
    # certainly two different people sharing a surname/address (e.g. family
    # members or colleagues at the same small practice) -- no combination of
    # matching address/zip/taxonomy should override a clear first-name
    # mismatch. We return confidence=0.0 immediately rather than let strong
    # address evidence drag a wrong pair above the threshold.
    if evidence["first_name"] < 0.55:
        evidence["last_name"] = evidence["last_name"]  # no-op: keep for display
        evidence["address"] = 0.0
        evidence["zip"] = 0.0
        evidence["taxonomy"] = 0.0
        return 0.0, evidence

    addr_a = doc_a.get("practice_address") or {}
    addr_b = doc_b.get("practice_address") or {}
    evidence["address"] = string_similarity(addr_a.get("line1"), addr_b.get("line1"))

    zip_a = (addr_a.get("postal_code") or "")[:5]
    zip_b = (addr_b.get("postal_code") or "")[:5]
    evidence["zip"] = 1.0 if (zip_a and zip_a == zip_b) else 0.0

    tax_a = {t["code"] for t in (doc_a.get("taxonomies") or [])}
    tax_b = {t["code"] for t in (doc_b.get("taxonomies") or [])}
    evidence["taxonomy"] = 1.0 if (tax_a and tax_b and tax_a & tax_b) else 0.0

    confidence = sum(evidence[field] * weight for field, weight in WEIGHTS.items())
    return confidence, evidence
def find_candidate_pairs(coll, limit=None):
    """
    BLOCKING: comparing every provider to every other provider is O(n^2) --
    for 29,000 records that's ~420 million comparisons, far too slow.
    Instead we "block" on (last_name, zip5): only compare providers who
    share the same last name AND the same 5-digit zip. This is tighter
    than blocking on last_name alone -- it directly prevents comparing
    same-surname providers at different addresses, which was producing
    noisy false-positive scores in testing.

    NOTE: Atlas free-tier (M0) clusters block allowDiskUse on aggregations,
    so we can't let MongoDB's $group stage do this bucketing server-side.
    Instead we stream filtered/projected docs with find() and bucket them
    ourselves in a Python dict.
    """
    from collections import defaultdict

    projection = {"npi": 1, "first_name": 1, "last_name": 1,
                  "practice_address": 1, "taxonomies": 1}
    query = {"status": "active", "entity_type": "individual", "last_name": {"$ne": None}}

    groups = defaultdict(list)
    for doc in coll.find(query, projection):
        zip5 = ((doc.get("practice_address") or {}).get("postal_code") or "")[:5]
        if not zip5:
            continue
        block_key = (doc["last_name"], zip5)
        groups[block_key].append(doc)

    pairs_yielded = 0
    for block_key, docs in groups.items():
        if len(docs) < 2:
            continue
        for i in range(len(docs)):
            for j in range(i + 1, len(docs)):
                yield docs[i], docs[j]
                pairs_yielded += 1
                if limit and pairs_yielded >= limit:
                    return

def run(args):
    try:
        from pymongo import MongoClient
    except ImportError:
        sys.exit("pymongo not installed. Run: python3 -m pip install pymongo")

    uri = args.mongo_uri or os.environ.get("MONGODB_URI")
    if not uri:
        sys.exit("No Mongo URI given. Set MONGODB_URI env var or pass --mongo-uri.")

    client = MongoClient(uri)
    db = client[args.db]
    providers = db["providers"]
    match_candidates = db["match_candidates"]

    pairs_checked = 0
    candidates_found = 0
    results = []
  
    for doc_a, doc_b in find_candidate_pairs(providers, limit=args.limit):
        pairs_checked += 1
        confidence, evidence = score_pair(doc_a, doc_b)

        if confidence >= args.threshold:
            candidates_found += 1
            record = {
                "npi_a": doc_a["npi"],
                "npi_b": doc_b["npi"],
                "name_a": f"{doc_a.get('first_name')} {doc_a.get('last_name')}",
                "name_b": f"{doc_b.get('first_name')} {doc_b.get('last_name')}",
                "confidence": round(confidence, 3),
                "evidence": {k: round(v, 3) for k, v in evidence.items()},
                "status": "pending_review",
            }
            results.append(record)

    print(f"Pairs checked: {pairs_checked} | Candidates >= {args.threshold} confidence: {candidates_found}")

    if args.dry_run:
        print("--- DRY RUN: top candidates (no DB write) ---")
        for r in sorted(results, key=lambda r: -r["confidence"])[:10]:
            print(r)
        return

    if results:
        match_candidates.insert_many(results)
        print(f"Wrote {len(results)} match_candidates documents.")
    else:
        print("No candidates met the threshold; nothing written.")


def main():
    p = argparse.ArgumentParser(description="Propose and score candidate duplicate providers.")
    p.add_argument("--mongo-uri", default=None)
    p.add_argument("--db", default="production")
    p.add_argument("--threshold", type=float, default=0.5, help="Min confidence to keep a candidate (0-1)")
    p.add_argument("--limit", type=int, default=None, help="Limit last-name groups scanned (testing)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
