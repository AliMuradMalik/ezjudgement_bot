"""Uniform logging setup used by FastAPI and uvicorn workers."""

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Clear handlers uvicorn may have installed so we don't double-log.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.addHandler(handler)

    # Quieten very chatty libraries — keep INFO on uvicorn itself.
    for noisy in ("httpcore", "httpx", "openai._base_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
