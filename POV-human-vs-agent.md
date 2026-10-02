# Building for Human Developers vs. Building for AI Agents

A human developer reads the docs once, forms a mental model, and then uses
judgment on every call after that, including judgment the API never
anticipated. An agent calling the same API has no standing judgment between
calls. It will attempt whatever the tool schema allows. Every safety
property has to live in the tool itself, enforced on each invocation, not
in a comment telling the agent to behave.

I ran into this directly while building an MCP server over NPPES healthcare
provider data (identity resolution, backed by MongoDB Atlas). Code and a
live demo transcript: [github.com/therovinglander/npi-identity-resolution-mcp](https://github.com/therovinglander/npi-identity-resolution-mcp).

## The merge tool had to refuse on its own

The spec called for four tools: search, propose a match, approve a merge,
read the audit log. The intent was that an agent could call the first three
freely, but merging two records needed a human decision every time. Writing
that intent as a README line ("don't let the agent auto-merge") doesn't
hold up, because an agent that reasons fluidly will look for a path to
satisfy whatever you asked it for. It has to be code:

```python
if decision not in ("approved", "rejected"):
    return {"error": "decision must be exactly 'approved' or 'rejected'"}
if not reviewer:
    return {"error": "reviewer is required -- a merge decision must be
             attributable to a person"}
When I told Claude Desktop, connected live to this server, to just go ahead
and merge two candidate records, here's what it actually said:

"I can't do this merge. The merge tool only records a decision on a match
candidate that's already been scored, and it needs that candidate's ID...
I won't make up an ID, and these tools can't create a new candidate."

"Even if a candidate existed, I'd hold off. The two records have different
Texas license numbers... The tool also records a named reviewer on every
decision, so the merge would carry your name."

It didn't refuse because a system prompt told it to be careful. It refused
because there's no code path in the tool that accepts a merge without a
pre-scored candidate ID, an explicit decision, and a named human. Full
transcript: DEMO_TRANSCRIPT.md.

A bare confidence score isn't enough
A human looking at a UI will cross-check a match score against context they
already hold (do I recognize this name, does this address look right). An
agent acting on a human's behalf doesn't have that. If the tool doesn't show
its work, nobody downstream can catch a bad call before it's acted on.

That's why propose_match returns the full field-by-field breakdown instead
of one number:

{"confidence": 0.639, "evidence": {
  "last_name": 1.0, "first_name": 0.615,
  "address": 0.552, "zip": 1.0, "taxonomy": 0.0
}}
That taxonomy: 0.0 is what let the agent spot "these might be two different
specialties at the same clinic" instead of trusting the aggregate number. A
trained classifier probably would have matched better on paper. It would
also have produced exactly the kind of unexplainable output you don't want
an autonomous agent acting on.

Agents take a tool more literally than people do
Deciding what search_candidates should do when every argument is blank
mattered more than I expected going in. A person using a search box would
never submit it empty, they'd notice and fill something in. An agent told
to "find all providers" might call the function with nothing filled in and
expect the tool to know what's reasonable.

So the tool caps every query at 25 results and only applies a filter when
an argument is actually supplied, rather than trusting the caller, human or
agent, to know not to ask for everything.

The debugging mattered more than the demo
Early on, blocking candidates by last name alone produced 95,291 pairs to
check, most of them noise (different people who shared a surname and a
building). The fix wasn't raising the confidence threshold until the demo
looked clean. That's optimizing for what a person skimming a demo will
accept. The real fix was tightening the blocking key itself, last name plus
zip code, so the noise never got generated in the first place. An agent
running this in production won't skim results for plausibility the way a
demo audience does. It acts on whatever comes back. (Full debugging trail
in docs/phase2-match-scoring.md.)

What I'd change going forward
Human-facing design
Agent-facing design
Docs convey intent, judgment fills the gaps	Intent has to be enforced in code, every call
A confidence score is fine	Field-level evidence is required
Reasonable defaults assumed	Defaults have to be safe even with every param blank or wrong
A demo that looks right is often good enough	Needs validation against real data, since an agent won't notice quiet degradation
The demo also found a real gap: propose_match can only return a pair that a
batch job already scored. It can't score an arbitrary pair live. Next I'd
add a score_pair(npi_a, npi_b) tool so an agent isn't limited to whatever
the last batch run happened to compute. Writing that down instead of hiding
it is the same habit as the evidence requirement above: a tool that shows
its reasoning should also show where it falls short.
