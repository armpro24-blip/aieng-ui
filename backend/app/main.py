from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import yaml

from .providers import get_provider
from . import runtime as _rt
from . import agent_workbench
from . import agent_engine

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


def read_package_yaml(archive: zipfile.ZipFile, member_name: str) -> Any:
    try:
        return yaml.safe_load(archive.read(member_name).decode("utf-8", errors="replace"))
    except KeyError:
        return None


def read_package_json_candidates(archive: zipfile.ZipFile, member_names: tuple[str, ...]) -> Any:
    for member_name in member_names:
        value = read_package_json(archive, member_name)
        if value is not None:
            return value
    return None


def read_package_yaml_candidates(archive: zipfile.ZipFile, member_names: tuple[str, ...]) -> Any:
    for member_name in member_names:
        value = read_package_yaml(archive, member_name)
        if value is not None:
            return value
    return None


def package_member_items(value: Any, preferred_keys: tuple[str, ...] = ()) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in preferred_keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        items = value.get("items")
        if isinstance(items, list):
            return items
    return []


def summarize_evidence_items(evidence_index: Any) -> list[dict[str, Any]]:
    evidence_items = package_member_items(evidence_index, ("evidence_items",))
    summarized: list[dict[str, Any]] = []
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        artifact = item.get("artifact") if isinstance(item.get("artifact"), dict) else {}
        verification = item.get("verification") if isinstance(item.get("verification"), dict) else {}
        claim_support = item.get("claim_support") if isinstance(item.get("claim_support"), list) else []
        summarized.append(
            {
                "evidence_id": item.get("evidence_id"),
                "evidence_type": item.get("evidence_type"),
                "artifact_path": artifact.get("path"),
                "artifact_kind": artifact.get("kind"),
                "verification_status": verification.get("status"),
                "notes": item.get("notes") or artifact.get("notes") or verification.get("notes"),
                "claim_support": claim_support,
            }
        )
    return summarized


def summarize_cae_payload(
    *,
    constraints: Any,
    parsed_materials: Any,
    parsed_boundary_conditions: Any,
    parsed_loads: Any,
    cae_mapping: Any,
    evidence_index: Any,
    validation_status: Any,
) -> dict[str, Any]:
    constraint_items = [item for item in package_member_items(constraints, ("constraints",)) if isinstance(item, dict)]
    material_items = package_member_items(parsed_materials, ("materials",))
    boundary_condition_items = package_member_items(parsed_boundary_conditions, ("boundary_conditions", "constraints", "bcs"))
    load_items = package_member_items(parsed_loads, ("loads", "forces"))
    evidence_items = summarize_evidence_items(evidence_index)
    result_evidence = [
        item for item in evidence_items if item.get("evidence_type") in {"solver_result", "mesh_evidence"}
    ]

    available_fields: list[str] = []
    for constraint in constraint_items:
        metric = str(constraint.get("metric") or "").lower()
        if "stress" in metric and "stress" not in available_fields:
            available_fields.append("stress")
        if "displacement" in metric and "displacement" not in available_fields:
            available_fields.append("displacement")

    solver_mesh_status = validation_status.get("solver_mesh_status", {}) if isinstance(validation_status, dict) else {}
    if isinstance(solver_mesh_status, dict):
        if "stress_validation" in solver_mesh_status and "stress" not in available_fields:
            available_fields.append("stress")
        if "displacement_validation" in solver_mesh_status and "displacement" not in available_fields:
            available_fields.append("displacement")

    constraint_type_counts = dict(Counter(str(item.get("type") or "unknown") for item in constraint_items))
    simulation_targets = [
        {
            "id": item.get("id"),
            "target": item.get("target"),
            "metric": item.get("metric"),
            "operator": item.get("operator"),
            "value": item.get("value"),
            "reason": item.get("reason"),
        }
        for item in constraint_items
        if item.get("type") == "simulation_target"
    ]
    protected_regions = [
        {
            "id": item.get("id"),
            "target": item.get("target"),
            "type": item.get("type"),
            "reason": item.get("reason"),
        }
        for item in constraint_items
        if str(item.get("type") or "").startswith("protect") or item.get("type") == "preserve_interface"
    ]

    present = any(
        [
            constraint_items,
            material_items,
            boundary_condition_items,
            load_items,
            evidence_items,
            isinstance(cae_mapping, dict) and bool(cae_mapping),
            isinstance(solver_mesh_status, dict) and bool(solver_mesh_status),
        ]
    )

    return {
        "present": present,
        "constraints_count": len(constraint_items),
        "constraint_types": constraint_type_counts,
        "materials_count": len(material_items),
        "boundary_conditions_count": len(boundary_condition_items),
        "loads_count": len(load_items),
        "evidence_count": len(evidence_items),
        "result_evidence_count": len(result_evidence),
        "results_available": bool(result_evidence),
        "available_fields": available_fields,
        "simulation_targets": simulation_targets,
        "protected_regions": protected_regions,
        "materials": material_items,
        "boundary_conditions": boundary_condition_items,
        "loads": load_items,
        "evidence": evidence_items,
        "mapping": cae_mapping,
        "solver_status": solver_mesh_status if isinstance(solver_mesh_status, dict) else {},
    }


def package_summary_fallback(package_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(package_path) as archive:
        members = sorted(archive.namelist())
        manifest = read_package_json(archive, "manifest.json")
        feature_graph = read_package_json(archive, "graph/feature_graph.json")
        topology = read_package_json(archive, "geometry/topology_map.json")
        interfaces = read_package_json(archive, "objects/interface_graph.json")
        task_spec = read_package_json_candidates(archive, ("task_spec.json", "task/task_spec.json"))
        if task_spec is None:
            task_spec = read_package_yaml_candidates(archive, ("task/task_spec.yaml", "task/task_spec.yml"))
        external_tool_requirements = read_package_json_candidates(
            archive,
            ("external_tool_requirements.json", "task/external_tool_requirements.json"),
        )
        claim_map = read_package_json(archive, "ai/claim_map.json")
        evidence_index = read_package_json(archive, "results/evidence_index.json")
        tool_trace = read_package_json(archive, "provenance/tool_trace.json")
        completeness_report = read_package_json(archive, "validation/completeness_report.json")
        evidence_report = read_package_json(archive, "validation/evidence_report.json")
        constraints = read_package_json(archive, "graph/constraints.json")
        parsed_materials = read_package_json(archive, "simulation/cae_imports/parsed_materials.json")
        parsed_boundary_conditions = read_package_json(archive, "simulation/cae_imports/parsed_boundary_conditions.json")
        parsed_loads = read_package_json(archive, "simulation/cae_imports/parsed_loads.json")
        cae_mapping = read_package_json(archive, "simulation/cae_mapping.json")
        validation_status = read_package_yaml(archive, "validation/status.yaml")
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
        "constraints": constraints,
        "task_spec": task_spec,
        "external_tool_requirements": external_tool_requirements,
        "claim_map": claim_map,
        "evidence_index": evidence_index,
        "tool_trace": tool_trace,
        "completeness_report": completeness_report,
        "evidence_report": evidence_report,
        "parsed_materials": parsed_materials,
        "parsed_boundary_conditions": parsed_boundary_conditions,
        "parsed_loads": parsed_loads,
        "cae_mapping": cae_mapping,
        "validation_status": validation_status,
        "cae": summarize_cae_payload(
            constraints=constraints,
            parsed_materials=parsed_materials,
            parsed_boundary_conditions=parsed_boundary_conditions,
            parsed_loads=parsed_loads,
            cae_mapping=cae_mapping,
            evidence_index=evidence_index,
            validation_status=validation_status,
        ),
        "ai_summary": ai_summary,
        "derived": derived,
        "warnings": warnings,
    }


def _detect_cae_artifacts(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_artifact_detector and scan the package.

    Uses temporary sys.path injection so the backend does not need
    aieng installed as a pip dependency.
    """
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_artifact_detector import detect_cae_artifacts

        return detect_cae_artifacts(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _generate_cae_result_summary(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_result_summary and generate a summary for the package.

    Uses temporary sys.path injection so the backend does not need
    aieng installed as a pip dependency.
    """
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_result_summary import generate_cae_result_summary

        return generate_cae_result_summary(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _generate_cae_preprocessing_summary(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_preprocessing_summary and generate a preprocessing summary for the package."""
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_preprocessing_summary import generate_preprocessing_summary

        return generate_preprocessing_summary(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def _generate_cae_simulation_run_summary(settings: Settings, package_path: Path) -> dict[str, Any] | None:
    """Import aieng cae_simulation_run_summary and generate a simulation run summary for the package."""
    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return None
    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_simulation_run_summary import generate_simulation_run_summary

        return generate_simulation_run_summary(package_path)
    except Exception:
        return None
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


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


def write_artifact_to_package(
    package_path: str | Path,
    artifact_path: str,
    source_path: str | Path,
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Write a single artifact file into an existing `.aieng` package ZIP.

    Uses the standard temp-file + atomic-move pattern so the package is
    never in a partially-written state.

    Args:
        package_path: Path to the `.aieng` package.
        artifact_path: Destination path inside the ZIP (e.g. ``results/computed_metrics.json``).
        source_path: Path to the file on disk to copy into the package.
        overwrite: Whether to overwrite an existing entry.

    Returns:
        Artifact metadata dict with ``path``, ``kind``, ``role``, ``source_path``.
    """
    path = Path(package_path)
    source = Path(source_path)
    if path.suffix != ".aieng":
        raise ValueError("package path must end with .aieng")
    if not source.exists():
        raise FileNotFoundError(f"source file not found: {source}")

    with zipfile.ZipFile(path, mode="r") as package:
        names = set(package.namelist())
        if "manifest.json" not in names:
            raise ValueError("package is missing manifest.json")
        if not overwrite and artifact_path in names:
            raise FileExistsError(
                f"{artifact_path} already exists in package; use overwrite=True to replace"
            )
        manifest = json.loads(package.read("manifest.json"))
        existing_members: list[tuple[zipfile.ZipInfo, bytes]] = []
        seen: set[str] = set()
        for info in package.infolist():
            if info.filename in seen or info.filename == artifact_path or info.filename == "manifest.json":
                continue
            seen.add(info.filename)
            data = b"" if info.is_dir() else package.read(info.filename)
            existing_members.append((info, data))

    manifest_json = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=path.parent) as temp_handle:
        temp_path = Path(temp_handle.name)

    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_package:
            for info, data in existing_members:
                out_package.writestr(info, data)
            out_package.writestr("manifest.json", manifest_json)
            out_package.writestr(artifact_path, source.read_bytes())
        shutil.move(str(temp_path), path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return {
        "path": artifact_path,
        "kind": Path(artifact_path).stem,
        "role": "artifact",
        "source_path": str(source),
    }


# ---------------------------------------------------------------------------
# Artifact review (Phase 26) — read-only inspection of .aieng package contents
# ---------------------------------------------------------------------------
# Purpose: enable humans to review what an agent or runtime tool wrote into a
# .aieng package. Read-only — does NOT execute solvers, mutate packages, or
# advance claims. This is evidence review, not engineering computation.

_ARTIFACT_MAX_TEXT_BYTES = 256 * 1024
_ARTIFACT_MAX_PARSE_BYTES = 2 * 1024 * 1024

_ARTIFACT_TEXT_SUFFIXES = frozenset(
    {".json", ".md", ".txt", ".yaml", ".yml", ".log", ".csv", ".inp"}
)
_ARTIFACT_MEDIA_HINTS: dict[str, str] = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".inp": "text/plain",
    ".frd": "application/octet-stream",
    ".vtu": "application/octet-stream",
    ".vtk": "application/octet-stream",
    ".step": "application/octet-stream",
    ".stp": "application/octet-stream",
    ".stl": "application/octet-stream",
    ".glb": "model/gltf-binary",
}


def _is_safe_artifact_path(p: str) -> bool:
    """Return True if `p` is a safe relative archive path.

    Rejects empty strings, leading separators, backslashes, and any `..`
    segment. Archive paths use forward slashes only.
    """
    if not p:
        return False
    if p.startswith("/") or p.startswith("./"):
        return False
    if "\\" in p:
        return False
    parts = p.split("/")
    if any(seg in ("", "..", ".") for seg in parts):
        return False
    return True


def _classify_artifact_media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _ARTIFACT_MEDIA_HINTS.get(suffix, "application/octet-stream")


def _read_artifact_from_package(
    package_path: Path,
    artifact_path: str,
) -> dict[str, Any]:
    """Read a single artifact from a `.aieng` package as a review payload.

    Always returns 200-shape data; callers handle 400/404 separately.
    """
    response: dict[str, Any] = {
        "path": artifact_path,
        "exists": False,
        "media_type": _classify_artifact_media_type(artifact_path),
        "warnings": [],
    }

    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            try:
                info = archive.getinfo(artifact_path)
            except KeyError:
                return response
            response["exists"] = True
            response["size_bytes"] = info.file_size
            data = archive.read(artifact_path)
    except zipfile.BadZipFile:
        response["warnings"].append("package is not a valid zip archive")
        return response

    suffix = Path(artifact_path).suffix.lower()
    is_textual = suffix in _ARTIFACT_TEXT_SUFFIXES
    has_null = b"\x00" in data[:4096]
    if has_null:
        is_textual = False
        response["warnings"].append("binary content detected; text omitted")

    if is_textual:
        if info.file_size <= _ARTIFACT_MAX_TEXT_BYTES:
            try:
                response["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                response["warnings"].append("utf-8 decode failed; text omitted")
        else:
            response["warnings"].append(
                f"file size {info.file_size} bytes exceeds inline text cap "
                f"{_ARTIFACT_MAX_TEXT_BYTES}; text omitted"
            )

    if suffix == ".json" and info.file_size <= _ARTIFACT_MAX_PARSE_BYTES and not has_null:
        try:
            response["parsed_json"] = json.loads(data)
        except json.JSONDecodeError as exc:
            response["warnings"].append(f"json parse failed: {exc.msg}")
    elif suffix == ".json" and info.file_size > _ARTIFACT_MAX_PARSE_BYTES:
        response["warnings"].append(
            f"file size {info.file_size} bytes exceeds parse cap "
            f"{_ARTIFACT_MAX_PARSE_BYTES}; parsed_json omitted"
        )

    return response


def _json_diff_paths(
    before: Any,
    after: Any,
    prefix: str = "",
) -> tuple[list[str], list[str], list[str]]:
    """Compute RFC-6901 JSON Pointer paths for changes between two JSON values.

    Returns (changed_paths, added_paths, removed_paths). Comparison is
    structural and recursive. Lists are compared element-by-element up to the
    shorter length; the tail is reported under added/removed. Primitive
    inequality at a leaf produces a changed path.

    Path encoding: `/` separators, `~0` for `~` and `~1` for `/` per RFC-6901.
    """
    def _escape(token: str) -> str:
        return token.replace("~", "~0").replace("/", "~1")

    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    if isinstance(before, dict) and isinstance(after, dict):
        before_keys = set(before.keys())
        after_keys = set(after.keys())
        for key in sorted(before_keys & after_keys):
            sub_changed, sub_added, sub_removed = _json_diff_paths(
                before[key], after[key], f"{prefix}/{_escape(str(key))}"
            )
            changed.extend(sub_changed)
            added.extend(sub_added)
            removed.extend(sub_removed)
        for key in sorted(after_keys - before_keys):
            added.append(f"{prefix}/{_escape(str(key))}")
        for key in sorted(before_keys - after_keys):
            removed.append(f"{prefix}/{_escape(str(key))}")
    elif isinstance(before, list) and isinstance(after, list):
        common = min(len(before), len(after))
        for i in range(common):
            sub_changed, sub_added, sub_removed = _json_diff_paths(
                before[i], after[i], f"{prefix}/{i}"
            )
            changed.extend(sub_changed)
            added.extend(sub_added)
            removed.extend(sub_removed)
        for i in range(common, len(after)):
            added.append(f"{prefix}/{i}")
        for i in range(common, len(before)):
            removed.append(f"{prefix}/{i}")
    else:
        if before != after:
            changed.append(prefix or "")

    return changed, added, removed


# ---------------------------------------------------------------------------
# cae.apply_setup_patch — constants and helpers
# ---------------------------------------------------------------------------

_ALLOWED_PATCH_PREFIXES = ("simulation/cae_imports/", "simulation/load_cases/")
_ALLOWED_PATCH_EXACT = frozenset(
    {"simulation/solver_settings.json", "simulation/cae_mapping.json", "graph/constraints.json"}
)
_SUPPORTED_PATCH_OPERATIONS = frozenset(
    {"create_file", "replace_json", "merge_object", "append_array_item"}
)
# Artifacts that become stale whenever setup files are changed.
_SETUP_STALE_ARTIFACTS = [
    "simulation/preprocessing_summary.json",
    "simulation/preprocessing_summary.md",
    "results/result_summary.json",
    "results/evidence_index.json",
    "results/postprocessing_summary.md",
]


def _is_allowed_patch_path(p: str) -> bool:
    if not p or p.startswith("/") or ".." in p.split("/"):
        return False
    if p in _ALLOWED_PATCH_EXACT:
        return True
    return any(p.startswith(prefix) for prefix in _ALLOWED_PATCH_PREFIXES)


def _parse_json_pointer(pointer: str) -> list[str]:
    """Decode a JSON Pointer (RFC 6901) into a list of path tokens."""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {pointer!r}")
    tokens = pointer[1:].split("/")
    return [t.replace("~1", "/").replace("~0", "~") for t in tokens]


def _json_pointer_get(obj: Any, tokens: list[str]) -> Any:
    cur: Any = obj
    for t in tokens:
        if isinstance(cur, dict):
            cur = cur[t]
        elif isinstance(cur, list):
            cur = cur[int(t)]
        else:
            raise KeyError(t)
    return cur


def _json_pointer_set(obj: Any, tokens: list[str], value: Any) -> None:
    """Set a value at the JSON Pointer location (mutates obj in-place)."""
    if not tokens:
        raise ValueError("Cannot replace root document via pointer")
    cur: Any = obj
    for t in tokens[:-1]:
        if isinstance(cur, dict):
            cur = cur[t]
        elif isinstance(cur, list):
            cur = cur[int(t)]
        else:
            raise KeyError(t)
    last = tokens[-1]
    if isinstance(cur, dict):
        cur[last] = value
    elif isinstance(cur, list):
        cur[int(last)] = value
    else:
        raise KeyError(last)


def _apply_single_patch(
    existing_content: bytes | None,
    op: dict[str, Any],
    path: str,
) -> bytes:
    """Apply one patch operation; returns new file bytes."""
    action = op.get("action_type") or op.get("operation") or ""
    patch_type = op.get("patch_type", "")

    if action == "create_file":
        content = op.get("content")
        if content is None:
            raise ValueError("create_file requires 'content'")
        if isinstance(content, (dict, list)):
            return (json.dumps(content, indent=2, sort_keys=True) + "\n").encode()
        return str(content).encode()

    if action in ("replace_json", "merge_object", "append_array_item"):
        if existing_content is None:
            raise ValueError(f"{action} requires an existing file at {path!r}")
        try:
            doc = json.loads(existing_content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"existing file at {path!r} is not valid JSON: {exc}") from exc

        pointer_str: str = op.get("pointer", "")
        value: Any = op.get("value")

        if action == "replace_json":
            if pointer_str:
                tokens = _parse_json_pointer(pointer_str)
                before = op.get("before")
                if before is not None:
                    current = _json_pointer_get(doc, tokens)
                    if current != before:
                        raise ValueError(
                            f"before mismatch at {pointer_str!r}: "
                            f"expected {before!r}, got {current!r}"
                        )
                _json_pointer_set(doc, tokens, value)
            else:
                if not isinstance(value, dict):
                    raise ValueError("replace_json without pointer requires value to be a dict")
                before = op.get("before")
                if before is not None and doc != before:
                    raise ValueError("before mismatch: document does not match expected value")
                doc = value
        elif action == "merge_object":
            if not isinstance(value, dict):
                raise ValueError("merge_object requires value to be a dict")
            if pointer_str:
                tokens = _parse_json_pointer(pointer_str)
                target = _json_pointer_get(doc, tokens)
                if not isinstance(target, dict):
                    raise ValueError(f"merge_object target at {pointer_str!r} is not an object")
                target.update(value)
            else:
                if not isinstance(doc, dict):
                    raise ValueError("merge_object without pointer requires document to be an object")
                doc.update(value)
        elif action == "append_array_item":
            if pointer_str:
                tokens = _parse_json_pointer(pointer_str)
                target = _json_pointer_get(doc, tokens)
                if not isinstance(target, list):
                    raise ValueError(f"append_array_item target at {pointer_str!r} is not an array")
                target.append(value)
            else:
                if not isinstance(doc, list):
                    raise ValueError("append_array_item without pointer requires document to be an array")
                doc.append(value)

        return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()

    raise ValueError(f"unsupported action_type: {action!r}")


def _apply_patches_to_package(
    package_path: Path,
    patches: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """
    Apply all patches atomically to the package; returns (changed_paths, warning_msgs).
    Reads the whole ZIP, applies patches in-memory, writes a new ZIP atomically.
    """
    with zipfile.ZipFile(package_path, mode="r") as zf:
        existing_names = set(zf.namelist())
        manifest_data = json.loads(zf.read("manifest.json")) if "manifest.json" in existing_names else {}
        members: dict[str, bytes] = {}
        for name in existing_names:
            info = zf.getinfo(name)
            if not info.is_dir():
                members[name] = zf.read(name)

    changed_paths: list[str] = []
    warnings_out: list[str] = []

    for patch in patches:
        path: str = patch.get("path", "")
        existing_bytes: bytes | None = members.get(path)
        new_bytes = _apply_single_patch(existing_bytes, patch, path)
        members[path] = new_bytes
        changed_paths.append(path)

    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".aieng", dir=package_path.parent
    ) as tmp:
        tmp_path = Path(tmp.name)

    try:
        manifest_json = (json.dumps(manifest_data, indent=2, sort_keys=True) + "\n").encode()
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            out_zf.writestr("manifest.json", manifest_json)
            seen: set[str] = {"manifest.json"}
            for name, data in members.items():
                if name in seen or name == "manifest.json":
                    continue
                seen.add(name)
                out_zf.writestr(name, data)
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return changed_paths, warnings_out


def _compute_stale_artifacts(
    changed_paths: list[str],
    refreshed_paths: list[str],
) -> list[str]:
    """Return stale artifact paths: those in _SETUP_STALE_ARTIFACTS not yet refreshed."""
    stale = []
    refreshed_set = set(refreshed_paths)
    for art in _SETUP_STALE_ARTIFACTS:
        if art not in refreshed_set:
            stale.append(art)
    return stale


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
        "constraints": None,
        "task_spec": None,
        "external_tool_requirements": None,
        "claim_map": None,
        "evidence_index": None,
        "tool_trace": None,
        "completeness_report": None,
        "evidence_report": None,
        "cae": {
            "present": False,
            "constraints_count": 0,
            "constraint_types": {},
            "materials_count": 0,
            "boundary_conditions_count": 0,
            "loads_count": 0,
            "evidence_count": 0,
            "result_evidence_count": 0,
            "results_available": False,
            "available_fields": [],
            "simulation_targets": [],
            "protected_regions": [],
            "materials": [],
            "boundary_conditions": [],
            "loads": [],
            "evidence": [],
            "mapping": None,
            "solver_status": {},
            "solver_fields": [],
        },
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
            summary["constraints"] = result.get("constraints")
            summary["task_spec"] = result.get("task_spec")
            summary["external_tool_requirements"] = result.get("external_tool_requirements")
            summary["claim_map"] = result.get("claim_map")
            summary["evidence_index"] = result.get("evidence_index")
            summary["tool_trace"] = result.get("tool_trace")
            summary["completeness_report"] = result.get("completeness_report")
            summary["evidence_report"] = result.get("evidence_report")
            summary["cae"] = result.get("cae") or summarize_cae_payload(
                constraints=result.get("constraints"),
                parsed_materials=result.get("parsed_materials"),
                parsed_boundary_conditions=result.get("parsed_boundary_conditions"),
                parsed_loads=result.get("parsed_loads"),
                cae_mapping=result.get("cae_mapping"),
                evidence_index=result.get("evidence_index"),
                validation_status=result.get("validation_status"),
            )
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
                summary["constraints"] = fallback["constraints"]
                summary["task_spec"] = fallback["task_spec"]
                summary["external_tool_requirements"] = fallback["external_tool_requirements"]
                summary["claim_map"] = fallback["claim_map"]
                summary["evidence_index"] = fallback["evidence_index"]
                summary["tool_trace"] = fallback["tool_trace"]
                summary["completeness_report"] = fallback["completeness_report"]
                summary["evidence_report"] = fallback["evidence_report"]
                summary["cae"] = fallback["cae"]
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

    _cae = summary.get("cae")
    if isinstance(_cae, dict):
        _field_defaults: dict[str, dict[str, Any]] = {
            "stress": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa"},
            "displacement": {"min_value": 0.0, "max_value": 5.0, "unit": "mm"},
        }
        _cae["solver_fields"] = [
            {
                "field_name": f,
                "descriptor_url": f"/api/projects/{project_id}/fields/{f}",
                **_field_defaults.get(f, {"min_value": 0.0, "max_value": 1.0, "unit": ""}),
                "format": "vertex_synthetic",
                "available": True,
            }
            for f in (_cae.get("available_fields") or [])
        ]
        if package_path and package_path.exists():
            _artifact_detection = _detect_cae_artifacts(settings, package_path)
            if _artifact_detection is not None:
                _cae["artifact_detection"] = _artifact_detection
            _result_summary = _generate_cae_result_summary(settings, package_path)
            if _result_summary is not None:
                _cae["result_summary"] = _result_summary
            _preprocessing_summary = _generate_cae_preprocessing_summary(settings, package_path)
            if _preprocessing_summary is not None:
                _cae["preprocessing_summary"] = _preprocessing_summary
            _simulation_run_summary = _generate_cae_simulation_run_summary(settings, package_path)
            if _simulation_run_summary is not None:
                _cae["simulation_run_summary"] = _simulation_run_summary

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

    @app.get("/api/capabilities")
    def list_capabilities() -> list[dict[str, Any]]:
        return agent_workbench.list_capabilities(active_settings)

    @app.post("/api/capabilities/preview")
    def preview_capability(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        return agent_workbench.preview_capability(active_settings, payload or {})

    @app.get("/api/runtime/workflows")
    def list_runtime_workflows() -> list[dict[str, Any]]:
        return agent_workbench.list_workflows()

    def _build_agent_response(data: dict[str, Any]) -> dict[str, Any]:
        message = str(data.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="message is required")
        project_id = data.get("project_id") or None
        project_summary: dict[str, Any] | None = None
        if project_id:
            project_summary = package_summary(active_settings, str(project_id))
        patch_json = data.get("patch_json") if isinstance(data.get("patch_json"), dict) else None
        return agent_engine.build_agent_plan(
            settings=active_settings,
            message=message,
            project_id=str(project_id) if project_id else None,
            project_summary=project_summary,
            runtime_tools=_rt.registered_tools_info(),
            capabilities=agent_workbench.list_capabilities(active_settings),
            llm_config=agent_engine.sanitize_llm_config(data.get("llm_config")),
            patch_json=patch_json,
            dry_run=bool(data.get("dry_run", False)),
        )

    @app.post("/api/agent/plan")
    def create_agent_plan(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        return _build_agent_response(payload or {})

    @app.post("/api/agent/runs")
    def create_agent_run(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        data = payload or {}
        agent_plan = data.get("plan") if isinstance(data.get("plan"), dict) else _build_agent_response(data)
        steps = agent_plan.get("steps") if isinstance(agent_plan.get("steps"), list) else []
        message = str(agent_plan.get("message") or data.get("message") or "agent run").strip()
        project_id = agent_plan.get("project_id") or data.get("project_id") or None
        run = _rt.RunRecord(
            run_id=uuid.uuid4().hex[:12],
            message=message,
            created_at=now_iso(),
            status="pending",
            project_id=str(project_id) if project_id else None,
        )
        ctx: dict[str, Any] = {
            "project_id": run.project_id,
            "workflow_id": "agent_chat",
            "agent_plan": {
                "mode": agent_plan.get("mode"),
                "warnings": agent_plan.get("warnings") or [],
                "errors": agent_plan.get("errors") or [],
            },
        }
        if isinstance(data.get("llm_config"), dict):
            ctx["llm_config"] = agent_engine.sanitize_llm_config(data.get("llm_config"))
        _rt.execute_run_with_plan(run, steps, ctx)
        if run.project_id:
            try:
                write_audit_log(active_settings, run.project_id, "agent_run", {
                    "kind": "agent_run",
                    "run_id": run.run_id,
                    "message": run.message,
                    "agent_plan": agent_plan,
                    "status": run.status,
                    "errors": run.errors,
                    "created_at": run.created_at,
                })
            except Exception:
                pass
        return {
            "agent": agent_plan,
            "run": _rt.run_to_dict(run),
        }

    @app.get("/api/benchmarks/scenarios")
    def list_benchmark_scenarios() -> list[dict[str, Any]]:
        return agent_workbench.list_benchmark_scenarios(active_settings)

    @app.post("/api/benchmarks/runs")
    def create_benchmark_run(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        return agent_workbench.run_benchmark_from_payload(active_settings, payload or {})

    @app.get("/api/benchmarks/runs/{run_id}")
    def get_benchmark_run(run_id: str) -> dict[str, Any]:
        run = agent_workbench.get_benchmark_run(active_settings, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="benchmark run not found")
        return run

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

    @app.get("/api/projects/{project_id}/cae-artifacts")
    def get_project_cae_artifacts(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _detect_cae_artifacts(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng detector unavailable")
        return result

    @app.get("/api/projects/{project_id}/cae-result-summary")
    def get_project_cae_result_summary(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _generate_cae_result_summary(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng summarizer unavailable")
        return result

    @app.get("/api/projects/{project_id}/cae-preprocessing-summary")
    def get_project_cae_preprocessing_summary(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _generate_cae_preprocessing_summary(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng preprocessing summarizer unavailable")
        return result

    @app.get("/api/projects/{project_id}/cae-simulation-run-summary")
    def get_project_cae_simulation_run_summary(project_id: str) -> dict[str, Any]:
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        result = _generate_cae_simulation_run_summary(active_settings, package_path)
        if result is None:
            raise HTTPException(status_code=503, detail="aieng simulation run summarizer unavailable")
        return result

    @app.get("/api/projects/{project_id}/artifact")
    def get_project_artifact(project_id: str, path: str = "") -> dict[str, Any]:
        """Read a single artifact from the project's .aieng package.

        Phase 26 — evidence review groundwork. Read-only. Does NOT execute
        solvers, mutate packages, or advance claims.

        Query parameters:
            path: Artifact path inside the package, e.g.
                  ``results/computed_metrics.json``. Must be a relative path
                  with forward slashes; leading ``/``, backslashes, ``.``,
                  and ``..`` segments are rejected with 400.

        Returns:
            ``{path, exists, media_type, size_bytes?, parsed_json?, text?, warnings}``.
            ``exists=false`` returns 200, not 404, so callers can probe
            artifact presence without exception handling. The package
            itself missing returns 404.
        """
        if not _is_safe_artifact_path(path):
            raise HTTPException(
                status_code=400,
                detail=(
                    "invalid artifact path: must be a relative archive path "
                    "with no leading '/', no '..' segments, and no backslashes"
                ),
            )
        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")
        return _read_artifact_from_package(package_path, path)

    @app.post("/api/projects/{project_id}/artifact/diff")
    def diff_project_artifact(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Compute changed JSON pointer paths between two arbitrary JSON values.

        Phase 26 — paired with the artifact read endpoint so callers can
        capture before/after JSON snapshots themselves and ask the server
        for a structural diff. Pure computation; no package access.

        Body:
            ``{"before": <any>, "after": <any>}``

        Returns:
            ``{"changed_paths": [...], "added_paths": [...], "removed_paths": [...]}``.
            Paths are RFC-6901 JSON pointers.
        """
        get_project(active_settings, project_id)
        body = payload or {}
        if "before" not in body or "after" not in body:
            raise HTTPException(
                status_code=400,
                detail="body must contain both 'before' and 'after' keys",
            )
        changed, added, removed = _json_diff_paths(body["before"], body["after"])
        return {
            "changed_paths": changed,
            "added_paths": added,
            "removed_paths": removed,
        }

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

    @app.get("/api/projects/{project_id}/fields/{field_name}")
    def get_field_descriptor(project_id: str, field_name: str) -> dict[str, Any]:
        get_project(active_settings, project_id)
        _known: dict[str, dict[str, Any]] = {
            "stress": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
            "displacement": {"min_value": 0.0, "max_value": 5.0, "unit": "mm", "colormap": "coolwarm"},
        }
        meta = _known.get(field_name, {"min_value": 0.0, "max_value": 1.0, "unit": "", "colormap": "thermal"})
        return {
            "field_name": field_name,
            "project_id": project_id,
            "format": "vertex_synthetic",
            "basis": "y_normalized",
            "min_value": meta["min_value"],
            "max_value": meta["max_value"],
            "unit": meta["unit"],
            "colormap": meta["colormap"],
            "source": "synthetic_mock",
        }

    # ── runtime tool registrations ────────────────────────────────────────────
    # Each closure captures active_settings so tool handlers call existing
    # business-logic functions without duplicating them.

    def _tool_inspect_package(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for aieng.inspect_package")
        return package_summary(active_settings, pid)

    def _tool_refresh_semantics(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for aieng.refresh_semantics")
        return validate_aieng_file(active_settings, pid)

    def _tool_generate_preview(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for aieng.generate_preview")
        return convert_asset(active_settings, pid)

    def _tool_read_audit_log(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        logs = recent_logs(active_settings, pid) if pid else []
        return {"project_id": pid, "recent_logs": logs}

    def _tool_freecad_inspect_geometry(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import freecad_bridge

        # Resolve input file: explicit path → package path → project source_step
        input_path: str | None = inp.get("inputPath") or inp.get("input_path")
        if not input_path:
            pid = inp.get("project_id")
            if pid:
                proj = read_json(metadata_path(active_settings, pid))
                if proj:
                    rel = proj.get("source_step")
                    if rel:
                        input_path = str(project_dir(active_settings, pid) / rel)

        if not input_path:
            return {
                "status": "error",
                "code": "missing_input",
                "message": (
                    "No input file provided. Pass inputPath or a project_id "
                    "that has an uploaded STEP file."
                ),
            }

        from pathlib import Path as _Path
        if not _Path(input_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Input file not found: {input_path}",
            }

        return freecad_bridge.inspect_geometry(
            input_path,
            freecad_cmd=active_settings.freecad_cmd,
            freecad_mcp_root=active_settings.freecad_mcp_root,
        )

    def _tool_freecad_export_step(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import freecad_bridge
        from pathlib import Path as _Path

        # Resolve input file: explicit path → project source_step
        input_path: str | None = inp.get("inputPath") or inp.get("input_path")
        if not input_path:
            pid = inp.get("project_id")
            if pid:
                proj = read_json(metadata_path(active_settings, pid))
                if proj:
                    rel = proj.get("source_step")
                    if rel:
                        input_path = str(project_dir(active_settings, pid) / rel)

        if not input_path:
            return {
                "status": "error",
                "code": "missing_input",
                "message": (
                    "No input file provided. Pass inputPath or a project_id "
                    "that has an uploaded STEP file."
                ),
            }

        if not _Path(input_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Input file not found: {input_path}",
            }

        # Resolve output path; never overwrite the source (append _export suffix)
        output_path: str | None = inp.get("outputPath") or inp.get("output_path")
        if not output_path:
            in_p = _Path(input_path)
            output_path = str(in_p.with_stem(in_p.stem + "_export").with_suffix(".step"))

        result = freecad_bridge.export_step(
            input_path,
            output_path,
            freecad_cmd=active_settings.freecad_cmd,
            freecad_mcp_root=active_settings.freecad_mcp_root,
        )

        # Per-project audit for artifact changes
        pid = inp.get("project_id")
        if pid and isinstance(result, dict) and result.get("artifacts"):
            try:
                write_audit_log(active_settings, pid, "freecad_export", {
                    "tool": "freecad.export_step",
                    "inputPath": input_path,
                    "outputPath": output_path,
                    "status": result.get("status"),
                    "artifacts": result.get("artifacts", []),
                })
            except Exception:
                pass

        return result

    def _tool_generate_computed_metrics(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import freecad_bridge
        from pathlib import Path as _Path

        input_path: str | None = inp.get("inputPath") or inp.get("input_path")
        output_path: str | None = inp.get("outputPath") or inp.get("output_path")
        project_id: str | None = inp.get("project_id")

        # Resolve output path: explicit → project workspace results/
        if not output_path and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                # Write into the same directory as the .aieng package
                output_path = str(pkg.parent / "results" / "computed_metrics.json")
            else:
                # Fallback to project directory
                output_path = str(
                    project_dir(active_settings, project_id) / "results" / "computed_metrics.json"
                )

        if not output_path:
            return {
                "status": "error",
                "code": "missing_computed_metrics_output_path",
                "message": (
                    "No output path provided and no project_id could be resolved. "
                    "Pass outputPath or a project_id."
                ),
            }

        if not input_path:
            return {
                "status": "error",
                "code": "missing_input",
                "message": "No input file provided. Pass inputPath.",
            }

        if not _Path(input_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Input file not found: {input_path}",
            }

        result = freecad_bridge.export_computed_metrics(
            input_path,
            output_path,
            freecad_mcp_root=active_settings.freecad_mcp_root,
            load_case_id=inp.get("loadCaseId") or inp.get("load_case_id") or "load_case_001",
            software=inp.get("software"),
            source_files=inp.get("sourceFiles") or inp.get("source_files") or [],
        )

        artifacts: list[dict[str, Any]] = [
            {
                "path": output_path,
                "kind": "computed_metrics",
                "role": "external_postprocessing_metrics",
            }
        ]
        warnings: list[str] = list(result.get("warnings") or [])

        # Write-back into .aieng package so refresh_cae_summary can read it
        if project_id:
            try:
                proj = get_project(active_settings, project_id)
                pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
                if pkg is not None and pkg.exists():
                    pkg_artifact = write_artifact_to_package(
                        pkg,
                        "results/computed_metrics.json",
                        output_path,
                        overwrite=True,
                    )
                    artifacts.append({
                        "path": pkg_artifact["path"],
                        "kind": "computed_metrics",
                        "role": "package_postprocessing_metrics",
                    })
                else:
                    warnings.append("computed_metrics_not_written_to_package: no .aieng package found")
            except Exception as exc:
                warnings.append(f"computed_metrics_not_written_to_package: {exc}")

        # Normalize to a runtime-friendly dict with artifacts
        return {
            "status": "ok",
            "output_path": output_path,
            "schema_version": result.get("schema_version"),
            "metrics_count": sum(
                len(lc.get("metrics", {})) for lc in result.get("load_cases", [])
            ),
            "artifacts": artifacts,
            "warnings": warnings,
        }

    def _tool_refresh_cae_summary(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")

        if not package_path and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path = str(pkg)

        if not package_path:
            return {
                "status": "error",
                "code": "missing_cae_summary_package_path",
                "message": (
                    "No package path provided and no project_id could be resolved. "
                    "Pass packagePath or a project_id with an .aieng file."
                ),
            }

        if not _Path(package_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path}",
            }

        overwrite = bool(inp.get("overwrite", True))
        result = aieng_bridge.refresh_cae_result_summary(
            package_path,
            aieng_root=active_settings.aieng_root,
            overwrite=overwrite,
        )
        return result

    def _tool_mcp_check(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for mcp.check")
        return mcp_check(active_settings, pid, inp)

    def _tool_mcp_parse_patch(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        patch_json = inp.get("patch_json")
        if not isinstance(patch_json, dict):
            raise ValueError("patch_json is required for mcp.parse_patch")
        return parse_patch(active_settings, {"patch_json": patch_json})

    def _tool_mcp_prepare_execution(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        pid = inp.get("project_id")
        if not pid:
            raise ValueError("project_id is required for mcp.prepare_execution")
        patch_json = inp.get("patch_json")
        if not isinstance(patch_json, dict):
            raise ValueError("patch_json is required for mcp.prepare_execution")
        return prepare_patch_execution(active_settings, pid, inp)

    def _tool_cae_apply_setup_patch(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        # Guard: reject claims_advanced requests
        if inp.get("claims_advanced"):
            return {
                "status": "error",
                "code": "unsupported_operation",
                "message": "claims_advanced=true is not supported in this version.",
            }

        package_path: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")

        if not package_path and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path = str(pkg)

        if not package_path:
            return {
                "status": "error",
                "code": "missing_package_path",
                "message": (
                    "No package path provided and no project_id could be resolved. "
                    "Pass packagePath or a project_id with an .aieng file."
                ),
            }

        if not _Path(package_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path}",
            }

        patches: list[dict[str, Any]] = inp.get("patches", [])
        if not patches:
            return {
                "status": "error",
                "code": "no_patches",
                "message": "No patches provided.",
            }

        # Validate all patches before applying any
        for i, patch in enumerate(patches):
            path = patch.get("path", "")
            action = patch.get("action_type") or patch.get("operation") or ""
            if not _is_allowed_patch_path(path):
                return {
                    "status": "error",
                    "code": "forbidden_path",
                    "message": (
                        f"Patch {i}: path {path!r} is not in the allowed patch locations. "
                        "Only simulation/cae_imports/, simulation/load_cases/, "
                        "simulation/solver_settings.json, simulation/cae_mapping.json, "
                        "and graph/constraints.json are writable."
                    ),
                }
            if action not in _SUPPORTED_PATCH_OPERATIONS:
                return {
                    "status": "error",
                    "code": "unsupported_operation",
                    "message": (
                        f"Patch {i}: action_type {action!r} is not supported. "
                        f"Supported: {sorted(_SUPPORTED_PATCH_OPERATIONS)}"
                    ),
                }

        try:
            changed_paths, apply_warnings = _apply_patches_to_package(
                _Path(package_path), patches
            )
        except ValueError as exc:
            return {"status": "error", "code": "patch_error", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "code": "patch_error", "message": f"Patch failed: {exc}"}

        refreshed_artifacts: list[dict[str, Any]] = []
        refresh_warnings: list[str] = []

        do_refresh = bool(inp.get("refresh_preprocessing_summary", True))
        if do_refresh:
            try:
                refresh_result = aieng_bridge.refresh_preprocessing_summary(
                    package_path,
                    aieng_root=active_settings.aieng_root,
                    overwrite=True,
                )
                refreshed_artifacts.extend(refresh_result.get("artifacts", []))
            except Exception as exc:
                refresh_warnings.append(
                    f"preprocessing_summary_refresh_failed: {exc}. "
                    "Refresh manually via postprocess.refresh_cae_summary."
                )

        refreshed_paths = [a["path"] for a in refreshed_artifacts]
        stale_artifacts = _compute_stale_artifacts(changed_paths, refreshed_paths)
        all_warnings = apply_warnings + refresh_warnings

        return {
            "status": "ok",
            "changed_artifacts": [
                {"path": p, "kind": "cae_setup_patch", "role": "patched_setup_artifact"}
                for p in changed_paths
            ],
            "refreshed_artifacts": refreshed_artifacts,
            "stale_artifacts": stale_artifacts,
            "warnings": all_warnings,
        }

    def _tool_cae_extract_solver_results(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        frd_path: str | None = inp.get("frdPath") or inp.get("frd_path")

        if not package_path and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path = str(pkg)

        if not package_path:
            return {
                "status": "error",
                "code": "missing_package_path",
                "message": (
                    "No package path provided and no project_id could be resolved. "
                    "Pass packagePath or a project_id with an .aieng file."
                ),
            }

        if not frd_path:
            return {
                "status": "error",
                "code": "missing_frd_path",
                "message": "No frdPath provided. Pass the path to the CalculiX .frd result file.",
            }

        if not _Path(package_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path}",
            }

        if not _Path(frd_path).exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"FRD file not found: {frd_path}",
            }

        load_case_id: str = inp.get("loadCaseId") or inp.get("load_case_id") or "load_case_001"
        software: str = inp.get("software") or "CalculiX"
        overwrite: bool = bool(inp.get("overwrite", True))

        try:
            result = aieng_bridge.extract_frd_solver_results(
                package_path,
                frd_path,
                aieng_root=active_settings.aieng_root,
                load_case_id=load_case_id,
                software=software,
                overwrite=overwrite,
            )
        except Exception as exc:
            return {"status": "error", "code": "extraction_error", "message": str(exc)}

        # Optionally refresh the result summary so the UI reflects real numbers
        refresh_warnings: list[str] = []
        if inp.get("refresh_result_summary", True):
            try:
                aieng_bridge.refresh_cae_result_summary(
                    package_path,
                    aieng_root=active_settings.aieng_root,
                    overwrite=True,
                )
            except Exception as exc:
                refresh_warnings.append(
                    f"result_summary_refresh_failed: {exc}. "
                    "Refresh manually via postprocess.refresh_cae_summary."
                )

        if refresh_warnings:
            result.setdefault("warnings", []).extend(refresh_warnings)

        return result

    def _tool_cae_prepare_solver_run(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        import zipfile as _zipfile

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        run_id: str = inp.get("runId") or inp.get("run_id") or "run_001"
        solver: str = inp.get("solver") or "CalculiX"
        load_case_id: str = inp.get("loadCaseId") or inp.get("load_case_id") or "load_case_001"
        input_deck_path_str: str | None = inp.get("inputDeckPath") or inp.get("input_deck_path")
        extract_results: bool = bool(inp.get("extractResults", inp.get("extract_results", True)))
        refresh_summary: bool = bool(inp.get("refreshSummary", inp.get("refresh_summary", True)))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.prepare_solver_run",
                "status": "error",
                "code": "missing_package_path",
                "message": (
                    "No package path provided and no project_id could be resolved. "
                    "Pass packagePath or a project_id with an .aieng file."
                ),
            }

        package_path = Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.prepare_solver_run",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            with _zipfile.ZipFile(package_path, "r") as zf:
                names = set(zf.namelist())
        except Exception as exc:
            return {
                "ok": False,
                "tool": "cae.prepare_solver_run",
                "status": "error",
                "code": "package_read_error",
                "message": f"Failed to read package: {exc}",
            }

        has_mesh = any(n.startswith("simulation/mesh/") for n in names)
        has_solver_settings = "simulation/solver_settings.json" in names
        has_load_case = f"simulation/load_cases/{load_case_id}.json" in names

        if input_deck_path_str:
            has_input_deck = Path(input_deck_path_str).exists()
        else:
            has_input_deck = f"simulation/runs/{run_id}/solver_input.inp" in names

        # Check ccx availability without executing it
        ccx_available = bool(
            shutil.which("ccx")
            or shutil.which("ccx_linux")
            or shutil.which("ccx2.21")
            or shutil.which("ccx_static")
        )

        missing_items: list[str] = []
        if not has_mesh:
            missing_items.append("simulation/mesh/ (no mesh files found in package)")
        if not has_solver_settings:
            missing_items.append("simulation/solver_settings.json")
        if not has_load_case:
            missing_items.append(f"simulation/load_cases/{load_case_id}.json")
        if not has_input_deck:
            deck_hint = f" (or external: {input_deck_path_str})" if input_deck_path_str else ""
            missing_items.append(f"simulation/runs/{run_id}/solver_input.inp{deck_hint}")
        if not ccx_available:
            missing_items.append("CalculiX executable (ccx) not found on PATH")

        ready_to_run = len(missing_items) == 0

        run_prefix = f"simulation/runs/{run_id}"
        planned_artifacts: list[dict[str, str]] = [
            {"path": f"{run_prefix}/solver_run.json", "kind": "solver_run_record", "role": "run_metadata"},
            {"path": f"{run_prefix}/solver_log.txt", "kind": "solver_log", "role": "solver_stdout"},
            {"path": f"{run_prefix}/outputs/result.frd", "kind": "frd_result", "role": "primary_result"},
        ]
        if extract_results:
            planned_artifacts.append(
                {"path": "results/computed_metrics.json", "kind": "computed_metrics", "role": "extracted_metrics"}
            )
        if refresh_summary:
            planned_artifacts.extend([
                {"path": "results/result_summary.json", "kind": "result_summary", "role": "postprocessing_summary"},
                {"path": "results/evidence_index.json", "kind": "evidence_index", "role": "evidence_index"},
                {"path": "results/postprocessing_summary.md", "kind": "markdown_report", "role": "human_readable_summary"},
            ])

        warnings: list[str] = [
            "No solver execution was performed.",
            "This is a preflight plan only. Solver execution requires external CalculiX setup.",
        ]
        if not ready_to_run:
            warnings.append(f"Run is not ready: {len(missing_items)} item(s) missing.")

        return {
            "ok": True,
            "tool": "cae.prepare_solver_run",
            "ready_to_run": ready_to_run,
            "solver": solver,
            "run_id": run_id,
            "load_case_id": load_case_id,
            "requires_approval": True,
            "solver_execution_performed": False,
            "preflight": {
                "has_mesh": has_mesh,
                "has_solver_settings": has_solver_settings,
                "has_load_case": has_load_case,
                "has_input_deck": has_input_deck,
                "ccx_available": ccx_available,
                "missing_items": missing_items,
            },
            "planned_artifacts": planned_artifacts,
            "warnings": warnings,
        }

    def _tool_cae_run_solver(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        import time as _time
        import subprocess as _subprocess
        import tempfile as _tempfile
        import zipfile as _zipfile
        from pathlib import Path as _Path
        from . import aieng_bridge

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        run_id: str = inp.get("runId") or inp.get("run_id") or "run_001"
        solver: str = inp.get("solver") or "CalculiX"
        load_case_id: str = inp.get("loadCaseId") or inp.get("load_case_id") or "load_case_001"
        input_deck_path_str: str | None = inp.get("inputDeckPath") or inp.get("input_deck_path")
        extract_results: bool = bool(inp.get("extractResults", inp.get("extract_results", True)))
        refresh_summary: bool = bool(inp.get("refreshSummary", inp.get("refresh_summary", True)))
        overwrite: bool = bool(inp.get("overwrite", True))
        timeout_seconds: int = int(inp.get("timeout_seconds", inp.get("timeoutSeconds", 120)))

        # Resolve package path
        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
                "solver_execution_performed": False,
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
                "solver_execution_performed": False,
            }

        # Validate input_deck_path
        if not input_deck_path_str:
            return {
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "missing_input_deck",
                "message": "No input_deck_path provided. Pass the path to the CalculiX .inp file inside the package.",
                "solver_execution_performed": False,
            }

        # Reject absolute paths and path traversal
        normalized = input_deck_path_str.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            return {
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "forbidden_path",
                "message": "input_deck_path must be a relative path inside the package and must not contain '..' or start with a separator.",
                "solver_execution_performed": False,
            }

        if not input_deck_path_str.lower().endswith(".inp"):
            return {
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "invalid_input_deck",
                "message": "input_deck_path must end with .inp",
                "solver_execution_performed": False,
            }

        # Verify input deck exists in package
        try:
            with _zipfile.ZipFile(package_path, "r") as zf:
                names = set(zf.namelist())
                if input_deck_path_str not in names:
                    return {
                        "ok": False,
                        "tool": "cae.run_solver",
                        "status": "error",
                        "code": "input_deck_not_found",
                        "message": f"Input deck not found in package: {input_deck_path_str}",
                        "solver_execution_performed": False,
                    }
                inp_data = zf.read(input_deck_path_str)
        except Exception as exc:
            return {
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "package_read_error",
                "message": f"Failed to read package: {exc}",
                "solver_execution_performed": False,
            }

        # Locate ccx
        ccx_cmd = None
        for candidate in ("ccx", "ccx_linux", "ccx2.21", "ccx_static"):
            ccx_cmd = shutil.which(candidate)
            if ccx_cmd:
                break

        if not ccx_cmd:
            return {
                "ok": False,
                "tool": "cae.run_solver",
                "status": "error",
                "code": "solver_not_found",
                "message": "CalculiX executable (ccx) not found on PATH.",
                "solver_execution_performed": False,
            }

        # Run solver in a temp directory
        started_at = datetime.now(timezone.utc).isoformat()
        start_ts = _time.monotonic()
        temp_dir = _tempfile.mkdtemp(prefix="aieng_solver_")
        work_dir = _Path(temp_dir)
        changed_artifacts: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []
        frd_path: _Path | None = None
        return_code: int | None = None
        stdout = ""
        stderr = ""

        try:
            stem = _Path(input_deck_path_str).stem
            local_inp = work_dir / f"{stem}.inp"
            local_inp.write_bytes(inp_data)

            try:
                proc = _subprocess.run(
                    [ccx_cmd, stem],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    shell=False,
                )
                return_code = proc.returncode
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
            except _subprocess.TimeoutExpired as exc:
                return_code = -1
                stdout = exc.stdout.decode() if exc.stdout else ""
                stderr = exc.stderr.decode() if exc.stderr else ""
                errors.append(f"Solver timed out after {timeout_seconds} seconds.")
                warnings.append("Solver execution was terminated due to timeout.")
            except Exception as exc:
                return {
                    "ok": False,
                    "tool": "cae.run_solver",
                    "status": "error",
                    "code": "solver_subprocess_error",
                    "message": f"Failed to run solver subprocess: {exc}",
                    "solver_execution_performed": False,
                }

            finished_at = datetime.now(timezone.utc).isoformat()
            duration_seconds = round(_time.monotonic() - start_ts, 3)

            # Write solver log
            log_path = work_dir / "solver_log.txt"
            log_path.write_text(
                f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n=== RETURN CODE ===\n{return_code}\n",
                encoding="utf-8",
            )

            solved = return_code == 0
            # Conservative: don't claim convergence without reliable evidence
            converged = None

            # Locate generated FRD in temp working directory
            result_frd = work_dir / f"{stem}.frd"
            if result_frd.exists():
                frd_path = result_frd

            # Build solver_run.json
            solver_run = {
                "run_id": run_id,
                "solver": solver,
                "state": "completed" if solved else "failed",
                "solved": solved,
                "converged": converged,
                "return_code": return_code,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": duration_seconds,
                "input_files": [input_deck_path_str],
                "output_files": [],
                "log_file": f"simulation/runs/{run_id}/solver_log.txt",
                "warnings": warnings,
                "errors": errors,
            }
            if frd_path:
                solver_run["output_files"].append(f"simulation/runs/{run_id}/outputs/result.frd")

            # Write artifacts back into package
            run_prefix = f"simulation/runs/{run_id}"

            def _write_safe(artifact_path: str, source: _Path) -> None:
                try:
                    art = write_artifact_to_package(
                        package_path, artifact_path, source, overwrite=overwrite
                    )
                    changed_artifacts.append(art)
                except FileExistsError:
                    warnings.append(f"{artifact_path} already exists and overwrite=False")
                except Exception as exc:
                    warnings.append(f"Failed to write {artifact_path}: {exc}")

            _write_safe(f"{run_prefix}/solver_input.inp", local_inp)
            _write_safe(f"{run_prefix}/solver_log.txt", log_path)

            run_json_path = work_dir / "solver_run.json"
            run_json_path.write_text(json.dumps(solver_run, indent=2), encoding="utf-8")
            _write_safe(f"{run_prefix}/solver_run.json", run_json_path)

            if frd_path:
                _write_safe(f"{run_prefix}/outputs/result.frd", frd_path)

            # Extract FRD results if requested
            extracted_metrics: dict[str, Any] | None = None
            if extract_results and frd_path:
                try:
                    ext_result = aieng_bridge.extract_frd_solver_results(
                        str(package_path),
                        str(frd_path),
                        aieng_root=active_settings.aieng_root,
                        load_case_id=load_case_id,
                        software=solver,
                        overwrite=overwrite,
                    )
                    extracted_metrics = ext_result.get("metrics")
                    changed_artifacts.extend(ext_result.get("artifacts", []))
                except Exception as exc:
                    warnings.append(f"FRD extraction failed: {exc}")

            # Refresh summaries if requested
            refreshed_summaries: list[str] = []
            if refresh_summary:
                try:
                    aieng_bridge.refresh_cae_result_summary(
                        str(package_path),
                        aieng_root=active_settings.aieng_root,
                        overwrite=True,
                    )
                    refreshed_summaries.append("result_summary")
                except Exception as exc:
                    warnings.append(f"CAE result summary refresh failed: {exc}")

                try:
                    aieng_bridge.refresh_preprocessing_summary(
                        str(package_path),
                        aieng_root=active_settings.aieng_root,
                        overwrite=True,
                    )
                    refreshed_summaries.append("preprocessing_summary")
                except Exception as exc:
                    warnings.append(f"Preprocessing summary refresh failed: {exc}")

            result: dict[str, Any] = {
                "ok": True,
                "tool": "cae.run_solver",
                "status": "completed" if solved else "failed",
                "solver_execution_performed": True,
                "return_code": return_code,
                "changed_artifacts": changed_artifacts,
                "warnings": warnings,
                "errors": errors,
            }
            if extracted_metrics is not None:
                result["extracted_metrics"] = extracted_metrics
            if refreshed_summaries:
                result["refreshed_summaries"] = refreshed_summaries
            return result

        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def _tool_freecad_run_macro(_inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        # The execute_run() approval gate should prevent this from being called.
        # This body is a defensive belt-and-suspenders guard.
        raise RuntimeError(
            "freecad.run_macro reached execution without approval — approval gate bypassed"
        )

    _rt.register_tool(
        "aieng.inspect_package",
        _tool_inspect_package,
        description="Inspect .aieng package and return full project semantic summary",
    )
    _rt.register_tool(
        "aieng.refresh_semantics",
        _tool_refresh_semantics,
        description="Re-validate package and refresh semantic state",
    )
    _rt.register_tool(
        "aieng.generate_preview",
        _tool_generate_preview,
        description="Generate 3-D web preview asset (GLB or STL)",
    )
    _rt.register_tool(
        "aieng.read_audit_log",
        _tool_read_audit_log,
        description="Return the most recent audit log entries for this project",
    )
    _rt.register_tool(
        "freecad.inspect_geometry",
        _tool_freecad_inspect_geometry,
        description="Inspect CAD geometry via FreeCADCmd: face/edge/vertex counts, bounding box, volume",
    )
    _rt.register_tool(
        "freecad.export_step",
        _tool_freecad_export_step,
        description="Export CAD geometry to STEP format via FreeCADCmd; returns artifact refs",
    )
    _rt.register_tool(
        "postprocess.generate_computed_metrics",
        _tool_generate_computed_metrics,
        description="Normalize external post-processing metrics into computed_metrics.json",
    )
    _rt.register_tool(
        "postprocess.refresh_cae_summary",
        _tool_refresh_cae_summary,
        description="Regenerate CAE result summary, evidence index, and markdown inside the .aieng package",
    )
    _rt.register_tool(
        "mcp.check",
        _tool_mcp_check,
        description="Check MCP guardrails, capability gaps, and operation policy for this project",
    )
    _rt.register_tool(
        "mcp.parse_patch",
        _tool_mcp_parse_patch,
        description="Parse an .aieng patch proposal without executing it",
    )
    _rt.register_tool(
        "mcp.prepare_execution",
        _tool_mcp_prepare_execution,
        description="Dry-run an .aieng patch proposal and return preflight side effects",
    )
    _rt.register_tool(
        "cae.apply_setup_patch",
        _tool_cae_apply_setup_patch,
        description=(
            "Apply a controlled patch to CAE setup artifacts inside a .aieng package. "
            "Supports create_file, replace_json, merge_object, append_array_item. "
            "Writes only to allowed setup paths; rejects results/ and path traversal."
        ),
    )
    _rt.register_tool(
        "cae.extract_solver_results",
        _tool_cae_extract_solver_results,
        description=(
            "Parse a CalculiX FRD result file and write computed_metrics.json "
            "(max displacement, max von Mises stress) into a .aieng package. "
            "Extracts real numerical extrema from per-node field data."
        ),
    )
    _rt.register_tool(
        "cae.prepare_solver_run",
        _tool_cae_prepare_solver_run,
        description=(
            "Inspect a .aieng package and return a reviewable solver run preflight plan. "
            "Checks for mesh, solver settings, load case, and input deck presence. "
            "No solver is executed; returns requires_approval=true and solver_execution_performed=false."
        ),
    )
    _rt.register_tool(
        "cae.run_solver",
        _tool_cae_run_solver,
        requires_approval=True,
        description=(
            "Execute an external CalculiX solver run on an existing input deck. "
            "Copies the .inp into a temp directory, runs ccx with a timeout, "
            "captures stdout/stderr, and writes solver_run.json, solver_log.txt, "
            "and result.frd back into the .aieng package. "
            "Requires explicit approval before execution."
        ),
    )
    _rt.register_tool(
        "freecad.run_macro",
        _tool_freecad_run_macro,
        requires_approval=True,
        description="Run a FreeCAD macro (requires explicit approval; potentially destructive)",
    )

    # Configure file-backed run persistence
    _rt.configure(
        Path(
            os.environ.get(
                "AIENG_RUNTIME_STATE_DIR",
                str(active_settings.data_root / "runtime" / "runs"),
            )
        )
    )

    # ── runtime endpoints ─────────────────────────────────────────────────────

    @app.get("/api/runtime/tools")
    def list_runtime_tools() -> list[dict[str, Any]]:
        return _rt.registered_tools_info()

    @app.get("/api/runtime/runs")
    def list_runtime_runs() -> list[dict[str, Any]]:
        runs = _rt.get_all_runs(limit=50)
        return [_rt.run_to_summary_dict(r) for r in runs]

    @app.post("/api/runtime/runs")
    def create_runtime_run(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        data = payload or {}
        run = _rt.RunRecord(
            run_id=uuid.uuid4().hex[:12],
            message=str(data.get("message") or "").strip(),
            created_at=now_iso(),
            status="pending",
            project_id=data.get("project_id") or None,
            package_path=data.get("package_path") or None,
        )
        ctx: dict[str, Any] = {"project_id": run.project_id}
        if "tool_input" in data and isinstance(data["tool_input"], dict):
            ctx["tool_input"] = data["tool_input"]
        if data.get("workflow_id"):
            ctx["workflow_id"] = data.get("workflow_id")
        if "llm_config" in data and isinstance(data["llm_config"], dict):
            # Keep raw API keys out of run records.
            ctx["llm_config"] = {k: v for k, v in data["llm_config"].items() if k != "api_key"}
        if isinstance(data.get("steps"), list):
            _rt.execute_run_with_plan(run, data["steps"], ctx)
        elif data.get("workflow_id"):
            workflows = {wf["id"]: wf for wf in agent_workbench.list_workflows()}
            workflow = workflows.get(str(data["workflow_id"]))
            if workflow is None:
                run.status = "failed"
                run.errors.append(f"workflow not found: {data['workflow_id']}")
                _rt.store_run(run)
            else:
                _rt.execute_run_with_plan(run, workflow.get("steps") or [], ctx)
        else:
            _rt.execute_run(run, ctx)
        all_artifacts = [
            a for tr in run.tool_results for a in tr.artifacts
        ]
        audit_payload: dict[str, Any] = {
            "kind": "runtime_run",
            "run_id": run.run_id,
            "message": run.message,
            "project_id": run.project_id,
            "tools": [tc.name for tc in run.tool_calls],
            "status": run.status,
            "errors": run.errors,
            "created_at": run.created_at,
            "artifacts": all_artifacts,
        }
        if run.project_id:
            try:
                write_audit_log(active_settings, run.project_id, "runtime_run", audit_payload)
            except Exception:
                pass
        return _rt.run_to_dict(run)

    @app.get("/api/runtime/runs/{run_id}")
    def get_runtime_run(run_id: str) -> dict[str, Any]:
        run = _rt.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _rt.run_to_dict(run)

    @app.get("/api/runtime/runs/{run_id}/events")
    def get_runtime_run_events(run_id: str) -> list[dict[str, Any]]:
        run = _rt.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return [
            {
                "id": e.id,
                "run_id": e.run_id,
                "type": e.type,
                "timestamp": e.timestamp,
                "payload": e.payload,
            }
            for e in run.events
        ]

    @app.post("/api/runtime/runs/{run_id}/approve")
    def approve_runtime_run(run_id: str) -> dict[str, Any]:
        run = _rt.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run.status != "awaiting_approval":
            raise HTTPException(
                status_code=409,
                detail=f"run is not awaiting approval (current status: {run.status})",
            )
        run = _rt.resume_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found after resume")
        if run.project_id:
            try:
                write_audit_log(
                    active_settings,
                    run.project_id,
                    "runtime_run",
                    {
                        "kind": "runtime_run_approved",
                        "run_id": run.run_id,
                        "status": run.status,
                        "created_at": now_iso(),
                    },
                )
            except Exception:
                pass
        return _rt.run_to_dict(run)

    @app.post("/api/runtime/runs/{run_id}/reject")
    def reject_runtime_run(run_id: str) -> dict[str, Any]:
        run = _rt.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run.status != "awaiting_approval":
            raise HTTPException(
                status_code=409,
                detail=f"run is not awaiting approval (current status: {run.status})",
            )
        run = _rt.reject_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found after reject")
        if run.project_id:
            try:
                write_audit_log(
                    active_settings,
                    run.project_id,
                    "runtime_run",
                    {
                        "kind": "runtime_run_rejected",
                        "run_id": run.run_id,
                        "status": run.status,
                        "created_at": now_iso(),
                    },
                )
            except Exception:
                pass
        return _rt.run_to_dict(run)

    return app


app = create_app()
