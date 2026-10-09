"""The desktop must receive throughput before a long model call completes."""

import time
from collections import deque

from run_agent import AIAgent
from tui_gateway import server


def _agent(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    agent = AIAgent.__new__(AIAgent)
    agent.model = "test/model"
    agent.stream_delta_callback = lambda text: None
    agent._stream_callback = None
    agent.reasoning_callback = None
    agent._enqueue_stream_hook = lambda *args, **kwargs: None
    agent._api_latency_history = deque([2.0], maxlen=10)
    agent._api_output_history = deque([100], maxlen=10)
    agent._claim_stream_writer()
    return agent, clock


def test_live_usage_changes_before_completion_and_returns_to_average(monkeypatch):
    agent, clock = _agent(monkeypatch)
    agent._emit_stream_start()
    assert server._get_usage(agent).get("streaming") is True
    assert server._get_usage(agent)["live_tps"] is None  # clears a previous call during prefill

    agent._fire_stream_delta("start")
    clock["now"] += 2.0
    agent._fire_reasoning_delta("r" * 200)
    agent._fire_stream_delta("a" * 200)
    usage = server._get_usage(agent)
    assert usage["live_tps"] == 50.0
    assert usage["live_tps_estimated"] is True
    assert usage["avg_tps"] == 50.0

    clock["now"] += 2.0  # a stalled stream must not keep a frozen rate
    assert server._get_usage(agent)["live_tps"] == 25.0
    agent._emit_stream_end(final_text="answer", finished=True, error=None)
    usage = server._get_usage(agent)
    assert usage["streaming"] is False
    assert usage["live_tps"] is None
    assert usage["avg_tps"] == 50.0


def test_provider_tokens_take_precedence_and_retry_starts_a_fresh_window(monkeypatch):
    agent, clock = _agent(monkeypatch)
    agent._emit_stream_start()
    agent._fire_stream_delta("start")
    agent._note_stream_velocity_tokens(100)
    clock["now"] += 2.0
    agent._note_stream_velocity_chars('{"code":"' + "x" * 400 + '"}')
    agent._note_stream_velocity_tokens(300)
    usage = server._get_usage(agent)
    assert usage["live_tps"] == 100.0
    assert usage["live_tps_estimated"] is False

    agent._emit_stream_end(final_text="", finished=False, error="retry")
    agent._emit_stream_start()
    clock["now"] += 30.0
    assert server._get_usage(agent)["live_tps"] is None
    agent._fire_stream_delta("one burst")
    assert server._get_usage(agent)["live_tps"] is None
