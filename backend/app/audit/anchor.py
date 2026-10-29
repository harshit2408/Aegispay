"""External R21 checkpoints on one append-only audit-anchors Git branch."""

import asyncio
import base64
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.audit.events import GENESIS_HASH, AuditIntegrityError, _timestamp

BRANCH = "refs/heads/audit-anchors"
GIT_OPERATION_SECONDS = 15


class AnchorUnavailable(RuntimeError):
    """Git checkpoint transport failed; no database-derived trust fallback is allowed."""


class AnchorConflict(AnchorUnavailable):
    """Another publisher advanced the remote; fetch and reverify before retrying."""


@dataclass(frozen=True)
class Checkpoint:
    event_seq: int
    head_hash: str
    created_at: datetime
    external_ref: str


@dataclass(frozen=True)
class Snapshot:
    checkpoint: Checkpoint | None
    commits: frozenset[str] = frozenset()


def validate_remote(remote: str) -> str:
    """Trusted configuration permits credential-free HTTPS or an existing absolute local repo."""
    try:
        url = urlsplit(remote)
        if (
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and not any(char.isspace() or ord(char) < 32 for char in remote)
        ):
            return remote
        if not url.scheme and Path(remote).is_absolute() and Path(remote).is_dir():
            return remote
        # Windows drive letters parse as schemes.
        if os.name == "nt" and Path(remote).is_absolute() and Path(remote).is_dir():
            return remote
    except (ValueError, OSError):
        pass
    raise ValueError("audit anchor repo requires credential-free HTTPS or an existing local repo")


class GitAnchors:
    """Fresh isolated Git repository per operation; remote files are never checked out."""

    def __init__(self, remote: str, token: str | None = None):
        self.remote = validate_remote(remote)
        if token and (not remote.startswith("https://") or any(ord(c) < 33 for c in token)):
            raise ValueError("invalid dedicated audit anchor token")
        self._token = token

    def _git(
        self, directory: str, *args: str, deadline: float | None = None
    ) -> subprocess.CompletedProcess:
        remaining = GIT_OPERATION_SECONDS if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise AnchorUnavailable("audit anchor operation timed out")
        env = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        env.update(
            HOME=directory,
            USERPROFILE=directory,
            XDG_CONFIG_HOME=directory,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GCM_INTERACTIVE="never",
            GIT_AUTHOR_NAME="AegisPay audit",
            GIT_AUTHOR_EMAIL="audit@aegispay.invalid",
            GIT_COMMITTER_NAME="AegisPay audit",
            GIT_COMMITTER_EMAIL="audit@aegispay.invalid",
        )
        if self._token:
            auth = base64.b64encode(("x-access-token:" + self._token).encode()).decode()
            env.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="http.extraHeader",
                GIT_CONFIG_VALUE_0="Authorization: Basic " + auth,
            )
        try:
            return subprocess.run(
                [
                    "git",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "core.hooksPath=" + os.devnull,
                    "-c",
                    "http.followRedirects=false",
                    "-c",
                    "protocol.ext.allow=never",
                    "-c",
                    "fetch.recurseSubmodules=false",
                    "-C",
                    directory,
                    *args,
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=remaining,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise AnchorUnavailable("audit anchor Git command unavailable") from None

    def _required(self, directory: str, *args: str, deadline: float) -> str:
        result = self._git(directory, *args, deadline=deadline)
        if result.returncode:
            raise AnchorUnavailable("audit anchor Git command failed")
        return result.stdout.strip()

    def _fetch(self, directory: str, *, deadline: float) -> Snapshot:
        self._required(directory, "init", "--quiet", deadline=deadline)
        listing = self._git(
            directory, "ls-remote", "--exit-code", "--refs", self.remote, BRANCH, deadline=deadline
        )
        if listing.returncode == 2:
            return Snapshot(None)
        if listing.returncode:
            raise AnchorUnavailable("audit anchor remote unavailable")
        self._required(
            directory, "fetch", "--quiet", "--no-tags", self.remote, BRANCH, deadline=deadline
        )
        self._required(directory, "update-ref", "HEAD", "FETCH_HEAD", deadline=deadline)
        tip = self._required(directory, "rev-parse", "HEAD", deadline=deadline)
        try:
            value = json.loads(
                self._required(directory, "show", "HEAD:anchor.json", deadline=deadline)
            )
            ts = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
            if (
                set(value) != {"event_seq", "head_hash", "created_at"}
                or type(value["event_seq"]) is not int
                or value["event_seq"] < 0
                or not re.fullmatch("[0-9a-f]{64}", value["head_hash"])
                or ts.tzinfo is None
            ):
                raise ValueError
            checkpoint = Checkpoint(value["event_seq"], value["head_hash"], ts, tip)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AuditIntegrityError("external audit checkpoint is malformed") from None
        commits = frozenset(
            self._required(directory, "rev-list", "HEAD", deadline=deadline).splitlines()
        )
        return Snapshot(checkpoint, commits)

    async def read(self) -> Snapshot:
        def read():
            deadline = time.monotonic() + GIT_OPERATION_SECONDS
            with tempfile.TemporaryDirectory(prefix="aegis-anchor-") as directory:
                return self._fetch(directory, deadline=deadline)

        return await asyncio.to_thread(read)

    async def publish(
        self, previous: Snapshot, *, event_seq: int, head_hash: str, now: datetime
    ) -> Snapshot:
        timestamp = _timestamp(now)
        if (
            type(event_seq) is not int
            or event_seq < 0
            or not isinstance(head_hash, str)
            or not re.fullmatch("[0-9a-f]{64}", head_hash)
            or (event_seq == 0 and head_hash != GENESIS_HASH)
            or (previous.checkpoint and event_seq < previous.checkpoint.event_seq)
            or (
                previous.checkpoint
                and event_seq == previous.checkpoint.event_seq
                and head_hash != previous.checkpoint.head_hash
            )
        ):
            raise ValueError("invalid or regressing audit checkpoint")

        def publish():
            deadline = time.monotonic() + GIT_OPERATION_SECONDS
            with tempfile.TemporaryDirectory(prefix="aegis-anchor-") as directory:
                current = self._fetch(directory, deadline=deadline)
                if current != previous:
                    raise AnchorConflict("audit anchor advanced before publication")
                value = {
                    "event_seq": event_seq,
                    "head_hash": head_hash,
                    "created_at": timestamp,
                }
                (Path(directory) / "anchor.json").write_text(
                    json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                self._required(directory, "add", "--", "anchor.json", deadline=deadline)
                self._required(
                    directory,
                    "commit",
                    "--quiet",
                    "--allow-empty",
                    "-m",
                    "Anchor audit head",
                    deadline=deadline,
                )
                tip = self._required(directory, "rev-parse", "HEAD", deadline=deadline)
                # Normal push is compare-and-swap: never overwrite another publisher's history.
                result = self._git(
                    directory, "push", "--quiet", self.remote, "HEAD:" + BRANCH, deadline=deadline
                )
                if result.returncode:
                    # A rejected push is a CAS race only if a fresh read confirms movement.
                    current = self._fetch(directory, deadline=deadline)
                    if current.checkpoint and current.checkpoint.external_ref == tip:
                        return current  # The server accepted the push before transport failed.
                    if current != previous:
                        raise AnchorConflict("audit anchor advanced during publication")
                    raise AnchorUnavailable("audit anchor publication was not confirmed")
                checkpoint = Checkpoint(event_seq, head_hash, now, tip)
                return Snapshot(checkpoint, previous.commits | {tip})

        return await asyncio.to_thread(publish)
