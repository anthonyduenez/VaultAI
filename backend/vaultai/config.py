"""Settings parsed once from the environment (and an optional .env file) at startup.

Every knob is a VAULTAI_* variable; .env.example documents them. Parsing fails
loudly on a bad value so misconfiguration never silently degrades privacy.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from vaultai.domain import LEGAL_DOC_TYPES, DocType, ModelKind

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
    model_name: str = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
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

    def __post_init__(self) -> None:
        _check(self)

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


_BY_TYPE = {
    "Path": _path, "str": str, "str | None": str, "int": int, "float": float, "bool": _bool,
    "Decimal": Decimal, "ModelKind": ModelKind, "frozenset[DocType]": _doc_types,
}
_PARSERS = {f.name: _BY_TYPE[f.type] for f in fields(Settings)}


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


# Explicit networks rather than ipaddress.is_private, which also counts documentation and
# benchmarking ranges (203.0.113.0/24, 198.18.0.0/15) and the unspecified 0.0.0.0 / ::.
LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
))


def is_local_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0].strip("[]"))
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in LOCAL_NETWORKS)


def is_local_host(host: str) -> bool:
    """Loopback, RFC 1918, ULA, or link-local. Hostnames other than localhost are not trusted."""
    return host in {"localhost", "localhost.localdomain"} or is_local_ip(host)


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
    _check_choices(settings)
    return settings


def _check(s: Settings) -> None:
    if s.model_backend is ModelKind.OPENAI_COMPAT:
        host = urlparse(s.model_endpoint).hostname or ""
        if not is_local_host(host):
            raise ConfigError(
                f"VAULTAI_MODEL_ENDPOINT host {host!r} is not a loopback or private-network address; "
                "VaultAI only talks to local models"
            )
    if legal := s.auto_approve_types & LEGAL_DOC_TYPES:
        raise ConfigError(
            f"VAULTAI_AUTO_APPROVE_TYPES cannot include {', '.join(sorted(legal))}; legal documents always need a human"
        )
    if not is_local_host(s.host):
        raise ConfigError("VAULTAI_HOST must be a loopback or private address; the desktop API is never public")


_CHOICES = {
    "date_order": ("MDY", "DMY"),
    "ocr_engine": ("auto", "tesseract", "none"),
    "scanner_backend": ("auto", "sane", "mock", "none"),
}


def _check_choices(s: Settings) -> None:
    for name, allowed in _CHOICES.items():
        if getattr(s, name) not in allowed:
            raise ConfigError(f"VAULTAI_{name.upper()} must be one of {', '.join(allowed)}")
