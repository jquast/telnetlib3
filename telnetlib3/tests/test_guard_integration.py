# std imports
import asyncio


async def test_connection_counter_integration():
    # local
    from telnetlib3.guard_shells import ConnectionCounter

    counter = ConnectionCounter(2)

    assert counter.try_acquire()
    assert counter.count == 1

    assert counter.try_acquire()
    assert counter.count == 2

    assert not counter.try_acquire()
    assert counter.count == 2

    counter.release()
    assert counter.count == 1

    assert counter.try_acquire()
    assert counter.count == 2


async def test_counter_release_on_completion():
    # local
    from telnetlib3.guard_shells import ConnectionCounter

    counter = ConnectionCounter(1)

    async def shell_with_finally():
        if not counter.try_acquire():
            raise RuntimeError("Counter should have allowed acquire")
        try:
            raise ValueError("Simulated error")
        finally:
            counter.release()

    assert counter.count == 0

    try:
        await shell_with_finally()
    except ValueError:
        pass

    assert counter.count == 0


async def test_counter_release_in_guarded_pattern():
    # local
    from telnetlib3.guard_shells import ConnectionCounter

    counter = ConnectionCounter(2)

    results = []

    async def guarded_shell(name):
        if not counter.try_acquire():
            results.append(f"{name}: rejected")
            return

        try:
            results.append(f"{name}: acquired (count={counter.count})")
            await asyncio.sleep(0.05)
        finally:
            counter.release()
            results.append(f"{name}: released (count={counter.count})")

    await asyncio.gather(
        guarded_shell("client1"),
        guarded_shell("client2"),
        guarded_shell("client3"),
    )

    acquired_count = sum(1 for r in results if "acquired" in r)
    released_count = sum(1 for r in results if "released" in r)
    rejected_count = sum(1 for r in results if "rejected" in r)

    assert acquired_count == 2
    assert released_count == 2
    assert rejected_count == 1
    assert counter.count == 0


async def test_guarded_shell_pattern_busy_shell():
    # local
    from telnetlib3.guard_shells import ConnectionCounter, busy_shell

    counter = ConnectionCounter(1)
    shell_calls = []
    busy_shell_calls = []
    shell_done = asyncio.Event()

    class MockWriter:
        def __init__(self):
            self._closing = False

        def write(self, data):
            pass

        async def drain(self):
            pass

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        def get_extra_info(self, key, default=None):
            return ("127.0.0.1", 12345) if key == "peername" else default

    class MockReader:
        def __init__(self):
            self._data = list("response\r")
            self._idx = 0

        async def read(self, n):
            if self._idx >= len(self._data):
                return ""
            result = self._data[self._idx]
            self._idx += 1
            return result

    async def inner_shell(reader, writer):
        shell_calls.append(True)
        await shell_done.wait()

    async def guarded_shell(reader, writer):
        if not counter.try_acquire():
            busy_shell_calls.append(True)
            await busy_shell(reader, writer)
            if not writer.is_closing():
                writer.close()
            return

        try:
            await inner_shell(reader, writer)
        finally:
            counter.release()

    writer1 = MockWriter()
    writer2 = MockWriter()
    reader1 = MockReader()
    reader2 = MockReader()

    task1 = asyncio.create_task(guarded_shell(reader1, writer1))
    await asyncio.sleep(0.01)
    task2 = asyncio.create_task(guarded_shell(reader2, writer2))

    await asyncio.sleep(0.01)
    shell_done.set()

    await asyncio.gather(task1, task2)

    assert len(shell_calls) == 1
    assert len(busy_shell_calls) == 1
    assert counter.count == 0


async def test_guarded_shell_pattern_robot_check():  # pylint: disable=too-complex
    # local
    from telnetlib3.guard_shells import ConnectionCounter

    counter = ConnectionCounter(5)
    shell_calls = []
    robot_shell_calls = []

    class MockWriter:
        def __init__(self):
            self._closing = False

        def write(self, data):
            pass

        async def drain(self):
            pass

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        def get_extra_info(self, key, default=None):
            return ("127.0.0.1", 12345) if key == "peername" else default

    class MockReader:
        def __init__(self):
            self._data = list("response\r")
            self._idx = 0

        async def read(self, n):
            if self._idx >= len(self._data):
                return ""
            result = self._data[self._idx]
            self._idx += 1
            return result

    robot_check_results = [True, False, True]
    robot_check_idx = [0]

    async def mock_robot_check(reader, writer):
        idx = robot_check_idx[0]
        robot_check_idx[0] += 1
        return robot_check_results[idx % len(robot_check_results)]

    async def mock_robot_shell(reader, writer):
        robot_shell_calls.append(True)

    async def inner_shell(reader, writer):
        shell_calls.append(True)

    async def guarded_shell(reader, writer):
        if not counter.try_acquire():
            return

        try:
            passed = await mock_robot_check(reader, writer)
            if not passed:
                await mock_robot_shell(reader, writer)
                if not writer.is_closing():
                    writer.close()
                return

            await inner_shell(reader, writer)
        finally:
            counter.release()

    tasks = []
    for i in range(3):
        reader = MockReader()
        writer = MockWriter()
        tasks.append(asyncio.create_task(guarded_shell(reader, writer)))

    await asyncio.gather(*tasks)

    assert len(shell_calls) == 2
    assert len(robot_shell_calls) == 1
    assert counter.count == 0


async def test_full_guarded_shell_flow():  # pylint: disable=too-complex
    # local
    from telnetlib3.guard_shells import ConnectionCounter, busy_shell, robot_shell

    counter = ConnectionCounter(2)
    shell_calls = []
    busy_calls = []
    robot_calls = []

    class MockWriter:
        def __init__(self):
            self._closing = False
            self.output = []

        def write(self, data):
            self.output.append(data)

        def echo(self, data):
            self.output.append(data)

        async def drain(self):
            pass

        def is_closing(self):
            return self._closing

        def close(self):
            self._closing = True

        def get_extra_info(self, key, default=None):
            return ("127.0.0.1", 12345) if key == "peername" else default

    class MockReader:
        def __init__(self, responses=None):
            self._data = responses or list("response\r")
            self._idx = 0

        async def read(self, n):
            if self._idx >= len(self._data):
                return ""
            result = self._data[self._idx]
            self._idx += 1
            return result

    robot_check_results = [True, False, True, True]
    robot_check_idx = [0]

    async def mock_robot_check(reader, writer):
        idx = robot_check_idx[0]
        robot_check_idx[0] += 1
        return robot_check_results[idx % len(robot_check_results)]

    async def inner_shell(reader, writer):
        shell_calls.append(True)
        writer.write("Shell active")

    async def guarded_shell(reader, writer, do_robot_check=True):
        if not counter.try_acquire():
            busy_calls.append(True)
            await busy_shell(reader, writer)
            if not writer.is_closing():
                writer.close()
            return

        try:
            if do_robot_check:
                passed = await mock_robot_check(reader, writer)
                if not passed:
                    robot_calls.append(True)
                    await robot_shell(reader, writer)
                    if not writer.is_closing():
                        writer.close()
                    return

            await inner_shell(reader, writer)
        finally:
            counter.release()

    writers = [MockWriter() for _ in range(4)]
    readers = [MockReader(list("y\ryes\r")) for _ in range(4)]

    await asyncio.gather(
        guarded_shell(readers[0], writers[0]),
        guarded_shell(readers[1], writers[1]),
        guarded_shell(readers[2], writers[2]),
        guarded_shell(readers[3], writers[3]),
    )

    assert len(shell_calls) >= 1
    assert len(robot_calls) >= 1
    assert counter.count == 0
