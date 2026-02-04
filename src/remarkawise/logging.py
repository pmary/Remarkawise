"""Logging utilities for Remarkawise.

This module provides centralized logging infrastructure for the application.

Usage Patterns:
    # Module-level logger initialization
    from remarkawise.logging import get_logger
    logger = get_logger("module_name")

    # Logging at different levels
    logger.debug("Detailed diagnostic info")
    logger.info("Progress updates")
    logger.warning("Recoverable issues")
    logger.error("Failures that need attention")

Error Handling Patterns:
    The application uses a tiered error handling approach:

    1. Configuration Errors:
       - Missing required settings (API tokens) -> exit code 1
       - Invalid paths/values -> exit code 1
       - Detected early, fail fast with clear messages

    2. API Errors:
       - Rate limiting -> ReadwiseRateLimitError (includes retry_after)
       - Authentication -> verify_token() returns False
       - Request failures -> ReadwiseAPIError with context

    3. Local Cache Errors:
       - Missing cache directory -> LocalCacheError
       - Corrupt metadata -> logged and skipped
       - Missing files -> logged and skipped

    4. Sync Errors:
       - Individual document failures are logged but don't halt sync
       - Errors are collected in SyncResult.errors
       - Sync continues with remaining documents

Log Levels:
    - WARNING (default): Only show errors and warnings
    - INFO (--verbose): Show sync progress and status
    - DEBUG: Full diagnostic output including API responses
"""

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
