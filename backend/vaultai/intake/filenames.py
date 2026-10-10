"""Display names for uploaded files. Never used as a filesystem path."""

from __future__ import annotations

import unicodedata

MAX_DISPLAY_CHARS = 120
FALLBACK_NAME = "untitled"

# Normalize client spellings onto the extensions Storage accepts.
EXTENSION_ALIASES = {"jpeg": "jpg", "jpg": "jpg", "tif": "tiff", "tiff": "tiff", "pdf": "pdf", "png": "png", "txt": "txt"}


def display_name(raw: str) -> str:
    base = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    # Cc = control, Cf = format (bidi overrides, zero-width), Cs/Co/Cn = surrogates, private, unassigned.
    base = "".join(ch for ch in base if unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co", "Cn"})
    base = " ".join(base.split()).strip(" .")
    if not base or set(base) <= {"."}:
        return FALLBACK_NAME
    if len(base) > MAX_DISPLAY_CHARS:
        stem, dot, ext = base.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            base = stem[: MAX_DISPLAY_CHARS - len(ext) - 1].rstrip() + "." + ext
        else:
            base = base[:MAX_DISPLAY_CHARS].rstrip()
    return base


def extension(name: str) -> str | None:
    """The canonical storage extension for a display name, or None when it is not a supported type."""
    _, dot, ext = name.rpartition(".")
    return EXTENSION_ALIASES.get(ext.lower()) if dot else None
