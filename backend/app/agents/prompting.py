"""Shared prompt helpers for zero-tool components (R14): random-boundary delimited data."""

import secrets

MAX_INPUT_TOKEN_BOUND = 272_000
FRAMING_TOKENS = 2_000


def delimit(text: str) -> tuple[str, str]:
    """Return (boundary token, body). The token never occurs in the document text."""
    while True:
        token = secrets.token_hex(16)
        if token not in text:
            break
    return token, f"BEGIN_DOCUMENT_{token}\n{text}\nEND_DOCUMENT_{token}"


def build_messages(system_template: str, text: str) -> tuple[list[dict], str, int]:
    """Return (messages, body, bound) with text-only user content."""
    token, body = delimit(text)
    system = system_template.format(token=token)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": body}]
    return msgs, body, len(system) + len(body) + FRAMING_TOKENS
