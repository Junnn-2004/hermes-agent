"""The Desktop status bar reads a ``session.usage`` tick off ``payload.usage`` (main's TUI shape, the
sidecar's ``_start_usage_ticker``) and paints the live ``t/s`` the ``StreamVelocity`` tracker reports.
On the session authority the frame carries both, and a ticker keeps it flowing while a reply streams.
"""
from __future__ import annotations

import time

from gateway.config import Platform
from tests.gateway.test_local_viewer_usage import FIELDS, _agent, _turn


class _Velocity:
    """``snapshot()`` walks the given samples and then repeats the last one."""

    def __init__(self, *samples):
        self._samples = list(samples)

    def snapshot(self):
        sample = self._samples.pop(0) if len(self._samples) > 1 else self._samples[0]
        return dict(sample)


STREAMING = {"streaming": True, "live_tps": 42.5, "live_tps_estimated": True}
IDLE = {"streaming": False, "live_tps": None, "live_tps_estimated": None}


def test_usage_frame_carries_main_tui_usage_with_the_live_stream_rate():
    from gateway.session_usage import usage_frame
    from tui_gateway.contracts.registry import EVENTS
    agent = _agent()
    agent._stream_velocity = _Velocity(STREAMING)
    frame = usage_frame(agent)
    assert set(frame) == FIELDS | {"usage"}
    usage = frame["usage"]
    # What the status bar paints: context meter, cache hit, rolling t/s, call count …
    assert usage["context_used"] == 900 and usage["context_max"] == 128000 and usage["context_percent"] == 1
    assert usage["cache_hit_pct"] == 9 and usage["avg_tps"] == 10.0 and usage["calls"] == 2
    # … and the live rate of the reply being streamed right now.
    assert usage["streaming"] is True and usage["live_tps"] == 42.5 and usage["live_tps_estimated"] is True
    EVENTS["session.usage"].payload.model_validate(frame)


def test_usage_frame_omits_the_stream_rate_for_an_agent_that_never_streamed():
    from gateway.session_usage import usage_frame
    usage = usage_frame(_agent())["usage"]
    assert "streaming" not in usage and "live_tps" not in usage


def test_each_api_step_frame_is_what_the_desktop_reads():
    published = []
    turn = _turn(Platform.LOCAL, published, _agent())
    turn._step_callback_sync(2, [])
    frame = next(payload for kind, payload in published if kind == "session.usage")
    assert frame["api_calls"] == 2 and frame["usage"]["context_percent"] == 1 and frame["usage"]["calls"] == 2


def test_completion_prefers_the_recorded_frame_with_the_stream_rate_cleared():
    """The turn-end frame (``streaming: false``) is what ``message.complete.usage`` carries: the Desktop
    merges usage ticks, so a final usage without the cleared rate would keep painting the last ``t/s``."""
    from gateway.session_results import execution_result
    from gateway.session_settlement_recovery import completion_payload
    from gateway.session_usage import publish_usage
    published = []
    agent = _agent()
    agent._stream_velocity = _Velocity(IDLE)
    turn = _turn(Platform.LOCAL, published, agent)
    captured = {}
    token = execution_result.set(captured)
    try:
        publish_usage(turn, agent, record=True)
    finally:
        execution_result.reset(token)
    complete = completion_payload({"admission_id": "a1", "request_id": "r1"}, {"outcome": "completed"}, "hi", captured)
    assert complete["usage"] == published[-1][1]["usage"]
    assert complete["usage"]["streaming"] is False and complete["usage"]["live_tps"] is None
    assert complete["usage"]["avg_tps"] == 10.0 and complete["usage"]["context_used"] == 900


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_ticker_publishes_only_when_the_frame_changes_and_stops_on_request():
    from gateway.session_usage import start_usage_ticker, stop_usage_ticker
    published = []
    agent = _agent()
    # Baseline (sampled before the thread starts), then two changed rates, then a frozen tail.
    agent._stream_velocity = _Velocity(
        {"streaming": True, "live_tps": None, "live_tps_estimated": None},
        {"streaming": True, "live_tps": 10.0, "live_tps_estimated": True},
        {"streaming": True, "live_tps": 20.0, "live_tps_estimated": True},
    )
    turn = _turn(Platform.LOCAL, published, agent)
    ticker = start_usage_ticker(turn, agent, interval=0.02)
    try:
        assert _wait_for(lambda: len(published) >= 2)
        # The frame froze on the last sample: no further ticks however long the turn runs.
        time.sleep(0.2)
        assert [p["usage"]["live_tps"] for _, p in published] == [10.0, 20.0]
        assert all(kind == "session.usage" for kind, _ in published)
    finally:
        stop_usage_ticker(ticker)
    assert not ticker[1].is_alive()
    # A changed frame after the stop never reaches the stream: the final frame is authoritative.
    agent._stream_velocity = _Velocity(IDLE)
    time.sleep(0.1)
    assert len(published) == 2


def test_ticker_survives_a_broken_agent_and_a_none_ticker_stops_quietly():
    from gateway.session_usage import start_usage_ticker, stop_usage_ticker

    class _Broken:
        def snapshot(self):
            raise RuntimeError("no velocity")
    published = []
    agent = _agent()
    agent._stream_velocity = _Broken()
    turn = _turn(Platform.LOCAL, published, agent)
    ticker = start_usage_ticker(turn, agent, interval=0.02)
    time.sleep(0.1)
    stop_usage_ticker(ticker)
    assert not ticker[1].is_alive()
    # A broken rate reader drops the rate, never the frame: the counters still reach the bar.
    from gateway.session_usage import usage_frame
    assert "live_tps" not in usage_frame(agent)["usage"] and usage_frame(agent)["usage"]["calls"] == 2
    stop_usage_ticker(None)


def test_ticker_is_the_local_viewer_gate_of_the_turn_runner():
    """The runner starts the ticker alongside the other local-viewer sinks and stops it BEFORE the
    turn-end frame (``publish_usage(record=True)``) -- read off the source, the way the sidecar's
    ordering contract (``_start_usage_ticker``'s docstring) was kept."""
    import inspect

    from gateway import run_turn_runner
    source = inspect.getsource(run_turn_runner)
    start = source.index("usage_ticker = start_usage_ticker(self, agent)")
    stop = source.index("stop_usage_ticker(usage_ticker)")
    final = source.index("publish_usage(self, agent, record=True)")
    assert start < stop < final
    gate = source.rindex("if local_viewer_turn(self) and not ctx.mute_notification_reply:", 0, start)
    assert source.index("local_viewer_events.wire(self, agent)", gate) < start
