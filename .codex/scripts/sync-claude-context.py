"""Port complete project Claude definitions to Codex; --check detects source drift."""

import argparse
import json
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HOST_MAPPING = """
## Codex host mapping

The original project instructions above are preserved in full. For Codex-specific operations:
- Dev B uses Codex; default AEGIS_DEV=B. Dev A keeps Claude. Follow root AGENTS.md and PRD.
- The four project agents are native .codex/agents/*.toml definitions. Delegate to the named
  agent using the available agent tool. If custom roles are unavailable in this client, pass
  that definition's complete developer_instructions to a worker; do not silently omit the role.
- /standup and /handoff mean $standup and $handoff. The canonical Codex skills live in
  .agents/skills/. Create/update repeated-procedure skills there; then regenerate the Claude
  ports with .venv/Scripts/python.exe .codex/scripts/sync-claude-context.py after source edits.
- .claude/settings.local.json is Claude-only. Codex Dev B defaults live in .codex/config.toml.
- Use --source-harness codex for ECC writes. Personal scratch belongs to Codex-local context,
  not Claude auto-memory. Shared knowledge still lives in the existing ECC team vault.
- Read/Edit/Bash/Grep/Glob mean Codex file, shell and search tools, under inherited permissions.
  Use shell-appropriate syntax (PowerShell on Windows; body files instead of Bash heredocs).
- Claude post-test hooks do not run in Codex. Inspect final exit codes, then explicitly run
  the required commit/pull/push sequence. Preserve PRD 6.1 branches/PRs, never force-push,
  and rerun checks if sync changes code. An unavailable upstream is a reported blocker.
- ecc:* agents/skills are not provided by these four project files. Use them only if actually
  installed; otherwise perform their named review discipline and disclose that limitation.
- The original memory-keeper's model: sonnet has no Codex equivalent configured here.
  All four ports inherit the current Codex model, tools and permission policy. Claude UI
  color metadata has no Codex equivalent. No provider/model or permissions are guessed.
""".lstrip()


def generated_files():
    for source in sorted((ROOT / ".claude/agents").glob("*.md")):
        text = source.read_text(encoding="utf-8")
        _, header, body = text.split("---", 2)
        meta = yaml.safe_load(header)
        instructions = body.lstrip("\n") + "\n" + HOST_MAPPING
        fields = {
            "name": meta["name"],
            "description": meta["description"],
            "developer_instructions": instructions,
        }
        if "'''" in instructions:
            raise ValueError(f"{source}: TOML literal delimiter in instructions")
        content = f"# Generated from {source.relative_to(ROOT).as_posix()}; edit the source.\n"
        content += f"name = {json.dumps(meta['name'])}\n"
        content += (
            f"description = {json.dumps(meta['description'], ensure_ascii=False)}\n"
        )
        content += f"developer_instructions = '''\n{instructions}'''\n"
        assert tomllib.loads(content) == fields
        yield ROOT / ".codex/agents" / f"{meta['name']}.toml", content
    for source in sorted((ROOT / ".claude/skills").glob("*/SKILL.md")):
        content = source.read_text(encoding="utf-8")
        yield (
            ROOT / ".agents/skills" / source.parent.name / "SKILL.md",
            content + "\n" + HOST_MAPPING,
        )
    for name in ("standup", "handoff"):
        alias = f"aegis-{name}"
        content = f"---\nname: {alias}\ndescription: Compatibility alias for AegisPay {name}. "
        content += f"Use when the user invokes {alias}.\n---\n\n"
        content += f"Follow `.agents/skills/{name}/SKILL.md` in full, including delegation to `memory-keeper`.\n"
        yield ROOT / ".agents/skills" / alias / "SKILL.md", content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    outputs = dict(generated_files())
    candidates = list((ROOT / ".codex/agents").glob("*.toml"))
    candidates += list((ROOT / ".agents/skills").glob("*/SKILL.md"))
    for target in candidates:
        content = target.read_text(encoding="utf-8")
        generated = (
            content.startswith("# Generated from .claude/agents/")
            or HOST_MAPPING in content
        )
        if generated and target not in outputs:
            stale.append(
                f"{target.relative_to(ROOT)} (orphan; remove after reviewing source deletion)"
            )
    for target, content in outputs.items():
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
    if stale:
        print("Claude/Codex ports are stale: " + ", ".join(stale), file=sys.stderr)
        return 1
    print(
        "Claude/Codex agent and skill ports " + ("match." if args.check else "updated.")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
