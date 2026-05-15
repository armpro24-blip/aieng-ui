from pathlib import Path

from fastapi.testclient import TestClient

from app.main import (
    Settings,
    app,
    default_project,
    get_project,
    import_aieng_file,
    project_dir,
    save_project,
)


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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

    def fake_run_bridge(active_settings: Settings, command: str, payload: dict[str, str], *, timeout: int = 180):
        assert active_settings == settings
        calls.append(command)
        if command == "import_step":
            out_path = Path(payload["out_path"])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"fake-aieng")
            return {"status": "ok", "package_size": out_path.stat().st_size}
        if command == "enrich_package":
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
        if command == "validate_package":
            return {"ok": True, "counts": {"PASS": 3}, "messages": []}
        raise AssertionError(f"unexpected bridge command: {command}")

    monkeypatch.setattr("app.main.run_bridge", fake_run_bridge)

    result = import_aieng_file(settings, project["id"])

    assert calls == ["import_step", "enrich_package", "validate_package"]
    assert result["topology_backend"] == "mock"
    assert result["validation"]["ok"] is True
    assert "geometry/topology_map.json" in result["generated_resources"]

    updated = get_project(settings, project["id"])
    assert updated["aieng_file"] == "packages/demo.aieng"
    assert updated["last_validation_ok"] is True
    assert updated["status"] == "validated"
