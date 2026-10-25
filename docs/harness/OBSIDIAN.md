---
tags: [harness, obsidian]
---
# Obsidian + mind maps in AegisPay

[kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) (MIT, by Obsidian's CEO) is installed at project
scope as the `obsidian` plugin. It teaches Claude Obsidian's open formats, so Claude can write notes and mind maps
that open natively in Obsidian — and that stay plain Markdown/JSON in git.

## The vault
**Use `docs/` as the Obsidian vault**, rather than the repository root. On Dev B's machine it is registered
as its own vault, with local wikilink/automatic-link-update settings and core Canvas/Bases/graph plugins.
Existing personal and repository-root vault registrations were preserved. `docs/.obsidian/` remains
git-ignored, so each developer keeps their own workspace settings.

Claude's `obsidian@obsidian-skills` **1.0.1** was installed at project scope on 2026-10-25 and verified
with `claude plugin list --json` (`enabled: true`, `projectEnabled: true`, this repository's project path).
An enabled entry in `.claude/settings.json` alone does not install the plugin. To repeat setup on another machine:

```powershell
claude plugin install obsidian@obsidian-skills --scope project
claude plugin list --json
```

The plugin teaches file formats to Claude; it is not an Obsidian community plugin or an MCP server.
Claude can edit the vault's Markdown, Canvas and Bases files directly. Open a new Claude session after
installation for plugin discovery. No additional credential or Obsidian community-plugin installation is needed.

What you see in it: [[PRD_SRS]], [[PLAN]], [[CHECKLIST]], [[SESSION_LOG]], ADRs, harness docs, and the canvases in `maps/`.
Notes link each other with `[[wikilinks]]`, so the graph view shows how spec, plan, decisions and docs connect.

## Skills and when to use them

| Skill | Use it for | Example prompt |
|---|---|---|
| `obsidian:json-canvas` | **Mind maps** and node/edge diagrams as `.canvas` files | "Add a canvas mind map of the S3 BEC scenario flow to docs/maps/" |
| `obsidian:obsidian-markdown` | Notes with properties, callouts, embeds, wikilinks | "Write ADR-007 as an Obsidian note linked to [[PLAN]]" |
| `obsidian:obsidian-bases` | `.base` table views over notes' properties | "Make a base listing all ADRs with status and date" |
| `obsidian:defuddle` | Clean a web page into Markdown for local reference | "Defuddle the AG Studio widget docs into docs/refs/" |
| `obsidian:obsidian-cli` | Drive the running Obsidian app through its native CLI | "Read [[PLAN]] from the AegisPay docs vault" |
| `obsidian:knap` | Render templates and JSON/CSV data into Markdown notes | "Render an ADR template using this JSON" |

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

## Native CLI on Dev B's Windows machine

The installed Obsidian app includes `Obsidian.com`. Its supported CLI setting is enabled, and the installed
Obsidian directory is registered on the user PATH (existing entries preserved). Restart a terminal/Claude
process to inherit the updated PATH. The app must be running; it was started hidden for verification.
In an existing terminal, use the full launcher path instead of relying on a stale PATH:

```powershell
$obsidian = Join-Path $env:LOCALAPPDATA 'Programs/Obsidian/Obsidian.com'
& $obsidian vault=docs vault info=path
& $obsidian vault=docs files ext=md total
& $obsidian vault=docs file path=maps/AegisPay.canvas
```

When several vaults have the same name, use the vault ID shown by `obsidian vaults verbose`.
Local verification used the registered docs vault ID and returned the exact `Aegispay/docs` path,
12 Markdown notes, and the existing `maps/AegisPay.canvas` (8901 bytes). The canvas JSON also passed
node/edge validation (27 unique nodes, 21 edges). These are observed local checks, not a claim that
Claude's model was prompted to use every skill or that a visual layout review was performed.

The optional format-helper CLIs are installed too: Defuddle **0.19.6** and Knap **0.6.0**.
Local smoke checks extracted Markdown from a temporary HTML fixture and rendered an AegisPay heading
from a temporary JSON fixture. To install them on another machine: `npm install -g defuddle knap`.

Official [CLI setup and vault targeting](https://obsidian.md/help/cli) documents the running-app requirement
and CLI registration. [Obsidian URI](https://help.obsidian.md/Extending+Obsidian/Obsidian+URI) can open the canvas
without a remote connection. All configuration and checks here are local; no vault was published or synced.
