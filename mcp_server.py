#!/usr/bin/env python3
"""
mcp_server.py - MCP server exposing identity-resolution tools over the
NPPES provider data in MongoDB Atlas.

Tools exposed (see build spec section 2):
  search_candidates(name, npi, org, location) -- READ-ONLY, safe for an
      agent to call freely.
  propose_match(match_id) -- READ-ONLY, returns a scored candidate pair
      with full evidence.
  approve_merge(match_id, decision, reviewer) -- WRITE, the ONLY tool
      that can record a merge decision. Requires a human-supplied
      decision every time; never auto-merges.
  get_audit_log(entity_id) -- READ-ONLY, full history for one NPI.

This is the concrete design point for the "human vs agent developer"
POV doc: an agent can call the three read-only tools autonomously, but
approve_merge enforces a human-in-the-loop boundary in the server's
design itself, not just in a policy document someone could forget to
follow.

Usage (as an MCP server, launched by an MCP client like Claude Desktop):
    Configure your MCP client to run:
    MONGODB_URI=mongodb+srv://... python3 mcp_server.py

Run standalone for a quick manual sanity check (no MCP client needed):
    MONGODB_URI=mongodb+srv://... python3 mcp_server.py --selftest
"""

import datetime as dt
import os
import sys

from mcp.server.fastmcp import FastMCP
from pymongo import MongoClient

MONGODB_URI = os.environ.get("MONGODB_URI")
if not MONGODB_URI:
    sys.exit("Set MONGODB_URI before starting the server.")

DB_NAME = os.environ.get("MONGODB_DB", "production")

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
providers = db["providers"]
match_candidates = db["match_candidates"]
merge_decisions = db["merge_decisions"]
audit_log = db["audit_log"]

# FastMCP is the high-level MCP server class: decorate a function with
# @mcp.tool() below and it's automatically exposed to any connected MCP
# client (Claude Desktop, Claude Code, etc.) with its name, docstring,
# and argument types turned into a tool definition the client can see.
mcp = FastMCP("npi-identity-resolution")


@mcp.tool()
def search_candidates(name: str = "", npi: str = "", org: str = "", location: str = "") -> list:
    """
    Find provider records matching loose criteria. Read-only -- safe for
    an agent to call freely with no side effects.

    Args:
        name: partial match against first/last name (case-insensitive)
        npi: exact NPI number
        org: partial match against organization name
        location: partial match against practice city or state
    """
    query = {"status": "active"}

    if npi:
        query["npi"] = npi
    if org:
        query["organization_name"] = {"$regex": org, "$options": "i"}
    if location:
        query["$or"] = [
            {"practice_address.city": {"$regex": location, "$options": "i"}},
            {"practice_address.state": {"$regex": location, "$options": "i"}},
        ]
    if name:
        parts = name.split()
        name_filters = []
        for part in parts:
            name_filters.append({"$or": [
                {"first_name": {"$regex": part, "$options": "i"}},
                {"last_name": {"$regex": part, "$options": "i"}},
            ]})
        query["$and"] = name_filters

    results = list(providers.find(query, {"_id": 0}).limit(25))
    return results


@mcp.tool()
def propose_match(match_id: str) -> dict:
    """
    Return a previously-scored candidate duplicate pair, with full
    per-field evidence. Read-only -- safe for an agent to call freely.

    Args:
        match_id: the Mongo _id (as a string) of a match_candidates document
    """
    from bson import ObjectId
    try:
        doc = match_candidates.find_one({"_id": ObjectId(match_id)})
    except Exception:
        return {"error": f"Invalid match_id format: {match_id}"}

    if not doc:
        return {"error": f"No match_candidate found with id {match_id}"}

    doc["_id"] = str(doc["_id"])
    return doc


@mcp.tool()
def approve_merge(match_id: str, decision: str, reviewer: str) -> dict:
    """
    Record a human decision on a proposed match. This is the ONLY tool
    in this server that writes a merge outcome -- it never merges
    automatically, and it requires every call to explicitly state who
    decided and what they decided. There is no "auto-approve" path.

    Args:
        match_id: the Mongo _id (as a string) of the match_candidates document
        decision: must be exactly "approved" or "rejected"
        reviewer: identifies the human making this decision (e.g. a name or email)
    """
    from bson import ObjectId

    if decision not in ("approved", "rejected"):
        return {"error": "decision must be exactly 'approved' or 'rejected'"}
    if not reviewer:
        return {"error": "reviewer is required -- a merge decision must be attributable to a person"}

    try:
        oid = ObjectId(match_id)
    except Exception:
        return {"error": f"Invalid match_id format: {match_id}"}

    match = match_candidates.find_one({"_id": oid})
    if not match:
        return {"error": f"No match_candidate found with id {match_id}"}

    now = dt.datetime.now(dt.timezone.utc)

    decision_doc = {
        "match_id": match_id,
        "npi_a": match["npi_a"],
        "npi_b": match["npi_b"],
        "confidence": match.get("confidence"),
        "evidence": match.get("evidence"),
        "decision": decision,
        "reviewer": reviewer,
        "decided_at": now,
    }
    merge_decisions.insert_one(decision_doc)

    # Every decision -- approved or rejected -- gets logged against BOTH
    # NPIs involved, so get_audit_log(either_npi) finds it regardless of
    # which side of the pair you look up.
    for npi, other_npi in [(match["npi_a"], match["npi_b"]), (match["npi_b"], match["npi_a"])]:
        audit_log.insert_one({
            "entity_id": npi,
            "event": f"merge {decision} against {other_npi}",
            "match_id": match_id,
            "confidence": match.get("confidence"),
            "reviewer": reviewer,
            "timestamp": now,
        })

    match_candidates.update_one({"_id": oid}, {"$set": {"status": decision}})

    return {"status": "recorded", "decision": decision, "match_id": match_id, "reviewer": reviewer}


@mcp.tool()
def get_audit_log(entity_id: str) -> list:
    """
    Return the full history of match proposals and merge decisions for
    one provider, newest first. Read-only -- safe for an agent to call
    freely. Use this to see WHY a merge was proposed and who
    approved/rejected it, before trusting any merge outcome.

    Args:
        entity_id: the NPI number to look up history for
    """
    entries = list(
        audit_log.find({"entity_id": entity_id}, {"_id": 0}).sort("timestamp", -1)
    )
    return entries


def _selftest():
    """Quick manual check: exercises all 4 tools against the real DB
    without needing an MCP client. Run with: python3 mcp_server.py --selftest
    """
    print("1. search_candidates(name='peterson'):")
    for doc in search_candidates(name="peterson"):
        print(" ", doc.get("npi"), doc.get("first_name"), doc.get("last_name"))

    print("\n2. Finding a real match_candidates _id to test with...")
    sample = match_candidates.find_one({})
    if not sample:
        print("  No match_candidates found -- run match_nppes.py first.")
        return
    match_id = str(sample["_id"])
    print(f"  Using match_id={match_id}")

    print("\n3. propose_match(match_id):")
    print(" ", propose_match(match_id))

    print("\n4. approve_merge with bad decision (should error):")
    print(" ", approve_merge(match_id, "maybe", "andrew"))

    print("\n5. approve_merge with no reviewer (should error):")
    print(" ", approve_merge(match_id, "approved", ""))

    print("\n6. approve_merge, valid call:")
    result = approve_merge(match_id, "approved", "andrew")
    print(" ", result)

    print("\n7. get_audit_log for npi_a:")
    for entry in get_audit_log(sample["npi_a"]):
        print(" ", entry)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        mcp.run(transport="stdio")