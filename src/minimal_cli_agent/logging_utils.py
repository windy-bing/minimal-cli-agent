from __future__ import annotations

import logging


LOGGER_NAME = "minimal_cli_agent"


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def configure_logging(verbose: bool = False, quiet: bool = False) -> None:
    level = logging.WARNING
    if verbose:
        level = logging.DEBUG
    if quiet:
        level = logging.ERROR
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
