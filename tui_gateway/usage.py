"""Usage snapshots shared by session RPCs and the live usage ticker."""

import contextlib
import os


def get_usage(agent) -> dict:
    from tui_gateway.server import is_truthy_value

    g = lambda k, fb=None: getattr(agent, k, 0) or (getattr(agent, fb, 0) if fb else 0)
    usage = {
        "model": getattr(agent, "model", "") or "",
        "input": g("session_input_tokens", "session_prompt_tokens"),
        "output": g("session_output_tokens", "session_completion_tokens"),
        "reasoning": g("session_reasoning_tokens"), "prompt": g("session_prompt_tokens"),
        "completion": g("session_completion_tokens"), "total": g("session_total_tokens"),
        "calls": g("session_api_calls"),
    }
    comp = getattr(agent, "context_compressor", None)
    if comp:
        from agent.context_breakdown import context_usage_fields
        usage.update(context_usage_fields(comp))
        usage["compressions"] = getattr(comp, "compression_count", 0) or 0
    # Cache-hit ratio + rolling latency/tps (CLI status-bar parity). Omitted, not fabricated, when there is no
    # data (Codex reports no latency; zero cache reads shows no hit% rather than an alarming 0).
    with contextlib.suppress(Exception):
        # Mirrors the classic CLI bar (cli.py _get_status_bar_snapshot / PR #98250): hit =
        # session_cache_read_tokens / session_prompt_tokens (CanonicalUsage.prompt_tokens = input +
        # cache_read + cache_write) latency/tps read the deque(maxlen=10) history maintained per API call in
        # agent/conversation_loop.py.
        _prompt_total = int(getattr(agent, "session_prompt_tokens", 0) or 0)
        _cache_read = int(getattr(agent, "session_cache_read_tokens", 0) or 0)
        if _prompt_total > 0 and _cache_read > 0:
            usage["cache_hit_pct"] = max(0, min(100, round(_cache_read / _prompt_total * 100)))
    with contextlib.suppress(Exception):  # a status-bar readout must never break usage reporting
        _lhist = list(getattr(agent, "_api_latency_history", []) or [])
        _ohist = list(getattr(agent, "_api_output_history", []) or [])
        if _n := min(len(_lhist), len(_ohist)):
            _total_lat = sum(_lhist[-_n:])
            _avg_vel = (sum(_ohist[-_n:]) / _total_lat) if _total_lat > 0 else None
            for _key, _val in (("avg_latency_s", _total_lat / _n), ("avg_tps", _avg_vel)):
                if _val is not None and _val == _val and 0 < _val < 1e6:  # guard NaN/negative/absurd provider timings
                    usage[_key] = round(float(_val), 1)
    velocity = getattr(agent, "_stream_velocity", None)
    if velocity is not None:
        usage.update(velocity.snapshot())
    # Live count of background/async subagents (CLI status bar ⛓ parity, same async_delegation registry).
    with contextlib.suppress(Exception):
        from tools.async_delegation import active_count as _async_active_count
        usage["active_subagents"] = _async_active_count()
    # Dev-only live credits-spent readout, gated on HERMES_DEV_CREDITS so the payload stays clean otherwise.
    if is_truthy_value(os.environ.get("HERMES_DEV_CREDITS")):
        with contextlib.suppress(Exception):
            spent = agent.get_credits_spent_micros()
            if spent is not None:
                usage["dev_credits_spent_micros"] = int(spent)
    return usage
