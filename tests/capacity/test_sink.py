"""Where usage records go (plan §5, §6.7): a sink the deploy may replace, fed
by a writer thread through a bounded queue, so a slow stdout never stalls a
request and a full queue loses rows visibly instead of growing memory."""

import io
import json
import threading
import time

from workspace_app.capacity.sink import StdoutSink, UsageSink, UsageWriter
from workspace_app.capacity.usage_wire import document


class _ListSink(UsageSink):
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def write(self, documents):
        self.docs.extend(documents)


def _doc(n: int) -> dict:
    return document("work", 0, pod="p", component="api", fields={"count": n})


def test_the_default_sink_writes_one_json_line_per_record():
    out = io.StringIO()

    StdoutSink(stream=out).write([_doc(1), _doc(2)])

    lines = out.getvalue().splitlines()
    assert [json.loads(x)["usage"]["count"] for x in lines] == [1, 2]


def test_the_default_sink_needs_no_arguments_so_config_can_name_it():
    """`capacity.sink` is loaded by dotted path with a zero-argument constructor."""
    assert isinstance(StdoutSink(), UsageSink)


def test_records_reach_the_sink_and_close_delivers_what_was_queued():
    sink = _ListSink()
    writer = UsageWriter(sink, queue_max=100)

    for n in range(5):
        assert writer.put(_doc(n))
    writer.close(timeout_s=5)

    assert [d["usage"]["count"] for d in sink.docs] == [0, 1, 2, 3, 4]


def test_a_full_queue_drops_and_counts_and_never_waits():
    release = threading.Event()

    class _Stuck(UsageSink):
        def write(self, documents):
            release.wait(5)

    writer = UsageWriter(_Stuck(), queue_max=3)
    started = time.monotonic()
    accepted = [writer.put(_doc(n)) for n in range(50)]
    elapsed = time.monotonic() - started

    # The writer thread may have taken one off the queue before sticking.
    assert accepted.count(True) in (3, 4)
    assert writer.take_dropped() == accepted.count(False)
    assert writer.take_dropped() == 0, "taking the count resets it"
    assert elapsed < 2, "put must not wait on a stuck sink (a hang guard, not a speed claim)"
    release.set()
    writer.close(timeout_s=5)


def test_a_sink_that_raises_loses_that_batch_and_keeps_writing():
    class _Flaky(UsageSink):
        def __init__(self) -> None:
            self.calls = 0
            self.docs: list[dict] = []

        def write(self, documents):
            self.calls += 1
            if self.calls == 1:
                raise OSError("pipe closed")
            self.docs.extend(documents)

    sink = _Flaky()
    writer = UsageWriter(sink, queue_max=10)
    writer.put(_doc(1))
    deadline = time.monotonic() + 5
    while sink.calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    writer.put(_doc(2))
    writer.close(timeout_s=5)

    assert [d["usage"]["count"] for d in sink.docs] == [2]
