"""Pytest configuration and fixtures."""

# std imports
import os

# 3rd party
import pytest


def pytest_xdist_auto_num_workers(config):
    """Scale xdist workers: max(6, ncpu // 2)."""
    return max(6, os.cpu_count() // 2)

try:
    from pytest_codspeed import BenchmarkFixture  # noqa: F401  pylint:disable=unused-import
except ImportError:
    # Provide a no-op benchmark fixture when pytest-codspeed is not installed
    @pytest.fixture
    def benchmark():
        """No-op benchmark fixture for environments without pytest-codspeed."""

        def _passthrough(func, *args, **kwargs):
            return func(*args, **kwargs)

        return _passthrough
