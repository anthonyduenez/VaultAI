"""VaultAI command line.

    python -m vaultai                 serve the desktop app (default)
    python -m vaultai init-db         create the data directories and database
    python -m vaultai load-samples    import and process samples/demo without the UI
    python -m vaultai delete-data     delete every document, derived file, export, and record
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from vaultai import deletion
from vaultai.app import build_context, create_app
from vaultai.config import load_settings
from vaultai.domain import PreflightStatus
from vaultai.intake.ingest import ingest_samples
from vaultai.logging_utils import configure_logging
from vaultai.pipeline.process import process_document, request_processing
from vaultai.security.egress import install_egress_guard


def serve() -> None:
    settings = load_settings()
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_level="info",
        access_log=False, proxy_headers=False, server_header=False,
    )


def init_db() -> None:
    ctx = build_context(load_settings())
    print(f"VaultAI data directory ready: {ctx.settings.data_dir}")


def load_samples() -> None:
    settings = load_settings()
    if settings.egress_guard:
        install_egress_guard()
    ctx = build_context(settings)
    items = ingest_samples(ctx, batch_id="cli-samples")
    accepted = [i.document_id for i in items if i.preflight is PreflightStatus.ACCEPTED and i.document_id]
    for doc_id in request_processing(ctx, accepted, actor=settings.reviewer_name):
        process_document(ctx, doc_id)
    with ctx.db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM documents GROUP BY status ORDER BY status").fetchall()
    print(f"Imported {len(accepted)} new sample documents ({len(items) - len(accepted)} already present).")
    print(", ".join(f"{r['status']}: {r['n']}" for r in rows))


def delete_data(confirmed: bool) -> None:
    settings = load_settings()
    if not confirmed:
        sys.exit(f"This deletes everything under {settings.data_dir}. Re-run with --yes to confirm.")
    deletion.delete_all(build_context(settings), actor=settings.reviewer_name)
    print(f"Deleted all VaultAI data under {settings.data_dir}.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m vaultai", description="VaultAI: local document AI for business.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="serve the desktop app (default)")
    sub.add_parser("init-db", help="create the data directories and database")
    sub.add_parser("load-samples", help="import and process samples/demo without the UI")
    delete = sub.add_parser("delete-data", help="delete every document, derived file, export, and record")
    delete.add_argument("--yes", action="store_true", help="confirm deletion")
    args = parser.parse_args(argv)
    configure_logging()
    match args.command:
        case None | "serve":
            serve()
        case "init-db":
            init_db()
        case "load-samples":
            load_samples()
        case "delete-data":
            delete_data(args.yes)


if __name__ == "__main__":
    main()
