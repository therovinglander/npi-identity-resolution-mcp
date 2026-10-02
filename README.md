# NPPES Provider Identity Resolution: MCP Server

An MCP server exposing identity-resolution tools over U.S. healthcare
provider data (NPPES), backed by MongoDB Atlas. An AI agent can search
providers and propose matches on its own, but merging two records always
needs an explicit, named human decision. That boundary lives in the tool
code, not in a policy document.

Read the reasoning behind that design, with a real unscripted transcript of
an agent refusing to merge: [POV-human-vs-agent.md](POV-human-vs-agent.md)
and [DEMO_TRANSCRIPT.md](DEMO_TRANSCRIPT.md).

## The four tools

| Tool | Access | Purpose |
|---|---|---|
| search_candidates | Read-only | Find providers by name, NPI, organization, or location |
| propose_match | Read-only | Return a scored candidate pair with per-field evidence |
| approve_merge | Write, human-gated | Record a merge decision. Requires an explicit decision and a named reviewer every call |
| get_audit_log | Read-only | Full history of proposals and decisions for one provider |

approve_merge is the only write path. It rejects anything that isn't
exactly "approved" or "rejected," and it rejects any call missing a
reviewer name. There's no code path that merges two records on its own.

## Why MongoDB

NPPES records are messy and semi-structured: repeated taxonomy blocks,
optional fields, inconsistent formatting. That fits a document model better
than a rigid relational schema. The audit log also benefits from easy
denormalization, trading some duplicate storage for simpler reads (an entry
gets written under both NPIs in a merge, not joined at read time).

## Why weighted linear scoring, not ML

There's no labeled training data for this dataset, and the goal was a
matcher whose reasoning you can actually check, not a black box. Every
score traces to five field comparisons (last name, first name, address,
zip, taxonomy) with explicit weights. See
[docs/phase2-match-scoring.md](docs/phase2-match-scoring.md) for the full
reasoning, including two real false-positive bugs traced back to root
cause in the raw data, not fixed by nudging a threshold until the demo
looked clean.

## Architecture

NPPES bulk CSV (public, free, no auth required)
|
v
ingest_nppes.py -> MongoDB Atlas (providers collection)
|
v
match_nppes.py -> match_candidates collection (scored, with evidence)
|
v
mcp_server.py -> 4 MCP tools (search, propose, approve, audit)
|
v
Any MCP client (Claude Desktop, Claude Code)


## Setup

Requires Python 3.10+ and a MongoDB Atlas cluster (the free M0 tier works
fine for a sample dataset).

```bash
pip install "mcp<2" pymongo
export MONGODB_URI="your-atlas-connection-string"
1. Ingest NPPES data
Download a weekly or monthly NPPES file from
download.cms.gov/nppes,
then:

python3 ingest_nppes.py --file npidata_pfile_XXXXXXXX.csv --db production --collection providers
2. Generate match candidates
python3 match_nppes.py --db production --dry-run   # preview, no writes
python3 match_nppes.py --db production              # writes match_candidates
3. Run the MCP server
Standalone self-test, no MCP client needed:

python3 mcp_server.py --selftest
As an MCP server for Claude Desktop or Claude Code, add it to your client's
MCP config pointing at this command:

python3 mcp_server.py
Known limitations
Blocking key (last_name + zip5) misses true duplicates whose zip
genuinely differs, for example a provider who changed practice
locations. This was a deliberate precision-over-recall tradeoff after
tracing most false positives to same-surname, different-zip pairs. See
docs/phase2-match-scoring.md.
propose_match can only return a pair the batch job already scored. It
can't score an arbitrary pair on demand. A live demo with Claude Desktop
surfaced this directly: see DEMO_TRANSCRIPT.md. A score_pair tool would
close the gap.
Weighted linear scoring doesn't learn field reliability from data.
Fellegi-Sunter or a trained classifier would be more rigorous, but both
need labeled match/non-match pairs, which don't exist for this dataset.
No fuzzy or phonetic name matching beyond difflib's ratio. A scope
decision to keep the scoring auditable, not an oversight.
Demo-scale dataset: tens of thousands of records from one weekly
NPPES file, not the full multi-million-record national file.
Project structure
ingest_nppes.py Ingest: CSV -> MongoDB providers collection
match_nppes.py Matching: scoring + candidate generation
mcp_server.py The 4 MCP tools
docs/phase2-match-scoring.md Matching design notes and debugging trail
DEMO_TRANSCRIPT.md Live Claude Desktop session, unedited
POV-human-vs-agent.md What this build taught about agent-facing API design