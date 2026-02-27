"""Small shared utility functions."""

from __future__ import annotations

import datetime


def relative_time(iso_str: str) -> str:
    """Format an ISO timestamp as a relative time like ``'2h ago'``."""
    if not iso_str:
        return ""
    try:
        then = datetime.datetime.fromisoformat(iso_str)
        if then.tzinfo is None:
            now = datetime.datetime.now()
        else:
            now = datetime.datetime.now(datetime.timezone.utc)
        delta = now - then
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return ""
        if seconds < 60:
            return f"{seconds}s ago"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except (ValueError, TypeError):
        return iso_str[:10]
