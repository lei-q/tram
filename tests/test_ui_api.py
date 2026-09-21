"""Read-only UI API: state / artifacts / events / SSE stream."""

from fastapi.testclient import TestClient

from tram.models.events import EventKind
from tram.ui.api import create_app


def _client(git_repo) -> TestClient:
    return TestClient(create_app(git_repo))


def test_state_snapshot(ctx, git_repo):
    resp = _client(git_repo).get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_name"] == "demo"
    assert data["phase"] == "initiating"
    assert data["scope_approved"] is False
    assert data["tasks"]["total"] == 0
    assert set(data["gate_status"]) >= {"g0_charter_gate", "g2_quality_gate"}
    assert data["gate_status"]["g0_charter_gate"] == "idle"


def test_state_reflects_baseline_approval(ctx, git_repo, monkeypatch):
    from typer.testing import CliRunner

    from tram.cli import app

    monkeypatch.chdir(git_repo)
    assert CliRunner().invoke(app, ["baseline", "approve", "--by", "lay"]).exit_code == 0
    data = _client(git_repo).get("/api/state").json()
    assert data["scope_approved"] is True


def test_state_includes_evm_snapshot(ctx, git_repo):
    from datetime import date

    from tram.metrics.evm import compute_snapshot, save_snapshot
    from tram.models.task import TaskRecord, TaskStatus

    client = _client(git_repo)
    assert client.get("/api/state").json()["evm"] is None

    state = ctx.load_state()
    state.tasks.append(
        TaskRecord(id="T-001", title="a", status=TaskStatus.DONE, est_points=4, spent_points=5)
    )
    ctx.state_store.save(state)
    save_snapshot(ctx, compute_snapshot(state, day=date(2026, 9, 21)))

    evm = client.get("/api/state").json()["evm"]
    assert evm["date"] == "2026-09-21"
    assert evm["spi"] == 1.0
    assert evm["cpi"] == 0.8
    assert len(evm["breaches"]) == 1 and "CPI" in evm["breaches"][0]


def test_artifacts_endpoint_empty_then_filled(ctx, git_repo):
    client = _client(git_repo)
    assert client.get("/api/artifacts").json() == []

    from tram.artifacts.generator import ArtifactGenerator

    ArtifactGenerator(ctx).generate("charter")
    items = client.get("/api/artifacts").json()
    assert len(items) == 1
    assert items[0]["kind"] == "charter"
    assert items[0]["verified"] is True
    assert items[0]["evidence_verified"] == items[0]["evidence_total"] > 0


def test_events_endpoint_returns_log(ctx, git_repo):
    resp = _client(git_repo).get("/api/events?limit=10")
    assert resp.status_code == 200
    events = resp.json()
    assert events, "init event should exist"
    assert events[-1]["kind"] == "project_initialized"


def test_stream_route_registered(ctx, git_repo):
    client = _client(git_repo)
    paths = {getattr(route, "path", "") for route in client.app.routes}
    assert "/api/stream" in paths


def test_tail_generator_replays_existing_events(ctx, git_repo):
    """The SSE generator replays the whole log before tailing (no await needed)."""
    import asyncio

    from tram.ui.api import tail_event_file

    async def scenario():
        gen = tail_event_file(ctx.events.path)
        first = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert first.startswith("id: 1\n")
        assert "project_initialized" in first
        await gen.aclose()

    asyncio.run(scenario())


def test_tail_generator_follows_appended_events(ctx, git_repo):
    import asyncio

    from tram.ui.api import tail_event_file

    async def scenario():
        total = ctx.events.count()  # replay yields exactly these, then tails
        gen = tail_event_file(ctx.events.path)
        for seq in range(1, total + 1):
            chunk = await asyncio.wait_for(gen.__anext__(), timeout=5)
            assert chunk.startswith(f"id: {seq}\n")
        # append a new event like another tram process would
        ctx.events.append(EventKind.GATE_EVALUATED, source="test")
        appended = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert "gate_evaluated" in appended
        await gen.aclose()

    asyncio.run(scenario())


def test_static_index_served(ctx, git_repo):
    resp = _client(git_repo).get("/")
    assert resp.status_code == 200
    assert "route-map" in resp.text
    assert "tokens.css" in resp.text
