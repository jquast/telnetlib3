"""Pytest configuration and fixtures."""

# std imports
import os
import asyncio

# 3rd party
import pytest
from pytest_asyncio.plugin import unused_tcp_port  # noqa: F401


try:
    import xdist  # noqa: F401

    def pytest_xdist_auto_num_workers(config):
        """Return 2 in CI, otherwise max(6, ncpu // 2)."""
        if os.environ.get("CI"):
            return 2
        return max(6, os.cpu_count() // 2)

except ImportError:
    pass


@pytest.fixture(scope="module", params=["127.0.0.1"])
def bind_host(request):
    """Localhost bind address."""
    return request.param


@pytest.fixture
def fast_sleep(monkeypatch):
    """Replace ``asyncio.sleep`` with a zero-delay yield to the event loop."""
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: _real_sleep(0))


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
