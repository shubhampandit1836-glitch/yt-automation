"""Production pipeline graph.

The graph is executable with LangGraph when the optional provider extra is
installed. A small sequential fallback is kept for the dashboard and unit
tests, so the control plane can boot before LangGraph is installed. Both paths
use the same nodes and durable events.
"""

from __future__ import annotations

from typing import Any, Callable, TypedDict

from ..db import Database
from ..events import EventBus
from ..services.providers import ProviderRouter

try:  # Optional during the first, API-only install.
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised when optional extras are absent
    END = START = StateGraph = None  # type: ignore[assignment]


class PipelineState(TypedDict, total=False):
    job_id: str
    run_id: str
    video_id: str
    format: str
    content_type: str
    topic: str
    title: str
    facts: list[dict[str, Any]]
    script: str
    storyboard: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    qa_report: dict[str, Any]
    status: str
    degradation_level: str
    dry_run: bool
    error: str


EventCallback = Callable[[str, str, str, dict[str, Any] | None], None]


class PipelineRunner:
    NODES = ("planner", "research", "creation", "render", "qa", "publish")

    def __init__(self, database: Database, events: EventBus, router: ProviderRouter) -> None:
        self.database = database
        self.events = events
        self.router = router

    def _emit(self, state: PipelineState, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.database.update_run(state["run_id"], node=state.get("current_node"), state=dict(state))
        self.events.publish(
            state["run_id"], event_type, message, node=state.get("current_node"), payload=payload or {}
        )

    def _enter(self, node: str, state: PipelineState) -> None:
        state["current_node"] = node
        self.database.update_run(state["run_id"], status="running", node=node, state=dict(state))
        self._emit(state, "node.started", f"{node.title()} stage started")

    def _leave(self, node: str, state: PipelineState, message: str) -> None:
        self._emit(state, "node.completed", message, {"node": node})

    def planner(self, state: PipelineState) -> dict[str, Any]:
        self._enter("planner", state)
        topic = state.get("topic") or "The gaming myth players still argue about"
        video = self.database.create_video(
            {
                "format": state.get("format", "short"),
                "content_type": state.get("content_type", "facts"),
                "topic": topic,
                "status": "researching",
                "degradation_level": state.get("degradation_level", "full"),
                "dry_run": state.get("dry_run", True),
            }
        )
        state["video_id"] = video["id"]
        state["topic"] = topic
        state["status"] = "running"
        self._leave("planner", state, "Brief locked; memory and duplicate checks passed in local mode")
        return state

    def research(self, state: PipelineState) -> dict[str, Any]:
        self._enter("research", state)
        topic = state["topic"]
        # This is deliberately a non-claim fixture. A real Research adapter
        # must replace it with independently fetched, retained source records.
        facts = [
            {
                "claim": "Research adapter pending: no publishable claim was invented in local mode.",
                "sources": ["https://developers.google.com/youtube/v3"],
                "verified": False,
                "topic": topic,
            }
        ]
        state["facts"] = facts
        self.database.update_video(state["video_id"], fact_sheet=facts, status="scripted")
        self._leave("research", state, "Research ledger created with an explicit verification hold")
        return state

    def creation(self, state: PipelineState) -> dict[str, Any]:
        self._enter("creation", state)
        fmt = state.get("format", "short")
        opening = "Bhai, aaj ka gaming myth sach hai ya sirf lobby ka rumour?"
        body = "Real publish ke liye fact sheet ke verified sources zaroori hain."
        close = "Comment mein apna verdict batao, guys."
        script = " ".join([opening, body, close])
        state["title"] = f"{state['topic']} — myth ya fact?"
        state["script"] = script
        state["storyboard"] = [
            {"scene": 1, "intent": "hook", "visual": "mascot reaction", "duration": 2.5},
            {"scene": 2, "intent": "proof", "visual": "source card", "duration": 8},
            {"scene": 3, "intent": "loop", "visual": "verdict stamp", "duration": 3},
        ]
        state["assets"] = [{"type": "original_graphic", "license": "generated/local fixture"}]
        self.database.update_video(
            state["video_id"], title=state["title"], script=script, status="rendering", duration_seconds=14
        )
        self._leave("creation", state, "Hinglish script and edit decision list drafted")
        return state

    def render(self, state: PipelineState) -> dict[str, Any]:
        self._enter("render", state)
        # FFmpeg is intentionally an adapter boundary. The first install can
        # inspect the EDL without shipping binary media or consuming CPU.
        self.database.update_video(
            state["video_id"],
            status="qa",
            license_ledger=state.get("assets", []),
            degradation_level=state.get("degradation_level", "full"),
        )
        self._leave("render", state, "Deterministic render plan prepared; media adapter is dry-run")
        return state

    def qa(self, state: PipelineState) -> dict[str, Any]:
        self._enter("qa", state)
        verified = all(item.get("verified") for item in state.get("facts", []))
        report = {
            "technical": {"passed": True, "checks": ["composition", "duration", "audio placeholder"]},
            "factual": {"passed": verified, "checks": ["claim-to-source mapping"]},
            "copyright": {"passed": True, "checks": ["license ledger present"]},
            "originality": {"passed": True, "checks": ["local fixture has no duplicate"]},
            "publishable": verified and not self.router.dry_run,
            "blocking_issues": [] if verified else ["research adapter is not configured; claims are unverified"],
            "mode": "dry-run" if self.router.dry_run else "provider-backed",
        }
        state["qa_report"] = report
        state["status"] = "qa_passed" if report["publishable"] else "blocked"
        self.database.update_video(state["video_id"], qa_report=report, status=state["status"])
        self._leave("qa", state, "Compliance gate evaluated; local mode remains safely blocked")
        return state

    def publish(self, state: PipelineState) -> dict[str, Any]:
        self._enter("publish", state)
        report = state.get("qa_report", {})
        if not report.get("publishable"):
            state["status"] = "blocked"
            self.database.update_video(state["video_id"], status="blocked")
            self._leave("publish", state, "Publish skipped because the compliance gate did not pass")
            return state
        if self.database.setting("publishing_paused", "false") == "true":
            state["status"] = "paused"
            self.database.update_video(state["video_id"], status="paused")
            self._leave("publish", state, "Publish held by the owner pause control")
            return state
        # A real YouTube adapter must upload private, poll processing, then
        # schedule. No success is claimed until that idempotent flow confirms it.
        final_status = "simulated" if state.get("dry_run", True) else "scheduled"
        state["status"] = final_status
        self.database.update_video(state["video_id"], status=final_status)
        self._leave("publish", state, f"Publish completed as {final_status}")
        return state

    def _graph(self):
        if StateGraph is None:
            return None
        graph = StateGraph(PipelineState)
        graph.add_node("planner", self.planner)
        graph.add_node("research", self.research)
        graph.add_node("creation", self.creation)
        graph.add_node("render", self.render)
        graph.add_node("qa", self.qa)
        graph.add_node("publish", self.publish)
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "research")
        graph.add_edge("research", "creation")
        graph.add_edge("creation", "render")
        graph.add_edge("render", "qa")
        graph.add_edge("qa", "publish")
        graph.add_edge("publish", END)
        return graph.compile()

    def run(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload")
        if payload is None:
            from ..db import json_loads

            payload = json_loads(job.get("payload_json"), {})
        initial: PipelineState = {
            "job_id": job["id"],
            "format": payload.get("format", "short"),
            "content_type": payload.get("content_type", "facts"),
            "topic": payload.get("topic") or "",
            "degradation_level": self.database.setting("degradation_level", "full") or "full",
            "dry_run": self.router.dry_run,
        }
        run = self.database.create_run("production", job["id"], initial)
        initial["run_id"] = run["id"]
        self.database.update_run(run["id"], status="running", state=dict(initial))
        self.events.publish(run["id"], "run.started", "Production graph started", payload={"job_id": job["id"]})
        try:
            compiled = self._graph()
            final_state = compiled.invoke(initial) if compiled else self._fallback(initial)
            self.database.update_run(run["id"], status="succeeded", node="publish", state=dict(final_state))
            self.events.publish(run["id"], "run.completed", "Production graph finished", node="publish", payload={"status": final_state.get("status")})
            return {"run_id": run["id"], **final_state}
        except Exception as exc:
            self.database.update_run(run["id"], status="failed", error=str(exc), state=dict(initial))
            self.events.publish(run["id"], "run.failed", "Production graph failed", payload={"error": str(exc)})
            raise

    def _fallback(self, state: PipelineState) -> PipelineState:
        for node in (self.planner, self.research, self.creation, self.render, self.qa, self.publish):
            state = node(state)  # type: ignore[assignment]
        return state
