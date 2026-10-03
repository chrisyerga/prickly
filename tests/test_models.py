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
