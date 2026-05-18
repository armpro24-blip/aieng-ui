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


def _load_freecad_mcp_registry_entries(settings: Settings) -> list[dict[str, Any]]:
    """Load freecad-mcp registry metadata without requiring an installed package."""
    mcp_src = settings.freecad_mcp_root / "src"
    if not mcp_src.exists():
        return []
    injected = False
    candidate = str(mcp_src)
    try:
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        for module_name in ("freecad_mcp.tool_registry", "freecad_mcp"):
            module = sys.modules.get(module_name)
            module_file = getattr(module, "__file__", None)
            if module_file is None:
                continue
            try:
                Path(module_file).resolve().relative_to(mcp_src.resolve())
            except ValueError:
                sys.modules.pop(module_name, None)
        from freecad_mcp.tool_registry import default_registry

        return [entry.model_dump(mode="json") for entry in default_registry().list_all()]
    except Exception:
        return []
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
# Solver input deck import (Phase 29)
# ---------------------------------------------------------------------------
# Closes the biggest functional gap in the vertical CAE MVP: the runtime
# previously assumed a `.inp` deck already existed inside the package. This
# importer accepts a pre-existing deck (typically authored externally) and
# writes it into the canonical run path so cae.run_solver can find it.
#
# This is import only — no mesh generation, no input deck generation, no
# physical correctness validation. The minimal parse below just confirms
# CalculiX keyword syntax is plausible; it does not validate the analysis.

_SOLVER_INPUT_MAX_BYTES = 10 * 1024 * 1024
_RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _is_safe_run_id(run_id: str) -> bool:
    return bool(_RUN_ID_PATTERN.match(run_id))


def _parse_calculix_input_deck(text: str) -> dict[str, Any]:
    """Minimal CalculiX `.inp` keyword scan.

    Returns ``{"keywords": [...], "keyword_count": N, "warnings": [...]}``.
    Detects CalculiX keyword lines (lines starting with ``*`` and not ``**``
    which is a comment). Does NOT validate the analysis: card order, parameter
    values, mesh consistency, and material laws are all out of scope.
    """
    keywords: list[str] = []
    warnings: list[str] = []
    saw_step = False
    saw_node = False

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("**"):
            continue
        if not stripped.startswith("*"):
            continue
        head = stripped[1:].split(",", 1)[0].strip().upper()
        if not head:
            continue
        keywords.append(head)
        if head == "STEP":
            saw_step = True
        elif head == "NODE":
            saw_node = True

    if not keywords:
        warnings.append("no CalculiX keywords (lines starting with '*') detected")
    if not saw_node:
        warnings.append("no *NODE block detected; deck may be incomplete")
    if not saw_step:
        warnings.append("no *STEP block detected; deck may be incomplete")

    return {
        "keywords": keywords,
        "keyword_count": len(keywords),
        "warnings": warnings,
    }


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
_GEOMETRY_STALE_ARTIFACTS = [
    "geometry/topology_map.json",
    "graph/aag.json",
    "graph/feature_graph.json",
    "objects/interface_graph.json",
    "objects/object_registry.json",
    "visual/annotation_layers.json",
    "visual/model_manifest.json",
    "simulation/mesh_handoff_contract.json",
    "simulation/mesh/mesh_metadata.json",
    "results/computed_metrics.json",
    "results/field_regions.json",
    "results/field_summary.json",
    "results/field_summary.md",
    *_SETUP_STALE_ARTIFACTS,
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
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """
    Apply all patches atomically to the package; returns (changed_paths, warning_msgs, artifact_diffs).
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
    artifact_diffs: list[dict[str, Any]] = []

    for patch in patches:
        path: str = patch.get("path", "")
        existing_bytes: bytes | None = members.get(path)
        new_bytes = _apply_single_patch(existing_bytes, patch, path)
        members[path] = new_bytes
        changed_paths.append(path)

        action = patch.get("action_type") or patch.get("operation") or ""
        diff_meta: dict[str, Any] = {
            "path": path,
            "operation": action,
            "json_pointer": patch.get("pointer", ""),
        }

        if action == "create_file":
            diff_meta["before"] = None
            diff_meta["after"] = patch.get("content")
            diff_meta["changed_paths"] = []
            diff_meta["added_paths"] = [""]
            diff_meta["removed_paths"] = []
        elif action in ("replace_json", "merge_object", "append_array_item"):
            before_doc = json.loads(existing_bytes) if existing_bytes else None
            after_doc = json.loads(new_bytes)
            changed, added, removed = _json_diff_paths(before_doc, after_doc)
            diff_meta["changed_paths"] = changed
            diff_meta["added_paths"] = added
            diff_meta["removed_paths"] = removed
            if action == "replace_json" and patch.get("pointer"):
                pointer_str = patch.get("pointer", "")
                tokens = _parse_json_pointer(pointer_str)
                diff_meta["before"] = _json_pointer_get(before_doc, tokens) if before_doc is not None else None
                diff_meta["after"] = _json_pointer_get(after_doc, tokens)
            else:
                diff_meta["before"] = before_doc
                diff_meta["after"] = after_doc
        else:
            # Fallback for any future actions
            diff_meta["before"] = None
            diff_meta["after"] = None
            diff_meta["changed_paths"] = []
            diff_meta["added_paths"] = []
            diff_meta["removed_paths"] = []

        artifact_diffs.append(diff_meta)

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

    return changed_paths, warnings_out, artifact_diffs


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


def _feature_list_from_graph(feature_graph: dict[str, Any]) -> list[dict[str, Any]]:
    features = feature_graph.get("features", [])
    if isinstance(features, dict):
        return [v for v in features.values() if isinstance(v, dict)]
    if isinstance(features, list):
        return [v for v in features if isinstance(v, dict)]
    return []


def _validate_cad_parameter_edit_contract(
    package_path: Path,
    feature_id: str,
    parameter_name: str,
    new_value: Any,
) -> dict[str, Any]:
    with zipfile.ZipFile(package_path, "r") as zf:
        names = set(zf.namelist())
        if "graph/feature_graph.json" not in names:
            raise ValueError("graph/feature_graph.json missing; cannot validate editable CAD parameter")
        feature_graph = json.loads(zf.read("graph/feature_graph.json"))

    feature = next((f for f in _feature_list_from_graph(feature_graph) if f.get("id") == feature_id), None)
    if feature is None:
        raise ValueError(f"feature_id not found in feature graph: {feature_id}")

    params = feature.get("parameters", [])
    if isinstance(params, dict):
        params = [{"name": k, **(v if isinstance(v, dict) else {"current_value": v})} for k, v in params.items()]
    if not isinstance(params, list):
        raise ValueError(f"feature {feature_id} does not declare editable parameters")

    param = next(
        (
            p for p in params
            if isinstance(p, dict)
            and (p.get("name") == parameter_name or p.get("freecad_parameter_name") == parameter_name)
        ),
        None,
    )
    if param is None:
        raise ValueError(f"parameter {parameter_name!r} is not declared on feature {feature_id!r}")

    editability = param.get("editability")
    if editability is False or (isinstance(editability, dict) and editability.get("executable") is False):
        raise ValueError(f"parameter {parameter_name!r} on feature {feature_id!r} is not editable")

    if isinstance(new_value, (int, float)) and not isinstance(new_value, bool):
        min_value = param.get("min_value")
        max_value = param.get("max_value")
        if min_value is not None and new_value < min_value:
            raise ValueError(f"new_value {new_value!r} is below min_value {min_value!r}")
        if max_value is not None and new_value > max_value:
            raise ValueError(f"new_value {new_value!r} is above max_value {max_value!r}")

    return {
        "feature": feature,
        "parameter": param,
        "freecad_object_name": feature.get("freecad_object_name") or feature_id,
        "freecad_parameter_name": param.get("freecad_parameter_name") or parameter_name,
    }


def _write_modified_step_into_package(
    package_path: Path,
    source_step: Path,
    *,
    feature_id: str,
    parameter_name: str,
) -> str:
    if not source_step.exists():
        raise FileNotFoundError(f"modified STEP artifact not found: {source_step}")
    safe_feature = "".join(c if c.isalnum() or c in "._-" else "_" for c in feature_id)
    safe_param = "".join(c if c.isalnum() or c in "._-" else "_" for c in parameter_name)
    dest = f"geometry/modified_{safe_feature}_{safe_param}.step"

    with zipfile.ZipFile(package_path, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != dest]
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in zf.namelist() else {"resources": {}}

    resources = manifest.setdefault("resources", {})
    geometry = resources.setdefault("geometry", {})
    if not isinstance(geometry, dict):
        geometry = {}
        resources["geometry"] = geometry
    geometry["modified"] = dest

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=package_path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            seen: set[str] = set()
            for info, data in members:
                if info.filename in seen or info.filename == "manifest.json":
                    continue
                seen.add(info.filename)
                out_zf.writestr(info, data)
            if "geometry/" not in seen:
                out_zf.writestr("geometry/", b"")
            out_zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            out_zf.writestr(dest, source_step.read_bytes())
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return dest


def _unpack_geometry_from_package(package_path: Path, internal_path: str) -> Path:
    """Extract a geometry file from inside a .aieng package to a temporary file.

    Returns the path to the temporary file. The caller is responsible for cleanup.
    """
    with zipfile.ZipFile(package_path, "r") as zf:
        data = zf.read(internal_path)
    temp_dir = Path(tempfile.mkdtemp(prefix="aieng_mesh_geometry_"))
    temp_path = temp_dir / Path(internal_path).name
    temp_path.write_bytes(data)
    return temp_path


def _write_mesh_into_package_atomic(
    package_path: Path,
    mesh_file: Path,
    internal_path: str,
) -> str:
    """Atomically write a mesh file into a .aieng package.

    Reads all existing members, updates manifest.resources.simulation.mesh,
    writes a new ZIP, and moves it over the original.
    """
    with zipfile.ZipFile(package_path, "r") as zf:
        members = [(info, b"" if info.is_dir() else zf.read(info.filename)) for info in zf.infolist() if info.filename != internal_path]
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in zf.namelist() else {"resources": {}}

    resources = manifest.setdefault("resources", {})
    sim = resources.setdefault("simulation", {})
    if not isinstance(sim, dict):
        sim = {}
        resources["simulation"] = sim
    sim["mesh"] = internal_path

    with tempfile.NamedTemporaryFile(delete=False, suffix=".aieng", dir=package_path.parent) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            seen: set[str] = set()
            for info, data in members:
                if info.filename in seen or info.filename == "manifest.json":
                    continue
                seen.add(info.filename)
                out_zf.writestr(info, data)
            if "simulation/" not in seen:
                out_zf.writestr("simulation/", b"")
            if "simulation/mesh/" not in seen:
                out_zf.writestr("simulation/mesh/", b"")
            out_zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            out_zf.writestr(internal_path, mesh_file.read_bytes())
        shutil.move(str(tmp_path), package_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return internal_path


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
        # Check whether a real FRD exists so solver_fields can advertise the
        # correct format upfront.
        _has_frd = False
        if package_path and package_path.exists():
            _has_frd = _resolve_frd_in_package(package_path) is not None

        _available_fields = list(_cae.get("available_fields") or [])
        _real_field_cache: dict[str, dict[str, Any]] = {}
        if _has_frd and package_path and package_path.exists():
            for candidate in ("stress", "displacement"):
                try:
                    real_field = _extract_frd_field_data(package_path, candidate, settings.aieng_root)
                except Exception:
                    real_field = None
                if real_field is not None:
                    _real_field_cache[candidate] = real_field
                    if candidate not in _available_fields:
                        _available_fields.append(candidate)
        _cae["available_fields"] = _available_fields
        if _has_frd:
            _cae["present"] = True
            _cae["results_available"] = True

        _solver_fields: list[dict[str, Any]] = []
        for f in _available_fields:
            _meta = _field_defaults.get(f, {"min_value": 0.0, "max_value": 1.0, "unit": ""})
            _field_entry: dict[str, Any] = {
                "field_name": f,
                "descriptor_url": f"/api/projects/{project_id}/fields/{f}",
                **_meta,
                "format": "vertex_json" if _has_frd else "vertex_synthetic",
                "available": True,
            }
            # If FRD is present, try to fetch real extrema so the frontend
            # legend is accurate before the first descriptor fetch.
            if _has_frd:
                try:
                    _real = _real_field_cache.get(f)
                    if _real is None and package_path and package_path.exists():
                        _real = _extract_frd_field_data(package_path, f, settings.aieng_root)
                    if _real is not None:
                        _field_entry["min_value"] = _real["min_value"]
                        _field_entry["max_value"] = _real["max_value"]
                        _field_entry["unit"] = _real["unit"]
                except Exception:
                    pass
            _solver_fields.append(_field_entry)
        _cae["solver_fields"] = _solver_fields
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


def _resolve_frd_in_package(package_path: Path) -> str | None:
    """Find the newest result.frd inside a .aieng package."""
    if not package_path.exists():
        return None
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            candidates = [
                name for name in zf.namelist()
                if name.endswith("/outputs/result.frd")
            ]
            if not candidates:
                return None
            # Pick the lexicographically last run (run_002 > run_001)
            return sorted(candidates)[-1]
    except zipfile.BadZipFile:
        return None


def _extract_frd_field_data(
    package_path: Path,
    field_name: str,
    aieng_root: Path,
) -> dict[str, Any] | None:
    """Extract per-node scalar values and coordinates from an FRD inside a package.

    Returns a dict with ``values``, ``node_coords``, ``min_value``,
    ``max_value``, ``unit``, ``warnings`` — or ``None`` if no usable FRD.
    """
    frd_entry = _resolve_frd_in_package(package_path)
    if frd_entry is None:
        return None

    # Extract FRD to temp file for parsing
    try:
        with zipfile.ZipFile(package_path, "r") as zf:
            frd_bytes = zf.read(str(frd_entry))
    except (KeyError, zipfile.BadZipFile):
        return None

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".frd", delete=False) as fh:
        fh.write(frd_bytes)
        temp_frd = Path(fh.name)

    try:
        aieng_src = aieng_root / "src"
        injected = False
        if str(aieng_src) not in sys.path:
            sys.path.insert(0, str(aieng_src))
            injected = True

        from aieng.simulation.frd_result_extractor import parse_frd
        from aieng.simulation.field_region_extractor import _extract_node_coords_from_frd

        fields = parse_frd(temp_frd)
        coords = _extract_node_coords_from_frd(temp_frd)
        if not coords:
            return None

        import math

        warnings: list[str] = []
        values: dict[int, float] = {}
        unit = ""

        if field_name == "stress":
            s_field = fields.get("S")
            if not s_field:
                warnings.append("S (stress tensor) field not found in FRD.")
                return None
            node_data = s_field["node_data"]
            for nid, vals in node_data.items():
                if nid not in coords:
                    continue
                if len(vals) < 6 or any(v is None for v in vals[:6]):
                    continue
                sxx, syy, szz = float(vals[0]), float(vals[1]), float(vals[2])
                sxy, sxz, syz = float(vals[3]), float(vals[4]), float(vals[5])
                vm = math.sqrt(
                    0.5 * (
                        (sxx - syy) ** 2
                        + (syy - szz) ** 2
                        + (szz - sxx) ** 2
                        + 6.0 * (sxy ** 2 + sxz ** 2 + syz ** 2)
                    )
                )
                values[nid] = vm
            unit = "MPa"

        elif field_name == "displacement":
            disp = fields.get("DISP")
            if not disp:
                warnings.append("DISP field not found in FRD.")
                return None
            components = disp["components"]
            node_data = disp["node_data"]
            all_idx = next((i for i, c in enumerate(components) if c == "ALL"), None)
            d1_idx = next((i for i, c in enumerate(components) if c == "D1"), None)
            d2_idx = next((i for i, c in enumerate(components) if c == "D2"), None)
            d3_idx = next((i for i, c in enumerate(components) if c == "D3"), None)
            for nid, vals in node_data.items():
                if nid not in coords:
                    continue
                if all_idx is not None and all_idx < len(vals) and vals[all_idx] is not None:
                    v = abs(float(vals[all_idx]))
                elif (
                    d1_idx is not None and d2_idx is not None and d3_idx is not None
                    and all(idx < len(vals) and vals[idx] is not None for idx in (d1_idx, d2_idx, d3_idx))
                ):
                    v = math.sqrt(
                        float(vals[d1_idx]) ** 2
                        + float(vals[d2_idx]) ** 2
                        + float(vals[d3_idx]) ** 2
                    )
                else:
                    continue
                values[nid] = v
            unit = "mm"
        else:
            warnings.append(f"Field '{field_name}' is not supported for FRD extraction.")
            return None

        if not values:
            warnings.append(f"No valid '{field_name}' values could be extracted from FRD.")
            return None

        # Sort by node_id for stable ordering
        sorted_ids = sorted(values.keys())
        value_list = [values[nid] for nid in sorted_ids]
        coord_list = [list(coords[nid]) for nid in sorted_ids]
        min_val = min(value_list)
        max_val = max(value_list)

        return {
            "values": value_list,
            "node_coords": coord_list,
            "min_value": min_val,
            "max_value": max_val,
            "unit": unit,
            "warnings": warnings,
        }
    except Exception:
        return None
    finally:
        try:
            temp_frd.unlink(missing_ok=True)
        except OSError:
            pass
        if injected:
            try:
                sys.path.remove(str(aieng_src))
            except ValueError:
                pass


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

    @app.post("/api/llm/test")
    def test_llm_provider_endpoint(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
        data = payload or {}
        llm_config = agent_engine.sanitize_llm_config(data.get("llm_config"))
        if not llm_config:
            raise HTTPException(status_code=400, detail="llm_config is required")
        verify = bool(data.get("verify_connection", False))
        return agent_engine.test_llm_provider(active_settings, llm_config, verify_connection=verify)

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

    @app.get("/api/agent/connections")
    def list_agent_connections() -> list[dict[str, Any]]:
        return agent_workbench.list_chat_connections(active_settings)

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

    @app.post("/api/projects/{project_id}/solver-input")
    def import_solver_input(
        project_id: str,
        payload: dict[str, Any] = Body(default=None),
    ) -> dict[str, Any]:
        """Import a CalculiX `.inp` solver input deck into the package.

        Phase 29 — closes the biggest functional gap in the vertical CAE MVP
        (the runtime previously required a pre-existing deck inside the
        package). This endpoint writes a caller-supplied deck to the
        canonical run path so ``cae.run_solver`` and ``cae.prepare_solver_run``
        can find it.

        Import only. Does NOT execute the solver, generate a mesh, generate a
        deck, or validate physical correctness. The minimal parse below just
        scans for CalculiX keyword lines so obviously empty or wrong-format
        bodies are rejected with a 400.

        Body:
            ``text`` (str, required): the `.inp` content as utf-8 text.
            ``run_id`` (str, optional): defaults to ``"run_001"``.
                Must match ``^[a-zA-Z0-9_-]{1,64}$``.
            ``overwrite`` (bool, optional): defaults to ``True``.

        Returns:
            ``{ok, package_path, artifact, keyword_count, keywords, warnings}``.
            The deck lands at ``simulation/runs/{run_id}/solver_input.inp``.
        """
        body = payload or {}
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(
                status_code=400,
                detail="body must contain a non-empty 'text' string with the .inp content",
            )
        size_bytes = len(text.encode("utf-8"))
        if size_bytes > _SOLVER_INPUT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"solver input deck {size_bytes} bytes exceeds cap "
                    f"{_SOLVER_INPUT_MAX_BYTES}"
                ),
            )
        run_id = str(body.get("run_id") or "run_001")
        if not _is_safe_run_id(run_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    "run_id must match ^[a-zA-Z0-9_-]{1,64}$ "
                    "(no path separators, no traversal)"
                ),
            )
        overwrite = bool(body.get("overwrite", True))

        parse = _parse_calculix_input_deck(text)
        if parse["keyword_count"] == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "no CalculiX keywords found in body 'text'; "
                    "expected at least one line starting with '*'"
                ),
            )

        project = get_project(active_settings, project_id)
        package_path = resolve_project_path(
            active_settings, project_id, project.get("aieng_file")
        )
        if package_path is None or not package_path.exists():
            raise HTTPException(status_code=404, detail=".aieng package not found")

        artifact_path = f"simulation/runs/{run_id}/solver_input.inp"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".inp", delete=False, encoding="utf-8", newline=""
        ) as fh:
            fh.write(text)
            tmp_path = Path(fh.name)
        try:
            try:
                artifact = write_artifact_to_package(
                    package_path,
                    artifact_path,
                    tmp_path,
                    overwrite=overwrite,
                )
            except FileExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)

        artifact["kind"] = "solver_input"
        artifact["role"] = "solver_input_deck"
        artifact["size_bytes"] = size_bytes
        artifact.pop("source_path", None)

        return {
            "ok": True,
            "package_path": str(package_path),
            "run_id": run_id,
            "artifact": artifact,
            "keyword_count": parse["keyword_count"],
            "keywords": parse["keywords"],
            "warnings": parse["warnings"],
        }

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
        project = get_project(active_settings, project_id)
        _known: dict[str, dict[str, Any]] = {
            "stress": {"min_value": 0.0, "max_value": 250.0, "unit": "MPa", "colormap": "thermal"},
            "displacement": {"min_value": 0.0, "max_value": 5.0, "unit": "mm", "colormap": "coolwarm"},
        }
        meta = _known.get(field_name, {"min_value": 0.0, "max_value": 1.0, "unit": "", "colormap": "thermal"})

        # Attempt real FRD extraction
        pkg = resolve_project_path(active_settings, project_id, project.get("aieng_file"))
        frd_data: dict[str, Any] | None = None
        if pkg is not None and pkg.exists():
            try:
                frd_data = _extract_frd_field_data(pkg, field_name, active_settings.aieng_root)
            except Exception:
                frd_data = None

        if frd_data is not None:
            return {
                "field_name": field_name,
                "project_id": project_id,
                "format": "vertex_json",
                "basis": "frd_nearest_node",
                "min_value": frd_data["min_value"],
                "max_value": frd_data["max_value"],
                "unit": frd_data["unit"],
                "colormap": meta["colormap"],
                "source": "frd",
                "values": frd_data["values"],
                "node_coords": frd_data["node_coords"],
                "warnings": frd_data["warnings"],
            }

        # Fallback to synthetic
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
            changed_paths, apply_warnings, artifact_diffs = _apply_patches_to_package(
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
            "artifact_diffs": artifact_diffs,
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

    def _tool_cae_extract_field_regions(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        frd_path: str | None = inp.get("frdPath") or inp.get("frd_path")
        field: str = inp.get("field") or "S"
        metric: str = inp.get("metric") or "von_mises"
        max_clusters: int = int(inp.get("maxClusters") or inp.get("max_clusters") or 3)
        threshold_percentile: float = float(
            inp.get("thresholdPercentile") or inp.get("threshold_percentile") or 90.0
        )
        overwrite: bool = bool(inp.get("overwrite", False))
        refresh_field_summary: bool = bool(inp.get("refreshFieldSummary", inp.get("refresh_field_summary", True)))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        if not frd_path:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "missing_frd_path",
                "message": "No frdPath provided. Pass the path to the CalculiX .frd result file.",
            }

        if not _Path(package_path_str).exists():
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        if not _Path(frd_path).exists():
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "file_not_found",
                "message": f"FRD file not found: {frd_path}",
            }

        try:
            result = aieng_bridge.extract_field_regions(
                package_path_str,
                frd_path,
                aieng_root=active_settings.aieng_root,
                field=field,
                metric=metric,
                max_clusters=max_clusters,
                threshold_percentile=threshold_percentile,
                overwrite=overwrite,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "extraction_error",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.extract_field_regions",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        field_summary_status = "not_requested"
        refreshed_artifacts: list[dict[str, Any]] = []
        warnings = list(result.get("warnings", []))
        if refresh_field_summary:
            try:
                summary_result = aieng_bridge.write_field_summary(
                    package_path_str,
                    aieng_root=active_settings.aieng_root,
                    overwrite=True,
                )
                refreshed_artifacts = summary_result.get("artifacts", [])
                field_summary_status = summary_result.get("status", "ok")
                if field_summary_status == "skipped":
                    warnings.append(
                        f"Field summary skipped: {summary_result.get('reason', 'aieng.cae_field_summary unavailable')}"
                    )
            except Exception as exc:
                field_summary_status = "error"
                warnings.append(
                    f"Field regions were extracted, but field summary refresh failed: {type(exc).__name__}: {exc}"
                )

        return {
            "ok": True,
            "tool": "cae.extract_field_regions",
            "status": "completed",
            "package_path": package_path_str,
            "out_path": result.get("out_path"),
            "cluster_count": result.get("cluster_count", 0),
            "clusters": result.get("clusters", []),
            "warnings": warnings,
            "artifacts": [
                {
                    "path": result.get("out_path", ""),
                    "kind": "field_regions",
                    "role": "high_magnitude_spatial_clusters",
                }
            ],
            "refreshed_artifacts": refreshed_artifacts,
            "field_summary_status": field_summary_status,
        }

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

    def _tool_cae_generate_solver_input(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        run_id: str = inp.get("runId") or inp.get("run_id") or "run_001"
        overwrite: bool = bool(inp.get("overwrite", False))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.generate_solver_input(
                package_path,
                aieng_root=active_settings.aieng_root,
                run_id=run_id,
                overwrite=overwrite,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "missing_setup",
                "message": str(exc),
                "missing_items": getattr(exc, "missing_items", []),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.generate_solver_input",
                "status": "error",
                "code": "generation_failed",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "cae.generate_solver_input",
            "status": "completed",
            "package_path": str(package_path),
            "out_path": result.get("out_path"),
            "warnings": result.get("warnings", []),
            "artifacts": [
                {
                    "path": result.get("out_path", ""),
                    "kind": "solver_input_deck",
                    "role": "calculix_linear_static_input",
                }
            ],
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
        auto_import_evidence: bool = bool(inp.get("autoImportEvidence", inp.get("auto_import_evidence", True)))

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

            # Auto-import solver evidence (.dat) if solver succeeded and file exists
            auto_import_result: dict[str, Any] | None = None
            if auto_import_evidence and solved:
                dat_path = work_dir / f"{stem}.dat"
                if dat_path.exists():
                    # Ensure evidence scaffold exists before importing
                    try:
                        with _zipfile.ZipFile(package_path, "r") as zf:
                            has_scaffold = (
                                "results/evidence_index.json" in zf.namelist()
                                and "results/claim_map.json" in zf.namelist()
                            )
                    except Exception:
                        has_scaffold = False
                    if not has_scaffold:
                        try:
                            aieng_bridge.write_evidence_scaffold(
                                package_path,
                                aieng_root=active_settings.aieng_root,
                                overwrite=False,
                            )
                        except Exception as exc:
                            warnings.append(f"Auto-scaffold for evidence import failed: {exc}")
                    try:
                        import_result = aieng_bridge.import_solver_evidence(
                            package_path,
                            dat_path,
                            aieng_root=active_settings.aieng_root,
                            result_format="calculix_dat",
                            producer_tool="calculix",
                            claim_support=["claim_solver_result_001"],
                        )
                        auto_import_result = {
                            "status": "ok",
                            "evidence_id": import_result.get("evidence_id"),
                            "artifacts": import_result.get("artifacts", []),
                        }
                        changed_artifacts.extend(import_result.get("artifacts", []))
                    except Exception as exc:
                        warnings.append(f"Auto-import of solver evidence failed: {exc}")
                        auto_import_result = {"status": "error", "message": str(exc)}

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
            if auto_import_result is not None:
                result["auto_import"] = auto_import_result
            return result

        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def _tool_cae_write_mesh_handoff(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))
        handoff_id: str = inp.get("handoffId") or inp.get("handoff_id") or "mesh_handoff_001"

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.write_mesh_handoff(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
                handoff_id=handoff_id,
            )
        except FileNotFoundError as exc:
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "topology_missing",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.write_mesh_handoff",
                "status": "error",
                "code": "handoff_write_failed",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "cae.write_mesh_handoff",
            "status": "completed",
            "package_path": str(package_path),
            "handoff_id": handoff_id,
            "artifacts": result.get("artifacts", []),
        }

    def _tool_aieng_validate(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.validate",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.validate",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.validate_package(
                package_path,
                aieng_root=active_settings.aieng_root,
            )
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.validate",
                "status": "error",
                "code": "validation_failed",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.validate",
            "status": "completed",
            "package_path": str(package_path),
            "validation_ok": result.get("ok"),
            "messages": result.get("messages", []),
            "counts": result.get("counts", {}),
        }

    def _tool_aieng_convert(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        source_path_str: str | None = inp.get("sourcePath") or inp.get("source_path")
        out_path_str: str | None = inp.get("outPath") or inp.get("out_path")
        project_id: str | None = inp.get("project_id")
        converter_id: str | None = inp.get("converterId") or inp.get("converter_id")
        overwrite: bool = bool(inp.get("overwrite", False))
        runtime_mode: str = inp.get("runtimeMode") or inp.get("runtime_mode") or "auto"
        model_id: str | None = inp.get("modelId") or inp.get("model_id")

        # Resolve source_path from project.source_step if not provided
        if not source_path_str and project_id:
            proj = get_project(active_settings, project_id)
            src = resolve_project_path(active_settings, project_id, proj.get("source_step"))
            if src is not None and src.exists():
                source_path_str = str(src)

        if not source_path_str:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "missing_source_path",
                "message": "No source path provided and no project source_step could be resolved.",
            }

        source_path = _Path(source_path_str)
        if not source_path.exists():
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "source_not_found",
                "message": f"Source file not found: {source_path_str}",
            }

        # Resolve out_path: default to project packages dir
        if not out_path_str and project_id:
            proj_name = _Path(source_path_str).stem
            out_path_str = str(project_dir(active_settings, project_id) / "packages" / f"{proj_name}.aieng")

        if not out_path_str:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "missing_out_path",
                "message": "No output path provided and could not infer one from project.",
            }

        out_path = _Path(out_path_str)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            result = aieng_bridge.convert_source_to_package(
                source_path,
                out_path,
                aieng_root=active_settings.aieng_root,
                model_id=model_id,
                converter_id=converter_id,
                overwrite=overwrite,
                runtime_mode=runtime_mode,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "conversion_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.convert",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        # Update project aieng_file if project_id is available
        if project_id:
            try:
                proj = get_project(active_settings, project_id)
                rel_out = project_relpath(active_settings, project_id, out_path)
                proj["aieng_file"] = rel_out
                proj["status"] = "converted"
                save_project(active_settings, proj)
            except Exception:
                pass  # Don't fail the tool if project update fails

        return {
            "ok": True,
            "tool": "aieng.convert",
            "status": "completed",
            "out_path": result.get("out_path"),
            "source_type": result.get("source_type"),
            "converter_id": result.get("converter_id"),
        }

    def _tool_aieng_write_completeness_report(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.write_completeness_report(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "write_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.write_completeness_report",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.write_completeness_report",
            "status": "completed",
            "package_path": str(package_path),
            "artifacts": result.get("artifacts", []),
        }

    def _tool_aieng_update_validation_status(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))
        extra_status: dict[str, Any] | None = inp.get("extraStatus") or inp.get("extra_status")

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.update_validation_status(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
                extra_status=extra_status,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "update_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.update_validation_status",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.update_validation_status",
            "status": "completed",
            "package_path": str(package_path),
            "artifacts": result.get("artifacts", []),
        }

    def _tool_aieng_write_evidence_scaffold(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        overwrite: bool = bool(inp.get("overwrite", False))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            result = aieng_bridge.write_evidence_scaffold(
                package_path,
                aieng_root=active_settings.aieng_root,
                overwrite=overwrite,
            )
        except FileExistsError as exc:
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "scaffold_exists",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "aieng.write_evidence_scaffold",
                "status": "error",
                "code": "scaffold_write_failed",
                "message": str(exc),
            }

        return {
            "ok": True,
            "tool": "aieng.write_evidence_scaffold",
            "status": "completed",
            "package_path": str(package_path),
            "artifacts": result.get("artifacts", []),
        }

    def _tool_cae_import_solver_evidence(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import aieng_bridge
        from pathlib import Path as _Path
        import zipfile as _zipfile

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        result_file: str | None = inp.get("resultFile") or inp.get("result_file")
        result_format: str = inp.get("resultFormat") or inp.get("result_format") or "calculix_dat"
        producer_tool: str = inp.get("producerTool") or inp.get("producer_tool") or "calculix"
        claim_support: list[str] = inp.get("claimSupport") or inp.get("claim_support") or ["claim_solver_result_001"]
        verification_status: str = inp.get("verificationStatus") or inp.get("verification_status") or "unverified"
        evidence_id: str | None = inp.get("evidenceId") or inp.get("evidence_id")
        auto_scaffold: bool = bool(inp.get("autoScaffold", inp.get("auto_scaffold", True)))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        if not result_file:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "missing_result_file",
                "message": "No result file provided. Pass resultFile.",
            }

        result_path = _Path(result_file)
        if not result_path.exists():
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "result_file_not_found",
                "message": f"Result file not found: {result_file}",
            }

        # Check if evidence scaffold is present; auto-create if requested
        scaffold_created = False
        if auto_scaffold:
            try:
                with _zipfile.ZipFile(package_path, "r") as zf:
                    has_scaffold = (
                        "results/evidence_index.json" in zf.namelist()
                        and "results/claim_map.json" in zf.namelist()
                    )
            except Exception:
                has_scaffold = False
            if not has_scaffold:
                try:
                    aieng_bridge.write_evidence_scaffold(
                        package_path,
                        aieng_root=active_settings.aieng_root,
                        overwrite=False,
                    )
                    scaffold_created = True
                except Exception:
                    pass

        try:
            result = aieng_bridge.import_solver_evidence(
                package_path,
                result_path,
                aieng_root=active_settings.aieng_root,
                result_format=result_format,
                producer_tool=producer_tool,
                claim_support=claim_support,
                verification_status=verification_status,
                evidence_id=evidence_id,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "import_validation_failed",
                "message": str(exc),
            }
        except RuntimeError as exc:
            return {
                "ok": False,
                "tool": "cae.import_solver_evidence",
                "status": "error",
                "code": "import_failed",
                "message": str(exc),
            }

        out = {
            "ok": True,
            "tool": "cae.import_solver_evidence",
            "status": "completed",
            "package_path": str(package_path),
            "evidence_id": result.get("evidence_id"),
            "artifacts": result.get("artifacts", []),
            "summary": result.get("summary", {}),
        }
        if scaffold_created:
            out["scaffold_created"] = True
            out.setdefault("warnings", []).append(
                "Evidence scaffold was auto-created because results/evidence_index.json and/or "
                "results/claim_map.json were missing."
            )
        return out

    def _tool_freecad_run_macro(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import freecad_bridge
        from pathlib import Path as _Path

        macro_path: str | None = inp.get("macroPath") or inp.get("macro_path")
        if not macro_path:
            return {
                "status": "error",
                "code": "missing_macro",
                "message": "No macro file provided. Pass macroPath.",
            }

        macro_file = _Path(macro_path)
        if not macro_file.exists():
            return {
                "status": "error",
                "code": "file_not_found",
                "message": f"Macro file not found: {macro_path}",
            }

        # Optional working document
        document_path: str | None = inp.get("documentPath") or inp.get("document_path")
        save_document: bool = bool(inp.get("saveDocument", inp.get("save_document", False)))
        timeout: int = int(inp.get("timeout", 300))

        result = freecad_bridge.run_macro(
            macro_file,
            freecad_cmd=active_settings.freecad_cmd,
            freecad_mcp_root=active_settings.freecad_mcp_root,
            document_path=document_path,
            save_document=save_document,
            timeout=timeout,
        )

        # Audit log
        pid = inp.get("project_id")
        if pid and isinstance(result, dict):
            try:
                write_audit_log(active_settings, pid, "freecad_macro", {
                    "tool": "freecad.run_macro",
                    "macroPath": macro_path,
                    "documentPath": document_path,
                    "status": result.get("status"),
                    "returnCode": result.get("return_code"),
                    "freecadVersion": result.get("freecad_version"),
                })
            except Exception:
                pass

        return result

    def _tool_cad_edit_parameter(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import freecad_bridge

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        feature_id: str | None = inp.get("featureId") or inp.get("feature_id")
        parameter_name: str | None = inp.get("parameterName") or inp.get("parameter_name")
        new_value: Any = inp.get("newValue", inp.get("new_value"))
        input_fcstd: str | None = inp.get("inputFcstd") or inp.get("input_fcstd")

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }
        if not feature_id or not parameter_name:
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "missing_parameter_edit_input",
                "message": "feature_id, parameter_name, and new_value are required.",
            }
        if new_value is None:
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "missing_new_value",
                "message": "new_value is required.",
            }

        package_path = Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        try:
            contract = _validate_cad_parameter_edit_contract(
                package_path,
                feature_id,
                parameter_name,
                new_value,
            )
        except (ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "preflight_failed",
                "message": str(exc),
            }

        artifact_dir = package_path.parent / "cad_edit_artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        try:
            bridge_result = freecad_bridge.edit_parameter(
                package_path,
                feature_id=feature_id,
                parameter_name=parameter_name,
                new_value=new_value,
                freecad_mcp_root=active_settings.freecad_mcp_root,
                input_fcstd=input_fcstd,
                artifact_output_dir=artifact_dir,
                executor_mode=os.environ.get("AIENG_FREECAD_EXECUTOR", "auto"),
                freecad_cmd=active_settings.freecad_cmd,
            )
        except Exception as exc:
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        if bridge_result.get("status") not in {"success", "partial"}:
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "edit_rejected_or_failed",
                "message": "; ".join(bridge_result.get("errors") or []) or "CAD parameter edit did not succeed.",
                "bridge_result": bridge_result,
            }

        source = bridge_result.get("source", "unknown")

        # Stub/mock mode: must not report completed, must not write fake artifacts
        if source == "stub_mock":
            return {
                "ok": True,
                "tool": "cad.edit_parameter",
                "status": "partial",
                "package_path": str(package_path),
                "feature_id": feature_id,
                "parameter_name": parameter_name,
                "new_value": new_value,
                "freecad_object_name": contract["freecad_object_name"],
                "freecad_parameter_name": contract["freecad_parameter_name"],
                "package_geometry_path": None,
                "stale_artifacts": list(_GEOMETRY_STALE_ARTIFACTS),
                "warnings": bridge_result.get("warnings", []),
                "bridge_result": bridge_result,
                "artifacts": [],
                "source": source,
            }

        geometry_artifacts: list[dict[str, str]] = []
        package_geometry_path: str | None = None
        for artifact in bridge_result.get("artifacts_written", []):
            artifact_path = Path(str(artifact))
            if artifact_path.suffix.lower() in {".step", ".stp"} and artifact_path.exists():
                package_geometry_path = _write_modified_step_into_package(
                    package_path,
                    artifact_path,
                    feature_id=feature_id,
                    parameter_name=parameter_name,
                )
                geometry_artifacts.append({
                    "path": package_geometry_path,
                    "kind": "step",
                    "role": "modified_geometry",
                })
                break

        # If a real executor claimed success but STEP is missing, do not lie
        if bridge_result.get("status") == "success" and not package_geometry_path:
            return {
                "ok": False,
                "tool": "cad.edit_parameter",
                "status": "error",
                "code": "missing_step_export",
                "message": "Parameter edit succeeded but STEP export artifact was not found on disk.",
                "bridge_result": bridge_result,
                "source": source,
            }

        stale_artifacts = [
            art for art in _GEOMETRY_STALE_ARTIFACTS
            if art != package_geometry_path
        ]

        return {
            "ok": True,
            "tool": "cad.edit_parameter",
            "status": "completed",
            "package_path": str(package_path),
            "feature_id": feature_id,
            "parameter_name": parameter_name,
            "new_value": new_value,
            "freecad_object_name": contract["freecad_object_name"],
            "freecad_parameter_name": contract["freecad_parameter_name"],
            "package_geometry_path": package_geometry_path,
            "stale_artifacts": stale_artifacts,
            "warnings": bridge_result.get("warnings", []),
            "bridge_result": bridge_result,
            "artifacts": geometry_artifacts,
            "source": source,
        }

    def _tool_cae_generate_mesh(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        from . import freecad_bridge
        from pathlib import Path as _Path

        package_path_str: str | None = inp.get("packagePath") or inp.get("package_path")
        project_id: str | None = inp.get("project_id")
        geometry_path: str | None = inp.get("geometry_path")
        mesh_size_mm: float = float(inp.get("mesh_size_mm", 5.0))
        element_type: str = str(inp.get("element_type", "tetrahedral"))
        output_format: str = str(inp.get("output_format", "inp"))

        if not package_path_str and project_id:
            proj = get_project(active_settings, project_id)
            pkg = resolve_project_path(active_settings, project_id, proj.get("aieng_file"))
            if pkg is not None and pkg.exists():
                package_path_str = str(pkg)

        if not package_path_str:
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "missing_package_path",
                "message": "No package path provided and no project_id could be resolved.",
            }

        package_path = _Path(package_path_str)
        if not package_path.exists():
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "file_not_found",
                "message": f"Package not found: {package_path_str}",
            }

        # Resolve geometry_path from manifest if not explicitly provided
        if not geometry_path:
            with zipfile.ZipFile(package_path, "r") as zf:
                manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in zf.namelist() else {"resources": {}}
                namelist = zf.namelist()
            resources = manifest.get("resources", {})
            geom = resources.get("geometry", {})
            if isinstance(geom, dict):
                geometry_path = geom.get("source") or geom.get("primary") or geom.get("modified")
            # Fallback: scan ZIP for geometry files
            if not geometry_path:
                for name in namelist:
                    lower = name.lower()
                    if lower.endswith(".step") or lower.endswith(".stp") or lower.endswith(".fcstd"):
                        geometry_path = name
                        break

        if not geometry_path:
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "missing_geometry",
                "message": "No geometry_path provided and package manifest has no primary geometry.",
            }

        # Unpack geometry from ZIP to a temporary file (must not assume filesystem path)
        try:
            temp_geometry_path = _unpack_geometry_from_package(package_path, geometry_path)
        except KeyError:
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "geometry_not_in_package",
                "message": f"Geometry path '{geometry_path}' not found inside the .aieng package.",
            }

        mesh_dir = package_path.parent / "simulation" / "mesh"
        mesh_dir.mkdir(parents=True, exist_ok=True)

        try:
            bridge_result = freecad_bridge.generate_mesh(
                temp_geometry_path,
                mesh_dir,
                mesh_size_mm=mesh_size_mm,
                freecad_cmd=active_settings.freecad_cmd,
                freecad_mcp_root=active_settings.freecad_mcp_root,
            )
        except (RuntimeError, FileNotFoundError) as exc:
            # FreeCAD not available → honest error (do not fake completed)
            msg = str(exc).lower()
            if "freecad" in msg or "freecadcmd" in msg or "no such file" in msg or "cannot find" in msg:
                return {
                    "ok": False,
                    "tool": "cae.generate_mesh",
                    "status": "error",
                    "code": "freecad_unavailable",
                    "message": f"FreeCAD/Gmsh bridge unavailable: {exc}",
                    "package_path": str(package_path),
                    "mesh_artifact_path": None,
                    "mesh_metadata_path": None,
                    "unpacked_geometry": str(temp_geometry_path),
                    "mesh_size_mm": mesh_size_mm,
                    "element_type": element_type,
                    "output_format": output_format,
                }
            raise
        except Exception as exc:
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "bridge_error",
                "message": str(exc),
            }

        if bridge_result.get("status") != "success":
            return {
                "ok": False,
                "tool": "cae.generate_mesh",
                "status": "error",
                "code": "mesh_generation_failed",
                "message": bridge_result.get("message", "Mesh generation failed"),
                "bridge_stdout": bridge_result.get("stdout"),
                "bridge_stderr": bridge_result.get("stderr"),
            }

        mesh_file_path = _Path(bridge_result["mesh_file_path"])
        safe_name = f"mesh_{mesh_size_mm}mm.{output_format}"
        package_mesh_path = _write_mesh_into_package_atomic(
            package_path,
            mesh_file_path,
            f"simulation/mesh/{safe_name}",
        )

        metadata = {
            "schema_version": "0.1",
            "mesh_size_mm": mesh_size_mm,
            "element_type": element_type,
            "output_format": output_format,
            "source_geometry": str(temp_geometry_path),
            "mesh_file": package_mesh_path,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata_path = mesh_dir / "mesh_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        _write_mesh_into_package_atomic(
            package_path,
            metadata_path,
            "simulation/mesh/mesh_metadata.json",
        )

        stale_artifacts = [
            "results/computed_metrics.json",
            "results/field_regions.json",
            "results/field_summary.json",
            "results/field_summary.md",
            "simulation/solver_input.inp",
            "simulation/solver_inputDeck.inp",
        ]

        return {
            "ok": True,
            "tool": "cae.generate_mesh",
            "status": "completed",
            "package_path": str(package_path),
            "mesh_artifact_path": package_mesh_path,
            "mesh_metadata_path": "simulation/mesh/mesh_metadata.json",
            "stale_artifacts": stale_artifacts,
            "mesh_size_mm": mesh_size_mm,
            "element_type": element_type,
            "output_format": output_format,
        }

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
        "aieng.write_completeness_report",
        _tool_aieng_write_completeness_report,
        description=(
            "Write a completeness/missingness report (validation/completeness_report.json) into a .aieng package. "
            "Assesses 19+ categories: geometry, topology, features, constraints, simulation setup, evidence, etc."
        ),
    )
    _rt.register_tool(
        "aieng.update_validation_status",
        _tool_aieng_update_validation_status,
        description=(
            "Update validation status (validation/status.yaml) inside a .aieng package. "
            "Records geometry, topology, feature, solver/mesh, and CAE import status with explicit claim policy."
        ),
    )
    _rt.register_tool(
        "aieng.write_evidence_scaffold",
        _tool_aieng_write_evidence_scaffold,
        description=(
            "Write evidence_index.json and claim_map.json scaffold into a .aieng package. "
            "Required before importing external solver or mesh evidence."
        ),
    )
    _rt.register_tool(
        "aieng.validate",
        _tool_aieng_validate,
        description=(
            "Validate a .aieng package against AIENG schemas and rules. "
            "Returns PASS/WARN/FAIL messages and an overall validation_ok boolean."
        ),
    )
    _rt.register_tool(
        "aieng.convert",
        _tool_aieng_convert,
        description=(
            "Convert a CAD source file (.FCStd or .step/.stp) to a .aieng package. "
            "Supports FreeCAD FCStd offline parsing and STEP evidence import. "
            "Automatically updates project aieng_file on success."
        ),
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
        "cae.extract_field_regions",
        _tool_cae_extract_field_regions,
        description=(
            "Extract high-magnitude spatial clusters from a CalculiX FRD result file. "
            "Partitions nodal stress or displacement fields into ≤ N clusters, "
            "reporting centroid, peak magnitude, and node count per cluster. "
            "Writes results/field_regions.json into the .aieng package."
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
        "cae.generate_solver_input",
        _tool_cae_generate_solver_input,
        description=(
            "Generate a runnable CalculiX solver input deck from existing .aieng setup artifacts. "
            "Preserves mesh from a previously imported source deck and assembles materials, BCs, loads, and step. "
            "Supports linear static only. Refuses with explicit missing_items if mesh or setup is absent."
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
        "cae.write_mesh_handoff",
        _tool_cae_write_mesh_handoff,
        description=(
            "Write a mesh handoff contract (simulation/mesh_handoff_contract.json) into a .aieng package. "
            "Reads topology_map.json and simulation/setup.yaml to produce a structured handoff spec "
            "for external Gmsh execution. Does not run a mesher."
        ),
    )
    _rt.register_tool(
        "cae.import_solver_evidence",
        _tool_cae_import_solver_evidence,
        description=(
            "Import an external solver result file as evidence into a .aieng package. "
            "Scans the result file for known numeric observations (max von Mises, max displacement, etc.) "
            "and appends them to results/evidence_index.json. Does not auto-advance claim status."
        ),
    )
    _rt.register_tool(
        "freecad.run_macro",
        _tool_freecad_run_macro,
        requires_approval=True,
        description="Run a FreeCAD macro (requires explicit approval; potentially destructive)",
    )
    _rt.register_tool(
        "cad.edit_parameter",
        _tool_cad_edit_parameter,
        requires_approval=True,
        description=(
            "Approval-gated CAD parameter edit. Validates feature_id, parameter_name, "
            "and declared bounds from graph/feature_graph.json before delegating to "
            "the FreeCAD bridge; marks geometry-derived artifacts stale."
        ),
    )
    _rt.register_tool(
        "cae.generate_mesh",
        _tool_cae_generate_mesh,
        requires_approval=True,
        description=(
            "Generate a finite-element mesh from CAD geometry via FreeCAD/Gmsh (approval-gated). "
            "Unpacks geometry from .aieng ZIP, runs FreeCAD+Gmsh macro, atomically writes "
            "simulation/mesh/*.inp and mesh_metadata.json back into the package. "
            "Returns error/freecad_unavailable when FreeCAD is not installed."
        ),
    )

    def _registry_entry_requires_approval(entry: dict[str, Any]) -> bool:
        return bool(
            entry.get("mutates_cad")
            or entry.get("mutates_package")
            or entry.get("may_update_claim_map")
            or entry.get("dry_run_support") == "none"
        )

    def _registry_tool_metadata(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "tool_name": entry.get("tool_name"),
            "source": "freecad-mcp",
            "category": entry.get("category"),
            "purpose": entry.get("purpose"),
            "required_inputs": entry.get("required_inputs") or [],
            "optional_inputs": entry.get("optional_inputs") or [],
            "side_effects": entry.get("side_effects") or [],
            "mutates_cad": bool(entry.get("mutates_cad")),
            "mutates_package": bool(entry.get("mutates_package")),
            "may_update_claim_map": bool(entry.get("may_update_claim_map")),
            "runtime_requirements": entry.get("runtime_requirements") or [],
            "dry_run_support": entry.get("dry_run_support") or "none",
            "claim_policy": entry.get("claim_policy") or {},
            "notes": entry.get("notes") or [],
        }

    def _make_unbound_freecad_mcp_tool(entry: dict[str, Any]) -> _rt.ToolHandler:
        metadata = _registry_tool_metadata(entry)
        tool_name = str(metadata.get("tool_name") or "freecad_mcp.unbound")

        def _handler(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
            missing_inputs = [
                name for name in metadata["required_inputs"]
                if name not in inp and name not in {"package_path"}
            ]
            return {
                "status": "unsupported",
                "code": "freecad_mcp_runtime_binding_missing",
                "operation": tool_name,
                "message": (
                    "This freecad-mcp registry entry is available to the planner, "
                    "but a direct aieng-ui runtime executor has not been bound yet."
                ),
                "missing_inputs": missing_inputs,
                "received_input_keys": sorted(str(key) for key in inp.keys()),
                "tool_metadata": metadata,
                "claim_policy": {
                    "claims_advanced": False,
                    "requires_explicit_update_claim": True,
                },
            }

        return _handler

    def _tool_registry_query(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        entries = _load_freecad_mcp_registry_entries(active_settings)
        category = inp.get("category")
        keyword = str(inp.get("keyword") or "").strip().lower()
        mutability = inp.get("mutability")
        filtered = entries
        if category:
            filtered = [entry for entry in filtered if entry.get("category") == category]
        if keyword:
            filtered = [
                entry for entry in filtered
                if keyword in str(entry.get("tool_name") or "").lower()
                or keyword in str(entry.get("purpose") or "").lower()
                or any(keyword in str(note).lower() for note in entry.get("notes") or [])
            ]
        if mutability == "cad":
            filtered = [entry for entry in filtered if entry.get("mutates_cad")]
        elif mutability == "package":
            filtered = [entry for entry in filtered if entry.get("mutates_package")]
        elif mutability == "claim_map":
            filtered = [entry for entry in filtered if entry.get("may_update_claim_map")]
        elif mutability == "none":
            filtered = [
                entry for entry in filtered
                if not entry.get("mutates_cad")
                and not entry.get("mutates_package")
                and not entry.get("may_update_claim_map")
            ]
        elif mutability == "any":
            filtered = [
                entry for entry in filtered
                if entry.get("mutates_cad")
                or entry.get("mutates_package")
                or entry.get("may_update_claim_map")
            ]
        return {
            "status": "success",
            "operation": "aieng_tool_registry_query",
            "count": len(filtered),
            "filters": {
                "category": category,
                "keyword": inp.get("keyword"),
                "mutability": mutability,
            },
            "entries": filtered,
            "claim_policy": {
                "claims_advanced": False,
                "requires_explicit_update_claim": True,
            },
        }

    def _tool_inspect_mcp_capabilities(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        desired = str(inp.get("desired_outcome") or inp.get("message") or "").strip().lower()
        caps = agent_workbench.list_capabilities(active_settings)
        if desired:
            tokens = [part for part in re.split(r"\W+", desired) if part]
            caps = [
                cap for cap in caps
                if any(
                    token in str(cap.get("name") or "").lower()
                    or token in str(cap.get("purpose") or "").lower()
                    or token in str(cap.get("category") or "").lower()
                    for token in tokens
                )
            ] or caps
        return {
            "status": "success",
            "operation": "aieng_inspect_capabilities",
            "desired_outcome": inp.get("desired_outcome") or "",
            "capabilities": caps[:80],
            "registered_runtime_tool_count": len(_rt.registered_tool_names()),
            "claim_policy": {
                "claims_advanced": False,
                "requires_explicit_update_claim": True,
            },
        }

    def _tool_freecad_runtime_capabilities(inp: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
        config, _, provider = resolve_provider_bundle(active_settings)
        return {
            "status": "success",
            "operation": "freecad_runtime_capabilities",
            "runtime_config": config,
            "capabilities": provider.probe_capabilities(whitelisted_tools=TOOLS_ALLOWED),
            "claim_policy": {
                "claims_advanced": False,
                "requires_explicit_update_claim": True,
            },
        }

    registry_bound_handlers: dict[str, _rt.ToolHandler] = {
        "aieng_parse_patch": _tool_mcp_parse_patch,
        "aieng_tool_registry_query": _tool_registry_query,
        "aieng_inspect_capabilities": _tool_inspect_mcp_capabilities,
        "aieng_plan_capabilities": _tool_inspect_mcp_capabilities,
        "freecad_runtime_capabilities": _tool_freecad_runtime_capabilities,
    }
    registered_names = set(_rt.registered_tool_names())
    for entry in _load_freecad_mcp_registry_entries(active_settings):
        tool_name = str(entry.get("tool_name") or "").strip()
        if not tool_name or tool_name in registered_names:
            continue
        handler = registry_bound_handlers.get(tool_name) or _make_unbound_freecad_mcp_tool(entry)
        _rt.register_tool(
            tool_name,
            handler,
            requires_approval=_registry_entry_requires_approval(entry),
            description=str(entry.get("purpose") or f"freecad-mcp registry tool: {tool_name}"),
        )
        registered_names.add(tool_name)

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
