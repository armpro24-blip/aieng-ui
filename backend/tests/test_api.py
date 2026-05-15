import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import (
    Settings,
    app,
    create_app,
    default_project,
    get_project,
    import_aieng_file,
    project_dir,
    save_project,
)
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
