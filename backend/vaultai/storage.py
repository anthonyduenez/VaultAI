"""Application-managed file storage.

Raw uploads, derived page images, capture pages, and exports live in separate
directories under data/, created owner-only (0700). Files are created with
O_EXCL and 0600 under server-generated names, so an upload can never choose
its own path or overwrite an existing file.
"""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

from vaultai.config import Settings

ALLOWED_EXTENSIONS = frozenset({"pdf", "png", "jpg", "tiff", "heic", "txt", "xlsx", "csv"})


class UnsafePath(ValueError):
    pass


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.raw = settings.raw_dir
        self.derived = settings.derived_dir
        self.exports = settings.exports_dir
        self.captures = settings.captures_dir
        self.tmp = settings.tmp_dir

    def ensure_dirs(self) -> None:
        os.umask(0o077)
        for d in (self.settings.data_dir, self.raw, self.derived, self.exports, self.captures, self.tmp):
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(d, 0o700)

    @staticmethod
    def new_name(ext: str) -> str:
        if ext not in ALLOWED_EXTENSIONS:
            raise UnsafePath(f"extension {ext!r} is not allowed")
        return f"{secrets.token_hex(16)}.{ext}"

    def resolve(self, base: Path, name: str) -> Path:
        """Join a server-generated name onto a storage dir, refusing anything that escapes it."""
        candidate = (base / name).resolve()
        if candidate.parent != base.resolve() and base.resolve() not in candidate.parents:
            raise UnsafePath("path escapes storage directory")
        return candidate

    def write_new(self, base: Path, name: str, data: bytes) -> Path:
        path = self.resolve(base, name)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return path

    def raw_path(self, stored_name: str) -> Path:
        return self.resolve(self.raw, stored_name)

    def derived_doc_dir(self, doc_id: str) -> Path:
        return self.resolve(self.derived, doc_id)

    def page_image_path(self, doc_id: str, image_name: str) -> Path:
        return self.resolve(self.derived_doc_dir(doc_id), image_name)

    def delete_document_files(self, doc_id: str, stored_name: str) -> None:
        raw = self.raw_path(stored_name)
        raw.unlink(missing_ok=True)
        shutil.rmtree(self.derived_doc_dir(doc_id), ignore_errors=True)

    def wipe(self) -> None:
        """Delete every stored file (raw, derived, captures, exports, tmp). The caller deletes the database."""
        for d in (self.raw, self.derived, self.captures, self.exports, self.tmp):
            shutil.rmtree(d, ignore_errors=True)
        self.ensure_dirs()
