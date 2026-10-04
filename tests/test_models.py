"""End-to-end checks against the real Whistle and Needle models."""

import os
import wave
from array import array
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(os.environ.get("PRICKLY_SKIP_MODELS") == "1", reason="models disabled"),
]

CLIPS = Path(__file__).parent.parent / "src" / "prickly" / "static" / "clips"


@pytest.fixture(scope="module")
def engine():
    from prickly.engine import Engine

    return Engine()


def _pcm(name: str) -> array:
    with wave.open(str(CLIPS / name), "rb") as clip:
        assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (16000, 1, 2)
        ints = array("h", clip.readframes(clip.getnframes()))
    return array("f", (s / 32768 for s in ints))


def test_text_plan(engine):
    plan = engine.plan_text("unlock the garage")
    assert [(c["name"], c["arguments"]) for c in plan.calls] == [
        ("unlock_door", {"door": "garage"})
    ]


def test_out_of_scope_request_makes_no_calls(engine):
    assert engine.plan_text("order a large pepperoni pizza").calls == []


@pytest.mark.parametrize("mode", ["pipeline", "fused"])
def test_voice_plan(engine, mode):
    plan = engine.transcribe_and_plan(_pcm("kitchen-dim.wav"), mode)
    assert "kitchen" in plan.transcript.lower()
    assert [c["name"] for c in plan.calls] == ["set_light_brightness", "lock_door"]
    assert plan.calls[0]["arguments"] == {"room": "kitchen", "brightness": 30}
    assert plan.timings["whistle_ttft_ms"] is not None


def test_language_literal_matches_whistle():
    from typing import get_args

    from needle.agent.whistle import LANGUAGES

    from prickly.engine import Language

    assert set(get_args(Language)) == set(LANGUAGES)


def test_transcribe_detects_language_with_word_timings(engine):
    heard = engine.transcribe(
        _pcm("de-weather.wav"), language=None, keywords=(), word_timestamps=True
    )
    assert heard.language == "de"
    assert "berlin" in heard.text.lower()
    assert heard.words and all(w["end"] >= w["start"] for w in heard.words)
    assert heard.timings["whistle_ms"] > 0


def test_keywords_fix_thermostat(engine):
    pcm = _pcm("thermostat.wav")
    biased = engine.transcribe(pcm, language="en", keywords=["thermostat"], word_timestamps=False)
    assert "thermostat" in biased.text.lower()
    assert biased.words == []
