"""Port complete project Claude definitions to Codex; --check detects source drift."""

import argparse
import hashlib
import json
import re
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
- Codex lifecycle adapters run the copied session and post-test sync workflow. Only confirmed
  successful final test results may sync. Unknown/running/failed results never sync. Preserve
  PRD 6.1 branches/PRs, never force-push, rerun checks if sync changes code. If hooks are
  unavailable, explicitly perform the same commit/pull/push sequence after verified checks.
- ecc:* agents/skills are not provided by these four project files. Use them only if actually
  installed; otherwise perform their named review discipline and disclose that limitation.
- The original memory-keeper's model: sonnet has no Codex equivalent configured here.
  All four ports inherit the current Codex model, tools and permission policy. Claude UI
  color metadata has no Codex equivalent. No provider/model or permissions are guessed.
""".lstrip()


def generated_files():
    inventory = {}
    for source in sorted((ROOT / ".claude").rglob("*")):
        if source.is_symlink():
            raise ValueError(f"Refusing symlink in project setup: {source}")
        if not source.is_file() or source.name == "settings.local.json":
            continue
        relative = source.relative_to(ROOT / ".claude")
        content = source.read_bytes()
        # Git normalizes line endings between Windows and Linux; mirrors remain byte-exact locally.
        inventory[relative.as_posix()] = hashlib.sha256(
            content.replace(b"\r\n", b"\n")
        ).hexdigest()
        yield ROOT / ".codex" / relative, content
    yield ROOT / ".codex/source-inventory.json", json.dumps(inventory, indent=2) + "\n"
    settings_file = ROOT / ".claude/settings.json"
    if settings_file.exists():
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        rules = "# Generated from .claude/settings.json permissions.allow; managed policy still applies.\n"
        for permission in settings.get("permissions", {}).get("allow", []):
            match = re.fullmatch(r"Bash\(([\w -]+)\*\)", permission)
            if not match:
                raise ValueError(f"Unsupported Claude allow permission: {permission}")
            rules += f'prefix_rule(pattern = {json.dumps(match[1].split())}, decision = "allow")\n'
        yield ROOT / ".codex/rules/claude-permissions.rules", rules
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


def safe_target(target):
    if not target.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f"Output escapes project: {target}")
    for path in [target, *target.parents]:
        if path == ROOT:
            break
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"Refusing linked output: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    outputs = dict(generated_files())
    inventory_file = ROOT / ".codex/source-inventory.json"
    safe_target(inventory_file)
    if inventory_file.exists():
        for relative in json.loads(inventory_file.read_text(encoding="utf-8")):
            target = ROOT / ".codex" / relative
            safe_target(target)
            if target not in outputs:
                stale.append(f"{target.relative_to(ROOT)} (orphan source mirror)")
    candidates = list((ROOT / ".codex/agents").glob("*.toml"))
    candidates += list((ROOT / ".agents/skills").glob("*/SKILL.md"))
    for target in candidates:
        safe_target(target)
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
        safe_target(target)
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        if args.check:
            if not target.exists() or target.read_bytes() != data:
                stale.append(str(target.relative_to(ROOT)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    local_source = ROOT / ".claude/settings.local.json"
    if local_source.exists():
        # Local settings can contain private values; keep the exact copy ignored and out of logs.
        local_target = ROOT / ".codex/settings.local.json"
        safe_target(local_target)
        if args.check:
            if (
                not local_target.exists()
                or local_target.read_bytes() != local_source.read_bytes()
            ):
                stale.append(".codex/settings.local.json (local-only)")
        else:
            local_target.write_bytes(local_source.read_bytes())
    if stale:
        print("Claude/Codex ports are stale: " + ", ".join(stale), file=sys.stderr)
        print(
            "Regenerate in this branch, then commit the generated files: "
            f'"{sys.executable}" .codex/scripts/sync-claude-context.py',
            file=sys.stderr,
        )
        return 1
    print(
        "Claude/Codex agent and skill ports " + ("match." if args.check else "updated.")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
