"""Logging for the `vaultai` logger tree.

Code logs ids, counts, durations, and error classes only. The filter below is a
second line of defense: it caps every message and argument so an accidental
log of document text cannot spill a page of content into the log.
"""

from __future__ import annotations

import logging

MAX_ARG_CHARS = 80
MAX_MESSAGE_CHARS = 300


def _clip(value: object) -> object:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    return text if len(text) <= MAX_ARG_CHARS else f"{text[:MAX_ARG_CHARS]}…[{len(text)} chars]"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_clip(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _clip(v) for k, v in record.args.items()}
        if isinstance(record.msg, str) and len(record.msg) > MAX_MESSAGE_CHARS:
            record.msg = record.msg[:MAX_MESSAGE_CHARS] + "…"
        # Tracebacks can quote document text through exception messages; keep only the class.
        if record.exc_info and record.exc_info[0] is not None:
            record.msg = f"{record.msg} [{record.exc_info[0].__name__}]"
            record.exc_info = None
            record.exc_text = None
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("vaultai")
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.propagate = True
