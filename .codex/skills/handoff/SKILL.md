---
name: handoff
description: AegisPay end-of-session handoff. Ticks finished checklist items, saves decisions/spike results/gotchas to the ECC team memory vault, spawns a project skill if a procedure repeated, appends docs/SESSION_LOG.md, commits and pushes. Use when the user says handoff, wrap up, end of day/session, or before switching tasks for a long time.
---
# /handoff

Delegate to the `memory-keeper` agent with: "Run the /handoff procedure for Dev $AEGIS_DEV.
Session summary: <2–5 bullets of what we did and decided in this conversation>."

Pass the summary yourself — the agent cannot see this conversation. Include:
- checklist IDs touched and their state,
- every decision or spike result (with the evidence: status codes, timings, error text),
- gotchas that cost time,
- anything the other developer must do or know first,
- current branch name and PR number/state (open, needs review, merged), plus any unmerged branch.

After the agent returns, show the user the session-log entry it wrote and the push result.
If tests have not been run since the last code change, run them first (the post-test hook syncs git).
