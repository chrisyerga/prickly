"""HTTP layer: sessions, request validation, and serving the single page."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import Language, Mode, Plan, Transcript, pcm_from_bytes
from .home import HouseState, apply_call

STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE = "prickly_sid"
MAX_SESSIONS = 500
MAX_KEYWORDS = 32
MAX_KEYWORD_CHARS = 48


class Planner(Protocol):
    load_ms: float

    def plan_text(self, text: str) -> Plan: ...

    def transcribe_and_plan(self, samples: Any, mode: Mode) -> Plan: ...

    def transcribe(
        self,
        samples: Any,
        *,
        language: Language | None,
        keywords: Sequence[str],
        word_timestamps: bool,
    ) -> Transcript: ...


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None


class CommandResult(BaseModel):
    mode: str
    transcript: str
    language: str | None
    calls: list[ToolCall]
    suppressed: list[ToolCall]
    reasoning: str | None
    confidence: float | None
    timings: dict[str, float | None]
    state: dict[str, Any]


class Word(BaseModel):
    word: str
    start: float
    end: float
    probability: float


class TranscribeResult(BaseModel):
    transcript: str
    language: str | None
    words: list[Word]
    timings: dict[str, float | None]


class TextCommand(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class Sessions:
    """In-memory houses keyed by cookie, oldest evicted first."""

    def __init__(self, limit: int = MAX_SESSIONS) -> None:
        self._houses: OrderedDict[str, HouseState] = OrderedDict()
        self._limit = limit

    def get(self, sid: str) -> HouseState:
        house = self._houses.get(sid)
        if house is None:
            house = self._houses[sid] = HouseState()
            while len(self._houses) > self._limit:
                self._houses.popitem(last=False)
        self._houses.move_to_end(sid)
        return house

    def reset(self, sid: str) -> HouseState:
        self._houses[sid] = HouseState()
        return self._houses[sid]


def _session_id(request: Request, response: Response) -> str:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        sid = uuid.uuid4().hex
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", max_age=86400 * 7)
    return sid


def _execute(plan: Plan, house: HouseState) -> CommandResult:
    t0 = time.perf_counter_ns()
    calls = [
        ToolCall(
            name=c["name"],
            arguments=c.get("arguments") or {},
            result=apply_call(house, c["name"], c.get("arguments") or {}),
        )
        for c in plan.calls
    ]
    tools_ms = round((time.perf_counter_ns() - t0) / 1e6, 3)
    return CommandResult(
        mode=plan.mode,
        transcript=plan.transcript,
        language=plan.language,
        calls=calls,
        suppressed=[
            ToolCall(name=c["name"], arguments=c.get("arguments") or {}) for c in plan.suppressed
        ],
        reasoning=plan.reasoning,
        confidence=plan.confidence,
        timings={**plan.timings, "tools_ms": tools_ms},
        state=house.to_dict(),
    )


def _default_engine() -> Planner:
    from .engine import Engine

    return Engine()


def create_app(engine_factory: Callable[[], Planner] = _default_engine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = await run_in_threadpool(engine_factory)
        app.state.sessions = Sessions()
        yield

    app = FastAPI(title="prickly", lifespan=lifespan)

    def engine(request: Request) -> Planner:
        return request.app.state.engine

    def sessions(request: Request) -> Sessions:
        return request.app.state.sessions

    @app.get("/healthz")
    def healthz(request: Request) -> dict[str, Any]:
        return {"ok": True, "model_load_ms": engine(request).load_ms}

    @app.get("/api/state")
    def get_state(request: Request, response: Response) -> dict[str, Any]:
        return sessions(request).get(_session_id(request, response)).to_dict()

    @app.post("/api/reset")
    def reset(request: Request, response: Response) -> dict[str, Any]:
        return sessions(request).reset(_session_id(request, response)).to_dict()

    @app.post("/api/text")
    def text_command(body: TextCommand, request: Request, response: Response) -> CommandResult:
        started = time.perf_counter_ns()
        house = sessions(request).get(_session_id(request, response))
        result = _execute(engine(request).plan_text(body.text), house)
        result.timings["server_ms"] = round((time.perf_counter_ns() - started) / 1e6, 2)
        return result

    @app.post("/api/command")
    async def voice_command(
        request: Request,
        response: Response,
        mode: Annotated[Mode, Query()] = "pipeline",
    ) -> CommandResult:
        """Body: raw little-endian float32 mono samples at 16 kHz."""
        started = time.perf_counter_ns()
        try:
            samples = pcm_from_bytes(await request.body())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        house = sessions(request).get(_session_id(request, response))
        plan = await run_in_threadpool(engine(request).transcribe_and_plan, samples, mode)
        result = _execute(plan, house)
        result.timings["server_ms"] = round((time.perf_counter_ns() - started) / 1e6, 2)
        return result

    @app.post("/api/transcribe")
    async def transcribe(
        request: Request,
        language: Annotated[Language | None, Query()] = None,
        keywords: Annotated[list[str] | None, Query(max_length=MAX_KEYWORDS)] = None,
        words: Annotated[bool, Query()] = True,
    ) -> TranscribeResult:
        """Whistle only. Body: raw little-endian float32 mono samples at 16 kHz."""
        started = time.perf_counter_ns()
        terms = [k.strip() for k in keywords or [] if k.strip()]
        if any(len(k) > MAX_KEYWORD_CHARS for k in terms):
            raise HTTPException(422, f"keywords must be at most {MAX_KEYWORD_CHARS} characters")
        try:
            samples = pcm_from_bytes(await request.body())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        heard = await run_in_threadpool(
            lambda: engine(request).transcribe(
                samples, language=language, keywords=terms, word_timestamps=words
            )
        )
        timings = {
            **heard.timings,
            "server_ms": round((time.perf_counter_ns() - started) / 1e6, 2),
        }
        return TranscribeResult(
            transcript=heard.text,
            language=heard.language,
            words=[Word.model_validate(w) for w in heard.words],
            timings=timings,
        )

    def page(name: str) -> Callable[[], FileResponse]:
        def serve() -> FileResponse:
            return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})

        return serve

    app.get("/", include_in_schema=False)(page("landing.html"))
    app.get("/house", include_in_schema=False)(page("house.html"))
    app.get("/whistle", include_in_schema=False)(page("whistle.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
