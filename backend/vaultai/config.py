"""Settings parsed once from the environment (and an optional .env file) at startup.

Every knob is a VAULTAI_* variable; .env.example documents them. Parsing fails
loudly on a bad value so misconfiguration never silently degrades privacy.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from vaultai.domain import DocType, ModelKind

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    data_dir: Path = REPO_ROOT / "data"
    samples_dir: Path = REPO_ROOT / "samples"
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    host: str = "127.0.0.1"
    port: int = 8765
    reviewer_name: str = "local-user"

    max_file_mb: int = 25
    max_pages: int = 50
    max_image_pixels: int = 40_000_000
    max_batch_files: int = 200
    reject_active_pdf: bool = True
    parse_timeout_s: int = 60
    parse_memory_mb: int = 1536
    workers: int = 2

    model_backend: ModelKind = ModelKind.MOCK
    model_endpoint: str = "http://127.0.0.1:8000/v1"
    model_name: str = "nemotron"
    model_api_key: str | None = None
    model_context_tokens: int = 8192
    model_max_tokens: int = 1024
    model_timeout_s: float = 60.0
    model_temperature: float = 0.0
    model_vision: bool = False
    model_json_mode: bool = True
    model_system_prefix: str = ""

    ocr_engine: str = "auto"
    tesseract_cmd: str = "tesseract"

    auto_approve_types: frozenset[DocType] = field(default_factory=lambda: frozenset({DocType.RECEIPT}))
    auto_approve_min_confidence: float = 0.9
    review_confidence_threshold: float = 0.75
    unusual_amount: Decimal = Decimal("10000")
    date_order: str = "MDY"

    phone_enabled: bool = False
    phone_host: str | None = None
    phone_port: int = 8766
    phone_session_ttl_s: int = 600
    phone_max_pages: int = 20
    phone_max_image_mb: int = 12

    scanner_backend: str = "auto"
    egress_guard: bool = True

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def captures_dir(self) -> Path:
        return self.data_dir / "captures"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vaultai.db"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


def _bool(v: str) -> bool:
    s = v.strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"expected a boolean, got {v!r}")


def _doc_types(v: str) -> frozenset[DocType]:
    return frozenset(DocType(t.strip()) for t in v.split(",") if t.strip())


def _path(v: str) -> Path:
    p = Path(v).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


_PARSERS = {
    "data_dir": _path, "samples_dir": _path, "frontend_dist": _path, "host": str, "port": int,
    "reviewer_name": str, "max_file_mb": int, "max_pages": int, "max_image_pixels": int,
    "max_batch_files": int, "reject_active_pdf": _bool, "parse_timeout_s": int, "parse_memory_mb": int,
    "workers": int, "model_backend": ModelKind, "model_endpoint": str, "model_name": str,
    "model_api_key": str, "model_context_tokens": int, "model_max_tokens": int, "model_timeout_s": float,
    "model_temperature": float, "model_vision": _bool, "model_json_mode": _bool, "model_system_prefix": str,
    "ocr_engine": str, "tesseract_cmd": str, "auto_approve_types": _doc_types,
    "auto_approve_min_confidence": float, "review_confidence_threshold": float, "unusual_amount": Decimal,
    "date_order": str, "phone_enabled": _bool, "phone_host": str, "phone_port": int,
    "phone_session_ttl_s": int, "phone_max_pages": int, "phone_max_image_mb": int,
    "scanner_backend": str, "egress_guard": _bool,
}


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def is_local_host(host: str) -> bool:
    """True for loopback, RFC1918/ULA private, and link-local hosts. Hostnames other than localhost are not trusted."""
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    # 0.0.0.0 and :: count as "private" to ipaddress but mean every interface.
    if ip.is_unspecified:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if env is None:
        file_env = read_env_file(Path(os.environ.get("VAULTAI_ENV_FILE", REPO_ROOT / ".env")))
        env = {**file_env, **os.environ}
    kwargs = {}
    for name, parse in _PARSERS.items():
        raw = env.get(f"VAULTAI_{name.upper()}")
        if raw is None or raw == "":
            continue
        try:
            kwargs[name] = parse(raw)
        except (ValueError, ArithmeticError) as exc:
            raise ConfigError(f"VAULTAI_{name.upper()}: {exc}") from exc
    settings = Settings(**kwargs)
    _check(settings)
    return settings


def _check(s: Settings) -> None:
    if s.model_backend is ModelKind.OPENAI_COMPAT:
        host = urlparse(s.model_endpoint).hostname or ""
        if not is_local_host(host):
            raise ConfigError(
                f"VAULTAI_MODEL_ENDPOINT host {host!r} is not a loopback or private-network address; "
                "VaultAI only talks to local models"
            )
    if s.date_order not in {"MDY", "DMY"}:
        raise ConfigError("VAULTAI_DATE_ORDER must be MDY or DMY")
    if s.ocr_engine not in {"auto", "tesseract", "none"}:
        raise ConfigError("VAULTAI_OCR_ENGINE must be auto, tesseract, or none")
    if s.scanner_backend not in {"auto", "sane", "mock", "none"}:
        raise ConfigError("VAULTAI_SCANNER_BACKEND must be auto, sane, mock, or none")
    if not is_local_host(s.host):
        raise ConfigError("VAULTAI_HOST must be a loopback or private address; the desktop API is never public")
