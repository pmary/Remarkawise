"""Logging utilities for Remarkawise."""

import logging
import sys
from typing import Optional

# Create logger for the package
logger = logging.getLogger("remarkawise")


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    """Configure logging for the application.

    Args:
        verbose: Enable verbose output (INFO level)
        debug: Enable debug output (DEBUG level)
    """
    level = logging.WARNING
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("  [%(levelname)s] %(message)s"))

    logger.setLevel(level)
    logger.handlers = [handler]


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger for a module.

    Args:
        name: Optional module name (will be prefixed with 'remarkawise.')

    Returns:
        Logger instance
    """
    if name:
        return logging.getLogger(f"remarkawise.{name}")
    return logger
