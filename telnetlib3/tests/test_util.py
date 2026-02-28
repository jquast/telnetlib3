"""Tests for telnetlib3._util module."""

from __future__ import annotations

# std imports
import datetime

# local
from telnetlib3._util import relative_time


def test_relative_time_empty():
    assert relative_time("") == ""


def test_relative_time_recent():
    now = datetime.datetime.now(datetime.timezone.utc)
    iso = now.isoformat()
    result = relative_time(iso)
    assert result.endswith("s ago")


def test_relative_time_hours():
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
    result = relative_time(then.isoformat())
    assert result == "3h ago"


def test_relative_time_days():
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)
    result = relative_time(then.isoformat())
    assert result == "5d ago"


def test_relative_time_invalid():
    result = relative_time("not-a-date")
    assert result == "not-a-date"[:10]
