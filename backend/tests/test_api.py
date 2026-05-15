import json
import zipfile
from pathlib import Path

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
