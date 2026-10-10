"""Throughput, review, and host metrics computed from the database and the OS on request."""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import threading
import time

from vaultai.context import AppContext
from vaultai.domain import AuditAction, DocStatus, DocTiming, GpuStatus, Metrics, Route, Severity
from vaultai.pipeline.cache import HITS, MISSES

GPU_TTL_S = 5.0
# A document counts as human-reviewed once a person approved, rejected, deferred, or corrected it.
REVIEW_ACTIONS = tuple(a.value for a in (AuditAction.APPROVE, AuditAction.REJECT, AuditAction.DEFER, AuditAction.EDIT_FIELD))
_REVIEWED = f"action IN ({', '.join('?' * len(REVIEW_ACTIONS))})"
_gpu_lock = threading.Lock()
_gpu_cache: tuple[float, GpuStatus | None] | None = None


def _number(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None


def gpu_status() -> GpuStatus | None:
    global _gpu_cache
    with _gpu_lock:
        if _gpu_cache and time.monotonic() - _gpu_cache[0] < GPU_TTL_S:
            return _gpu_cache[1]
    status = None
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            proc = subprocess.run(
                [exe, "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, timeout=2, check=False, env={"PATH": os.defpath},
            )
            first = proc.stdout.decode("utf-8", "replace").strip().splitlines()
            parts = [p.strip() for p in first[0].split(",")] if first else []
            if proc.returncode == 0 and len(parts) >= 4:
                status = GpuStatus(name=parts[0], memory_used_mb=_number(parts[1]), memory_total_mb=_number(parts[2]),
                                   utilization_pct=_number(parts[3]))
        except (OSError, subprocess.TimeoutExpired):
            status = None
    with _gpu_lock:
        _gpu_cache = (time.monotonic(), status)
    return status


def _rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports kilobytes.
    return round(rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024, 1)


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def compute_metrics(ctx: AppContext) -> Metrics:
    with ctx.db() as conn:
        done = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(page_count), 0) AS pages, COALESCE(SUM(processing_ms), 0) AS ms "
            "FROM documents WHERE processed_at IS NOT NULL").fetchone()
        n, pages, ms = done["n"], done["pages"], done["ms"]
        with_errors = conn.execute(
            "SELECT COUNT(DISTINCT i.doc_id) FROM issues i JOIN documents d ON d.id = i.doc_id "
            "WHERE i.severity = ? AND d.processed_at IS NOT NULL", (Severity.ERROR.value,)).fetchone()[0]
        reviewed = conn.execute(
            f"SELECT COUNT(DISTINCT doc_id) FROM audit_log WHERE {_REVIEWED} AND doc_id IN "  # noqa: S608
            "(SELECT id FROM documents WHERE processed_at IS NOT NULL)", REVIEW_ACTIONS).fetchone()[0]
        auto = conn.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM audit_log WHERE action = ? AND doc_id IN "
            "(SELECT id FROM documents WHERE processed_at IS NOT NULL)", (AuditAction.AUTO_APPROVE.value,)).fetchone()[0]
        corrections = conn.execute(
            "SELECT COUNT(*) AS fields, COALESCE(SUM(edited_at IS NOT NULL), 0) AS edited FROM fields WHERE doc_id IN "
            f"(SELECT DISTINCT doc_id FROM audit_log WHERE {_REVIEWED})", REVIEW_ACTIONS).fetchone()  # noqa: S608
        export_ms = conn.execute("SELECT AVG(duration_ms) FROM exports").fetchone()[0]
        counters = dict(conn.execute("SELECT name, value FROM counters WHERE name IN (?, ?)", (HITS, MISSES)).fetchall())
        recent = [
            DocTiming(doc_id=r["id"], filename=r["filename"], pages=r["page_count"] or 0, ms=r["processing_ms"] or 0,
                      route=Route(r["route"]) if r["route"] else None)
            for r in conn.execute(
                "SELECT id, filename, page_count, processing_ms, route FROM documents WHERE processed_at IS NOT NULL "
                "AND status != ? ORDER BY processed_at DESC LIMIT 10", (DocStatus.PROCESSING.value,))
        ]
    per_page = round(ms / pages, 1) if pages else None
    return Metrics(
        documents_processed=n,
        pages_processed=pages,
        avg_ms_per_page=per_page,
        pages_per_minute=round(60000 / per_page, 1) if per_page else None,
        validation_failure_rate=_rate(with_errors, n),
        human_review_rate=_rate(reviewed, n),
        auto_approval_rate=_rate(auto, n),
        field_correction_rate=_rate(corrections["edited"], corrections["fields"]),
        avg_export_ms=round(export_ms, 1) if export_ms is not None else None,
        cache_hits=counters.get(HITS, 0),
        cache_misses=counters.get(MISSES, 0),
        load_avg=[round(v, 2) for v in os.getloadavg()] if hasattr(os, "getloadavg") else None,
        rss_mb=_rss_mb(),
        gpu=gpu_status(),
        recent=recent,
    )
