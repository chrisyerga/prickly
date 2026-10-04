import array
from collections.abc import Iterator, Sequence

import pytest
from fastapi.testclient import TestClient

from prickly.engine import SAMPLE_RATE, Language, Mode, Plan, Transcript
from prickly.main import create_app


class FakeEngine:
    load_ms = 1.0

    def plan_text(self, text: str) -> Plan:
        return Plan(
            mode="text",
            transcript=text,
            language=None,
            calls=[{"name": "turn_on_light", "arguments": {"room": "kitchen"}}],
            suppressed=[],
            reasoning=None,
            confidence=0.9,
            timings={"needle_ms": 5.0},
        )

    def transcribe_and_plan(self, samples: array.array, mode: Mode) -> Plan:
        return Plan(
            mode=mode,
            transcript="lock the back door",
            language="en",
            calls=[{"name": "unlock_door", "arguments": {"door": "back"}}],
            suppressed=[
                {"name": "set_light_brightness", "arguments": {"room": "office", "brightness": 50}}
            ],
            reasoning=None,
            confidence=0.5,
            timings={"whistle_ms": 2.0},
        )

    def transcribe(
        self,
        samples: array.array,
        *,
        language: Language | None,
        keywords: Sequence[str],
        word_timestamps: bool,
    ) -> Transcript:
        self.last_transcribe = {"language": language, "keywords": list(keywords)}
        words = [{"word": "hola", "start": 0.1, "end": 0.4, "probability": 0.9}]
        return Transcript(
            text="hola",
            language=language or "es",
            words=words if word_timestamps else [],
            timings={"whistle_ms": 3.0, "audio_ms": 1000.0},
        )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(FakeEngine)) as c:
        yield c


def test_healthz(client: TestClient):
    assert client.get("/healthz").json()["ok"] is True


def test_landing_links_both_demos(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/house"' in response.text
    assert 'href="/whistle"' in response.text


def test_house_page_served(client: TestClient):
    response = client.get("/house")
    assert response.status_code == 200
    assert "app.js" in response.text


def test_text_command_updates_session_house(client: TestClient):
    result = client.post("/api/text", json={"text": "kitchen on"}).json()
    assert result["calls"][0]["result"] == {"room": "kitchen", "brightness": 100}
    assert "server_ms" in result["timings"]
    state = client.get("/api/state").json()
    assert state["lights"]["kitchen"]["brightness"] == 100


def test_sessions_are_isolated(client: TestClient):
    client.post("/api/text", json={"text": "kitchen on"})
    with TestClient(client.app) as other:
        assert other.get("/api/state").json()["lights"]["kitchen"]["brightness"] == 0


def test_voice_command_runs_calls_but_not_suppressed(client: TestClient):
    pcm = array.array("f", [0.0]) * SAMPLE_RATE
    result = client.post("/api/command?mode=fused", content=pcm.tobytes()).json()
    assert result["mode"] == "fused"
    assert result["state"]["locks"]["back"] is False
    assert result["suppressed"][0]["result"] is None
    assert result["state"]["lights"]["office"]["brightness"] == 0


def test_voice_command_rejects_bad_audio(client: TestClient):
    assert client.post("/api/command", content=b"abc").status_code == 422
    assert client.post("/api/command", content=b"\x00" * 16).status_code == 422
    assert client.post("/api/command?mode=nope", content=b"\x00" * 8000).status_code == 422


def test_whistle_page_served(client: TestClient):
    response = client.get("/whistle")
    assert response.status_code == 200
    assert "whistle.js" in response.text


def test_transcribe_passes_options(client: TestClient):
    pcm = array.array("f", [0.0]) * SAMPLE_RATE
    result = client.post(
        "/api/transcribe?keywords=thermostat&keywords=%20&keywords=garage&words=true",
        content=pcm.tobytes(),
    ).json()
    assert result["transcript"] == "hola"
    assert result["language"] == "es"
    assert result["words"][0]["word"] == "hola"
    assert "server_ms" in result["timings"]
    fake = client.app.state.engine  # type: ignore[attr-defined]
    assert fake.last_transcribe == {"language": None, "keywords": ["thermostat", "garage"]}


def test_transcribe_without_words(client: TestClient):
    pcm = array.array("f", [0.0]) * SAMPLE_RATE
    result = client.post("/api/transcribe?words=false&language=de", content=pcm.tobytes()).json()
    assert result["words"] == []
    assert result["language"] == "de"


def test_transcribe_rejects_bad_input(client: TestClient):
    pcm = (array.array("f", [0.0]) * SAMPLE_RATE).tobytes()
    assert client.post("/api/transcribe?language=xx", content=pcm).status_code == 422
    assert client.post(f"/api/transcribe?keywords={'a' * 49}", content=pcm).status_code == 422
    too_many = "&".join(f"keywords=k{i}" for i in range(33))
    assert client.post(f"/api/transcribe?{too_many}", content=pcm).status_code == 422
    assert client.post("/api/transcribe", content=b"\x00" * 16).status_code == 422


def test_reset(client: TestClient):
    client.post("/api/text", json={"text": "kitchen on"})
    assert client.post("/api/reset").json()["lights"]["kitchen"]["brightness"] == 0
