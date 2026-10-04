"""Owns the native Whistle and Needle models and times every stage.

The engine is one C library per process holding one Whistle model and one
active Needle agent, and it is not thread-safe, so every call goes through a
single lock. The time spent waiting on that lock is reported separately so
contention never inflates the model numbers.
"""

from __future__ import annotations

import array
import ctypes
import json
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import needle

from .home import KEYWORDS, SYSTEM_PROMPT, TOOL_SCHEMAS

SAMPLE_RATE = 16_000
MAX_SECONDS = 30
MAX_SAMPLES = SAMPLE_RATE * MAX_SECONDS
MIN_SAMPLES = SAMPLE_RATE // 10

Mode = Literal["pipeline", "fused"]
Language = Literal["en", "de", "fr", "es", "it", "nl", "pl"]


@dataclass
class Transcript:
    """Whistle output on its own, with no tool planning."""

    text: str
    language: str | None
    words: list[dict[str, Any]]
    timings: dict[str, float | None] = field(default_factory=dict)


@dataclass
class Plan:
    """What the models produced for one command, before tools run."""

    mode: str
    transcript: str
    language: str | None
    calls: list[dict[str, Any]]
    suppressed: list[dict[str, Any]]
    reasoning: str | None
    confidence: float | None
    timings: dict[str, float | None] = field(default_factory=dict)


def _ms(start_ns: int, end_ns: int) -> float:
    return round((end_ns - start_ns) / 1e6, 2)


def pcm_from_bytes(body: bytes) -> array.array:
    """Decode a request body of little-endian float32 mono samples at 16 kHz."""
    if len(body) % 4:
        raise ValueError("audio body must be float32 samples (length divisible by 4)")
    samples = array.array("f")
    samples.frombytes(body)
    if len(samples) > MAX_SAMPLES:
        raise ValueError(f"audio is longer than {MAX_SECONDS} s")
    if len(samples) < MIN_SAMPLES:
        raise ValueError("audio is too short; hold the button while you speak")
    return samples


class Engine:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        started = time.perf_counter_ns()
        self._whistle = needle.Whistle()
        self._agent = needle.Needle(tools=TOOL_SCHEMAS, system=SYSTEM_PROMPT, stateless=True)
        self._buffer = ctypes.create_string_buffer(1 << 16)
        self.load_ms = _ms(started, time.perf_counter_ns())
        self._warm_up()

    def _warm_up(self) -> None:
        silence = array.array("f", [0.0]) * SAMPLE_RATE
        self.transcribe_and_plan(silence, "pipeline")
        self.transcribe_and_plan(silence, "fused")
        self.plan_text("turn on the kitchen light")
        self.transcribe(silence, language=None, keywords=(), word_timestamps=True)

    def transcribe(
        self,
        samples: array.array,
        *,
        language: Language | None,
        keywords: Sequence[str],
        word_timestamps: bool,
    ) -> Transcript:
        """Speech to text only. `language=None` lets Whistle detect it."""
        with self._locked() as queue_ms:
            t0 = time.perf_counter_ns()
            heard = self._whistle.transcribe(
                samples,
                language=language,
                keywords=list(keywords) or None,
                word_timestamps=word_timestamps,
            )
            t1 = time.perf_counter_ns()
        return Transcript(
            text=(heard.get("text") or "").strip(),
            language=heard.get("language"),
            words=list(heard.get("words") or []),
            timings={
                "queue_ms": queue_ms,
                "audio_ms": round(len(samples) / SAMPLE_RATE * 1000, 1),
                "whistle_ms": _ms(t0, t1),
                "whistle_ttft_ms": heard.get("ttft_ms"),
                "whistle_tps": heard.get("decode_tps"),
            },
        )

    def plan_text(self, text: str) -> Plan:
        with self._locked() as queue_ms:
            t0 = time.perf_counter_ns()
            response = self._agent.complete(text)
            t1 = time.perf_counter_ns()
        return _plan(
            "text",
            text,
            None,
            response,
            {
                "queue_ms": queue_ms,
                "needle_ms": _ms(t0, t1),
                **_needle_stats(response),
            },
        )

    def transcribe_and_plan(self, samples: array.array, mode: Mode) -> Plan:
        if mode == "fused":
            return self._fused(samples)
        with self._locked() as queue_ms:
            t0 = time.perf_counter_ns()
            heard = self._whistle.transcribe(samples, language="en", keywords=KEYWORDS)
            t1 = time.perf_counter_ns()
            text = heard.get("text") or ""
            response = self._agent.complete(text) if text.strip() else _empty_response()
            t2 = time.perf_counter_ns()
        return _plan(
            "pipeline",
            text,
            heard.get("language"),
            response,
            {
                "queue_ms": queue_ms,
                "audio_ms": round(len(samples) / SAMPLE_RATE * 1000, 1),
                "whistle_ms": _ms(t0, t1),
                "whistle_ttft_ms": heard.get("ttft_ms"),
                "whistle_tps": heard.get("decode_tps"),
                "needle_ms": _ms(t1, t2),
                **_needle_stats(response),
            },
        )

    def _fused(self, samples: array.array) -> Plan:
        """One native call: audio in, transcript and tool calls out.

        cactus-needle 3.1 exposes this path only in C (`needle_complete` with a
        sample buffer and NULL text), so it reaches through the package's
        private `_lib` handle.
        """
        with self._locked() as queue_ms:
            self._agent.reset()
            lib = needle._lib(3)
            buffer = (ctypes.c_float * len(samples)).from_buffer(samples)
            t0 = time.perf_counter_ns()
            rc = lib.needle_complete(
                None, buffer, len(samples), 512, self._buffer, len(self._buffer)
            )
            t1 = time.perf_counter_ns()
            raw = self._buffer.value.decode("utf-8", "replace")
        if rc < 0:
            raise RuntimeError(f"needle_complete failed: {raw}")
        response = json.loads(raw)
        return _plan(
            "fused",
            response.get("audio_text") or "",
            response.get("audio_language"),
            response,
            {
                "queue_ms": queue_ms,
                "audio_ms": round(len(samples) / SAMPLE_RATE * 1000, 1),
                "fused_ms": _ms(t0, t1),
                "whistle_ttft_ms": response.get("audio_ttft_ms"),
                "whistle_tps": response.get("audio_decode_tps"),
                **_needle_stats(response),
            },
        )

    @contextmanager
    def _locked(self) -> Iterator[float]:
        """Hold the engine lock, yielding how many ms were spent waiting for it."""
        t = time.perf_counter_ns()
        with self._lock:
            yield _ms(t, time.perf_counter_ns())


def _empty_response() -> dict[str, Any]:
    return {
        "function_calls": [],
        "suppressed_calls": [],
        "reasoning": "no speech heard",
        "confidence": None,
    }


def _needle_stats(response: dict[str, Any]) -> dict[str, float | None]:
    return {
        "needle_prefill_tps": response.get("prefill_tps"),
        "needle_decode_tps": response.get("decode_tps"),
        "peak_ram_mb": response.get("peak_ram_mb"),
    }


def _plan(
    mode: str,
    transcript: str,
    language: str | None,
    response: dict[str, Any],
    timings: dict[str, float | None],
) -> Plan:
    return Plan(
        mode=mode,
        transcript=transcript.strip(),
        language=language,
        calls=list(response.get("function_calls") or []),
        suppressed=list(response.get("suppressed_calls") or []),
        reasoning=response.get("reasoning"),
        confidence=response.get("confidence"),
        timings=timings,
    )
