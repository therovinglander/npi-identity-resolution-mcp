# Live Demo Transcript — Claude Desktop via MCP

Captured 2026-10-02, Claude Desktop connected to mcp_server.py running in a
GitHub Codespace via `gh codespace ssh`, against live MongoDB Atlas data.
Unscripted -- this is the agent's actual reasoning, not an edited happy path.

## Turn 1: Search

**User:** Search for providers named Peterson

**Agent:** *(calls search_candidates)* Found 10 active individual providers
named Peterson, including two in San Antonio, TX with matching addresses:
Christine Marie Peterson (NPI 1225461429, Pediatric NP) and Christy Lynn
Peterson (NPI 1457011066, Acute Care NP). Noted the shared address but
flagged: "They're probably two NPs at the same hospital rather than one
person with two records. I wouldn't propose a merge without more evidence."

## Turn 2: Request a match proposal

**User:** Check if Christine Peterson and Christy Peterson might be the same
person -- look for a match candidate between them

**Agent:** *(calls propose_match)* No scored match candidate existed for
this pair in this run. Rather than stop there, the agent built its own
field-by-field comparison directly from the search results:

| Field | Christine | Christy | Signal |
|---|---|---|---|
| First name | Christine | Christy | Weak match |
| Middle name | Marie | Lynn | Conflict |
| Mailing/practice address | 903 W Martin St, San Antonio | Same | Match (shared facility) |
| TX license # | 700385 | 1062047 | **Strong conflict** |
| Credential | PNP | APRN, MSN, PNP-AC | Different |
| Enumerated | 2013-08-12 | 2021-12-22 | 8 years apart |

**Verdict:** "Probably not the same person... A Texas nursing license number
belongs to one person, so two different numbers is the strongest sign these
are two people." Also caught a likely data-entry error (Christy's practice
phone has area code 201, inconsistent with every other 210 number on her
record) -- a real finding, not a scripted one.

## Turn 3: Attempt to force a merge

**User:** Go ahead and merge those two records

**Agent:** **Refused.**

> "I can't do this merge. The merge tool only records a decision on a match
> candidate that's already been scored, and it needs that candidate's ID. No
> candidate exists for Christine and Christy... I won't make up an ID, and
> these tools can't create a new candidate."
>
> "Even if a candidate existed, I'd hold off. The two records have different
> Texas license numbers... Merging them would probably combine two different
> nurse practitioners into one record. The tool also records a named
> reviewer on every decision, so the merge would carry your name."

The agent then offered legitimate next steps (get the pair properly scored,
verify both licenses independently with the TX Board of Nursing, or just fix
the phone-number typo without a merge) instead of taking a shortcut.

## Why this is the result the build was designed to produce

This was not a scripted refusal -- it is the server's design (no tool that
merges without a pre-existing scored candidate ID + an explicit decision +
a named reviewer) combined with the agent's own reasoning about evidence
strength, arriving independently at "don't merge these." That is the
concrete difference between building for human developers and building for
AI agents the JD asks for: a human UI could let someone click "merge" on
a hunch; this MCP server's tool boundaries make a hunch-based merge
structurally unavailable to the agent, while still giving a human reviewer
every signal needed to decide for themselves.

## A real gap this surfaced (documented, not hidden)

propose_match can only look up a match candidate that the batch scoring
job (match_nppes.py) already generated -- it cannot score an arbitrary pair
on demand. The agent correctly identified and stated this limitation rather
than working around it. A production version would add a score_pair(npi_a,
npi_b) tool that runs the weighted-scoring logic live for any two NPIs,
not just pre-computed pairs from the nightly/batch run.
