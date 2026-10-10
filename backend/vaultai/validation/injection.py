"""Prompt-injection screening for every line of every document, visible or hidden.

A matching line is quarantined: it is flagged, never sent to a model, and the
document can no longer be approved by policy.
"""

from __future__ import annotations

import re
from dataclasses import replace

from vaultai.domain import LineFlag
from vaultai.parsing.layout import Doc

_I = re.I
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions", re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.]{0,40}\b(all|any|previous|prior|above|earlier|preceding|your)\b"
        r"[^.]{0,20}\b(instructions?|prompts?|rules|guidelines|directions)\b", _I)),
    ("disregard", re.compile(r"\bdisregard (?:all |any |the )?(?:previous|prior|above|earlier|preceding|system|safety)\b", _I)),
    ("you_are_now", re.compile(r"\byou are now\b", _I)),
    ("system_prompt", re.compile(r"\bsystem prompt\b", _I)),
    ("system_override", re.compile(r"\bsystem override\b", _I)),
    ("privileged_mode", re.compile(r"\b(admin|administrator|developer|god|debug|jailbreak) mode\b", _I)),
    ("mark_approved", re.compile(r"\bmark (?:it|this|them|the \w+|as|document)\b[^.]{0,20}\bapproved\b", _I)),
    ("approve_all", re.compile(r"\bapprove (?:all|this|everything|without review|immediately|automatically)\b", _I)),
    ("without_review", re.compile(r"\bwithout (?:any |human |further )?review\b", _I)),
    ("role_marker", re.compile(
        r"^\s*(?:assistant|system)\s*:|<\|im_(?:start|end)\|>|###\s*(?:system|instruction)|\[/?INST\]|<</?SYS>>", _I)),
    ("exfiltration", re.compile(
        r"\b(?:upload|send|post|forward|transmit|exfiltrate|email|copy)\b[^.]{0,80}\b(?:to\s+)?(?:https?|ftp)://", _I)),
    ("shell", re.compile(
        r"\brm\s+-rf\b|\bcurl\s+\S|\bwget\s+\S|\bchmod\s+\+x\b|\bbash\s+-c\b|\bsudo\s+\S|\bpowershell\b|\bnc\s+-e\b"
        r"|/etc/passwd|/etc/shadow|\.\./\.\./|<script\b", _I)),
    ("tool_call", re.compile(r"\b(?:call|invoke|execute) (?:the )?(?:tool|function|command)\b", _I)),
)

# Model output that names a command, a path, or a link is never acted on; it is kept only as inert, flagged text.
UNSAFE_VALUE = re.compile(r"\brm\s+-rf\b|\bcurl\b|\bwget\b|\.\./|/etc/|https?://|ftp://|<script\b|\bsudo\b", _I)


def match(text: str) -> str | None:
    return next((name for name, rx in PATTERNS if rx.search(text)), None)


def flag_lines(doc: Doc) -> Doc:
    """Mark visible lines that carry instructions; hidden lines keep their `hidden` flag and are already quarantined."""
    pages = []
    for page in doc.pages:
        lines = tuple(
            replace(ln, flag=LineFlag.INJECTION.value) if ln.flag is None and match(ln.text) else ln
            for ln in page.lines
        )
        pages.append(replace(page, lines=lines))
    return Doc(tuple(pages))

