import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import (
    Settings,
    app,
    create_app,
    default_project,
    get_project,
    import_aieng_file,
    package_summary,
    project_dir,
    save_project,
    summarize_cae_payload,
)
from app import runtime as _rt
from app.providers.freecad.adapter import FreeCADAdapter
from app.providers.registry import get_provider


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_provider_registry_returns_freecad_adapter(tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    config = {
        "provider": "freecad",
        "aieng_root": str(settings.aieng_root),
        "freecad_mcp_root": str(settings.freecad_mcp_root),
        "freecad_home": str(settings.freecad_home),
        "topology_backend": "mock",
    }

    provider = get_provider(settings, config)

    assert isinstance(provider, FreeCADAdapter)
    assert provider.provider == "freecad"


def test_import_aieng_runs_complete_semantic_chain(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )

    project = save_project(settings, default_project("demo"))
    source_path = project_dir(settings, project["id"]) / "source" / "demo.step"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("ISO-10303-21;", encoding="utf-8")
    project["source_step"] = "source/demo.step"
    save_project(settings, project)

    calls: list[str] = []

    class FakeProvider:
        def import_step_to_package(self, *, step_path: Path, out_path: Path) -> dict[str, object]:
            assert step_path == source_path
            calls.append("import_step")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake-aieng")
            return {"status": "ok", "package_size": out_path.stat().st_size}

        def enrich_package(self, *, package_path: Path, topology_backend: str) -> dict[str, object]:
            assert package_path.name == "demo.aieng"
            assert topology_backend == "mock"
            calls.append("enrich_package")
            return {
                "status": "ok",
                "package_size": 1234,
                "topology_backend": "mock",
                "generated_resources": [
                    "geometry/topology_map.json",
                    "graph/aag.json",
                    "graph/feature_graph.json",
                    "validation/status.yaml",
                    "validation/completeness_report.json",
                    "README_FOR_AI.md",
                    "ai/summary.md",
                ],
            }

        def validate_package(self, *, package_path: Path) -> dict[str, object]:
            assert package_path.name == "demo.aieng"
            calls.append("validate_package")
            return {"ok": True, "counts": {"PASS": 3}, "messages": []}

    runtime_config = {
        "provider": "freecad",
        "aieng_root": str(settings.aieng_root),
        "freecad_mcp_root": str(settings.freecad_mcp_root),
        "freecad_home": str(settings.freecad_home),
        "topology_backend": "mock",
    }

    monkeypatch.setattr(
        "app.main.resolve_provider_bundle",
        lambda active_settings, overrides=None: (runtime_config, active_settings, FakeProvider()),
    )

    result = import_aieng_file(settings, project["id"])

    assert calls == ["import_step", "enrich_package", "validate_package"]
    assert result["topology_backend"] == "mock"
    assert result["validation"]["ok"] is True
    assert "geometry/topology_map.json" in result["generated_resources"]

    updated = get_project(settings, project["id"])
    assert updated["aieng_file"] == "packages/demo.aieng"
    assert updated["last_validation_ok"] is True
    assert updated["status"] == "validated"


def test_runtime_config_endpoints_persist_and_report_probe(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad-default",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    probe_calls: list[dict[str, object]] = []

    class FakeProvider:
        def probe_capabilities(self, *, whitelisted_tools: list[str]) -> dict[str, object]:
            probe_calls.append({"whitelisted_tools": whitelisted_tools})
            return {
                "provider": "freecad",
                "topology_backend_requested": "mock",
                "topology_backend_resolved": "mock",
                "aieng_root": str((tmp_path / "custom" / "aieng").resolve()),
                "aieng_src_exists": True,
                "freecad_mcp_root": str((tmp_path / "custom" / "freecad-mcp").resolve()),
                "freecad_mcp_src_exists": True,
                "freecad_home": str((tmp_path / "custom" / "freecad").resolve()),
                "freecad_cmd": str((tmp_path / "custom" / "freecad" / "bin" / "FreeCADCmd.exe").resolve()),
                "freecad_python": str((tmp_path / "custom" / "freecad" / "bin" / "python.exe").resolve()),
                "freecad_cmd_exists": True,
                "freecad_python_exists": True,
                "ready": True,
                "issues": [],
                "bridge": {"status": "ok"},
                "whitelisted_tools": whitelisted_tools,
            }

    monkeypatch.setattr(
        "app.main.resolve_provider_bundle",
        lambda active_settings, overrides=None: (
            {
                "provider": "freecad",
                "aieng_root": str((tmp_path / "custom" / "aieng").resolve()),
                "freecad_mcp_root": str((tmp_path / "custom" / "freecad-mcp").resolve()),
                "freecad_home": str((tmp_path / "custom" / "freecad").resolve()),
                "topology_backend": "mock",
            },
            active_settings,
            FakeProvider(),
        ),
    )
    client = TestClient(create_app(settings))
    payload = {
        "provider": "freecad",
        "aieng_root": str((tmp_path / "custom" / "aieng").resolve()),
        "freecad_mcp_root": str((tmp_path / "custom" / "freecad-mcp").resolve()),
        "freecad_home": str((tmp_path / "custom" / "freecad").resolve()),
        "topology_backend": "mock",
    }

    response = client.put("/api/runtime-config", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["config"] == payload
    assert body["probe"]["provider"] == "freecad"
    assert body["probe"]["topology_backend_resolved"] == "mock"
    assert body["probe"]["ready"] is True
    assert Path(body["config_path"]).exists()
    assert json.loads(Path(body["config_path"]).read_text(encoding="utf-8")) == payload

    current = client.get("/api/runtime-config")
    assert current.status_code == 200
    assert current.json()["config"] == payload

    runtime = client.get("/api/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["config"] == payload

    test_response = client.post(
        "/api/runtime-config/test",
        json={**payload, "topology_backend": "auto"},
    )
    assert test_response.status_code == 200
    assert test_response.json()["config"]["topology_backend"] == "auto"
    assert len(probe_calls) >= 3


def test_package_summary_exposes_optional_cae_payload(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )

    project = save_project(settings, default_project("cae-demo"))
    package_path = project_dir(settings, project["id"]) / "packages" / "cae-demo.aieng"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"model_id": "cae-demo"}))
        archive.writestr(
            "graph/constraints.json",
            json.dumps(
                {
                    "format_version": "0.1.0",
                    "constraints": [
                        {
                            "id": "con_static_001",
                            "type": "simulation_target",
                            "target": "sim_static_001",
                            "metric": "max_von_mises_stress_mpa",
                            "operator": "<=",
                            "value": 120,
                            "reason": "Keep stress below target.",
                        }
                    ],
                }
            ),
        )
        archive.writestr(
            "simulation/cae_imports/parsed_loads.json",
            json.dumps({"loads": [{"id": "load_001", "kind": "force", "target": "LOAD_FACE"}]}),
        )
        archive.writestr(
            "results/evidence_index.json",
            json.dumps(
                {
                    "format_version": "0.1.0",
                    "evidence_items": [
                        {
                            "evidence_id": "ev_solver_001",
                            "evidence_type": "solver_result",
                            "artifact": {"kind": "json", "path": "results/solver/result.json"},
                            "verification": {"status": "available"},
                        }
                    ],
                }
            ),
        )
        archive.writestr(
            "validation/status.yaml",
            "solver_mesh_status:\n  mesh_generation: not_run\n  stress_validation: not_validated\n",
        )

    project["aieng_file"] = "packages/cae-demo.aieng"
    save_project(settings, project)

    def raise_bridge(*args: object, **kwargs: object) -> object:
        raise RuntimeError("bridge unavailable")

    monkeypatch.setattr("app.main.resolve_provider_bundle", raise_bridge)
    monkeypatch.setattr("app.main.runtime_status", lambda active_settings: {"provider": "freecad", "ready": False})

    summary = package_summary(settings, project["id"])

    assert summary["constraints"]["constraints"][0]["metric"] == "max_von_mises_stress_mpa"
    assert summary["cae"]["present"] is True
    assert summary["cae"]["loads_count"] == 1
    assert summary["cae"]["results_available"] is True
    assert "stress" in summary["cae"]["available_fields"]
    solver_fields = summary["cae"]["solver_fields"]
    assert isinstance(solver_fields, list) and len(solver_fields) > 0
    stress_field = next(sf for sf in solver_fields if sf["field_name"] == "stress")
    assert stress_field["format"] == "vertex_synthetic"
    assert f"/api/projects/{project['id']}/fields/stress" == stress_field["descriptor_url"]
    assert stress_field["min_value"] == 0.0
    assert stress_field["max_value"] == 250.0
    assert stress_field["unit"] == "MPa"


def test_field_descriptor_endpoint_returns_synthetic_contract(tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("field-test"))
    client = TestClient(create_app(settings))

    response = client.get(f"/api/projects/{project['id']}/fields/stress")
    assert response.status_code == 200
    data = response.json()
    assert data["field_name"] == "stress"
    assert data["project_id"] == project["id"]
    assert data["format"] == "vertex_synthetic"
    assert data["basis"] == "y_normalized"
    assert data["min_value"] == 0.0
    assert data["max_value"] == 250.0
    assert data["unit"] == "MPa"
    assert data["colormap"] == "thermal"
    assert data["source"] == "synthetic_mock"

    disp = client.get(f"/api/projects/{project['id']}/fields/displacement")
    assert disp.status_code == 200
    d = disp.json()
    assert d["field_name"] == "displacement"
    assert d["max_value"] == 5.0
    assert d["colormap"] == "coolwarm"

    unknown = client.get(f"/api/projects/{project['id']}/fields/temperature")
    assert unknown.status_code == 200
    u = unknown.json()
    assert u["field_name"] == "temperature"
    assert u["format"] == "vertex_synthetic"

    missing = client.get("/api/projects/nonexistent123456/fields/stress")
    assert missing.status_code == 404


def test_cae_artifacts_endpoint_returns_detection_result(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("cae-test"))
    pkg_dir = project_dir(settings, project["id"]) / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = pkg_dir / "test.aieng"
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test"}))
    project["aieng_file"] = "packages/test.aieng"
    save_project(settings, project)

    fake_result = {
        "mode": "cae_setup",
        "artifacts": {"graph/constraints.json": True, "simulation/mesh/model.vtu": False},
        "has_cae_setup": True,
        "has_mesh": False,
        "has_solver_settings": False,
        "has_results": False,
        "has_fields": False,
        "has_validation": False,
        "detected_count": 1,
        "total_count": 15,
    }
    monkeypatch.setattr("app.main._detect_cae_artifacts", lambda _s, _p: fake_result)

    client = TestClient(create_app(settings))
    response = client.get(f"/api/projects/{project['id']}/cae-artifacts")
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "cae_setup"
    assert data["has_cae_setup"] is True
    assert data["artifacts"]["graph/constraints.json"] is True
    assert data["artifacts"]["simulation/mesh/model.vtu"] is False


def test_cae_artifacts_endpoint_404_when_no_package(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("cae-test"))
    client = TestClient(create_app(settings))
    response = client.get(f"/api/projects/{project['id']}/cae-artifacts")
    assert response.status_code == 404


def test_cae_result_summary_endpoint_returns_summary(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )
    project = save_project(settings, default_project("cae-test"))
    pkg_dir = project_dir(settings, project["id"]) / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = pkg_dir / "test.aieng"
    with zipfile.ZipFile(pkg_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test"}))
    project["aieng_file"] = "packages/test.aieng"
    save_project(settings, project)

    fake_result = {
        "schema_version": "0.1",
        "summary_type": "cae_postprocessing",
        "status": {"mode": "cad_only", "warnings": []},
        "computed_values": {"extrema_computed": False, "max_displacement": None, "max_von_mises_stress": None, "minimum_safety_factor": None},
        "llm_summary": {"one_line": "CAD-only package; no CAE artifacts detected.", "key_findings": [], "risks": [], "recommended_next_actions": [], "limitations": []},
    }
    monkeypatch.setattr("app.main._generate_cae_result_summary", lambda _s, _p: fake_result)

    client = TestClient(create_app(settings))
    response = client.get(f"/api/projects/{project['id']}/cae-result-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == "0.1"
    assert data["status"]["mode"] == "cad_only"


def test_summarize_cae_payload_does_not_include_solver_fields() -> None:
    result = summarize_cae_payload(
        constraints={"constraints": [{"id": "c1", "type": "simulation_target", "metric": "stress"}]},
        parsed_materials=None,
        parsed_boundary_conditions=None,
        parsed_loads=None,
        cae_mapping=None,
        evidence_index=None,
        validation_status=None,
    )
    assert "stress" in result["available_fields"]
    assert "solver_fields" not in result


# ── runtime tests ──────────────────────────────────────────────────────────────

def _make_runtime_settings(tmp_path: Path) -> Settings:
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        aieng_root=tmp_path / "workspace" / "aieng",
        freecad_mcp_root=tmp_path / "workspace" / "aieng-freecad-mcp",
        freecad_home=tmp_path / "workspace" / "freecad",
        sample_step=tmp_path / "workspace" / "sample.step",
    )


def test_runtime_run_completed_with_registered_tool(tmp_path: Path) -> None:
    called: list[dict] = []

    def fake_tool(inp: dict, ctx: dict) -> dict:
        called.append({"inp": inp, "ctx": ctx})
        return {"result": "ok", "data": 42}

    _rt.register_tool("test.echo", fake_tool)
    try:
        run = _rt.RunRecord(
            run_id="test001",
            message="echo test",
            created_at="2026-01-01T00:00:00+00:00",
            status="pending",
        )
        # Override plan to use our registered test tool
        run.plan = [{"name": "test.echo", "description": "echo", "input": {"x": 1}}]
        _rt._STORE.pop("test001", None)

        # patch build_plan to return our custom step
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.echo", "description": "echo", "input": {"x": 1}}]
        try:
            result = _rt.execute_run(run, {})
        finally:
            _rt.build_plan = original_build

        assert result.status == "completed"
        assert len(result.tool_results) == 1
        assert result.tool_results[0].status == "success"
        assert result.tool_results[0].output == {"result": "ok", "data": 42}
        event_types = [e.type for e in result.events]
        assert "run_started" in event_types
        assert "tool_started" in event_types
        assert "tool_succeeded" in event_types
        assert "run_completed" in event_types
        assert called[0]["inp"] == {"x": 1}
    finally:
        _rt._REGISTRY.pop("test.echo", None)
        _rt._STORE.pop("test001", None)


def test_runtime_run_failed_tool_produces_error_event(tmp_path: Path) -> None:
    def boom_tool(inp: dict, ctx: dict) -> dict:
        raise ValueError("something went wrong")

    _rt.register_tool("test.boom", boom_tool)
    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.boom", "description": "fail", "input": {}}]
        try:
            run = _rt.RunRecord(
                run_id="test002",
                message="boom",
                created_at="2026-01-01T00:00:00+00:00",
                status="pending",
            )
            _rt._STORE.pop("test002", None)
            result = _rt.execute_run(run, {})
        finally:
            _rt.build_plan = original_build

        assert result.status == "failed"
        assert len(result.errors) == 1
        assert "ValueError" in result.errors[0]
        assert "something went wrong" in result.errors[0]
        event_types = [e.type for e in result.events]
        assert "tool_failed" in event_types
        assert "run_failed" in event_types
        failed_ev = next(e for e in result.events if e.type == "tool_failed")
        assert failed_ev.payload["tool"] == "test.boom"
    finally:
        _rt._REGISTRY.pop("test.boom", None)
        _rt._STORE.pop("test002", None)


def test_runtime_freecad_run_macro_requires_approval(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/runtime/runs",
        json={"message": "run macro"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "awaiting_approval"
    approval_events = [e for e in data["events"] if e["type"] == "approval_required"]
    assert len(approval_events) == 1
    assert approval_events[0]["payload"]["tool"] == "freecad.run_macro"
    needs = [r for r in data["tool_results"] if r["status"] == "needs_approval"]
    assert len(needs) == 1


def test_runtime_run_status_readable_after_execution(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/runtime/runs",
        json={"message": "inspect package"},
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    get_resp = client.get(f"/api/runtime/runs/{run_id}")
    assert get_resp.status_code == 200
    fetched = get_resp.json()
    assert fetched["run_id"] == run_id
    assert fetched["status"] in ("completed", "failed", "awaiting_approval")

    events_resp = client.get(f"/api/runtime/runs/{run_id}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()
    assert isinstance(events, list)
    assert any(e["type"] == "run_started" for e in events)


def test_runtime_run_not_found_returns_404(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    assert client.get("/api/runtime/runs/doesnotexist").status_code == 404
    assert client.get("/api/runtime/runs/doesnotexist/events").status_code == 404


def test_runtime_inspect_package_tool_via_endpoint(monkeypatch, tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("rt-test"))

    monkeypatch.setattr(
        "app.main.resolve_provider_bundle",
        lambda s, overrides=None: ({}, s, type("P", (), {"probe_capabilities": lambda *a, **kw: {}})()),
    )
    monkeypatch.setattr("app.main.runtime_status", lambda s: {"provider": "mock", "ready": False})

    client = TestClient(create_app(settings))
    response = client.post(
        "/api/runtime/runs",
        json={"message": "inspect the package", "project_id": project["id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == project["id"]
    assert any(tc["name"] == "aieng.inspect_package" for tc in data["tool_calls"])
    assert data["status"] in ("completed", "failed")


# ── Phase 1 hardening tests ────────────────────────────────────────────────────

def test_runtime_run_is_persisted_after_creation(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/api/runtime/runs", json={"message": "inspect package"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]

    state_dir = tmp_path / "data" / "runtime" / "runs"
    run_file = state_dir / f"{run_id}.json"
    assert run_file.exists(), "run should be persisted to disk"
    persisted = json.loads(run_file.read_text(encoding="utf-8"))
    assert persisted["run_id"] == run_id
    assert persisted["status"] in ("completed", "failed", "awaiting_approval")


def test_runtime_run_listing_endpoint(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp1 = client.post("/api/runtime/runs", json={"message": "inspect package"})
    assert resp1.status_code == 200
    run_id = resp1.json()["run_id"]

    list_resp = client.get("/api/runtime/runs")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert isinstance(items, list)
    found = next((r for r in items if r["run_id"] == run_id), None)
    assert found is not None, "created run must appear in listing"
    assert "status" in found
    assert "message" in found
    assert "created_at" in found
    assert "event_count" in found
    assert "last_event_type" in found


def test_runtime_run_remains_readable_after_store_reload(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.post("/api/runtime/runs", json={"message": "inspect package"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    # Simulate server restart: clear in-memory store
    _rt._STORE.pop(run_id, None)

    # The run must still be loadable via the GET endpoint (from disk)
    get_resp = client.get(f"/api/runtime/runs/{run_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["run_id"] == run_id


def test_runtime_approval_required_pauses_run(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post("/api/runtime/runs", json={"message": "run macro"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "awaiting_approval"
    assert data["pending_step_index"] is not None
    assert any(e["type"] == "approval_required" for e in data["events"])


def test_runtime_approve_run_executes_pending_tool(tmp_path: Path) -> None:
    """A custom approval-gated tool executes successfully after approve is called."""
    executed: list[dict] = []

    def _approvalable_tool(inp: dict, ctx: dict) -> dict:
        executed.append(inp)
        return {"approved": True}

    _rt.register_tool("test.gated", _approvalable_tool, requires_approval=True, description="test")
    settings = _make_runtime_settings(tmp_path)

    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.gated", "description": "test", "input": {}}]
        client = TestClient(create_app(settings))
        try:
            resp = client.post("/api/runtime/runs", json={"message": "run gated"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "awaiting_approval"
            run_id = data["run_id"]

            approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
            assert approve_resp.status_code == 200
            approved = approve_resp.json()
            assert approved["status"] == "completed"
            assert len(executed) == 1
            assert any(e["type"] == "approval_granted" for e in approved["events"])
            assert any(e["type"] == "tool_succeeded" for e in approved["events"])
            assert any(e["type"] == "run_completed" for e in approved["events"])
        finally:
            _rt.build_plan = original_build
    finally:
        _rt._REGISTRY.pop("test.gated", None)


def test_runtime_reject_run_does_not_execute_tool(tmp_path: Path) -> None:
    """Rejecting an approval-gated run does not execute the tool."""
    executed: list[dict] = []

    def _dangerous_tool(inp: dict, ctx: dict) -> dict:
        executed.append(inp)
        return {"oops": "should not reach here"}

    _rt.register_tool("test.dangerous", _dangerous_tool, requires_approval=True, description="test")
    settings = _make_runtime_settings(tmp_path)

    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.dangerous", "description": "test", "input": {}}]
        client = TestClient(create_app(settings))
        try:
            resp = client.post("/api/runtime/runs", json={"message": "run dangerous"})
            assert resp.status_code == 200
            run_id = resp.json()["run_id"]

            reject_resp = client.post(f"/api/runtime/runs/{run_id}/reject")
            assert reject_resp.status_code == 200
            rejected = reject_resp.json()
            assert rejected["status"] == "rejected"
            assert len(executed) == 0, "tool must NOT have been executed"
            assert any(e["type"] == "approval_rejected" for e in rejected["events"])
            assert any(e["type"] == "run_rejected" for e in rejected["events"])
            assert len(rejected["tool_errors"]) > 0
        finally:
            _rt.build_plan = original_build
    finally:
        _rt._REGISTRY.pop("test.dangerous", None)


def test_runtime_approve_nonexistent_run_returns_404(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))
    assert client.post("/api/runtime/runs/nonexistent/approve").status_code == 404
    assert client.post("/api/runtime/runs/nonexistent/reject").status_code == 404


def test_runtime_tools_endpoint_returns_registry(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    tools = resp.json()
    assert isinstance(tools, list)
    names = [t["name"] for t in tools]
    assert "aieng.inspect_package" in names
    assert "freecad.run_macro" in names
    macro = next(t for t in tools if t["name"] == "freecad.run_macro")
    assert macro["requires_approval"] is True
    assert isinstance(macro["description"], str) and len(macro["description"]) > 0
    for tool in tools:
        assert "name" in tool
        assert "requires_approval" in tool
        assert "description" in tool


def test_agent_plan_dry_run_without_api_key_returns_guarded_plan(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("agent-test"))
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/agent/plan",
        json={
            "message": "帮我检查这个模型并准备减重建模",
            "project_id": project["id"],
            "dry_run": True,
            "llm_config": {
                "provider": "openai-compatible",
                "model": "fake",
                "api_key": "must-not-persist",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "heuristic"
    assert data["project_id"] == project["id"]
    tools = data["preview"]["tools"]
    assert "aieng.inspect_package" in tools
    assert "mcp.check" in tools
    assert "api_key" not in data["llm_config"]
    assert data["warnings"], "modeling requests without patch_json should explain the missing executable patch"


def test_agent_run_without_project_completes_with_empty_safe_plan(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/agent/runs",
        json={"message": "解释一下如何开始建模", "dry_run": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["mode"] == "heuristic"
    assert data["agent"]["steps"] == []
    assert data["run"]["status"] == "completed"
    assert data["run"]["project_id"] is None


# ── Phase 2: freecad.inspect_geometry tests ───────────────────────────────────

def test_freecad_inspect_geometry_success_via_mocked_bridge(monkeypatch, tmp_path: Path) -> None:
    """inspect_geometry returns bridge output when bridge succeeds."""
    settings = _make_runtime_settings(tmp_path)

    fake_result = {
        "status": "ok",
        "input_path": "/fake/part.step",
        "freecad_version": "0.21.0",
        "object_count": 1,
        "objects": [{"name": "Shape", "solid_count": 1, "face_count": 6}],
        "total_solid_count": 1,
        "total_face_count": 6,
        "total_edge_count": 12,
        "total_vertex_count": 8,
        "total_volume_mm3": 1000.0,
        "total_area_mm2": 600.0,
        "bounding_box": {"xmin": 0, "xmax": 10, "ymin": 0, "ymax": 10, "zmin": 0, "zmax": 10,
                         "xlen": 10, "ylen": 10, "zlen": 10},
    }

    monkeypatch.setattr("app.freecad_bridge.inspect_geometry", lambda *a, **kw: fake_result)

    # Create a project with a source_step pointing to a real file
    project = save_project(settings, default_project("geo-test"))
    source_dir = settings.projects_root / project["id"] / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    step_file = source_dir / "part.step"
    step_file.write_text("ISO-10303-21;", encoding="utf-8")
    project["source_step"] = "source/part.step"
    save_project(settings, project)

    client = TestClient(create_app(settings))
    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "freecad.inspect_geometry", "description": "inspect", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post(
            "/api/runtime/runs",
            json={"message": "inspect geometry", "project_id": project["id"]},
        )
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert len(data["tool_results"]) == 1
    assert data["tool_results"][0]["status"] == "success"
    out = data["tool_results"][0]["output"]
    assert out["status"] == "ok"
    assert out["total_face_count"] == 6


def test_freecad_inspect_geometry_missing_input_returns_error(monkeypatch, tmp_path: Path) -> None:
    """When no input file is available, the tool returns a structured error."""
    settings = _make_runtime_settings(tmp_path)

    # Project with no source_step
    project = save_project(settings, default_project("geo-missing"))

    client = TestClient(create_app(settings))
    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "freecad.inspect_geometry", "description": "inspect", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post(
            "/api/runtime/runs",
            json={"message": "inspect geometry", "project_id": project["id"]},
        )
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    # Tool returns structured error dict, not an exception — run completes
    assert data["status"] == "completed"
    out = data["tool_results"][0]["output"]
    assert out["status"] == "error"
    assert out["code"] == "missing_input"


def test_freecad_inspect_geometry_bridge_exception_produces_tool_failed(monkeypatch, tmp_path: Path) -> None:
    """When the bridge raises, the run records tool_failed and run_failed."""
    settings = _make_runtime_settings(tmp_path)

    def _fail(*a, **kw):
        raise RuntimeError("FreeCADCmd not found")

    monkeypatch.setattr("app.freecad_bridge.inspect_geometry", _fail)

    project = save_project(settings, default_project("geo-fail"))
    source_dir = settings.projects_root / project["id"] / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    step_file = source_dir / "part.step"
    step_file.write_text("ISO-10303-21;", encoding="utf-8")
    project["source_step"] = "source/part.step"
    save_project(settings, project)

    client = TestClient(create_app(settings))
    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "freecad.inspect_geometry", "description": "inspect", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post(
            "/api/runtime/runs",
            json={"message": "inspect geometry", "project_id": project["id"]},
        )
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    event_types = [e["type"] for e in data["events"]]
    assert "tool_failed" in event_types
    assert "run_failed" in event_types


def test_freecad_run_macro_approval_unchanged_by_phase2(tmp_path: Path) -> None:
    """Phase 2 must not change the approval requirement for freecad.run_macro."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.post("/api/runtime/runs", json={"message": "run macro"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "awaiting_approval"
    assert any(e["type"] == "approval_required" for e in data["events"])
    approval_ev = next(e for e in data["events"] if e["type"] == "approval_required")
    assert approval_ev["payload"]["tool"] == "freecad.run_macro"


def _execute_run_macro(client, tool_input):
    """Start a macro run via the runtime endpoint and auto-approve if gated."""
    resp = client.post("/api/runtime/runs", json={
        "message": "run macro",
        "tool_input": tool_input,
    })
    assert resp.status_code == 200
    data = resp.json()
    if data["status"] == "awaiting_approval":
        run_id = data["run_id"]
        approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
        assert approve_resp.status_code == 200
        data = approve_resp.json()
    return data


def test_freecad_run_macro_success_via_mocked_bridge(monkeypatch, tmp_path: Path) -> None:
    """freecad.run_macro returns bridge output when macro execution succeeds."""
    settings = _make_runtime_settings(tmp_path)

    fake_result = {
        "status": "ok",
        "macro_path": "/fake/macro.py",
        "document_path": "",
        "freecad_version": "0.21.0",
        "stdout": "Macro executed successfully",
        "stderr": "",
        "return_code": 0,
        "warnings": [],
    }

    monkeypatch.setattr("app.freecad_bridge.run_macro", lambda *a, **kw: fake_result)

    macro_file = tmp_path / "macro.py"
    macro_file.write_text("print('hello')", encoding="utf-8")

    client = TestClient(create_app(settings))
    data = _execute_run_macro(client, {"macro_path": str(macro_file)})

    assert data["status"] == "completed"
    out = data["tool_results"][0]["output"]
    assert out["status"] == "ok"
    assert out["stdout"] == "Macro executed successfully"
    assert out["return_code"] == 0


def test_freecad_run_macro_missing_input_returns_error(tmp_path: Path) -> None:
    """When no macro file is provided, the tool returns a structured error."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    data = _execute_run_macro(client, {})

    assert data["status"] == "completed"
    out = data["tool_results"][0]["output"]
    assert out["status"] == "error"
    assert out["code"] == "missing_macro"


def test_freecad_run_macro_bridge_exception_produces_tool_failed(monkeypatch, tmp_path: Path) -> None:
    """When the bridge raises, the run records tool_failed and run_failed."""
    settings = _make_runtime_settings(tmp_path)

    def _fail(*a, **kw):
        raise RuntimeError("FreeCADCmd not found")

    monkeypatch.setattr("app.freecad_bridge.run_macro", _fail)

    macro_file = tmp_path / "macro.py"
    macro_file.write_text("print('hello')", encoding="utf-8")

    client = TestClient(create_app(settings))
    data = _execute_run_macro(client, {"macro_path": str(macro_file)})

    assert data["status"] == "failed"
    event_types = [e["type"] for e in data["events"]]
    assert "tool_failed" in event_types


# ── Phase 2.5: freecad.export_step tests ──────────────────────────────────────

def test_freecad_export_step_success_via_mocked_bridge(monkeypatch, tmp_path: Path) -> None:
    """export_step returns artifacts in tool_results when bridge succeeds."""
    settings = _make_runtime_settings(tmp_path)

    fake_result = {
        "status": "ok",
        "inputPath": "/fake/part.step",
        "outputPath": "/fake/part_export.step",
        "adapter": "freecad",
        "freecad_version": "0.21.0",
        "object_count": 1,
        "artifacts": [
            {"path": "/fake/part_export.step", "kind": "step", "role": "primary_geometry"}
        ],
        "warnings": [],
    }

    monkeypatch.setattr("app.freecad_bridge.export_step", lambda *a, **kw: fake_result)

    project = save_project(settings, default_project("export-test"))
    source_dir = settings.projects_root / project["id"] / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    step_file = source_dir / "part.step"
    step_file.write_text("ISO-10303-21;", encoding="utf-8")
    project["source_step"] = "source/part.step"
    save_project(settings, project)

    client = TestClient(create_app(settings))
    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "freecad.export_step", "description": "export", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post(
            "/api/runtime/runs",
            json={"message": "export step", "project_id": project["id"]},
        )
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert len(data["tool_results"]) == 1
    tr = data["tool_results"][0]
    assert tr["status"] == "success"
    assert tr["output"]["status"] == "ok"
    assert len(tr["artifacts"]) == 1
    assert tr["artifacts"][0]["kind"] == "step"
    assert tr["artifacts"][0]["role"] == "primary_geometry"


def test_freecad_export_step_missing_input_returns_error(monkeypatch, tmp_path: Path) -> None:
    """When no input file is available, export_step returns a structured error."""
    settings = _make_runtime_settings(tmp_path)

    # Project with no source_step
    project = save_project(settings, default_project("export-missing"))

    client = TestClient(create_app(settings))
    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "freecad.export_step", "description": "export", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post(
            "/api/runtime/runs",
            json={"message": "export step", "project_id": project["id"]},
        )
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    out = data["tool_results"][0]["output"]
    assert out["status"] == "error"
    assert out["code"] in ("missing_input", "file_not_found")


def test_freecad_export_step_bridge_exception_produces_tool_failed(monkeypatch, tmp_path: Path) -> None:
    """When the bridge raises, the run records tool_failed and run_failed."""
    settings = _make_runtime_settings(tmp_path)

    def _fail(*a, **kw):
        raise RuntimeError("FreeCADCmd not found")

    monkeypatch.setattr("app.freecad_bridge.export_step", _fail)

    project = save_project(settings, default_project("export-fail"))
    source_dir = settings.projects_root / project["id"] / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    step_file = source_dir / "part.step"
    step_file.write_text("ISO-10303-21;", encoding="utf-8")
    project["source_step"] = "source/part.step"
    save_project(settings, project)

    client = TestClient(create_app(settings))
    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "freecad.export_step", "description": "export", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post(
            "/api/runtime/runs",
            json={"message": "export step", "project_id": project["id"]},
        )
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    event_types = [e["type"] for e in data["events"]]
    assert "tool_failed" in event_types
    assert "run_failed" in event_types


def test_freecad_export_step_generates_output_path_when_not_provided(monkeypatch, tmp_path: Path) -> None:
    """When no outputPath is provided, the handler generates a safe _export suffix path."""
    settings = _make_runtime_settings(tmp_path)

    captured: list[dict] = []

    def _fake_export(input_path, output_path, **kw):
        captured.append({"input": str(input_path), "output": str(output_path)})
        return {
            "status": "ok",
            "inputPath": str(input_path),
            "outputPath": str(output_path),
            "adapter": "freecad",
            "freecad_version": "0.21.0",
            "object_count": 1,
            "artifacts": [{"path": str(output_path), "kind": "step", "role": "primary_geometry"}],
            "warnings": [],
        }

    monkeypatch.setattr("app.freecad_bridge.export_step", _fake_export)

    project = save_project(settings, default_project("export-autopath"))
    source_dir = settings.projects_root / project["id"] / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    step_file = source_dir / "part.step"
    step_file.write_text("ISO-10303-21;", encoding="utf-8")
    project["source_step"] = "source/part.step"
    save_project(settings, project)

    client = TestClient(create_app(settings))
    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "freecad.export_step", "description": "export", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post(
            "/api/runtime/runs",
            json={"message": "export step", "project_id": project["id"]},
        )
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert len(captured) == 1
    assert "_export" in captured[0]["output"]
    assert captured[0]["output"].endswith(".step")


def test_runtime_artifacts_extracted_into_tool_result(tmp_path: Path) -> None:
    """Artifacts returned by a tool handler are hoisted into ToolResult.artifacts."""
    artifact = {"path": "/fake/out.step", "kind": "step", "role": "primary_geometry"}

    def artifact_tool(inp: dict, ctx: dict) -> dict:
        return {"status": "ok", "artifacts": [artifact]}

    _rt.register_tool("test.artifact", artifact_tool)
    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [
            {"name": "test.artifact", "description": "test", "input": {}}
        ]
        run = _rt.RunRecord(
            run_id="art001",
            message="artifact test",
            created_at="2026-01-01T00:00:00+00:00",
            status="pending",
        )
        _rt._STORE.pop("art001", None)
        try:
            result = _rt.execute_run(run, {})
        finally:
            _rt.build_plan = original_build

        assert result.status == "completed"
        assert len(result.tool_results) == 1
        tr = result.tool_results[0]
        assert tr.status == "success"
        assert len(tr.artifacts) == 1
        assert tr.artifacts[0]["kind"] == "step"
        assert tr.artifacts[0]["path"] == "/fake/out.step"
    finally:
        _rt._REGISTRY.pop("test.artifact", None)
        _rt._STORE.pop("art001", None)


def test_runtime_plan_selects_export_intent(tmp_path: Path) -> None:
    """'export cad' and 'export step' messages route to freecad.export_step."""
    from app.runtime import build_plan

    for msg in ["export cad", "export step", "导出step"]:
        plan = build_plan(msg, None)
        assert len(plan) == 1, f"Expected 1 step for {msg!r}, got {plan}"
        assert plan[0]["name"] == "freecad.export_step", (
            f"Expected freecad.export_step for {msg!r}, got {plan[0]['name']}"
        )


def test_runtime_plan_selects_computed_metrics_intent(tmp_path: Path) -> None:
    """'generate computed metrics' routes to postprocess.generate_computed_metrics."""
    from app.runtime import build_plan

    for msg in ["generate computed metrics", "import computed metrics", "归一化指标"]:
        plan = build_plan(msg, None)
        assert len(plan) == 1, f"Expected 1 step for {msg!r}, got {plan}"
        assert plan[0]["name"] == "postprocess.generate_computed_metrics", (
            f"Expected postprocess.generate_computed_metrics for {msg!r}, got {plan[0]['name']}"
        )


def test_runtime_tool_input_merged_into_step_input(tmp_path: Path) -> None:
    """Structured tool_input from ctx is merged into each plan step."""
    called: list[dict] = []

    def capture_tool(inp: dict, ctx: dict) -> dict:
        called.append(inp)
        return {"status": "ok"}

    _rt.register_tool("test.capture", capture_tool)
    try:
        original_build = _rt.build_plan
        _rt.build_plan = lambda msg, pid: [{"name": "test.capture", "description": "capture", "input": {"base": 1}}]
        try:
            run = _rt.RunRecord(
                run_id="ti001",
                message="capture test",
                created_at="2026-01-01T00:00:00+00:00",
                status="pending",
            )
            _rt._STORE.pop("ti001", None)
            result = _rt.execute_run(run, {"tool_input": {"extra": 2}})
        finally:
            _rt.build_plan = original_build

        assert result.status == "completed"
        assert called[0]["base"] == 1
        assert called[0]["extra"] == 2
    finally:
        _rt._REGISTRY.pop("test.capture", None)
        _rt._STORE.pop("ti001", None)


def test_generate_computed_metrics_tool_registered(tmp_path: Path) -> None:
    """The runtime tool registry includes postprocess.generate_computed_metrics."""
    from app.runtime import registered_tools_info

    names = [t["name"] for t in registered_tools_info()]
    assert "postprocess.generate_computed_metrics" in names


def test_refresh_cae_summary_tool_registered(tmp_path: Path) -> None:
    """The runtime tool registry includes postprocess.refresh_cae_summary."""
    from app.runtime import registered_tools_info

    names = [t["name"] for t in registered_tools_info()]
    assert "postprocess.refresh_cae_summary" in names


def test_runtime_plan_selects_refresh_cae_summary_intent(tmp_path: Path) -> None:
    """'refresh cae summary' includes postprocess.refresh_cae_summary in plan."""
    from app.runtime import build_plan

    for msg in ["refresh cae summary", "update postprocessing summary", "刷新cae摘要"]:
        plan = build_plan(msg, None)
        names = [s["name"] for s in plan]
        assert "postprocess.refresh_cae_summary" in names, (
            f"Expected postprocess.refresh_cae_summary in plan for {msg!r}, got {names}"
        )


def test_refresh_cae_summary_missing_package_path_returns_error(tmp_path: Path) -> None:
    """refresh_cae_summary returns structured error when no package path can be resolved."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "postprocess.refresh_cae_summary", "description": "refresh", "input": {}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "refresh cae summary",
            "tool_input": {},
        })
        assert resp.status_code == 200
        run = resp.json()
        # Runtime treats handler returns without exception as success;
        # the error is encoded in the tool result output.
        assert run["status"] == "completed"
        results = run["tool_results"]
        assert any(
            r.get("output", {}).get("code") == "missing_cae_summary_package_path"
            for r in results
        )
    finally:
        _rt.build_plan = original_build


def test_postprocessing_smoke_metrics_import_and_summary_refresh(tmp_path: Path) -> None:
    """Generic end-to-end smoke test for the post-processing workflow.

    Flow:
      1. Create a temp project with a minimal .aieng package.
      2. Write a generic metrics CSV to a temp path.
      3. Run postprocess.generate_computed_metrics via runtime.
      4. Assert computed_metrics.json was written back into the .aieng package.
      5. Run postprocess.refresh_cae_summary via runtime.
      6. Assert the refreshed summary contains the imported metrics.

    This test uses generic names only (no part-family-specific fixtures).
    """
    from app.main import Settings, create_app, default_project, get_project, project_dir, save_project
    import zipfile

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(__file__).resolve().parents[3] / "aieng",
        freecad_mcp_root=Path(__file__).resolve().parents[3] / "aieng-freecad-mcp",
        freecad_home=workspace / "freecad",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    # 1. Create project and minimal .aieng package
    project = save_project(settings, default_project("generic-smoke"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "generic-smoke.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "generic-smoke", "resources": {}}))
    project["aieng_file"] = "generic-smoke.aieng"
    save_project(settings, project)

    # 2. Write generic metrics CSV
    metrics_csv = tmp_path / "generic_metrics.csv"
    metrics_csv.write_text(
        "name,value,unit\n"
        "max_von_mises_stress,187.4,MPa\n"
        "max_displacement,0.82,mm\n"
        "minimum_safety_factor,1.33,\n",
        encoding="utf-8",
    )

    # 3. Generate computed metrics via runtime
    gen_resp = client.post("/api/runtime/runs", json={
        "message": "generate computed metrics",
        "project_id": project_id,
        "tool_input": {
            "inputPath": str(metrics_csv),
            "project_id": project_id,
            "loadCaseId": "load_case_001",
            "software": "External postprocessor",
        },
    })
    assert gen_resp.status_code == 200
    gen_run = gen_resp.json()
    assert gen_run["status"] == "completed", f"generate computed metrics failed: {gen_run}"
    # Assert artifact was produced on disk
    computed_metrics_path = pkg_path.parent / "results" / "computed_metrics.json"
    assert computed_metrics_path.exists(), f"computed_metrics.json not found at {computed_metrics_path}"
    # Assert artifact was written back into the .aieng package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/computed_metrics.json" in zf.namelist(), "computed_metrics.json not in package"

    # 4. Refresh CAE summary via runtime
    refresh_resp = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "overwrite": True,
        },
    })
    assert refresh_resp.status_code == 200
    refresh_run = refresh_resp.json()
    assert refresh_run["status"] == "completed", f"refresh cae summary failed: {refresh_run}"
    # Assert changed artifacts include the summary files
    artifact_paths = [
        a["path"]
        for tr in refresh_run["tool_results"]
        for a in (tr.get("artifacts") or [])
        if isinstance(a, dict) and "path" in a
    ]
    assert any("result_summary.json" in p for p in artifact_paths), artifact_paths
    assert any("evidence_index.json" in p for p in artifact_paths), artifact_paths
    assert any("postprocessing_summary.md" in p for p in artifact_paths), artifact_paths

    # 5. Read the refreshed summary and assert imported metrics are visible
    summary_resp = client.get(f"/api/projects/{project_id}/cae-result-summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["computed_values"]["extrema_computed"] is True
    assert summary["computed_values"]["max_von_mises_stress"]["value"] == 187.4
    assert summary["computed_values"]["max_displacement"]["value"] == 0.82
    assert summary["computed_values"]["minimum_safety_factor"]["value"] == 1.33


def test_write_artifact_to_package_adds_new_file(tmp_path: Path) -> None:
    """write_artifact_to_package inserts a new file into an .aieng package."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("other.txt", b"keep me")

    source = tmp_path / "computed_metrics.json"
    source.write_text('{"metrics": []}', encoding="utf-8")

    result = write_artifact_to_package(pkg, "results/computed_metrics.json", source, overwrite=True)
    assert result["path"] == "results/computed_metrics.json"

    with zipfile.ZipFile(pkg, "r") as zf:
        names = set(zf.namelist())
        assert "results/computed_metrics.json" in names
        assert "other.txt" in names
        assert "manifest.json" in names
        assert zf.read("other.txt") == b"keep me"
        assert json.loads(zf.read("results/computed_metrics.json")) == {"metrics": []}


def test_write_artifact_to_package_overwrites_existing(tmp_path: Path) -> None:
    """write_artifact_to_package replaces an existing entry without duplicates."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("results/computed_metrics.json", b"old")

    source = tmp_path / "computed_metrics.json"
    source.write_text('{"metrics": [1]}', encoding="utf-8")

    write_artifact_to_package(pkg, "results/computed_metrics.json", source, overwrite=True)

    with zipfile.ZipFile(pkg, "r") as zf:
        names = zf.namelist()
        assert names.count("results/computed_metrics.json") == 1
        assert zf.read("results/computed_metrics.json") == b'{"metrics": [1]}'


def test_write_artifact_to_package_refuses_overwrite_by_default(tmp_path: Path) -> None:
    """write_artifact_to_package raises FileExistsError when overwrite=False."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("results/computed_metrics.json", b"old")

    source = tmp_path / "computed_metrics.json"
    source.write_text('{"metrics": [1]}', encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_artifact_to_package(pkg, "results/computed_metrics.json", source, overwrite=False)


def test_write_artifact_to_package_missing_source_raises(tmp_path: Path) -> None:
    """write_artifact_to_package raises FileNotFoundError when source is missing."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))

    with pytest.raises(FileNotFoundError):
        write_artifact_to_package(pkg, "results/x.json", tmp_path / "missing.json")


def test_write_artifact_to_package_missing_manifest_raises(tmp_path: Path) -> None:
    """write_artifact_to_package raises ValueError when package lacks manifest.json."""
    from app.main import write_artifact_to_package
    import zipfile

    pkg = tmp_path / "test.aieng"
    with zipfile.ZipFile(pkg, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("other.txt", b"data")

    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="missing manifest"):
        write_artifact_to_package(pkg, "results/x.json", source)


def test_capabilities_endpoint_exposes_runtime_and_mcp_registry(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    mcp_src = settings.freecad_mcp_root / "src" / "freecad_mcp"
    mcp_src.mkdir(parents=True)
    (mcp_src / "__init__.py").write_text("", encoding="utf-8")
    (mcp_src / "tool_registry.py").write_text(
        """
from dataclasses import dataclass

@dataclass
class Entry:
    def model_dump(self, mode='json'):
        return {
            'tool_name': 'cad_test_mutation',
            'category': 'cad',
            'purpose': 'Test mutation tool.',
            'required_inputs': ['object_name'],
            'optional_inputs': ['package_path'],
            'side_effects': ['Writes /tmp/out.step'],
            'mutates_cad': True,
            'mutates_package': False,
            'may_update_claim_map': False,
            'runtime_requirements': ['freecad'],
            'dry_run_support': 'partial',
            'claim_policy': {'claims_advanced_default': False},
        }

class Registry:
    def list_all(self):
        return [Entry()]

def default_registry():
    return Registry()
""",
        encoding="utf-8",
    )

    client = TestClient(create_app(settings))
    response = client.get("/api/capabilities")

    assert response.status_code == 200
    capabilities = response.json()
    names = {item["name"] for item in capabilities}
    assert "aieng.inspect_package" in names
    assert "cad_test_mutation" in names
    assert "benchmark.ai_usefulness.run" in names


def test_capability_preview_requires_approval_for_mutating_tool(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    mcp_src = settings.freecad_mcp_root / "src" / "freecad_mcp"
    mcp_src.mkdir(parents=True)
    (mcp_src / "__init__.py").write_text("", encoding="utf-8")
    (mcp_src / "tool_registry.py").write_text(
        """
from dataclasses import dataclass

@dataclass
class Entry:
    def model_dump(self, mode='json'):
        return {
            'tool_name': 'cad_set_parameter',
            'category': 'cad',
            'purpose': 'Set parameter.',
            'required_inputs': ['object_name', 'parameter_name', 'value'],
            'optional_inputs': ['file_path'],
            'side_effects': ['Writes modified artifact'],
            'mutates_cad': True,
            'mutates_package': True,
            'may_update_claim_map': False,
            'runtime_requirements': ['freecad'],
            'dry_run_support': 'partial',
            'claim_policy': {'claims_advanced_default': False},
        }

class Registry:
    def list_all(self):
        return [Entry()]

def default_registry():
    return Registry()
""",
        encoding="utf-8",
    )

    client = TestClient(create_app(settings))
    response = client.post(
        "/api/capabilities/preview",
        json={"operation_name": "cad_set_parameter", "inputs": {"file_path": "out.step"}},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "approval_required"
    assert data["approval_required"] is True
    assert data["preview"]["would_write_artifacts"] == ["out.step"]
    assert "feature_graph_existence" in data["preview"]["guard_checks_required"]


def test_cae_preprocessing_and_simulation_summary_endpoints(monkeypatch, tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("cae-summaries"))
    pkg = project_dir(settings, project["id"]) / "packages" / "test.aieng"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test"}))
    project["aieng_file"] = "packages/test.aieng"
    save_project(settings, project)

    monkeypatch.setattr(
        "app.main._generate_cae_preprocessing_summary",
        lambda _settings, _pkg: {"summary_type": "cae_preprocessing", "status": {"ready_for_solver": False}},
    )
    monkeypatch.setattr(
        "app.main._generate_cae_simulation_run_summary",
        lambda _settings, _pkg: {"summary_type": "cae_simulation_run", "status": {"run_count": 0}},
    )

    client = TestClient(create_app(settings))
    prep = client.get(f"/api/projects/{project['id']}/cae-preprocessing-summary")
    sim = client.get(f"/api/projects/{project['id']}/cae-simulation-run-summary")

    assert prep.status_code == 200
    assert prep.json()["summary_type"] == "cae_preprocessing"
    assert sim.status_code == 200
    assert sim.json()["summary_type"] == "cae_simulation_run"


def test_runtime_workflow_endpoint_executes_explicit_steps(tmp_path: Path) -> None:
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.post(
        "/api/runtime/runs",
        json={
            "message": "workflow smoke",
            "workflow_id": "custom",
            "steps": [
                {"id": "llm-plan", "kind": "llm", "description": "Plan with LLM", "status": "pending"},
                {"id": "artifact-note", "kind": "artifact", "description": "Record artifact", "status": "pending"},
            ],
            "llm_config": {"provider": "openai-compatible", "model": "demo", "api_key": "must_not_persist"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert [step["kind"] for step in data["plan"]] == ["llm", "artifact"]
    assert "must_not_persist" not in json.dumps(data)


def test_benchmark_dry_run_endpoint_uses_provider_without_api_key(tmp_path: Path, monkeypatch) -> None:
    settings = _make_runtime_settings(tmp_path)
    # Build a tiny fake aieng.benchmarking module so this test is isolated from
    # optional benchmark dependencies and never calls an external LLM.
    bench_pkg = settings.aieng_root / "src" / "aieng" / "benchmarking"
    bench_pkg.mkdir(parents=True)
    (settings.aieng_root / "src" / "aieng" / "__init__.py").write_text("", encoding="utf-8")
    (bench_pkg / "__init__.py").write_text(
        """
from dataclasses import dataclass

@dataclass(frozen=True)
class BenchmarkPaths:
    benchmark_scenario: str
    question_file: object
    rubric_file: object
    condition_a_path: object
    condition_b_index_file: object
    condition_b_source: object
    results_dir: object
    schema_file: object

@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    input_price_per_million_tokens: float | None = None
    output_price_per_million_tokens: float | None = None
    max_output_tokens: int = 8192
    temperature: float = 0.0
    top_p: float = 1.0
    seed: int | None = None

@dataclass(frozen=True)
class BenchmarkRunConfig:
    condition: str
    provider: ProviderConfig
    dry_run: bool = False
    output_path: object | None = None

def run_benchmark(paths, config, provider, prepare_condition_b=None, progress=None):
    if progress:
        progress('fake dry run')
    return {
        'run_id': 'run_fake',
        'mode': 'dry_run' if config.dry_run else 'run',
        'benchmark_scenario': paths.benchmark_scenario,
        'provider': config.provider.provider,
        'model': config.provider.model,
        'cost_estimate': {'estimated_calls': 2},
        'warnings': [],
        'dry_run_notes': ['fake note'],
    }
""",
        encoding="utf-8",
    )
    scenario = settings.aieng_root / "benchmarks" / "ai_usefulness" / "scenarios" / "sample"
    scenario.mkdir(parents=True)
    for name in ("questions.md", "condition_a.md", "condition_b_index.md"):
        (scenario / name).write_text("1. Question?\n", encoding="utf-8")
    (settings.aieng_root / "benchmarks" / "ai_usefulness" / "scoring_rubric.md").parent.mkdir(parents=True, exist_ok=True)
    (settings.aieng_root / "benchmarks" / "ai_usefulness" / "scoring_rubric.md").write_text("rubric", encoding="utf-8")
    (settings.aieng_root / "benchmarks" / "ai_usefulness" / "results.schema.json").write_text("{}", encoding="utf-8")
    monkeypatch.delitem(__import__("sys").modules, "aieng", raising=False)
    monkeypatch.delitem(__import__("sys").modules, "aieng.benchmarking", raising=False)

    client = TestClient(create_app(settings))
    response = client.post(
        "/api/benchmarks/runs",
        json={
            "scenario_id": "sample",
            "dry_run": True,
            "llm_config": {"provider": "openai-compatible", "model": "fake-model", "api_key": "secret"},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["dry_run"] is True
    assert data["result"]["mode"] == "dry_run"
    assert "secret" not in json.dumps(data)

def test_get_cae_preprocessing_summary_endpoint(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cae-preprocessing-summary returns preprocessing summary."""
    from app.main import Settings, create_app, default_project, project_dir, save_project
    import zipfile

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng"),
        freecad_mcp_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng_freecad_mcp"),
        freecad_home=workspace / "freecad",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preproc-test"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preproc-test.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("simulation/cae_imports/parsed_materials.json", json.dumps({"materials": [{"name": "Steel"}]}).encode())
    project["aieng_file"] = "preproc-test.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-preprocessing-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == "0.1"
    assert data["summary_type"] == "cae_preprocessing"
    assert data["status"]["has_materials"] is True
    assert data["status"]["has_mesh"] is False


def test_get_cae_simulation_run_summary_endpoint(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cae-simulation-run-summary returns simulation run summary."""
    from app.main import Settings, create_app, default_project, project_dir, save_project
    import zipfile

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng"),
        freecad_mcp_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng_freecad_mcp"),
        freecad_home=workspace / "freecad",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("runs-test"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "runs-test.aieng"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    run = json.dumps({
        "run_id": "run_001",
        "solver": "CalculiX",
        "software": "FreeCAD FEM",
        "status": {"state": "completed", "solved": True, "converged": True, "warnings": [], "errors": []},
    })
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "test", "resources": {}}))
        zf.writestr("simulation/runs/run_001/solver_run.json", run.encode())
    project["aieng_file"] = "runs-test.aieng"
    save_project(settings, project)

    resp = client.get(f"/api/projects/{project_id}/cae-simulation-run-summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == "0.1"
    assert data["summary_type"] == "cae_simulation_run"
    assert data["status"]["run_count"] == 1
    assert data["status"]["latest_run_id"] == "run_001"


def test_get_cae_preprocessing_summary_missing_package_returns_404(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cae-preprocessing-summary returns 404 when package missing."""
    from app.main import Settings, create_app, default_project, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng"),
        freecad_mcp_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng_freecad_mcp"),
        freecad_home=workspace / "freecad",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("no-pkg"))
    resp = client.get(f"/api/projects/{project['id']}/cae-preprocessing-summary")
    assert resp.status_code == 404


def test_get_cae_simulation_run_summary_missing_package_returns_404(tmp_path: Path) -> None:
    """GET /api/projects/{id}/cae-simulation-run-summary returns 404 when package missing."""
    from app.main import Settings, create_app, default_project, save_project

    workspace = tmp_path / "workspace"
    settings = Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng"),
        freecad_mcp_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng_freecad_mcp"),
        freecad_home=workspace / "freecad",
        sample_step=workspace / "sample.step",
    )
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("no-pkg"))
    resp = client.get(f"/api/projects/{project['id']}/cae-simulation-run-summary")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 18 — cae.apply_setup_patch runtime tool
# ---------------------------------------------------------------------------

def _make_patch_settings(tmp_path: Path):
    from app.main import Settings
    workspace = tmp_path / "workspace"
    return Settings(
        platform_root=tmp_path / "platform",
        workspace_root=workspace,
        data_root=tmp_path / "data",
        aieng_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng"),
        freecad_mcp_root=Path(r"C:\Users\RL_Carla\Desktop\workspace_aieng\aieng_freecad_mcp"),
        freecad_home=workspace / "freecad",
        sample_step=workspace / "sample.step",
    )


def _make_setup_package(pkg_path: Path, extra: dict | None = None) -> None:
    """Create a minimal .aieng package suitable for setup-patch tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    solver_settings = {"solver": "CalculiX", "n_cpus": 4, "time_limit_s": 3600}
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "patch-test", "resources": {}}))
        zf.writestr("simulation/solver_settings.json", json.dumps(solver_settings))
        zf.writestr(
            "simulation/cae_imports/parsed_loads.json",
            json.dumps({"loads": [{"id": "load_001", "kind": "force", "magnitude": 1000.0}]}),
        )
        if extra:
            for name, content in extra.items():
                zf.writestr(name, json.dumps(content) if isinstance(content, dict) else content)


def test_cae_setup_patch_rejects_path_traversal(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects paths containing '..'."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-traversal"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{"path": "simulation/../secret.json", "action_type": "create_file", "content": {}}],
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "forbidden_path"


def test_cae_setup_patch_rejects_absolute_path(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects absolute paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-abspath"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{"path": "/etc/passwd", "action_type": "create_file", "content": "x"}],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "forbidden_path"


def test_cae_setup_patch_rejects_results_write(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects writes to results/ paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-results"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{"path": "results/result_summary.json", "action_type": "create_file", "content": {}}],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "forbidden_path"


def test_cae_setup_patch_rejects_unsupported_operation(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects unknown action_type values."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-badop"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "delete_file",
            }],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "unsupported_operation"


def test_cae_setup_patch_rejects_before_mismatch(tmp_path: Path) -> None:
    """cae.apply_setup_patch rejects replace_json when 'before' does not match current value."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-before"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "before": 99,
                "value": 8,
            }],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "patch_error"
    assert "before mismatch" in result["message"]


def test_cae_setup_patch_create_file_success(tmp_path: Path) -> None:
    """cae.apply_setup_patch creates a new load-case file inside the package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-create"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    new_load_case = {"id": "load_case_001", "name": "Static", "loads": []}
    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/load_cases/load_case_001.json",
                "action_type": "create_file",
                "content": new_load_case,
            }],
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "ok"
    changed = [a["path"] for a in result["changed_artifacts"]]
    assert "simulation/load_cases/load_case_001.json" in changed

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/load_cases/load_case_001.json" in zf.namelist()
        written = json.loads(zf.read("simulation/load_cases/load_case_001.json"))
        assert written["id"] == "load_case_001"


def test_cae_setup_patch_replace_json_mutates_value(tmp_path: Path) -> None:
    """cae.apply_setup_patch replace_json via pointer updates the target field."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-replace"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "before": 4,
                "value": 8,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    assert any(a["path"] == "simulation/solver_settings.json" for a in result["changed_artifacts"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        updated = json.loads(zf.read("simulation/solver_settings.json"))
    assert updated["n_cpus"] == 8
    assert updated["solver"] == "CalculiX"


def test_cae_setup_patch_preserves_unrelated_entries(tmp_path: Path) -> None:
    """cae.apply_setup_patch leaves unrelated package entries intact."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-preserve"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path, extra={"simulation/mesh/model.vtu": b"<vtu/>"})
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/load_cases/lc_new.json",
                "action_type": "create_file",
                "content": {"id": "lc_new"},
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
    assert "simulation/mesh/model.vtu" in names
    assert "simulation/solver_settings.json" in names
    assert "simulation/cae_imports/parsed_loads.json" in names
    assert "simulation/load_cases/lc_new.json" in names


def test_cae_setup_patch_no_duplicate_zip_entries(tmp_path: Path) -> None:
    """cae.apply_setup_patch does not create duplicate ZIP entries when replacing."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-nodup"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "value": 2,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    assert resp.json()["tool_results"][0]["output"]["status"] == "ok"

    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
    assert names.count("simulation/solver_settings.json") == 1
    assert names.count("manifest.json") == 1


def test_cae_setup_patch_returns_stale_artifacts_and_warnings(tmp_path: Path) -> None:
    """cae.apply_setup_patch returns stale_artifacts and a warning when preprocessing refresh fails."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-stale"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    # Use refresh_preprocessing_summary=True (default) — refresh will fail since
    # aieng package is not importable in test env, so all stale artifacts remain.
    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "value": 16,
            }],
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    stale = result["stale_artifacts"]
    assert isinstance(stale, list)
    # At minimum the result summary and evidence index are stale
    assert any("result_summary" in p for p in stale)
    assert any("evidence_index" in p for p in stale)


def test_cae_setup_patch_replace_json_returns_artifact_diffs(tmp_path: Path) -> None:
    """cae.apply_setup_patch replace_json returns artifact_diffs with path, operation, pointer, before, after, changed_paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-diff-replace"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/n_cpus",
                "before": 4,
                "value": 8,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    diffs = result.get("artifact_diffs", [])
    assert len(diffs) == 1
    d = diffs[0]
    assert d["path"] == "simulation/solver_settings.json"
    assert d["operation"] == "replace_json"
    assert d["json_pointer"] == "/n_cpus"
    assert d["before"] == 4
    assert d["after"] == 8
    assert "/n_cpus" in d["changed_paths"]
    assert d["added_paths"] == []
    assert d["removed_paths"] == []


def test_cae_setup_patch_create_file_returns_artifact_diffs(tmp_path: Path) -> None:
    """cae.apply_setup_patch create_file returns artifact_diffs with added_paths and null before."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-diff-create"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    new_lc = {"id": "load_case_001", "loads": []}
    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/load_cases/load_case_001.json",
                "action_type": "create_file",
                "content": new_lc,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    diffs = result.get("artifact_diffs", [])
    assert len(diffs) == 1
    d = diffs[0]
    assert d["path"] == "simulation/load_cases/load_case_001.json"
    assert d["operation"] == "create_file"
    assert d["before"] is None
    assert d["after"] == new_lc
    assert d["added_paths"] == [""]
    assert d["changed_paths"] == []
    assert d["removed_paths"] == []


def test_cae_setup_patch_merge_object_returns_artifact_diffs(tmp_path: Path) -> None:
    """cae.apply_setup_patch merge_object returns artifact_diffs with changed/added paths."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-diff-merge"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "merge_object",
                "value": {"new_key": "new_value"},
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    diffs = result.get("artifact_diffs", [])
    assert len(diffs) == 1
    d = diffs[0]
    assert d["path"] == "simulation/solver_settings.json"
    assert d["operation"] == "merge_object"
    assert "/new_key" in d["added_paths"]
    # stale_artifacts should still be present
    assert "stale_artifacts" in result
    assert isinstance(result["stale_artifacts"], list)


def test_cae_setup_patch_stale_artifacts_still_present(tmp_path: Path) -> None:
    """cae.apply_setup_patch still returns stale_artifacts after setup changes even with artifact_diffs."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("patch-stale-28"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "patch-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "patch-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "apply cae setup patch",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "patches": [{
                "path": "simulation/solver_settings.json",
                "action_type": "replace_json",
                "pointer": "/time_limit_s",
                "value": 7200,
            }],
            "refresh_preprocessing_summary": False,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "ok"
    assert "artifact_diffs" in result
    assert "stale_artifacts" in result
    stale = result["stale_artifacts"]
    assert isinstance(stale, list)
    assert len(stale) > 0
    assert any("result_summary" in p for p in stale)


# ---------------------------------------------------------------------------
# Phase 19 — cae.extract_solver_results runtime tool
# ---------------------------------------------------------------------------

def _frd_value(v: float) -> str:
    return f"{v:12.5E}"


def _frd_node_line(node_id: int, values: list) -> str:
    return "    -1" + f"{node_id:12d}" + "".join(_frd_value(v) for v in values)


def _make_test_frd(
    disp_nodes: dict | None,
    stress_nodes: dict | None,
) -> str:
    lines = ["    1C                                                                         1"]
    if disp_nodes is not None:
        lines += [
            "    -4  DISP        4    1",
            "    -5  D1          1    2    1    0",
            "    -5  D2          1    2    2    0",
            "    -5  D3          1    2    3    0",
            "    -5  ALL         1    2    0    1",
        ]
        for nid, vals in disp_nodes.items():
            lines.append(_frd_node_line(nid, vals))
        lines.append("    -3")
    if stress_nodes is not None:
        lines += [
            "    -4  S           6    1",
            "    -5  SXX         1    4    1    1",
            "    -5  SYY         1    4    2    1",
            "    -5  SZZ         1    4    3    1",
            "    -5  SXY         1    4    4    1",
            "    -5  SXZ         1    4    5    1",
            "    -5  SYZ         1    4    6    1",
        ]
        for nid, vals in stress_nodes.items():
            lines.append(_frd_node_line(nid, vals))
        lines.append("    -3")
    lines.append(" 9999")
    return "\n".join(lines) + "\n"


def test_cae_extract_solver_results_success(tmp_path: Path) -> None:
    """cae.extract_solver_results parses FRD and writes computed_metrics.json into package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-extract"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "extract-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "extract-test.aieng"
    save_project(settings, project)

    frd_path = tmp_path / "job.frd"
    frd_path.write_text(
        _make_test_frd(
            {1: [1.0, 0.0, 0.0, 1.0], 2: [5.0, 0.0, 0.0, 5.0]},
            {1: [200.0, 100.0, 50.0, 10.0, 0.0, 0.0]},
        ),
        encoding="utf-8",
    )

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(frd_path),
            "loadCaseId": "load_case_001",
            "refresh_result_summary": False,
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["status"] == "ok"
    assert any("computed_metrics" in a["path"] for a in result["artifacts"])

    # Verify actual values were extracted
    metrics = result["metrics"]
    lc = metrics["load_cases"][0]
    assert abs(lc["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4
    assert "max_von_mises_stress" in lc["metrics"]

    # Verify package was updated
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/computed_metrics.json" in zf.namelist()
        written = json.loads(zf.read("results/computed_metrics.json"))
    assert written["schema_version"] == "0.1"
    assert abs(written["load_cases"][0]["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4


def test_cae_extract_solver_results_missing_frd_returns_error(tmp_path: Path) -> None:
    """cae.extract_solver_results returns error when frdPath does not exist."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-missing"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "extract-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "extract-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(tmp_path / "nonexistent.frd"),
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "file_not_found"


def test_cae_extract_solver_results_missing_frd_path_returns_error(tmp_path: Path) -> None:
    """cae.extract_solver_results returns error when frdPath is not provided."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("frd-nopath"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "extract-test.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "extract-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["status"] == "error"
    assert result["code"] == "missing_frd_path"


# ---------------------------------------------------------------------------
# cae.write_mesh_handoff
# ---------------------------------------------------------------------------

def _make_package_with_topology(pkg_path: Path) -> None:
    """Create a minimal .aieng package with topology_map.json for handoff tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    topology = {
        "format_version": "0.1",
        "entities": [
            {"id": "body_001", "type": "solid"},
            {"id": "face_001", "type": "face"},
            {"id": "edge_001", "type": "edge"},
        ],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "handoff-test", "resources": {}}))
        zf.writestr("geometry/topology_map.json", json.dumps(topology))
        zf.writestr("simulation/setup.yaml", yaml.safe_dump({"mesh": {"element_size": 2.5}}))


def test_cae_write_mesh_handoff_success(tmp_path: Path) -> None:
    """cae.write_mesh_handoff writes mesh_handoff_contract.json into package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("handoff"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "handoff-test.aieng"
    _make_package_with_topology(pkg_path)
    project["aieng_file"] = "handoff-test.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "write mesh handoff",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "handoff_id": "handoff_001"},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["handoff_id"] == "handoff_001"
    assert any(a["path"] == "simulation/mesh_handoff_contract.json" for a in result["artifacts"])

    # Verify package was updated
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/mesh_handoff_contract.json" in zf.namelist()
        contract = json.loads(zf.read("simulation/mesh_handoff_contract.json"))
    assert contract["handoff_id"] == "handoff_001"
    assert contract["mesher_target"] == "gmsh"
    assert "topology_refs" in contract


def test_cae_write_mesh_handoff_missing_topology_returns_error(tmp_path: Path) -> None:
    """cae.write_mesh_handoff returns error when topology_map.json is missing."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("handoff-no-topo"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "handoff-no-topo.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "handoff-no-topo.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "write mesh handoff",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "topology_missing"


# ---------------------------------------------------------------------------
# cae.import_solver_evidence
# ---------------------------------------------------------------------------

def _make_package_with_evidence_scaffold(pkg_path: Path) -> None:
    """Create a minimal .aieng package with evidence scaffold for solver evidence tests."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_index = {
        "format_version": "0.1",
        "evidence_items": [],
    }
    claim_map = {
        "format_version": "0.1",
        "claims": [
            {"claim_id": "claim_solver_result_001", "claim_type": "solver/result_available", "verification_status": "unsupported"}
        ],
    }
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "ev-test", "resources": {}}))
        zf.writestr("simulation/solver_settings.json", json.dumps({"solver": "CalculiX", "n_cpus": 4}))
        zf.writestr("results/evidence_index.json", json.dumps(evidence_index))
        zf.writestr("results/claim_map.json", json.dumps(claim_map))


def test_cae_import_solver_evidence_success(tmp_path: Path) -> None:
    """cae.import_solver_evidence imports solver result as evidence into package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-ev"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver-ev.aieng"
    _make_package_with_evidence_scaffold(pkg_path)
    project["aieng_file"] = "solver-ev.aieng"
    save_project(settings, project)

    result_file = tmp_path / "job.dat"
    result_file.write_text(
        "max von Mises stress = 250.0 MPa\n"
        "maximum displacement = 1.23 mm\n",
        encoding="utf-8",
    )

    resp = client.post("/api/runtime/runs", json={
        "message": "import solver evidence",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "result_file": str(result_file),
            "result_format": "calculix_dat",
            "producer_tool": "calculix",
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert any(a["path"] == "results/evidence_index.json" for a in result["artifacts"])


def test_cae_import_solver_evidence_missing_result_file_returns_error(tmp_path: Path) -> None:
    """cae.import_solver_evidence returns error when result_file does not exist."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-ev-missing"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver-ev-missing.aieng"
    _make_package_with_evidence_scaffold(pkg_path)
    project["aieng_file"] = "solver-ev-missing.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "import solver evidence",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "result_file": str(tmp_path / "nonexistent.dat"),
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "result_file_not_found"


# ---------------------------------------------------------------------------
# aieng.write_evidence_scaffold
# ---------------------------------------------------------------------------

def test_aieng_validate_success(tmp_path: Path) -> None:
    """aieng.validate returns PASS/WARN/FAIL messages for a real package."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("validate-test"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "validate.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "validate.aieng"
    save_project(settings, project)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.validate", "description": "validate", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "validate package",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert "validation_ok" in result
    assert "messages" in result
    assert "counts" in result
    assert isinstance(result["messages"], list)
    assert any(m["level"] == "PASS" for m in result["messages"])


def test_aieng_validate_missing_package_returns_error(tmp_path: Path) -> None:
    """aieng.validate returns error when package is missing."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("validate-missing"))
    project_id = project["id"]

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.validate", "description": "validate", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "validate package",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_package_path"


def test_aieng_convert_step_success_via_mocked_bridge(monkeypatch, tmp_path: Path) -> None:
    """aieng.convert returns out_path and source_type on successful conversion."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("convert-test"))
    project_id = project["id"]
    step_path = project_dir(settings, project_id) / "source" / "test_part.step"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    step_path.write_text("dummy step content")
    project["source_step"] = "source/test_part.step"
    save_project(settings, project)

    def _mock_convert(*a, **kw):
        return {
            "status": "ok",
            "out_path": str(project_dir(settings, project_id) / "packages" / "test_part.aieng"),
            "converter_id": "step_importer",
            "source_type": "step",
        }

    monkeypatch.setattr("app.aieng_bridge.convert_source_to_package", _mock_convert)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.convert", "description": "convert", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "convert step to aieng",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["out_path"].endswith("test_part.aieng")
    assert result["source_type"] == "step"
    assert result["converter_id"] == "step_importer"


def test_aieng_convert_missing_source_returns_error(tmp_path: Path) -> None:
    """aieng.convert returns error when no source path can be resolved."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("convert-missing"))
    project_id = project["id"]

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.convert", "description": "convert", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "convert step to aieng",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_source_path"


def test_aieng_convert_bridge_exception_produces_tool_failed(monkeypatch, tmp_path: Path) -> None:
    """aieng.convert propagates bridge RuntimeError as tool_failed."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("convert-fail"))
    project_id = project["id"]
    step_path = project_dir(settings, project_id) / "source" / "fail.step"
    step_path.parent.mkdir(parents=True, exist_ok=True)
    step_path.write_text("dummy")
    project["source_step"] = "source/fail.step"
    save_project(settings, project)

    def _fail(*a, **kw):
        raise Exception("converter exploded")

    monkeypatch.setattr("app.aieng_bridge.convert_source_to_package", _fail)

    original_build = _rt.build_plan
    _rt.build_plan = lambda msg, pid: [
        {"name": "aieng.convert", "description": "convert", "input": {"project_id": pid}}
    ]
    try:
        resp = client.post("/api/runtime/runs", json={
            "message": "convert step to aieng",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    finally:
        _rt.build_plan = original_build

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    event_types = [e["type"] for e in data["events"]]
    assert "tool_failed" in event_types
    assert "run_failed" in event_types


def test_runtime_plan_selects_convert_intent(tmp_path: Path) -> None:
    from app.runtime import build_plan
    for msg in ["convert step to aieng", "convert fcstd file", "import step to aieng"]:
        plan = build_plan(msg, None)
        assert len(plan) == 1, f"Expected 1 step for {msg!r}, got {plan!r}"
        assert plan[0]["name"] == "aieng.convert"


def test_aieng_write_evidence_scaffold_success(tmp_path: Path) -> None:
    """aieng.write_evidence_scaffold creates evidence_index.json and claim_map.json."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("scaffold"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "scaffold.aieng"
    _make_setup_package(pkg_path)
    project["aieng_file"] = "scaffold.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "write evidence scaffold",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert any(a["path"] == "results/evidence_index.json" for a in result["artifacts"])
    assert any(a["path"] == "results/claim_map.json" for a in result["artifacts"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/evidence_index.json" in zf.namelist()
        assert "results/claim_map.json" in zf.namelist()


def test_aieng_write_evidence_scaffold_missing_package_returns_error(tmp_path: Path) -> None:
    """aieng.write_evidence_scaffold returns error when package is missing."""
    from app.main import create_app, default_project, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("scaffold-missing"))
    project_id = project["id"]

    resp = client.post("/api/runtime/runs", json={
        "message": "write evidence scaffold",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "missing_package_path"


# ---------------------------------------------------------------------------
# cae.import_solver_evidence auto-scaffold
# ---------------------------------------------------------------------------

def test_cae_import_solver_evidence_auto_scaffold_when_missing(tmp_path: Path) -> None:
    """cae.import_solver_evidence auto-creates scaffold when it is missing."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-ev-auto"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver-ev-auto.aieng"
    # Use _make_setup_package which does NOT include evidence scaffold
    _make_setup_package(pkg_path)
    project["aieng_file"] = "solver-ev-auto.aieng"
    save_project(settings, project)

    result_file = tmp_path / "job.dat"
    result_file.write_text(
        "max von Mises stress = 250.0 MPa\n"
        "maximum displacement = 1.23 mm\n",
        encoding="utf-8",
    )

    resp = client.post("/api/runtime/runs", json={
        "message": "import solver evidence",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "result_file": str(result_file),
            "result_format": "calculix_dat",
            "producer_tool": "calculix",
        },
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result.get("scaffold_created") is True
    assert any("auto-created" in w for w in result.get("warnings", []))
    assert any(a["path"] == "results/evidence_index.json" for a in result["artifacts"])


# ---------------------------------------------------------------------------
# cae.prepare_solver_run (Phase 20B)
# ---------------------------------------------------------------------------

def _make_preflight_package(pkg_path: Path, *, mesh: bool = True, solver_settings: bool = True,
                             load_case: bool = True, input_deck: bool = False,
                             load_case_id: str = "load_case_001", run_id: str = "run_001") -> None:
    """Create a .aieng package for preflight tests with selectable artifact presence."""
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "preflight-test", "resources": {}}))
        if mesh:
            zf.writestr("simulation/mesh/mesh_metadata.json", json.dumps({"elements": 4000, "nodes": 800}))
        if solver_settings:
            zf.writestr("simulation/solver_settings.json", json.dumps({"solver": "CalculiX", "n_cpus": 4}))
        if load_case:
            zf.writestr(
                f"simulation/load_cases/{load_case_id}.json",
                json.dumps({"id": load_case_id, "loads": []}),
            )
        if input_deck:
            zf.writestr(
                f"simulation/runs/{run_id}/solver_input.inp",
                "** CalculiX input deck placeholder\n",
            )


def test_prepare_solver_run_reports_missing_artifacts(tmp_path: Path) -> None:
    """cae.prepare_solver_run honestly reports missing mesh, settings, load case, and input deck."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-missing"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    # Package with nothing — no mesh, no solver settings, no load case, no input deck
    _make_preflight_package(pkg_path, mesh=False, solver_settings=False, load_case=False, input_deck=False)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    result = run["tool_results"][0]["output"]

    assert result["ok"] is True
    assert result["ready_to_run"] is False
    preflight = result["preflight"]
    assert preflight["has_mesh"] is False
    assert preflight["has_solver_settings"] is False
    assert preflight["has_load_case"] is False
    assert preflight["has_input_deck"] is False
    assert len(preflight["missing_items"]) >= 4


def test_prepare_solver_run_ready_to_run_false_when_ccx_unavailable(tmp_path: Path) -> None:
    """cae.prepare_solver_run returns ready_to_run=false when ccx is not on PATH."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-noccx"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    # Package with all artifacts present except ccx
    _make_preflight_package(pkg_path, mesh=True, solver_settings=True, load_case=True, input_deck=True)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    # Patch shutil.which to simulate ccx not found
    with patch("app.main.shutil.which", return_value=None):
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["ready_to_run"] is False
    assert result["preflight"]["ccx_available"] is False
    assert any("ccx" in item.lower() for item in result["preflight"]["missing_items"])


def test_prepare_solver_run_planned_artifacts_include_frd_and_summaries(tmp_path: Path) -> None:
    """planned_artifacts include FRD, computed_metrics, and result summaries when requested."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-artifacts"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "run_id": "run_001",
            "extract_results": True,
            "refresh_summary": True,
        },
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["ok"] is True

    paths = [a["path"] for a in result["planned_artifacts"]]
    assert any("result.frd" in p for p in paths)
    assert any("computed_metrics.json" in p for p in paths)
    assert any("result_summary.json" in p for p in paths)
    assert any("evidence_index.json" in p for p in paths)
    assert any("postprocessing_summary.md" in p for p in paths)


def test_prepare_solver_run_always_has_approval_and_no_execution(tmp_path: Path) -> None:
    """requires_approval is always true and solver_execution_performed is always false."""
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-contracts"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    resp = client.post("/api/runtime/runs", json={
        "message": "prepare solver run",
        "project_id": project_id,
        "tool_input": {"project_id": project_id},
    })
    assert resp.status_code == 200
    result = resp.json()["tool_results"][0]["output"]
    assert result["requires_approval"] is True
    assert result["solver_execution_performed"] is False
    assert any("No solver execution" in w for w in result["warnings"])


def test_prepare_solver_run_tool_registered_in_introspection(tmp_path: Path) -> None:
    """cae.prepare_solver_run appears in /api/runtime/tools introspection."""
    from app.main import create_app
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    tools = resp.json()
    names = [t["name"] for t in tools]
    assert "cae.prepare_solver_run" in names


def test_prepare_solver_run_no_solver_subprocess(tmp_path: Path) -> None:
    """cae.prepare_solver_run never invokes a subprocess (no solver execution)."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("preflight-nosub"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "preflight.aieng"
    _make_preflight_package(pkg_path)
    project["aieng_file"] = "preflight.aieng"
    save_project(settings, project)

    mock_run = MagicMock()
    with patch("subprocess.run", mock_run), patch("subprocess.Popen", MagicMock()):
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id},
        })

    assert resp.status_code == 200
    mock_run.assert_not_called()


def _execute_run_solver(client, project_id, tool_input):
    """Start a solver run via the runtime endpoint and auto-approve if gated."""
    resp = client.post("/api/runtime/runs", json={
        "message": "execute solver run",
        "project_id": project_id,
        "tool_input": tool_input,
    })
    assert resp.status_code == 200
    data = resp.json()
    if data["status"] == "awaiting_approval":
        run_id = data["run_id"]
        approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
        assert approve_resp.status_code == 200
        data = approve_resp.json()
    return data


def test_run_solver_rejects_path_traversal(tmp_path: Path) -> None:
    """cae.run_solver rejects input_deck_path containing '..'."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-traversal"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value="/fake/ccx"):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/../secret.inp",
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "forbidden_path"
    assert result["solver_execution_performed"] is False


def test_run_solver_rejects_non_inp(tmp_path: Path) -> None:
    """cae.run_solver rejects input_deck_path that does not end with .inp."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-noninp"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value="/fake/ccx"):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.txt",
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "invalid_input_deck"
    assert result["solver_execution_performed"] is False


def test_run_solver_ccx_unavailable_returns_error(tmp_path: Path) -> None:
    """cae.run_solver returns a clear error when ccx is not on PATH."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-noccx"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    with patch("app.main.shutil.which", return_value=None):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is False
    assert result["code"] == "solver_not_found"
    assert result["solver_execution_performed"] is False
    assert "ccx" in result["message"].lower()


def test_run_solver_mocked_subprocess_success(tmp_path: Path) -> None:
    """cae.run_solver invokes ccx with shell=False and writes solver_run.json + solver_log.txt."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-success"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="solver completed\n", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run) as mock_run:
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == 0
    assert result["status"] == "completed"

    # Verify subprocess args
    assert len(mock_run.call_args_list) == 1
    args, kwargs = mock_run.call_args_list[0]
    assert args[0] == ["/fake/ccx", "solver_input"]
    assert kwargs.get("shell") is False

    # Verify package artifacts
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "simulation/runs/run_001/solver_input.inp" in names
        assert "simulation/runs/run_001/solver_log.txt" in names
        assert "simulation/runs/run_001/solver_run.json" in names
        assert "simulation/runs/run_001/outputs/result.frd" in names

    # Verify solver_run.json content
    with zipfile.ZipFile(pkg_path, "r") as zf:
        run_meta = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
    assert run_meta["run_id"] == "run_001"
    assert run_meta["solver"] == "CalculiX"
    assert run_meta["state"] == "completed"
    assert run_meta["solved"] is True
    assert run_meta["converged"] is None
    assert run_meta["return_code"] == 0
    assert "started_at" in run_meta
    assert "finished_at" in run_meta
    assert "duration_seconds" in run_meta
    assert run_meta["input_files"] == ["simulation/runs/run_001/solver_input.inp"]
    assert "simulation/runs/run_001/outputs/result.frd" in run_meta["output_files"]


def test_run_solver_auto_imports_evidence_when_dat_present(tmp_path: Path) -> None:
    """cae.run_solver auto-imports solver evidence when .dat file is present after successful run."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-auto-import"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        dat_path = cwd / "solver_input.dat"
        dat_path.write_text(
            "max von Mises stress = 180.5 MPa\n"
            "maximum displacement = 0.42 mm\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="solver completed\n", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["return_code"] == 0
    assert result.get("auto_import") is not None
    assert result["auto_import"]["status"] == "ok"
    assert any(a["path"] == "results/evidence_index.json" for a in result["changed_artifacts"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/evidence_index.json" in zf.namelist()


def test_run_solver_skips_auto_import_when_disabled(tmp_path: Path) -> None:
    """cae.run_solver skips auto-import when auto_import_evidence is false."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-no-auto"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        dat_path = cwd / "solver_input.dat"
        dat_path.write_text("max von Mises stress = 180.5 MPa\n", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
            "auto_import_evidence": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert "auto_import" not in result


def test_run_solver_writes_frd_to_outputs(tmp_path: Path) -> None:
    """cae.run_solver writes result.frd into simulation/runs/<run_id>/outputs/."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-frd"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "simulation/runs/run_001/outputs/result.frd" in zf.namelist()


def test_run_solver_extracts_results_when_requested(tmp_path: Path) -> None:
    """cae.run_solver calls existing FRD extraction when extract_results=true."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-extract"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    extract_called: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_extract(package_path, frd_path, *, aieng_root, load_case_id, software, overwrite):
        extract_called["package_path"] = package_path
        extract_called["frd_path"] = frd_path
        extract_called["load_case_id"] = load_case_id
        extract_called["software"] = software
        return {
            "status": "ok",
            "metrics": {"load_cases": [{"id": load_case_id, "metrics": {}}]},
            "artifacts": [{"path": "results/computed_metrics.json", "kind": "computed_metrics", "role": "extracted_metrics"}],
        }

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("app.aieng_bridge.extract_frd_solver_results", fake_extract):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": True,
            "refresh_summary": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert extract_called.get("load_case_id") == "load_case_001"
    assert extract_called.get("software") == "CalculiX"
    assert "extracted_metrics" in result


def test_run_solver_refreshes_summaries_when_requested(tmp_path: Path) -> None:
    """cae.run_solver refreshes CAE summaries when refresh_summary=true."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-refresh"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    refreshed: list[str] = []

    def fake_run(cmd, **kwargs):
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_refresh_result(pkg, *, aieng_root, overwrite=True):
        refreshed.append("result_summary")

    def fake_refresh_preproc(pkg, *, aieng_root, overwrite=True):
        refreshed.append("preprocessing_summary")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("app.aieng_bridge.refresh_cae_result_summary", fake_refresh_result), \
         patch("app.aieng_bridge.refresh_preprocessing_summary", fake_refresh_preproc):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": True,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert "result_summary" in refreshed
    assert "preprocessing_summary" in refreshed
    assert result.get("refreshed_summaries") == ["result_summary", "preprocessing_summary"]


def test_run_solver_timeout_records_failed_metadata(tmp_path: Path) -> None:
    """cae.run_solver handles timeout by recording failed run metadata."""
    from unittest.mock import patch
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient
    import subprocess

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-timeout"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "timeout_seconds": 1,
            "extract_results": False,
            "refresh_summary": False,
        })

    assert data["status"] == "completed"
    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["status"] == "failed"
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == -1
    assert any("timed out" in w.lower() for w in result["errors"])

    with zipfile.ZipFile(pkg_path, "r") as zf:
        run_meta = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
    assert run_meta["state"] == "failed"
    assert run_meta["solved"] is False
    assert run_meta["return_code"] == -1
    assert any("timed out" in w.lower() for w in run_meta["errors"])


def test_run_solver_registered_in_introspection(tmp_path: Path) -> None:
    """cae.run_solver appears in /api/runtime/tools with requires_approval=true."""
    from app.main import create_app
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    tools = resp.json()
    names = [t["name"] for t in tools]
    assert "cae.run_solver" in names
    solver_tool = next(t for t in tools if t["name"] == "cae.run_solver")
    assert solver_tool["requires_approval"] is True


def test_run_solver_no_mesh_generation(tmp_path: Path) -> None:
    """cae.run_solver does not attempt mesh generation or input deck generation."""
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("solver-nomesh"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"
    _make_preflight_package(pkg_path, input_deck=True)
    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        cwd = Path(kwargs.get("cwd", "."))
        frd_path = cwd / "solver_input.frd"
        frd_path.write_text(_make_test_frd({1: [1.0, 0.0, 0.0, 1.0]}, None), encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.main.shutil.which", return_value="/fake/ccx"), \
         patch("subprocess.run", side_effect=fake_run):
        data = _execute_run_solver(client, project_id, {
            "project_id": project_id,
            "input_deck_path": "simulation/runs/run_001/solver_input.inp",
            "extract_results": False,
            "refresh_summary": False,
        })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    # Only one subprocess invocation: ccx
    assert len(calls) == 1
    assert calls[0] == ["/fake/ccx", "solver_input"]


@pytest.mark.skipif(
    shutil.which("ccx") is None,
    reason="CalculiX executable (ccx) not found on PATH — skipping real solver smoke test.",
)
def test_run_solver_real_ccx_skipped_if_unavailable(tmp_path: Path) -> None:
    """Real CalculiX smoke test: runs ccx against minimal cantilever fixture if available.

    This test verifies that the external solver adapter (cae.run_solver) can:
      - locate a real ccx executable on PATH
      - execute it in a temp working directory
      - capture stdout/stderr and return code
      - write solver_run.json, solver_log.txt, and result.frd back into the .aieng package

    If ccx is not installed, the test is skipped cleanly so CI/environments without
    CalculiX do not fail.
    """
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    project = save_project(settings, default_project("real-ccx-smoke"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "solver.aieng"

    # Load the real fixture input deck
    fixture_path = Path(__file__).with_name("fixtures") / "minimal_cantilever.inp"
    inp_content = fixture_path.read_text(encoding="utf-8")

    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "real-ccx-smoke", "resources": {}}))
        zf.writestr("simulation/runs/run_001/solver_input.inp", inp_content)

    project["aieng_file"] = "solver.aieng"
    save_project(settings, project)

    data = _execute_run_solver(client, project_id, {
        "project_id": project_id,
        "input_deck_path": "simulation/runs/run_001/solver_input.inp",
        "extract_results": True,
        "refresh_summary": True,
    })

    result = data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == 0
    assert result["status"] == "completed"

    # Verify artifacts were written back into the package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = set(zf.namelist())
        assert "simulation/runs/run_001/solver_run.json" in names
        assert "simulation/runs/run_001/solver_log.txt" in names
        assert "simulation/runs/run_001/solver_input.inp" in names
        assert "simulation/runs/run_001/outputs/result.frd" in names

        # Verify solver_run.json content
        run_json = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
        assert run_json["solver"] == "CalculiX"
        assert run_json["solved"] is True
        assert run_json["converged"] is None  # honest boundary: no convergence claim
        assert run_json["return_code"] == 0
        assert "simulation/runs/run_001/outputs/result.frd" in run_json["output_files"]

    # Verify extracted metrics were produced
    assert "extracted_metrics" in result


def test_vertical_cae_workflow_end_to_end(tmp_path: Path) -> None:
    """Full CAE vertical workflow: preflight -> solver run -> FRD extraction -> summary refresh.

    This is the Phase 22 benchmark / agent-run vertical demo. It demonstrates that
    the runtime can execute the full CAE lifecycle -- preflight, external solver
    execution (mocked), FRD scalar extraction, and summary refresh -- entirely
    through the runtime REST API, producing honest evidence-backed results.
    """
    from unittest.mock import patch, MagicMock
    from app.main import create_app, default_project, project_dir, save_project
    from starlette.testclient import TestClient

    def _output_for_tool(run_data: dict[str, Any], tool_name: str) -> dict[str, Any]:
        for tc, tr in zip(run_data["tool_calls"], run_data["tool_results"]):
            if tc["name"] == tool_name:
                return tr["output"]
        raise AssertionError(f"Tool {tool_name} not found in run tool_calls")

    settings = _make_patch_settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    # fixture: generic .aieng package with all CAE setup artifacts
    project = save_project(settings, default_project("cae-benchmark"))
    project_id = project["id"]
    pkg_path = project_dir(settings, project_id) / "benchmark.aieng"
    _make_preflight_package(pkg_path, mesh=True, solver_settings=True, load_case=True, input_deck=True)
    project["aieng_file"] = "benchmark.aieng"
    save_project(settings, project)

    # Mock ccx availability for both preflight and solver run
    with patch("app.main.shutil.which", return_value="/fake/ccx"):
        # Step 1: prepare solver run (reads evidence, no execution)
        resp = client.post("/api/runtime/runs", json={
            "message": "prepare solver run",
            "project_id": project_id,
            "tool_input": {"project_id": project_id, "run_id": "run_001"},
        })
        assert resp.status_code == 200
        preflight = resp.json()["tool_results"][0]["output"]
        assert preflight["ok"] is True
        assert preflight["solver_execution_performed"] is False
        assert preflight["ready_to_run"] is True

        # Step 2: run solver (mocked ccx producing a parseable FRD)
        def fake_run(cmd, **kwargs):
            cwd = Path(kwargs.get("cwd", "."))
            frd_path = cwd / "solver_input.frd"
            frd_path.write_text(
                _make_test_frd(
                    {1: [1.0, 0.0, 0.0, 1.0], 2: [5.0, 0.0, 0.0, 5.0]},
                    {1: [200.0, 100.0, 50.0, 10.0, 0.0, 0.0]},
                ),
                encoding="utf-8",
            )
            return MagicMock(returncode=0, stdout="solver completed\n", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            resp = client.post("/api/runtime/runs", json={
                "message": "execute solver run",
                "project_id": project_id,
                "tool_input": {
                    "project_id": project_id,
                    "run_id": "run_001",
                    "input_deck_path": "simulation/runs/run_001/solver_input.inp",
                    "extract_results": False,
                    "refresh_summary": False,
                },
            })
            assert resp.status_code == 200
            run_data = resp.json()
            # Approval gate: cae.run_solver requires explicit approval
            assert run_data["status"] == "awaiting_approval"
            run_id = run_data["run_id"]
            approve_resp = client.post(f"/api/runtime/runs/{run_id}/approve")
            assert approve_resp.status_code == 200
            run_data = approve_resp.json()

    result = run_data["tool_results"][0]["output"]
    assert result["ok"] is True
    assert result["solver_execution_performed"] is True
    assert result["return_code"] == 0
    assert result["status"] == "completed"
    assert any("solver_run.json" in a["path"] for a in result["changed_artifacts"])
    assert any("solver_log.txt" in a["path"] for a in result["changed_artifacts"])
    assert any("result.frd" in a["path"] for a in result["changed_artifacts"])

    # Verify solver artifacts persisted in the package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        names = zf.namelist()
        assert "simulation/runs/run_001/solver_run.json" in names
        assert "simulation/runs/run_001/solver_log.txt" in names
        assert "simulation/runs/run_001/outputs/result.frd" in names
        solver_run = json.loads(zf.read("simulation/runs/run_001/solver_run.json"))
    assert solver_run["solved"] is True
    assert solver_run["converged"] is None  # conservative: no reliable convergence evidence

    # Step 3: extract FRD scalar results
    frd_path = tmp_path / "solver_input.frd"
    with zipfile.ZipFile(pkg_path, "r") as zf:
        frd_content = zf.read("simulation/runs/run_001/outputs/result.frd")
    frd_path.write_bytes(frd_content)

    resp = client.post("/api/runtime/runs", json={
        "message": "extract solver results",
        "project_id": project_id,
        "tool_input": {
            "project_id": project_id,
            "frdPath": str(frd_path),
            "loadCaseId": "load_case_001",
            "refresh_result_summary": False,
        },
    })
    assert resp.status_code == 200
    extract_result = resp.json()["tool_results"][0]["output"]
    assert extract_result["status"] == "ok"
    assert any(a["path"] == "results/computed_metrics.json" for a in extract_result["artifacts"])

    # Verify computed_metrics.json inside the package
    with zipfile.ZipFile(pkg_path, "r") as zf:
        assert "results/computed_metrics.json" in zf.namelist()
        metrics = json.loads(zf.read("results/computed_metrics.json"))
    assert metrics["schema_version"] == "0.1"
    lc = metrics["load_cases"][0]
    assert lc["id"] == "load_case_001"
    assert abs(lc["metrics"]["max_displacement"]["value"] - 5.0) < 1e-4
    assert "max_von_mises_stress" in lc["metrics"]

    # Step 4: refresh CAE result summary
    resp = client.post("/api/runtime/runs", json={
        "message": "refresh cae summary",
        "project_id": project_id,
        "tool_input": {"project_id": project_id, "overwrite": True},
    })
    assert resp.status_code == 200
    refresh_run = resp.json()
    refresh_result = _output_for_tool(refresh_run, "postprocess.refresh_cae_summary")
    assert refresh_result["status"] == "ok"

    # Verify the summary endpoint now reports real extrema
    resp = client.get(f"/api/projects/{project_id}/cae-result-summary")
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["computed_values"]["extrema_computed"] is True
    assert summary["computed_values"]["max_displacement"] is not None
    assert summary["computed_values"]["max_von_mises_stress"] is not None
    assert len(summary["llm_summary"]["limitations"]) > 0

    # Benchmark checklist
    assert preflight["solver_execution_performed"] is False  # reads evidence before acting
    assert result["solver_execution_performed"] is True      # uses prepare/run/extract flow
    assert run_data["status"] == "completed"                 # approval semantics respected
    assert solver_run["converged"] is None                   # does not claim convergence
    assert extract_result["status"] == "ok"                  # distinguishes extraction from execution
    assert "limitations" in summary["llm_summary"]           # reports limitations honestly


# ---------------------------------------------------------------------------
# Bridge schema_version validation hook
# ---------------------------------------------------------------------------

def test_bridge_check_schema_version_matching_returns_no_warnings() -> None:
    """A matching on-disk schema_version yields an empty warnings list."""
    from app.aieng_bridge import _check_schema_version

    warnings = _check_schema_version("0.3", "0.3", "cae_result_summary")
    assert warnings == []


def test_bridge_check_schema_version_mismatch_returns_regenerate_warning() -> None:
    """A drifted on-disk schema_version produces an actionable warning."""
    from app.aieng_bridge import _check_schema_version

    warnings = _check_schema_version("0.1", "0.3", "cae_result_summary")
    assert len(warnings) == 1
    assert "regenerate" in warnings[0].lower()
    assert "'0.1'" in warnings[0]
    assert "'0.3'" in warnings[0]
    assert "cae_result_summary" in warnings[0]


def test_bridge_check_schema_version_missing_returns_regenerate_warning() -> None:
    """A missing on-disk schema_version produces an actionable warning."""
    from app.aieng_bridge import _check_schema_version

    warnings = _check_schema_version(None, "0.3", "cae_result_summary")
    assert len(warnings) == 1
    assert "regenerate" in warnings[0].lower()
    assert "missing" in warnings[0].lower()


# ---------------------------------------------------------------------------
# Runtime CAE tool contract
# ---------------------------------------------------------------------------
# Critical runtime tools that agent-facing surfaces (MCP wrappers, capability
# registry, agent vertical workflow) depend on. Adding a tool here means it
# must survive any future runtime-registry refactor.
#
# Subset membership only — we do not assert an exact total, since non-critical
# tools (aieng.*, mcp.*) are free to change without breaking this contract.

CRITICAL_RUNTIME_TOOLS: tuple[str, ...] = (
    "freecad.inspect_geometry",
    "freecad.export_step",
    "postprocess.generate_computed_metrics",
    "postprocess.refresh_cae_summary",
    "cae.apply_setup_patch",
    "cae.extract_solver_results",
    "cae.prepare_solver_run",
    "cae.run_solver",
)


def test_runtime_introspection_includes_critical_cae_tools(tmp_path: Path) -> None:
    """Every critical CAE/postprocess runtime tool must appear in
    /api/runtime/tools introspection with a non-empty description."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    tools_by_name = {t["name"]: t for t in resp.json()}

    missing = [name for name in CRITICAL_RUNTIME_TOOLS if name not in tools_by_name]
    assert not missing, (
        f"Critical runtime tools missing from introspection: {missing}. "
        f"Either register them in app.main.create_app or remove them from "
        f"CRITICAL_RUNTIME_TOOLS if intentionally deprecated."
    )

    for name in CRITICAL_RUNTIME_TOOLS:
        entry = tools_by_name[name]
        assert isinstance(entry["description"], str) and entry["description"], (
            f"{name} is registered but has no description"
        )
        assert "requires_approval" in entry


def test_run_solver_introspection_requires_approval(tmp_path: Path) -> None:
    """cae.run_solver is potentially destructive; the approval gate must
    survive any future refactor of the registry."""
    settings = _make_runtime_settings(tmp_path)
    client = TestClient(create_app(settings))

    resp = client.get("/api/runtime/tools")
    assert resp.status_code == 200
    run_solver = next(t for t in resp.json() if t["name"] == "cae.run_solver")
    assert run_solver["requires_approval"] is True


def test_capability_registry_includes_critical_runtime_tools(tmp_path: Path) -> None:
    """The capability registry (agent_workbench.list_capabilities) re-exports
    every runtime tool. Critical CAE tools must therefore be visible to the
    agent capability surface, not only to the raw /api/runtime/tools endpoint."""
    from app.agent_workbench import list_capabilities

    settings = _make_runtime_settings(tmp_path)
    # Trigger app construction so register_tool calls execute.
    create_app(settings)

    caps_by_name = {c["name"]: c for c in list_capabilities(settings)}
    missing = [name for name in CRITICAL_RUNTIME_TOOLS if name not in caps_by_name]
    assert not missing, (
        f"Critical runtime tools missing from capability registry: {missing}"
    )


# ---------------------------------------------------------------------------
# Phase 26 — artifact review endpoint
# ---------------------------------------------------------------------------

def _setup_project_with_package(
    tmp_path: Path,
    package_members: dict[str, bytes],
) -> tuple[TestClient, str]:
    """Build a project with a .aieng package containing the supplied members."""
    from app.main import default_project, project_dir

    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("artifact-review"))
    pkg_dir = project_dir(settings, project["id"]) / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = pkg_dir / "review.aieng"
    with zipfile.ZipFile(pkg_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"model_id": "review"}))
        for name, data in package_members.items():
            zf.writestr(name, data)
    project["aieng_file"] = "packages/review.aieng"
    save_project(settings, project)
    return TestClient(create_app(settings)), project["id"]


def test_artifact_read_returns_parsed_json(tmp_path: Path) -> None:
    payload = {"schema_version": "0.3", "load_cases": [{"id": "lc1"}]}
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/computed_metrics.json": json.dumps(payload).encode()},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == "results/computed_metrics.json"
    assert body["exists"] is True
    assert body["media_type"] == "application/json"
    assert body["size_bytes"] > 0
    assert body["parsed_json"] == payload
    assert "text" in body  # JSON is also returned as text
    assert body["warnings"] == []


def test_artifact_read_returns_text_for_markdown(tmp_path: Path) -> None:
    markdown = "# Result Summary\n\n- max stress: 187.4 MPa\n"
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/postprocessing_summary.md": markdown.encode("utf-8")},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/postprocessing_summary.md"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["media_type"] == "text/markdown"
    assert body["text"] == markdown
    assert "parsed_json" not in body


def test_artifact_read_returns_exists_false_for_missing(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["path"] == "results/computed_metrics.json"
    assert "size_bytes" not in body
    assert "parsed_json" not in body
    assert "text" not in body


def test_artifact_read_rejects_parent_traversal(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "../../../etc/passwd"},
    )
    assert resp.status_code == 400
    assert "invalid artifact path" in resp.json()["detail"]


def test_artifact_read_rejects_absolute_path(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "/etc/passwd"},
    )
    assert resp.status_code == 400


def test_artifact_read_rejects_backslash(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results\\computed_metrics.json"},
    )
    assert resp.status_code == 400


def test_artifact_read_rejects_empty_path(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.get(f"/api/projects/{pid}/artifact", params={"path": ""})
    assert resp.status_code == 400


def test_artifact_read_large_text_returns_size_only(tmp_path: Path) -> None:
    # 300 KB markdown exceeds the 256 KB inline cap.
    big_md = ("line " + "x" * 100 + "\n") * 3000
    assert len(big_md.encode("utf-8")) > 256 * 1024
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/postprocessing_summary.md": big_md.encode("utf-8")},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/postprocessing_summary.md"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["size_bytes"] > 256 * 1024
    assert "text" not in body
    assert any("exceeds inline text cap" in w for w in body["warnings"])


def test_artifact_read_binary_suppresses_text(tmp_path: Path) -> None:
    # Synthetic binary blob with embedded NUL bytes inside the first 4 KB.
    binary = b"FRD\x00\x00binary content\x00\x01\x02more"
    client, pid = _setup_project_with_package(
        tmp_path,
        {"simulation/runs/run_001/outputs/result.frd": binary},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "simulation/runs/run_001/outputs/result.frd"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["media_type"] == "application/octet-stream"
    assert "text" not in body
    assert "parsed_json" not in body
    assert any("binary content detected" in w for w in body["warnings"])


def test_artifact_read_invalid_json_returns_warning(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(
        tmp_path,
        {"results/computed_metrics.json": b"{not valid json}"},
    )

    resp = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert "parsed_json" not in body
    assert any("json parse failed" in w for w in body["warnings"])
    assert body["text"] == "{not valid json}"  # text still returned


def test_artifact_read_404_when_package_missing(tmp_path: Path) -> None:
    from app.main import default_project

    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("no-package"))
    client = TestClient(create_app(settings))

    resp = client.get(
        f"/api/projects/{project['id']}/artifact",
        params={"path": "results/computed_metrics.json"},
    )
    assert resp.status_code == 404


def test_artifact_diff_reports_changed_added_removed_paths(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    before = {
        "schema_version": "0.1",
        "load_cases": [{"id": "lc1", "metrics": {"max_stress": 100.0}}],
        "removed_block": {"obsolete": True},
    }
    after = {
        "schema_version": "0.3",
        "load_cases": [
            {"id": "lc1", "metrics": {"max_stress": 187.4}},
            {"id": "lc2"},
        ],
        "added_block": {"new": True},
    }

    resp = client.post(
        f"/api/projects/{pid}/artifact/diff",
        json={"before": before, "after": after},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "/schema_version" in body["changed_paths"]
    assert "/load_cases/0/metrics/max_stress" in body["changed_paths"]
    assert "/load_cases/1" in body["added_paths"]
    assert "/added_block" in body["added_paths"]
    assert "/removed_block" in body["removed_paths"]


def test_artifact_diff_identical_documents_empty(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    doc = {"a": 1, "b": [1, 2, 3], "c": {"d": "x"}}
    resp = client.post(
        f"/api/projects/{pid}/artifact/diff",
        json={"before": doc, "after": doc},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed_paths"] == []
    assert body["added_paths"] == []
    assert body["removed_paths"] == []


def test_artifact_diff_rejects_missing_before_after(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(
        f"/api/projects/{pid}/artifact/diff",
        json={"before": {"a": 1}},
    )
    assert resp.status_code == 400
    assert "before" in resp.json()["detail"] and "after" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Phase 29 — solver input deck import endpoint
# ---------------------------------------------------------------------------

_FIXTURE_INP_PATH = Path(__file__).resolve().parent / "fixtures" / "minimal_cantilever.inp"


def _read_fixture_inp() -> str:
    return _FIXTURE_INP_PATH.read_text(encoding="utf-8")


def test_solver_input_happy_path_writes_into_package(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["run_id"] == "run_001"
    assert body["artifact"]["path"] == "simulation/runs/run_001/solver_input.inp"
    assert body["artifact"]["kind"] == "solver_input"
    assert body["artifact"]["role"] == "solver_input_deck"
    assert body["artifact"]["size_bytes"] == len(deck.encode("utf-8"))
    assert body["keyword_count"] > 0
    # The fixture deck has *HEADING / *NODE / *ELEMENT / *MATERIAL / *STEP etc.
    assert "HEADING" in body["keywords"]
    assert "NODE" in body["keywords"]
    assert "STEP" in body["keywords"]
    # No missing-block warnings for a complete deck.
    assert all("*NODE" not in w and "*STEP" not in w for w in body["warnings"])

    # The package now contains the deck on disk at the canonical path.
    from app.main import get_project, resolve_project_path

    settings = _make_runtime_settings(tmp_path)
    project = get_project(settings, pid)
    package_path = resolve_project_path(settings, pid, project["aieng_file"])
    with zipfile.ZipFile(package_path, "r") as zf:
        assert "simulation/runs/run_001/solver_input.inp" in zf.namelist()
        assert zf.read("simulation/runs/run_001/solver_input.inp").decode("utf-8") == deck


def test_solver_input_custom_run_id(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "run_id": "experiment_42"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "experiment_42"
    assert body["artifact"]["path"] == "simulation/runs/experiment_42/solver_input.inp"


def test_solver_input_overwrites_existing_by_default(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    deck_a = "*HEADING\nfirst\n*NODE\n1, 0, 0, 0\n*STEP\n*STATIC\n*END STEP\n"
    deck_b = "*HEADING\nsecond\n*NODE\n2, 1, 1, 1\n*STEP\n*STATIC\n*END STEP\n"

    resp1 = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck_a})
    assert resp1.status_code == 200

    resp2 = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck_b})
    assert resp2.status_code == 200

    from app.main import get_project, resolve_project_path

    settings = _make_runtime_settings(tmp_path)
    project = get_project(settings, pid)
    package_path = resolve_project_path(settings, pid, project["aieng_file"])
    with zipfile.ZipFile(package_path, "r") as zf:
        contents = zf.read("simulation/runs/run_001/solver_input.inp").decode("utf-8")
    assert contents == deck_b


def test_solver_input_overwrite_false_conflicts(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = "*HEADING\nfirst\n*NODE\n1, 0, 0, 0\n*STEP\n*STATIC\n*END STEP\n"

    resp1 = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck})
    assert resp1.status_code == 200

    resp2 = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "overwrite": False},
    )
    assert resp2.status_code == 409


def test_solver_input_rejects_empty_text(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": ""})
    assert resp.status_code == 400
    assert "text" in resp.json()["detail"]


def test_solver_input_rejects_missing_text(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(f"/api/projects/{pid}/solver-input", json={})
    assert resp.status_code == 400


def test_solver_input_rejects_non_string_text(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": 42})
    assert resp.status_code == 400


def test_solver_input_rejects_text_without_keywords(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": "this is not a CalculiX deck\njust prose\n"},
    )
    assert resp.status_code == 400
    assert "CalculiX" in resp.json()["detail"]


def test_solver_input_rejects_run_id_traversal(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "run_id": "../etc"},
    )
    assert resp.status_code == 400


def test_solver_input_rejects_run_id_with_slash(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    resp = client.post(
        f"/api/projects/{pid}/solver-input",
        json={"text": deck, "run_id": "run/001"},
    )
    assert resp.status_code == 400


def test_solver_input_rejects_oversized_deck(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    # Build a >10 MB string with a valid header so size triggers before parse.
    header = "*HEADING\noversized\n*NODE\n"
    bulk = ("1, 0.0, 0.0, 0.0\n") * 700_000
    deck = header + bulk
    assert len(deck.encode("utf-8")) > 10 * 1024 * 1024

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck})
    assert resp.status_code == 413


def test_solver_input_warns_on_incomplete_deck(tmp_path: Path) -> None:
    client, pid = _setup_project_with_package(tmp_path, {})
    # A deck with a keyword but missing *NODE and *STEP — accepted with warnings.
    minimal = "*HEADING\nincomplete\n"

    resp = client.post(f"/api/projects/{pid}/solver-input", json={"text": minimal})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert any("*NODE" in w for w in body["warnings"])
    assert any("*STEP" in w for w in body["warnings"])


def test_solver_input_404_when_package_missing(tmp_path: Path) -> None:
    from app.main import default_project

    settings = _make_runtime_settings(tmp_path)
    project = save_project(settings, default_project("no-package-import"))
    client = TestClient(create_app(settings))

    resp = client.post(
        f"/api/projects/{project['id']}/solver-input",
        json={"text": _read_fixture_inp()},
    )
    assert resp.status_code == 404


def test_solver_input_imported_deck_is_visible_via_artifact_api(tmp_path: Path) -> None:
    """The artifact-read endpoint should surface the just-imported deck so a
    reviewer can confirm what landed inside the package before running it."""
    client, pid = _setup_project_with_package(tmp_path, {})
    deck = _read_fixture_inp()

    post = client.post(f"/api/projects/{pid}/solver-input", json={"text": deck})
    assert post.status_code == 200

    get = client.get(
        f"/api/projects/{pid}/artifact",
        params={"path": "simulation/runs/run_001/solver_input.inp"},
    )
    assert get.status_code == 200
    body = get.json()
    assert body["exists"] is True
    assert body["media_type"] == "text/plain"
    assert body["text"] == deck
