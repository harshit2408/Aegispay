"""Verify complete instruction preservation and drift detection for the host port."""

import contextlib
import importlib.util
import io
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "sync_context", ROOT / ".codex/scripts/sync-claude-context.py"
)
PORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PORT)


class ContextPortTests(unittest.TestCase):
    def test_every_shared_claude_file_has_an_exact_native_directory_mirror(self):
        generated = dict(PORT.generated_files())
        for source in (ROOT / ".claude").rglob("*"):
            if source.is_file() and source.name != "settings.local.json":
                target = ROOT / ".codex" / source.relative_to(ROOT / ".claude")
                self.assertEqual(generated[target], source.read_bytes())

    def test_linked_or_escaped_outputs_are_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            with patch.object(PORT, "ROOT", root):
                with self.assertRaises(ValueError):
                    PORT.safe_target(root / ".." / "outside")
                link = root / "linked"
                try:
                    link.symlink_to(Path(directory), target_is_directory=True)
                except OSError:
                    return  # Windows requires symlink privilege; escape coverage above still runs.
                with self.assertRaises(ValueError):
                    PORT.safe_target(link / "target")

    def test_all_source_agents_and_skills_preserve_full_bodies(self):
        generated = dict(PORT.generated_files())
        for source in (ROOT / ".claude/agents").glob("*.md"):
            _, header, body = source.read_text(encoding="utf-8").split("---", 2)
            meta = PORT.yaml.safe_load(header)
            output = tomllib.loads(
                generated[ROOT / ".codex/agents" / f"{meta['name']}.toml"]
            )
            self.assertEqual(output["name"], meta["name"])
            self.assertEqual(output["description"], meta["description"])
            self.assertTrue(
                output["developer_instructions"].startswith(body.lstrip("\n"))
            )
            self.assertNotIn("model", output)
        for source in (ROOT / ".claude/skills").glob("*/SKILL.md"):
            self.assertTrue(
                generated[
                    ROOT / ".agents/skills" / source.parent.name / "SKILL.md"
                ].startswith(source.read_text(encoding="utf-8"))
            )

    def test_check_detects_changed_source_without_overwriting_generated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / ".claude/agents/ledger.md"
            source.parent.mkdir(parents=True)
            source.write_text(
                "---\nname: ledger\ndescription: Money\n---\nNever use floats.\n",
                encoding="utf-8",
            )
            with patch.object(PORT, "ROOT", root), patch("sys.argv", ["sync"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(PORT.main(), 0)
                target = root / ".codex/agents/ledger.toml"
                original = target.read_bytes()
                source.write_text(
                    source.read_text(encoding="utf-8") + "Never expose secrets.\n",
                    encoding="utf-8",
                )
                error = io.StringIO()
                with (
                    patch("sys.argv", ["sync", "--check"]),
                    contextlib.redirect_stderr(error),
                ):
                    self.assertEqual(PORT.main(), 1)
                self.assertIn(
                    f'"{PORT.sys.executable}" .codex/scripts/sync-claude-context.py',
                    error.getvalue(),
                )
                self.assertEqual(target.read_bytes(), original)
                source.unlink()
                with (
                    patch("sys.argv", ["sync", "--check"]),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(PORT.main(), 1)
                self.assertEqual(target.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
