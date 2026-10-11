"""OCR for page images that have no text layer.

Engines come from a registry keyed by settings.ocr_engine, so tests (and a
future engine) register an entry instead of patching internals. Tesseract runs
as its own process on the PNG the sandboxed parser re-encoded, never on the
uploaded bytes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from functools import lru_cache
from typing import Protocol

from vaultai.config import Settings
from vaultai.parsing.layout import Word

_ENV = {"PATH": os.defpath, "LC_ALL": "C", "OMP_THREAD_LIMIT": "1"}


class OcrEngine(Protocol):
    name: str

    def read(self, png: bytes) -> list[Word]:
        """Words with normalized top-left boxes and confidence in 0..1."""
        ...


def parse_tsv(tsv: str) -> list[Word]:
    rows = [r.split("\t") for r in tsv.splitlines()]
    if not rows or rows[0][:1] != ["level"]:
        return []
    head = {name: i for i, name in enumerate(rows[0])}
    page_w = page_h = 0.0
    words: list[Word] = []
    for r in rows[1:]:
        if len(r) < len(head):
            continue
        level = r[head["level"]]
        left, top, w, h = (float(r[head[k]]) for k in ("left", "top", "width", "height"))
        if level == "1":
            page_w, page_h = w, h
            continue
        text, conf = r[head["text"]].strip(), float(r[head["conf"]])
        if level != "5" or not text or conf < 0 or not page_w or not page_h:
            continue
        words.append(Word(text, (left / page_w, top / page_h, (left + w) / page_w, (top + h) / page_h), round(conf / 100, 4)))
    return words


class Tesseract:
    def __init__(self, cmd: str, version: str, timeout_s: int) -> None:
        self.cmd = cmd
        self.name = f"tesseract {version}"
        self.timeout_s = timeout_s

    def read(self, png: bytes) -> list[Word]:
        proc = subprocess.run([self.cmd, "stdin", "stdout", "--psm", "3", "tsv"], input=png, capture_output=True,
                              env=_ENV, timeout=self.timeout_s, check=False)
        if proc.returncode != 0:
            raise RuntimeError("tesseract failed")
        return parse_tsv(proc.stdout.decode("utf-8", "replace"))


@lru_cache(maxsize=4)
def _version(cmd: str) -> str | None:
    try:
        proc = subprocess.run([cmd, "--version"], capture_output=True, env=_ENV, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = (proc.stdout or proc.stderr).decode("utf-8", "replace").strip().splitlines()
    return first[0].split()[-1] if first and first[0].lower().startswith("tesseract") else None


def _tesseract(settings: Settings) -> OcrEngine | None:
    cmd = shutil.which(settings.tesseract_cmd)
    version = _version(cmd) if cmd else None
    return Tesseract(cmd, version, settings.parse_timeout_s) if cmd and version else None


ENGINES: dict[str, Callable[[Settings], OcrEngine | None]] = {
    "auto": _tesseract,
    "tesseract": _tesseract,
    "none": lambda _settings: None,
}


def engine_for(settings: Settings) -> OcrEngine | None:
    return ENGINES.get(settings.ocr_engine, ENGINES["none"])(settings)


def ocr_status(settings: Settings) -> tuple[bool, str | None]:
    engine = engine_for(settings)
    return (engine is not None, engine.name if engine else None)
