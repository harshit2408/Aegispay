---
name: standup
description: AegisPay start-of-session brief. Pulls the teammate's work and reports phase progress, what the other developer did, your next checklist items, blockers and relevant team memories. Use at the start of every session, or when asked "where are we", "what's next", "status".
---
# /standup

Delegate to the `memory-keeper` agent with: "Run the /standup procedure for Dev $AEGIS_DEV."

If `AEGIS_DEV` is unset, ask the user whether they are Dev A (backend, PayPal, agents) or Dev B
(full stack: frontend + backend, AG Studio, video) and tell them to add it to `.claude/settings.local.json`:

```json
{ "env": { "AEGIS_DEV": "A" } }
```

Also run `gh pr list` and `git branch -r --no-merged origin/main`; add PRs awaiting your review
(or the other dev's unmerged branches) to the brief. Report if your current branch is behind `origin/main`.

Present the brief in this shape (keep it under 20 lines):

```
Phase P1 (Oct 23–27) · exit: headless S1 · 4 days left · checklist 12/97
Other dev since your last session: P1-13, P1-14 done; P1-15 WIP
Your next: P1-03 …, P1-04 …, P1-05 …
PRs: #4 (B) awaiting your review; a/P1-03-x unmerged
Blocked: none
Team memory to know: SPIKE-2 = no (reads via VendorGateway)
Suggested first move: claim P1-03 → route money-paypal → agent ponytail-backend
```
