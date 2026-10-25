"""Validate and read per-device polling options."""

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)


def validate_poll_interval(value: Any) -> int:
    """Require a whole number of seconds within the supported range."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not MIN_POLL_INTERVAL <= value <= MAX_POLL_INTERVAL
        or int(value) != value
    ):
        raise ValueError("Polling interval must be a whole number from 5 to 3600")
    return int(value)


def poll_interval_seconds(options: Mapping[str, Any]) -> int:
    """Keep existing entries and malformed stored options on a safe default."""
    try:
        return validate_poll_interval(
            options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
    except ValueError:
        return DEFAULT_POLL_INTERVAL
