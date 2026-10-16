"""Session-cumulative usage for a local viewer (wave A2 / CALLMAP W3).

Main's HermesCLI read its status bar (context, ``◷`` latency, ``t/s``, the spinner's ``↓ N tok``),
``/usage`` and the exit summary straight off the in-process agent's ``session_*`` counters,
``context_compressor`` and ``get_rate_limit_state()``. On the session authority the agent lives in
the gateway, so the same numbers are projected here: published as ``session.usage`` after every API
call and at turn end of a local-viewer turn, and returned by the ``session.stats`` verb.
"""
from __future__ import annotations

import contextlib
import dataclasses
import threading

_COUNTERS = (("input_tokens", "session_input_tokens"), ("output_tokens", "session_output_tokens"),
             ("cache_read_tokens", "session_cache_read_tokens"),
             ("cache_write_tokens", "session_cache_write_tokens"),
             ("prompt_tokens", "session_prompt_tokens"), ("completion_tokens", "session_completion_tokens"),
             ("total_tokens", "session_total_tokens"), ("reasoning_tokens", "session_reasoning_tokens"),
             ("api_calls", "session_api_calls"))
# The output-token count at the start of the viewer's current turn (``↓ N tok`` on main's spinner).
TURN_BASE_ATTR = "_viewer_turn_output_base"
# Captured at import time: tests stub ``threading.Thread`` with a synchronous runner, and the ticker only
# exits once ``stop`` is set AFTER the turn returns -- inline it would spin forever (sidecar parity).
_RealThread = threading.Thread


def _finite(value):
    return round(float(value), 2) if value is not None and value == value and 0 < value < 1e6 else None


def _context(agent, compressor) -> dict:
    """Main's status-bar context figures (``_get_status_bar_snapshot``), clamped the same way."""
    if compressor is None:
        return {"last_prompt_tokens": 0, "context_tokens": 0, "context_length": None,
                "config_context_length": None, "context_source": None, "compression_count": 0}
    from agent.context_breakdown import context_display_source
    last = max(0, getattr(compressor, "last_prompt_tokens", 0) or 0)
    tokens = last
    with contextlib.suppress(Exception):
        from agent.usage_anchor import anchored_context_tokens
        messages = getattr(agent, "_session_messages", None)
        anchored = anchored_context_tokens(messages if isinstance(messages, list) else [],
                                           getattr(agent, "_turn_base_usage_anchor", None),
                                           charge_stale_thinking=False)
        if anchored is not None and anchored > 0:
            tokens = anchored
    length = max(0, getattr(compressor, "context_length", 0) or 0)
    return {"last_prompt_tokens": last, "context_tokens": min(tokens, length) if length else tokens,
            "context_length": length or None,
            "config_context_length": getattr(compressor, "_config_context_length", None),
            "context_source": context_display_source(compressor),
            "compression_count": getattr(compressor, "compression_count", 0) or 0}


def _rate_limit(agent):
    with contextlib.suppress(Exception):
        state = agent.get_rate_limit_state()
        if state is not None and dataclasses.is_dataclass(state) and not isinstance(state, type):
            return dataclasses.asdict(state)
    return None


def usage_stats(agent) -> dict:
    """The frozen ``session.usage`` / ``session.stats`` dict; zeros before the first turn built an agent."""
    stats: dict = {key: int(getattr(agent, attr, 0) or 0) for key, attr in _COUNTERS}
    stats.update(_context(agent, getattr(agent, "context_compressor", None)))
    avg_latency = tps = None
    with contextlib.suppress(Exception):
        latencies = list(getattr(agent, "_api_latency_history", None) or [])
        outputs = list(getattr(agent, "_api_output_history", None) or [])
        if n := min(len(latencies), len(outputs)):
            total = sum(latencies[-n:])
            avg_latency = _finite(total / n)
            tps = _finite(sum(outputs[-n:]) / total) if total > 0 else None
    active = 0
    with contextlib.suppress(Exception):
        from tools.async_delegation import active_count
        active = active_count()
    base = getattr(agent, TURN_BASE_ATTR, None)
    stats.update(avg_latency_s=avg_latency, tokens_per_s=tps,
                 last_turn_tokens=max(0, stats["output_tokens"] - base) if isinstance(base, int) else 0,
                 rate_limit=_rate_limit(agent) if agent is not None else None, active_subagents=active,
                 model=str(getattr(agent, "model", "") or ""), provider=str(getattr(agent, "provider", "") or ""),
                 fallback_model=(str(getattr(agent, "model", "") or "") or None)
                 if getattr(agent, "_fallback_activated", False) is True else None)
    return stats


def carried_compression_count(live, agent=None) -> int:
    """Compactions of this conversation: main's agent counted them on its compressor for the
    session's life. A canonical compress evicts the gateway's agent (a session boundary), so the
    count rides the live session and seeds the successor (``seed_compression_count``)."""
    own = getattr(getattr(agent, "context_compressor", None), "compression_count", 0)
    carried = getattr(live, "compression_count", 0)
    return max(carried if isinstance(carried, int) else 0, own if isinstance(own, int) else 0)


def note_compression(authority, ref, live) -> None:
    """A committed (never an exact-retried) compress: one more compaction than the evicted agent saw."""
    agent = None
    with contextlib.suppress(AttributeError, KeyError):  # a runner without an agent cache has none
        agent = authority.agent(ref)
    live.compression_count = carried_compression_count(live, agent) + 1


def seed_compression_count(live, agent) -> None:
    compressor = getattr(agent, "context_compressor", None)
    if live is not None and compressor is not None:
        with contextlib.suppress(Exception):
            compressor.compression_count = carried_compression_count(live, agent)


def velocity_fields(agent) -> dict:
    """The live output-rate readout of the ``StreamVelocity`` tracker the agent keeps while a reply
    streams (``streaming``, ``live_tps``, ``live_tps_estimated``): what the TUI sidecar's ``_get_usage``
    merged into every usage tick. ``{}`` for an agent that never streamed -- absent, never fabricated."""
    velocity = getattr(agent, "_stream_velocity", None)
    if velocity is None:
        return {}
    with contextlib.suppress(Exception):
        snapshot = velocity.snapshot()
        return dict(snapshot) if isinstance(snapshot, dict) else {}
    return {}


def tui_usage(stats: dict, velocity: dict | None = None) -> dict:
    """``stats`` in main's TUI ``Usage`` shape (``tui_gateway/server.py::_get_usage``): the fields its
    status bar (context used/max/bar, ``◷`` latency, ``t/s``, ``cmp`` badge, cache hit) and ``/usage``
    read off ``message.complete.usage``. Absent figures are omitted, never fabricated as zeros.
    ``velocity`` (``velocity_fields``) adds the live stream rate the Desktop paints mid-turn."""
    usage = {"model": stats.get("model") or "", "input": stats.get("input_tokens") or stats.get("prompt_tokens") or 0,
             "output": stats.get("output_tokens") or stats.get("completion_tokens") or 0,
             "reasoning": stats.get("reasoning_tokens") or 0, "prompt": stats.get("prompt_tokens") or 0,
             "completion": stats.get("completion_tokens") or 0, "total": stats.get("total_tokens") or 0,
             "calls": stats.get("api_calls") or 0}
    used, maximum = stats.get("last_prompt_tokens") or 0, stats.get("context_length") or 0
    if used and maximum:
        # ``agent/context_breakdown.py::context_usage_fields``: current occupancy, clamped.
        used = min(used, maximum)
        source = stats.get("context_source") or "provider_usage"
        usage.update(context_used=used, context_max=maximum,
                     context_percent=max(0, min(100, round(used / maximum * 100))),
                     context_source=source, context_estimated=source != "provider_usage")
    if stats.get("context_length") is not None:
        usage["compressions"] = stats.get("compression_count") or 0
    prompt_total, cache_read = stats.get("prompt_tokens") or 0, stats.get("cache_read_tokens") or 0
    if prompt_total > 0 and cache_read > 0:
        usage["cache_hit_pct"] = max(0, min(100, round(cache_read / prompt_total * 100)))
    for key, source_key in (("avg_latency_s", "avg_latency_s"), ("avg_tps", "tokens_per_s")):
        if isinstance(stats.get(source_key), (int, float)):
            usage[key] = round(float(stats[source_key]), 1)
    if isinstance(stats.get("active_subagents"), int):
        usage["active_subagents"] = stats["active_subagents"]
    if velocity:
        usage.update(velocity)
    return usage


def usage_frame(agent) -> dict:
    """The ``session.usage`` payload: ``usage_stats`` at the top level (what ``session.stats`` returns)
    plus ``usage`` in main's TUI shape with the live stream rate. The Desktop's status bar (context
    meter, cache hit, ``t/s``) reads ONLY ``payload.usage`` -- the sidecar's ticker shape -- so a frame
    without it leaves every readout at ``--`` for the whole turn."""
    stats = usage_stats(agent)
    return {**stats, "usage": tui_usage(stats, velocity_fields(agent))}


def publish_usage(turn, agent, *, record: bool = False) -> None:
    """One ``session.usage`` frame on the viewer's stream; never breaks the turn. At turn end
    (``record``) the same frame rides the captured execution result, so the turn's
    ``message.complete`` carries main's ``usage`` (``session_settlement_recovery.completion_payload``)."""
    if agent is None:
        return
    with contextlib.suppress(Exception):
        frame = usage_frame(agent)
        turn._publish_execution("session.usage", frame)
        if record:
            from gateway.session_results import execution_result
            captured = execution_result.get()
            if captured is not None:
                captured["session_usage"] = frame


def start_usage_ticker(turn, agent, interval: float = 1.0):
    """Push a ``session.usage`` frame every ``interval`` s while a local viewer's turn runs -- the
    sidecar's ``_start_usage_ticker``. The step callback fires only between API calls, so without
    this the live ``t/s`` and the context meter freeze for the whole of a streamed reply. Frozen
    counters (one long call in flight, no stream yet) publish nothing. Returns ``(stop, thread)``;
    ``stop_usage_ticker`` must run before the turn-end ``publish_usage`` -- a late tick would roll
    the final usage back."""
    stop = threading.Event()
    baseline = None
    with contextlib.suppress(Exception):
        baseline = usage_frame(agent)

    def _loop() -> None:
        last = baseline
        while not stop.wait(interval):
            with contextlib.suppress(Exception):
                frame = usage_frame(agent)
                if frame == last:
                    continue
                last = frame
                if stop.is_set():
                    break  # the turn ended while snapshotting; the final frame is authoritative
                turn._publish_execution("session.usage", frame)
    thread = _RealThread(target=_loop, name="local-viewer-usage-ticker", daemon=True)
    thread.start()
    return stop, thread


def stop_usage_ticker(ticker, timeout: float = 2.0) -> None:
    """Set the ticker's stop flag and join it, so no tick follows the turn-end frame."""
    if ticker is None:
        return
    stop, thread = ticker
    stop.set()
    with contextlib.suppress(Exception):
        thread.join(timeout)


async def stats(connection, ref, params):
    """``session.stats {session_id}``: the same dict ``session.usage`` carries."""
    from hermes_state_runtime import RuntimeStoreError
    if set(params) - {"session_id", "profile"}:
        raise RuntimeStoreError("invalid_params")
    await connection.authority.resolve(connection.actor, ref)
    agent = connection.authority.agent(ref)
    result = usage_stats(agent)
    result["compression_count"] = carried_compression_count(connection.authority.sessions.get(ref.session_id), agent)
    return result
