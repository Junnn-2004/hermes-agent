"""Per-attempt output throughput, read by the existing usage ticker."""

import threading
import time


def calibrate_stream_velocity(agent, output_tokens):
    velocity = getattr(agent, "_stream_velocity", None)
    if velocity is not None:
        velocity.calibrate(int(output_tokens or 0))


class StreamVelocity:
    def __init__(self):
        self._lock = threading.Lock()
        self._tokens_per_char = 0.25
        self._active = False
        self._completed = False
        self._chars = 0
        self._first_chars = 0
        self._first_at = None
        self._tokens = None

    def start(self):
        with self._lock:
            self._active = True
            self._completed = False
            self._chars = self._first_chars = 0
            self._first_at = self._tokens = None

    def stop(self, *, finished):
        with self._lock:
            self._active = False
            self._completed = finished

    def note_text(self, text):
        if not isinstance(text, str) or not text:
            return
        with self._lock:
            if not self._active:
                return
            self._chars += len(text)
            if self._first_at is None:
                self._first_at = time.monotonic()
                self._first_chars = self._chars

    def note_tokens(self, tokens):
        if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
            return
        with self._lock:
            if not self._active:
                return
            if self._tokens is None or tokens < self._tokens[2]:
                self._tokens = (tokens, time.monotonic(), tokens)
            else:
                self._tokens = (*self._tokens[:2], tokens)

    def calibrate(self, output_tokens):
        # Answer, native/inline reasoning and tool arguments must share the same
        # denominator as provider completion tokens. Never learn from a failed attempt.
        with self._lock:
            if self._completed and self._chars >= 200 and output_tokens > 0:
                ratio = output_tokens / self._chars
                if 0.05 <= ratio <= 2.0:
                    self._tokens_per_char = 0.6 * self._tokens_per_char + 0.4 * ratio

    def snapshot(self):
        with self._lock:
            # Desktop merges usage ticks, so explicitly clear the previous call's
            # rate while waiting for the first output sample and after completion.
            result = {"streaming": self._active, "live_tps": None, "live_tps_estimated": None}
            if not self._active:
                return result
            now = time.monotonic()
            rate = None
            estimated = True
            if self._tokens is not None:
                first, since, latest = self._tokens
                elapsed = now - since
                if elapsed >= 0.5 and latest > first:
                    rate = (latest - first) / elapsed
                    estimated = False
            if rate is None and self._first_at is not None:
                elapsed = now - self._first_at
                if elapsed >= 0.5:
                    rate = (self._chars - self._first_chars) * self._tokens_per_char / elapsed
            if rate is not None and 0 < rate < 1e6:
                result.update(live_tps=round(rate, 1), live_tps_estimated=estimated)
            return result
