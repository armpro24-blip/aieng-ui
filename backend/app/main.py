from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .providers import get_provider

APP_ROOT = Path(__file__).resolve().parents[1]
PLATFORM_ROOT = APP_ROOT.parent
WORKSPACE_ROOT = PLATFORM_ROOT.parent
AIENG_EXT = ".aieng"
STEP_EXTENSIONS = {".step", ".stp"}
PROJECT_ID = re.compile(r"[a-f0-9]{12}")
SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")
TOOLS_ALLOWED = [
    "cad_import_step",
    "cad_export_step",
    "cad_export_stl",
    "aieng_parse_patch",
    "aieng_execute_patch",
]
RUNTIME_CONFIG_FILENAME = "runtime_config.json"
SUPPORTED_CAD_PROVIDERS = {"freecad"}
SUPPORTED_TOPOLOGY_BACKENDS = {"auto", "mock", "occ"}


@dataclass(slots=True)
class Settings:
    platform_root: Path
    workspace_root: Path
    data_root: Path
    aieng_root: Path
    freecad_mcp_root: Path
    freecad_home: Path
    sample_step: Path

    @property
    def projects_root(self) -> Path:
        return self.data_root / "projects"

    @property
    def runtime_config_path(self) -> Path:
        return self.data_root / RUNTIME_CONFIG_FILENAME

    @property
    def freecad_cmd(self) -> Path:
        return self.freecad_home / "bin" / "FreeCADCmd.exe"

    @property
    def freecad_python(self) -> Path:
        return self.freecad_home / "bin" / "python.exe"

    @classmethod
    def from_env(cls) -> Settings:
        platform_root = PLATFORM_ROOT
        workspace_root = WORKSPACE_ROOT
        return cls(
            platform_root=platform_root,
            workspace_root=workspace_root,
            data_root=Path(os.environ.get("AIENG_PLATFORM_DATA", platform_root / "data")).resolve(),
            aieng_root=Path(os.environ.get("AIENG_ROOT", workspace_root / "aieng")).resolve(),
            freecad_mcp_root=Path(os.environ.get("FREECAD_MCP_ROOT", workspace_root / "aieng-freecad-mcp")).resolve(),
            freecad_home=Path(
                os.environ.get(
                    "FREECAD_MCP_FREECAD_PATH",
                    workspace_root / "FreeCAD_1.1.1-Windows-x86_64-py311",
                )
            ).resolve(),
            sample_step=Path(
                os.environ.get("AIENG_SAMPLE_STEP", workspace_root / "SFA-5.41" / "nist_ctc_05.stp")
            ).resolve(),
        )


PROJECT_TEMPLATE = {
    "id": "",
    "name": "",
    "status": "empty",
    "created_at": "",
    "updated_at": "",
    "source_step": None,
    "aieng_file": None,
    "web_asset": None,
    "web_asset_format": None,
    "preview_info": None,
    "last_validation_ok": None,
    "last_error": None,
    "last_chat_audit": None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_runtime_config(settings: Settings) -> dict[str, str]:
    return {
        "provider": "freecad",
        "aieng_root": str(settings.aieng_root),
        "freecad_mcp_root": str(settings.freecad_mcp_root),
        "freecad_home": str(settings.freecad_home),
        "topology_backend": "auto",
    }


def normalize_runtime_config(settings: Settings, payload: dict[str, Any] | None) -> dict[str, str]:
    defaults = default_runtime_config(settings)
    merged = {**defaults, **(payload or {})}

    provider = str(merged.get("provider") or defaults["provider"]).strip().lower()
    if provider not in SUPPORTED_CAD_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_CAD_PROVIDERS))
        raise HTTPException(status_code=400, detail=f"unsupported CAD provider: {provider}; supported: {supported}")

    topology_backend = str(merged.get("topology_backend") or defaults["topology_backend"]).strip().lower()
    if topology_backend not in SUPPORTED_TOPOLOGY_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_TOPOLOGY_BACKENDS))
        raise HTTPException(
            status_code=400,
            detail=f"unsupported topology backend: {topology_backend}; supported: {supported}",
        )

    normalized: dict[str, str] = {
        "provider": provider,
        "topology_backend": topology_backend,
    }
    for key in ("aieng_root", "freecad_mcp_root", "freecad_home"):
        raw_value = str(merged.get(key) or "").strip()
        if not raw_value:
            raise HTTPException(status_code=400, detail=f"{key} must be a non-empty string")
        normalized[key] = str(Path(raw_value).resolve())
    return normalized


def read_persisted_runtime_config(settings: Settings) -> dict[str, Any]:
    try:
        stored = read_json(settings.runtime_config_path, {})
    except (OSError, json.JSONDecodeError):
        return {}
    return stored if isinstance(stored, dict) else {}


def resolve_runtime_config(settings: Settings, overrides: dict[str, Any] | None = None) -> dict[str, str]:
    persisted = read_persisted_runtime_config(settings)
    return normalize_runtime_config(settings, {**persisted, **(overrides or {})})


def persist_runtime_config(settings: Settings, payload: dict[str, Any] | None) -> dict[str, Any]:
    ensure_dirs(settings)
    config = resolve_runtime_config(settings, payload)
    write_json(settings.runtime_config_path, config)
    return runtime_config_snapshot(settings)


def settings_with_runtime_config(settings: Settings, config: dict[str, str]) -> Settings:
    return Settings(
        platform_root=settings.platform_root,
        workspace_root=settings.workspace_root,
        data_root=settings.data_root,
        aieng_root=Path(config["aieng_root"]).resolve(),
        freecad_mcp_root=Path(config["freecad_mcp_root"]).resolve(),
        freecad_home=Path(config["freecad_home"]).resolve(),
        sample_step=settings.sample_step,
    )


def resolve_effective_settings(settings: Settings, overrides: dict[str, Any] | None = None) -> Settings:
    return settings_with_runtime_config(settings, resolve_runtime_config(settings, overrides))


def resolve_provider_bundle(
    settings: Settings,
    overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, str], Settings, Any]:
    config = resolve_runtime_config(settings, overrides)
    effective_settings = settings_with_runtime_config(settings, config)
    provider = get_provider(effective_settings, config)
    return config, effective_settings, provider


def runtime_probe(settings: Settings, config: dict[str, str]) -> dict[str, Any]:
    _, _, provider = resolve_provider_bundle(settings, config)
    return provider.probe_capabilities(whitelisted_tools=TOOLS_ALLOWED)


def runtime_config_snapshot(settings: Settings, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    config = resolve_runtime_config(settings, overrides)
    return {
        "config": config,
        "defaults": default_runtime_config(settings),
        "probe": runtime_probe(settings, config),
        "config_path": str(settings.runtime_config_path),
        "persisted_exists": settings.runtime_config_path.exists(),
    }


def package_member_count(value: Any, preferred_keys: tuple[str, ...] = ()) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return len(candidate)
            if isinstance(candidate, dict):
                return len(candidate)
        numeric_count = value.get("count")
        if isinstance(numeric_count, int):
            return numeric_count
        return len(value)
    return None


def read_package_json(archive: zipfile.ZipFile, member_name: str) -> Any:
    try:
        return json.loads(archive.read(member_name).decode("utf-8"))
    except KeyError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def read_package_text(archive: zipfile.ZipFile, member_name: str) -> str | None:
    try:
        return archive.read(member_name).decode("utf-8", errors="replace")
    except KeyError:
        return None


def package_summary_fallback(package_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(package_path) as archive:
        members = sorted(archive.namelist())
        manifest = read_package_json(archive, "manifest.json")
        feature_graph = read_package_json(archive, "graph/feature_graph.json")
        topology = read_package_json(archive, "geometry/topology_map.json")
        interfaces = read_package_json(archive, "objects/interface_graph.json")
        task_spec = read_package_json(archive, "task_spec.json")
        external_tool_requirements = read_package_json(archive, "external_tool_requirements.json")
        claim_map = read_package_json(archive, "ai/claim_map.json")
        evidence_index = read_package_json(archive, "results/evidence_index.json")
        tool_trace = read_package_json(archive, "provenance/tool_trace.json")
        completeness_report = read_package_json(archive, "validation/completeness_report.json")
        evidence_report = read_package_json(archive, "validation/evidence_report.json")
        ai_summary = read_package_text(archive, "ai/summary.md")

    derived: dict[str, Any] = {}
    feature_count = package_member_count(feature_graph, ("features", "nodes", "items", "elements"))
    topology_count = package_member_count(topology, ("bodies", "solids", "faces", "edges", "vertices"))
    interface_count = package_member_count(interfaces, ("interfaces", "edges", "links"))
    if feature_count is not None:
        derived["feature_graph"] = {"count": feature_count}
    if topology_count is not None:
        derived["topology"] = {"count": topology_count}
    if interface_count is not None:
        derived["interfaces"] = {"count": interface_count}

    warnings = [
        member_name
        for member_name in (
            "geometry/topology_map.json",
            "graph/feature_graph.json",
            "objects/interface_graph.json",
            "validation/completeness_report.json",
            "validation/evidence_report.json",
        )
        if member_name not in members
    ]

    return {
        "members": members,
        "member_count": len(members),
        "manifest": manifest,
        "feature_graph": feature_graph,
        "topology": topology,
        "interfaces": interfaces,
        "task_spec": task_spec,
        "external_tool_requirements": external_tool_requirements,
        "claim_map": claim_map,
        "evidence_index": evidence_index,
        "tool_trace": tool_trace,
        "completeness_report": completeness_report,
        "evidence_report": evidence_report,
        "ai_summary": ai_summary,
        "derived": derived,
        "warnings": warnings,
    }


def ensure_dirs(settings: Settings) -> None:
    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.projects_root.mkdir(parents=True, exist_ok=True)


def project_dir(settings: Settings, project_id: str) -> Path:
    if not PROJECT_ID.fullmatch(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    return settings.projects_root / project_id


def metadata_path(settings: Settings, project_id: str) -> Path:
    return project_dir(settings, project_id) / "metadata.json"


def default_project(name: str) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        **PROJECT_TEMPLATE,
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def normalize_project(project: dict[str, Any]) -> dict[str, Any]:
    normalized = {**PROJECT_TEMPLATE, **(project or {})}
    normalized["name"] = str(normalized.get("name") or "Untitled project")
    return normalized


def project_relpath(settings: Settings, project_id: str, path: Path) -> str:
    return str(path.relative_to(project_dir(settings, project_id))).replace("\\", "/")


def save_project(settings: Settings, project: dict[str, Any]) -> dict[str, Any]:
    project = normalize_project(project)
    project["updated_at"] = now_iso()
    base = project_dir(settings, project["id"])
    for folder in ("source", "packages", "viewer", "logs"):
        (base / folder).mkdir(parents=True, exist_ok=True)
    write_json(metadata_path(settings, project["id"]), project)
    return project


def get_project(settings: Settings, project_id: str) -> dict[str, Any]:
    path = metadata_path(settings, project_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="project not found")
    return normalize_project(read_json(path, {}))


def resolve_project_path(settings: Settings, project_id: str, relpath: str | None) -> Path | None:
    if not relpath:
        return None
    resolved = (project_dir(settings, project_id) / relpath).resolve()
    try:
        resolved.relative_to(project_dir(settings, project_id).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid project path") from exc
    return resolved


def convert_stl_to_glb(stl_path: Path, glb_path: Path) -> dict[str, Any]:
    try:
        import trimesh
    except Exception as exc:
        return {"ok": False, "error": f"trimesh unavailable: {type(exc).__name__}: {exc}"}

    try:
        loaded = trimesh.load_mesh(stl_path, force="mesh")
        if isinstance(loaded, trimesh.Scene):
            scene = loaded
        else:
            scene = trimesh.Scene(loaded)
        glb_bytes = scene.export(file_type="glb")
        if isinstance(glb_bytes, str):
            glb_bytes = glb_bytes.encode("utf-8")
        glb_path.write_bytes(glb_bytes)
        bounds = scene.bounds.tolist() if getattr(scene, "bounds", None) is not None else None
        return {"ok": True, "glb_path": str(glb_path), "glb_size": glb_path.stat().st_size, "bounds": bounds}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def write_audit_log(settings: Settings, project_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    audit_id = uuid.uuid4().hex
    path = project_dir(settings, project_id) / "logs" / f"{kind}_{audit_id}.json"
    write_json(path, payload)
    return {
        "audit_id": audit_id,
        "audit_path": project_relpath(settings, project_id, path),
        "audit_url": f"/assets/projects/{project_id}/{project_relpath(settings, project_id, path)}",
    }


def extract_step_from_package(settings: Settings, project_id: str, package_path: Path) -> Path:
    for member in ("geometry/source.step", "geometry/normalized.step"):
        with zipfile.ZipFile(package_path) as archive:
            if member not in archive.namelist():
                continue
            suffix = Path(member).suffix or ".step"
            target = project_dir(settings, project_id) / "source" / f"{package_path.stem}_extracted{suffix}"
            target.write_bytes(archive.read(member))
            return target
    raise HTTPException(status_code=400, detail="package does not contain source STEP geometry")


def ensure_step_source(settings: Settings, project_id: str, project: dict[str, Any]) -> Path:
    source = resolve_project_path(settings, project_id, project.get("source_step"))
    if source and source.exists():
        return source
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if package_path and package_path.exists():
        extracted = extract_step_from_package(settings, project_id, package_path)
        project["source_step"] = project_relpath(settings, project_id, extracted)
        save_project(settings, project)
        return extracted
    raise HTTPException(status_code=400, detail="STEP source not found")


def runtime_status(settings: Settings) -> dict[str, Any]:
    return runtime_config_snapshot(settings)


def compact_chat_output(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    if tool == "project.summary":
        return {
            "status": "ok",
            "member_count": result.get("package", {}).get("member_count"),
            "feature_count": result.get("derived", {}).get("feature_graph", {}).get("count"),
            "topology_count": result.get("derived", {}).get("topology", {}).get("count"),
            "validation_ok": result.get("validation", {}).get("report_ok"),
            "viewer_url": result.get("viewer_url"),
        }
    if tool == "aieng.import":
        return {
            "status": result.get("status"),
            "aieng_file": result.get("aieng_file"),
            "topology_backend": result.get("topology_backend"),
            "generated_resources": result.get("generated_resources", []),
            "validation_ok": result.get("validation", {}).get("ok"),
        }
    if tool == "viewer.convert":
        return {
            "status": result.get("status"),
            "asset_format": result.get("asset_format"),
            "viewer_url": result.get("viewer_url"),
        }
    if tool == "aieng.validate":
        return {
            "ok": result.get("ok"),
            "counts": result.get("counts"),
        }
    if tool == "mcp.check":
        guard = result.get("guard", {})
        return {
            "allowed": guard.get("allowed"),
            "mode": guard.get("mode"),
            "warnings": guard.get("warnings"),
            "reasons": guard.get("reasons"),
        }
    if tool == "mcp.parse_patch":
        return {
            "supported_operation_count": result.get("supported_operation_count"),
            "unsupported_operation_count": result.get("unsupported_operation_count"),
            "warnings": result.get("plan", {}).get("warnings", []),
        }
    if tool == "mcp.prepare_execution":
        return {
            "status": result.get("status"),
            "preflight_status": result.get("preflight", {}).get("status"),
            "step_count": len(result.get("preflight", {}).get("steps", [])),
            "warnings": result.get("preflight", {}).get("warnings", []),
            "errors": result.get("preflight", {}).get("errors", []),
        }
    return result


def find_patch_json(explicit_patch: Any, message: str) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(explicit_patch, dict):
        return explicit_patch, None
    if isinstance(explicit_patch, str) and explicit_patch.strip():
        try:
            return json.loads(explicit_patch), None
        except json.JSONDecodeError as exc:
            return None, f"patch_json is not valid JSON: {exc}"
    block_match = re.search(r"```json\\s*(\\{.*?\\})\\s*```", message, flags=re.DOTALL)
    if block_match:
        try:
            return json.loads(block_match.group(1)), None
        except json.JSONDecodeError as exc:
            return None, f"embedded patch JSON is invalid: {exc}"
    return None, None


def build_chat_plan(project: dict[str, Any], message: str, patch_json: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = message.lower()
    wants_summary = (
        not message.strip()
        or any(token in text for token in ["summary", "manifest", "feature", "topology", "semantic", "package"])
    )
    wants_import = "aieng" in text or ("import" in text and "step" in text) or ("package" in text and not project.get("aieng_file"))
    wants_convert = any(token in text for token in ["preview", "viewer", "glb", "stl", "convert", "render", "view"])
    wants_validate = any(token in text for token in ["validate", "validation"])
    wants_whitelist = any(token in text for token in ["whitelist", "guard", "safe", "allowed", "policy", "check"])
    wants_patch = patch_json is not None or "patch" in text
    wants_prepare = wants_patch and any(token in text for token in ["prepare", "execute", "apply", "audit", "run", "preflight"])

    steps: list[dict[str, Any]] = []
    if wants_summary:
        steps.append(
            {
                "id": "summary",
                "title": "Refresh project summary",
                "tool": "project.summary",
                "status": "planned",
                "safe": True,
                "reason": "Load the latest package, topology, validation, and viewer state.",
            }
        )
    if wants_import and not project.get("aieng_file"):
        steps.append(
            {
                "id": "import",
                "title": "Import STEP into .aieng",
                "tool": "aieng.import",
                "status": "planned",
                "safe": True,
                "reason": "Create a semantic engineering package from the current STEP source.",
            }
        )
    if wants_convert:
        steps.append(
            {
                "id": "convert",
                "title": "Build web preview asset",
                "tool": "viewer.convert",
                "status": "planned",
                "safe": True,
                "reason": "Generate an inspectable web asset, preferring GLB with fallback when needed.",
            }
        )
    if wants_validate:
        steps.append(
            {
                "id": "validate",
                "title": "Validate current package",
                "tool": "aieng.validate",
                "status": "planned",
                "safe": True,
                "reason": "Run .aieng package validation and collect pass/warn/fail messages.",
            }
        )
    if wants_whitelist or wants_prepare:
        operation = "cad_set_parameter" if wants_patch else "cad_export_step"
        target_feature_id = None
        if patch_json:
            operations = patch_json.get("operations", [])
            if isinstance(operations, list) and operations:
                first = operations[0]
                if isinstance(first, dict):
                    target_feature_id = first.get("target_feature_id") or first.get("feature_id")
        steps.append(
            {
                "id": "mcp-check",
                "title": "Check MCP guardrails",
                "tool": "mcp.check",
                "status": "planned",
                "safe": True,
                "inputs": {
                    "operation": operation,
                    "target_feature_id": target_feature_id,
                    "is_modification": wants_patch,
                },
                "reason": "Inspect whitelist, package context, and protected-region guard behavior.",
            }
        )
    if wants_patch:
        steps.append(
            {
                "id": "parse-patch",
                "title": "Parse patch proposal",
                "tool": "mcp.parse_patch",
                "status": "planned",
                "safe": True,
                "reason": "Validate supported and unsupported patch operations without executing them.",
            }
        )
    if wants_prepare:
        steps.append(
            {
                "id": "prepare-execution",
                "title": "Prepare patch execution",
                "tool": "mcp.prepare_execution",
                "status": "planned",
                "safe": True,
                "reason": "Run a dry-run preflight for auditable execution readiness.",
            }
        )
    if not steps:
        steps.append(
            {
                "id": "summary",
                "title": "Refresh project summary",
                "tool": "project.summary",
                "status": "planned",
                "safe": True,
                "reason": "No explicit action detected, so load the current project state.",
            }
        )
    return steps, {
        "wants_summary": wants_summary,
        "wants_import": wants_import,
        "wants_convert": wants_convert,
        "wants_validate": wants_validate,
        "wants_whitelist": wants_whitelist,
        "wants_patch": wants_patch,
        "wants_prepare": wants_prepare,
    }


def import_aieng_file(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    source = ensure_step_source(settings, project_id, project)
    out_path = project_dir(settings, project_id) / "packages" / f"{source.stem}{AIENG_EXT}"
    runtime_config, _, provider = resolve_provider_bundle(settings)
    import_result = provider.import_step_to_package(step_path=source, out_path=out_path)
    enrich_result = provider.enrich_package(package_path=out_path, topology_backend=runtime_config["topology_backend"])
    validation_result = provider.validate_package(package_path=out_path)
    project["aieng_file"] = project_relpath(settings, project_id, out_path)
    project["last_validation_ok"] = validation_result.get("ok")
    project["status"] = "validated" if validation_result.get("ok") else "validation_failed"
    project["last_error"] = None if validation_result.get("ok") else "package validation reported failures"
    save_project(settings, project)
    return {
        "status": import_result["status"],
        "aieng_file": project["aieng_file"],
        "package_size": enrich_result.get("package_size", import_result.get("package_size")),
        "topology_backend": enrich_result.get("topology_backend"),
        "generated_resources": enrich_result.get("generated_resources", []),
        "validation": validation_result,
    }


def validate_aieng_file(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    if package_path is None or not package_path.exists():
        raise HTTPException(status_code=400, detail=".aieng package not found")
    _, _, provider = resolve_provider_bundle(settings)
    result = provider.validate_package(package_path=package_path)
    project["last_validation_ok"] = result.get("ok")
    project["status"] = "validated" if result.get("ok") else "validation_failed"
    project["last_error"] = None if result.get("ok") else "package validation reported failures"
    save_project(settings, project)
    return result


def convert_asset(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    source = ensure_step_source(settings, project_id, project)
    viewer_root = project_dir(settings, project_id) / "viewer"
    stl_path = viewer_root / "model.stl"
    glb_path = viewer_root / "model.glb"
    metadata_path = viewer_root / "preview.json"

    _, _, provider = resolve_provider_bundle(settings)
    freecad_result = provider.export_step_preview_to_stl(step_path=source, stl_path=stl_path)
    glb_attempt = convert_stl_to_glb(stl_path, glb_path)

    if glb_attempt.get("ok"):
        asset_path = glb_path
        asset_format = "glb"
    else:
        asset_path = stl_path
        asset_format = "stl"

    preview_info = {
        "source_step": project_relpath(settings, project_id, source),
        "selected_asset": project_relpath(settings, project_id, asset_path),
        "selected_format": asset_format,
        "freecad_preview": freecad_result,
        "glb_attempt": glb_attempt,
    }
    write_json(metadata_path, preview_info)

    project["web_asset"] = project_relpath(settings, project_id, asset_path)
    project["web_asset_format"] = asset_format
    project["preview_info"] = preview_info
    project["status"] = f"viewer_ready_{asset_format}"
    project["last_error"] = None if asset_format == "glb" else glb_attempt.get("error")
    save_project(settings, project)
    return {
        "status": "ok",
        "asset_path": project["web_asset"],
        "asset_format": asset_format,
        "viewer_url": f"/assets/projects/{project_id}/{project['web_asset']}",
        "preview_info": preview_info,
    }


def recent_logs(settings: Settings, project_id: str, limit: int = 8) -> list[dict[str, Any]]:
    logs_root = project_dir(settings, project_id) / "logs"
    items: list[dict[str, Any]] = []
    for path in sorted(logs_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        items.append(
            {
                "name": path.name,
                "path": project_relpath(settings, project_id, path),
                "url": f"/assets/projects/{project_id}/{project_relpath(settings, project_id, path)}",
                "size": path.stat().st_size,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return items


def package_summary(settings: Settings, project_id: str) -> dict[str, Any]:
    project = get_project(settings, project_id)
    source_path = resolve_project_path(settings, project_id, project.get("source_step"))
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    viewer_path = resolve_project_path(settings, project_id, project.get("web_asset"))
    viewer_metadata_path = project_dir(settings, project_id) / "viewer" / "preview.json"

    summary: dict[str, Any] = {
        "project": project,
        "files": {
            "source_step": {
                "path": project.get("source_step"),
                "exists": bool(source_path and source_path.exists()),
                "size": source_path.stat().st_size if source_path and source_path.exists() else None,
            },
            "aieng_file": {
                "path": project.get("aieng_file"),
                "exists": bool(package_path and package_path.exists()),
                "size": package_path.stat().st_size if package_path and package_path.exists() else None,
            },
            "web_asset": {
                "path": project.get("web_asset"),
                "exists": bool(viewer_path and viewer_path.exists()),
                "size": viewer_path.stat().st_size if viewer_path and viewer_path.exists() else None,
            },
        },
        "viewer": {
            "asset_format": project.get("web_asset_format"),
            "asset_path": project.get("web_asset"),
            "asset_exists": bool(viewer_path and viewer_path.exists()),
            "metadata": read_json(viewer_metadata_path, None),
        },
        "viewer_url": f"/assets/projects/{project_id}/{project['web_asset']}" if project.get("web_asset") else None,
        "package": {
            "path": project.get("aieng_file"),
            "member_count": 0,
        },
        "members": [],
        "manifest": None,
        "feature_graph": None,
        "topology": None,
        "interfaces": None,
        "task_spec": None,
        "external_tool_requirements": None,
        "claim_map": None,
        "evidence_index": None,
        "tool_trace": None,
        "completeness_report": None,
        "evidence_report": None,
        "validation": {
            "report_ok": None,
            "messages": [],
            "counts": {},
            "status": None,
        },
        "ai_summary": None,
        "derived": {},
        "summary_error": None,
        "summary_mode": "none",
        "integration": runtime_status(settings),
        "recent_logs": recent_logs(settings, project_id),
    }
    if package_path and package_path.exists():
        try:
            _, _, provider = resolve_provider_bundle(settings)
            result = provider.package_summary_snapshot(package_path=package_path)
            summary["members"] = result.get("members", [])
            summary["package"]["member_count"] = result.get("member_count", 0)
            summary["manifest"] = result.get("manifest")
            summary["feature_graph"] = result.get("feature_graph")
            summary["topology"] = result.get("topology")
            summary["interfaces"] = result.get("interfaces")
            summary["task_spec"] = result.get("task_spec")
            summary["external_tool_requirements"] = result.get("external_tool_requirements")
            summary["claim_map"] = result.get("claim_map")
            summary["evidence_index"] = result.get("evidence_index")
            summary["tool_trace"] = result.get("tool_trace")
            summary["completeness_report"] = result.get("completeness_report")
            summary["evidence_report"] = result.get("evidence_report")
            summary["validation"] = {
                "report_ok": result.get("validation_report", {}).get("ok"),
                "messages": result.get("validation_report", {}).get("messages", []),
                "counts": result.get("validation_report", {}).get("counts", {}),
                "status": result.get("validation_status"),
            }
            summary["ai_summary"] = result.get("ai_summary")
            summary["derived"] = result.get("derived", {})
            summary["summary_mode"] = "bridge"
        except Exception as exc:
            summary["summary_error"] = f"{type(exc).__name__}: {exc}"
            try:
                fallback = package_summary_fallback(package_path)
            except Exception as fallback_exc:
                summary["validation"] = {
                    "report_ok": project.get("last_validation_ok"),
                    "messages": [
                        {
                            "level": "WARN",
                            "text": "package_summary failed and fallback package inspection was unavailable",
                        },
                        {"level": "WARN", "text": f"{type(fallback_exc).__name__}: {fallback_exc}"},
                    ],
                    "counts": {"WARN": 2},
                    "status": "degraded",
                }
                summary["summary_error"] = (
                    f"{summary['summary_error']} | fallback failed: {type(fallback_exc).__name__}: {fallback_exc}"
                )
                summary["summary_mode"] = "error_fallback"
            else:
                summary["members"] = fallback["members"]
                summary["package"]["member_count"] = fallback["member_count"]
                summary["manifest"] = fallback["manifest"]
                summary["feature_graph"] = fallback["feature_graph"]
                summary["topology"] = fallback["topology"]
                summary["interfaces"] = fallback["interfaces"]
                summary["task_spec"] = fallback["task_spec"]
                summary["external_tool_requirements"] = fallback["external_tool_requirements"]
                summary["claim_map"] = fallback["claim_map"]
                summary["evidence_index"] = fallback["evidence_index"]
                summary["tool_trace"] = fallback["tool_trace"]
                summary["completeness_report"] = fallback["completeness_report"]
                summary["evidence_report"] = fallback["evidence_report"]
                summary["ai_summary"] = fallback["ai_summary"]
                summary["derived"] = fallback["derived"]
                summary["validation"] = {
                    "report_ok": project.get("last_validation_ok"),
                    "messages": [
                        {
                            "level": "WARN",
                            "text": "package_summary degraded to zip fallback because optional package resources are missing",
                        },
                        *[
                            {"level": "WARN", "text": f"{member_name} missing"}
                            for member_name in fallback["warnings"]
                        ],
                    ],
                    "counts": {"WARN": len(fallback["warnings"]) + 1},
                    "status": "degraded",
                }
                summary["summary_mode"] = "zip_fallback"
    return summary


def mcp_check(settings: Settings, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    _, _, provider = resolve_provider_bundle(settings)
    result = provider.check_mcp_operation(
        package_path=str(package_path) if package_path and package_path.exists() else None,
        payload=payload,
        whitelisted_tools=TOOLS_ALLOWED,
    )
    result["project_id"] = project_id
    result["package_path"] = project.get("aieng_file")
    return result


def parse_patch(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    _, _, provider = resolve_provider_bundle(settings)
    return provider.parse_patch_proposal(patch_json=payload.get("patch_json") or {})


def prepare_patch_execution(settings: Settings, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = get_project(settings, project_id)
    package_path = resolve_project_path(settings, project_id, project.get("aieng_file"))
    _, _, provider = resolve_provider_bundle(settings)
    result = provider.prepare_patch_preflight(
        package_path=str(package_path) if package_path and package_path.exists() else None,
        payload=payload,
    )
    result["project_id"] = project_id
    return result


def execute_chat_step(
    settings: Settings,
    project_id: str,
    step: dict[str, Any],
    patch_json: dict[str, Any] | None,
) -> dict[str, Any]:
    tool = step["tool"]
    if tool == "project.summary":
        return package_summary(settings, project_id)
    if tool == "aieng.import":
        return import_aieng_file(settings, project_id)
    if tool == "viewer.convert":
        return convert_asset(settings, project_id)
    if tool == "aieng.validate":
        return validate_aieng_file(settings, project_id)
    if tool == "mcp.check":
        return mcp_check(settings, project_id, step.get("inputs", {}))
    if tool == "mcp.parse_patch":
        return parse_patch(settings, {"patch_json": patch_json or {}})
    if tool == "mcp.prepare_execution":
        return prepare_patch_execution(settings, project_id, {"patch_json": patch_json or {}})
    return {"status": "unsupported"}


def chat_orchestrator(settings: Settings, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = get_project(settings, project_id)
    message = str(payload.get("message") or "").strip()
    execute = bool(payload.get("execute", False))
    patch_json, patch_error = find_patch_json(payload.get("patch_json"), message)
    plan, intent = build_chat_plan(project, message, patch_json)
    errors: list[str] = []

    if patch_error:
        errors.append(patch_error)

    if execute and not patch_error:
        for step in plan:
            try:
                output = execute_chat_step(settings, project_id, step, patch_json)
                step["output"] = compact_chat_output(step["tool"], output)
                step["status"] = "done"
            except Exception as exc:
                step["status"] = "failed"
                step["error"] = f"{type(exc).__name__}: {exc}"
                errors.append(step["error"])
                break

    executed_steps = [step for step in plan if step.get("status") == "done"]
    if execute and not errors:
        reply = f"Executed {len(executed_steps)} safe step(s) and refreshed the project state."
    elif execute and errors:
        reply = f"Stopped after {len(executed_steps)} safe step(s) because a later step failed."
    else:
        reply = f"Built a guarded plan with {len(plan)} step(s)."

    audit_payload = {
        "kind": "chat",
        "project_id": project_id,
        "message": message,
        "intent": intent,
        "execute": execute,
        "patch_json": patch_json,
        "plan": plan,
        "errors": errors,
        "created_at": now_iso(),
    }
    audit_meta = write_audit_log(settings, project_id, "chat", audit_payload)
    project["last_chat_audit"] = audit_meta["audit_path"]
    save_project(settings, project)
    return {
        "reply": reply,
        "intent": intent,
        "plan": plan,
        "executed": execute,
        "errors": errors,
        "audit_id": audit_meta["audit_id"],
        "audit_log_url": audit_meta["audit_url"],
        "patch_json": patch_json,
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_env()
    ensure_dirs(active_settings)
    app = FastAPI(title="aieng-platform")
    app.state.settings = active_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/assets", StaticFiles(directory=str(active_settings.data_root)), name="assets")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/api/runtime")
    def runtime() -> dict[str, Any]:
        return runtime_status(active_settings)

    @app.get("/api/runtime-config")
    def get_runtime_config() -> dict[str, Any]:
        return runtime_config_snapshot(active_settings)

    @app.put("/api/runtime-config")
    def update_runtime_config(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        return persist_runtime_config(active_settings, payload or {})

    @app.post("/api/runtime-config/test")
    def test_runtime_config(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        return runtime_config_snapshot(active_settings, payload or {})

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        items = [normalize_project(read_json(path, {})) for path in active_settings.projects_root.glob("*/metadata.json")]
        return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)

    @app.post("/api/projects")
    def create_project(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        data = payload or {}
        name = str(data.get("name") or "Untitled project").strip() or "Untitled project"
        return save_project(active_settings, default_project(name))

    @app.post("/api/projects/sample")
    def create_sample_project() -> dict[str, Any]:
        project = save_project(active_settings, default_project("SFA-5.41 sample"))
        if active_settings.sample_step.exists():
            target = project_dir(active_settings, project["id"]) / "source" / active_settings.sample_step.name
            shutil.copy2(active_settings.sample_step, target)
            project["source_step"] = project_relpath(active_settings, project["id"], target)
            project["status"] = "sample_ready"
            project["last_error"] = None
        else:
            project["status"] = "sample_missing"
            project["last_error"] = f"Sample STEP not found: {active_settings.sample_step}"
        return save_project(active_settings, project)

    @app.post("/api/projects/{project_id}/upload")
    async def upload(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        filename = SAFE_NAME.sub("_", file.filename or "upload.bin")
        suffix = Path(filename).suffix.lower()
        if suffix not in STEP_EXTENSIONS | {AIENG_EXT}:
            raise HTTPException(status_code=400, detail="only STEP/.aieng uploads are supported")
        folder = "packages" if suffix == AIENG_EXT else "source"
        destination = project_dir(active_settings, project_id) / folder / filename
        with destination.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        relpath = project_relpath(active_settings, project_id, destination)
        if folder == "packages":
            project["aieng_file"] = relpath
            project["status"] = "package_uploaded"
        else:
            project["source_step"] = relpath
            project["status"] = "step_uploaded"
        project["last_error"] = None
        return save_project(active_settings, project)

    @app.get("/api/projects/{project_id}")
    def get_project_summary(project_id: str) -> dict[str, Any]:
        return package_summary(active_settings, project_id)

    @app.post("/api/projects/{project_id}/import-aieng")
    def import_project(project_id: str) -> dict[str, Any]:
        result = import_aieng_file(active_settings, project_id)
        audit_meta = write_audit_log(active_settings, project_id, "import", result)
        return {**result, **audit_meta}

    @app.post("/api/projects/{project_id}/validate")
    def validate_project(project_id: str) -> dict[str, Any]:
        result = validate_aieng_file(active_settings, project_id)
        audit_meta = write_audit_log(active_settings, project_id, "validate", result)
        return {**result, **audit_meta}

    @app.post("/api/projects/{project_id}/convert")
    def convert_project(project_id: str) -> dict[str, Any]:
        result = convert_asset(active_settings, project_id)
        audit_meta = write_audit_log(active_settings, project_id, "convert", result)
        return {**result, **audit_meta}

    @app.post("/api/projects/{project_id}/mcp/check")
    def mcp_check_endpoint(project_id: str, payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        result = mcp_check(active_settings, project_id, payload or {})
        audit_meta = write_audit_log(active_settings, project_id, "mcp_check", result)
        return {**result, **audit_meta}

    @app.post("/api/projects/{project_id}/mcp/parse-patch")
    def parse_patch_endpoint(project_id: str, payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        get_project(active_settings, project_id)
        result = parse_patch(active_settings, payload or {})
        audit_meta = write_audit_log(active_settings, project_id, "parse_patch", result)
        return {**result, **audit_meta}

    @app.post("/api/projects/{project_id}/mcp/prepare-execution")
    def prepare_execution_endpoint(project_id: str, payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        result = prepare_patch_execution(active_settings, project_id, payload or {})
        audit_meta = write_audit_log(active_settings, project_id, "prepare_execution", result)
        return {**result, **audit_meta}

    @app.post("/api/projects/{project_id}/chat")
    def chat(project_id: str, payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        return chat_orchestrator(active_settings, project_id, payload or {})

    return app


app = create_app()
