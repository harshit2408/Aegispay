---
tags: [harness, obsidian]
---
# Obsidian + mind maps in AegisPay

[kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) (MIT, by Obsidian's CEO) is installed at project
scope as the `obsidian` plugin. It teaches Claude Obsidian's open formats, so Claude can write notes and mind maps
that open natively in Obsidian — and that stay plain Markdown/JSON in git.

## The vault
**Open `docs/` as an Obsidian vault** (Obsidian → Open folder as vault → `Aegispay/docs`). Nothing to install in the repo;
`docs/.obsidian/` is git-ignored, so each dev keeps their own workspace settings.

What you see in it: [[PRD_SRS]], [[PLAN]], [[CHECKLIST]], [[SESSION_LOG]], ADRs, harness docs, and the canvases in `maps/`.
Notes link each other with `[[wikilinks]]`, so the graph view shows how spec, plan, decisions and docs connect.

## Skills and when to use them

| Skill | Use it for | Example prompt |
|---|---|---|
| `obsidian:json-canvas` | **Mind maps** and node/edge diagrams as `.canvas` files | "Add a canvas mind map of the S3 BEC scenario flow to docs/maps/" |
| `obsidian:obsidian-markdown` | Notes with properties, callouts, embeds, wikilinks | "Write ADR-007 as an Obsidian note linked to [[PLAN]]" |
| `obsidian:obsidian-bases` | `.base` table views over notes' properties | "Make a base listing all ADRs with status and date" |
| `obsidian:defuddle` | Clean a web page into Markdown (PayPal/AG Studio docs for offline reference) | "Defuddle the AG Studio custom widget docs into docs/refs/" |
| `obsidian:obsidian-cli` | Drive a running Obsidian app (needs the Obsidian CLI) | optional |

The routing table sends prompts containing *mind map, canvas, obsidian, diagram, wikilink* to these skills automatically.

## Mind maps that exist
- `docs/maps/AegisPay.canvas` — system (Decide/Execute split, PayPal apps, R-rules), the phases with exit criteria,
  owners, and the harness. Open it in Obsidian; nodes link to the real notes.

## Good uses during the build
- **Spike board**: one canvas with SPIKE-1…9 as cards, coloured by result (green pass / red fallback). Update as spikes land.
- **Scenario flows**: S1–S4 as node graphs for the video storyboard (PRD §20) — Dev B can screen-grab them.
- **Decision trail**: ADR notes + team memory decisions linked from the canvas.
- **Video storyboard**: a canvas column per beat with the on-screen element and owner.

Rules: canvases and notes are documentation, not state — task state stays in [[CHECKLIST]].
Keep canvases small (≤ ~40 nodes); split by topic instead of one giant map.
