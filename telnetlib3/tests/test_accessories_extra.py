# std imports
import shlex
import asyncio
import logging
from collections import OrderedDict

# 3rd party
import pytest

# local
from telnetlib3.accessories import make_logger, repr_mapping, function_lookup, make_reader_task


def test_make_logger_no_file():
    logger = make_logger("acc_no_file", loglevel="info")
    assert logger.name == "acc_no_file"
    # ensure level applied
    assert logger.level == logging.INFO
    assert logger.isEnabledFor(logging.INFO)


def test_make_logger_with_file(tmp_path):
    log_path = tmp_path / "acc.log"
    logger = make_logger("acc_with_file", loglevel="warning", logfile=str(log_path))
    assert logger.name == "acc_with_file"
    assert logger.level == logging.WARNING
    assert logger.isEnabledFor(logging.WARNING)
    # emit (do not assert file contents to avoid coupling with global logging config)
    logger.warning("file logging branch executed")


def test_repr_mapping_quotes_roundtrip():
    mapping = OrderedDict(
        [
            ("a", "simple"),
            ("b", "needs space"),
            ("c", "quote'"),
            ("d", 42),
        ]
    )
    result = repr_mapping(mapping)
    expected = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in mapping.items())
    assert result == expected


def test_function_lookup_success_and_not_callable():
    fn = function_lookup("telnetlib3.accessories.get_version")
    assert callable(fn)
    # call to ensure the returned object is usable
    assert isinstance(fn(), str)

    with pytest.raises(AssertionError):
        function_lookup("telnetlib3.accessories.__all__")


class _DummyReader:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def read(self, size):
        self.calls.append(size)
        return self.payload


@pytest.mark.asyncio
async def test_make_reader_task_awaits_and_uses_default_size():
    reader = _DummyReader("abc")
    task = make_reader_task(reader)
    result = await asyncio.wait_for(task, timeout=0.5)
    assert result == "abc"
    assert reader.calls and reader.calls[0] == 2**12
