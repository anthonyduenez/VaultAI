"""Run the parser worker in a separate, resource-limited process.

The worker gets an isolated interpreter (-I: no PYTHON* variables, no user
site, no script directory on sys.path), an environment with no VAULTAI_*
values, the storage tmp dir as cwd, and a hard timeout. Every failure mode
(timeout, crash, garbage output, parser exception) becomes ParseFailed with a
fixed reason token, never document content.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from vaultai.config import Settings

WORKER = Path(__file__).with_name("worker.py")
KIND_BY_EXT = {"pdf": "pdf", "png": "image", "jpg": "image", "tiff": "image", "heic": "image", "txt": "text"}
DPI = 150
_ENV = {"PATH": os.defpath, "LC_ALL": "C"}
_MAX_STDOUT = 256 * 1024 * 1024


class ParseFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _run(settings: Settings, request: dict) -> dict:
    req = {
        **request,
        "cpu_s": settings.parse_timeout_s + 1,
        "fsize_bytes": max(64 * 1024 * 1024, settings.max_image_pixels * 4),
        "mem_bytes": settings.parse_memory_mb * 1024 * 1024,
        "max_pixels": settings.max_image_pixels,
        "max_pages": settings.max_pages,
    }
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(WORKER)],
            input=json.dumps(req).encode(),
            capture_output=True,
            cwd=settings.tmp_dir,
            env=_ENV,
            timeout=settings.parse_timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParseFailed("timeout") from exc
    if proc.returncode != 0:
        raise ParseFailed("crashed")
    if len(proc.stdout) > _MAX_STDOUT:
        raise ParseFailed("oversized_output")
    try:
        out = json.loads(proc.stdout)
    except ValueError as exc:
        raise ParseFailed("invalid_output") from exc
    if not isinstance(out, dict) or out.get("ok") is not True:
        error = out.get("error") if isinstance(out, dict) else None
        raise ParseFailed(str(error)[:60] if error else "parser_error")
    return out


def probe(settings: Settings, path: Path, ext: str) -> dict:
    return _run(settings, {"mode": "probe", "kind": KIND_BY_EXT[ext], "path": str(path.resolve())})


def parse(settings: Settings, path: Path, ext: str, out_dir: Path) -> dict:
    return _run(settings, {"mode": "parse", "kind": KIND_BY_EXT[ext], "path": str(path.resolve()), "out_dir": str(out_dir.resolve()), "dpi": DPI})
