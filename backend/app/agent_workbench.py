from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import runtime as _rt


AIENG_PACKAGE_MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "get_manifest",
        "category": "reference",
        "purpose": "Return manifest.json for the loaded .aieng package.",
    },
    {
        "name": "get_feature",
        "category": "reference",
        "purpose": "Return a specific feature by ID from graph/feature_graph.json.",
        "required_inputs": ["feature_id"],
    },
    {
        "name": "get_topology",
        "category": "reference",
        "purpose": "Return topology entities, optionally filtered by type.",
        "optional_inputs": ["entity_type"],
    },
    {
        "name": "get_interfaces",
        "category": "reference",
        "purpose": "Return interface graph entries, optionally filtered by role.",
        "optional_inputs": ["role"],
    },
    {
        "name": "get_validation_status",
        "category": "reference",
        "purpose": "Return validation/status.yaml as a structured dict.",
    },
    {
        "name": "get_aag_neighbors",
        "category": "reference",
        "purpose": "Return AAG neighbors adjacent to a topology face ID.",
        "required_inputs": ["face_id"],
    },
    {
        "name": "get_task_spec",
        "category": "reference",
        "purpose": "Return task/task_spec.yaml if present.",
    },
    {
        "name": "get_external_tool_requirements",
        "category": "reference",
        "purpose": "Return task/external_tool_requirements.json if present.",
    },
    {
        "name": "get_evidence_index",
        "category": "evidence",
        "purpose": "Return results/evidence_index.json if present.",
    },
    {
        "name": "get_claim_map",
        "category": "claim",
        "purpose": "Return results/claim_map.json if present.",
    },
    {
        "name": "get_tool_trace",
        "category": "audit",
        "purpose": "Return provenance/tool_trace.json if present.",
    },
    {
        "name": "get_completeness_report",
        "category": "audit",
        "purpose": "Return validation/completeness_report.json if present.",
    },
    {
        "name": "get_evidence_report",
        "category": "audit",
        "purpose": "Return validation/evidence_report.json if present.",
    },
    {
        "name": "get_summary",
        "category": "reference",
        "purpose": "Return ai/summary.md content.",
    },
    {
        "name": "resolve_ref",
        "category": "reference",
        "purpose": "Resolve a canonical @aieng[...] reference.",
        "required_inputs": ["ref"],
    },
    {
        "name": "propose_patch",
        "category": "orchestration",
        "purpose": "Run the rule-based patch proposer.",
        "required_inputs": ["intent"],
        "mutates_package": True,
        "side_effects": ["Writes a patch proposal under ai/patches/"],
        "dry_run_support": "none",
    },
)


WORKFLOW_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "inspect_semantic_package",
        "title": "Inspect semantic package",
        "description": "Read .aieng resources, summarize semantic coverage, and report missing context.",
        "required_context": ["project_id", ".aieng package"],
        "steps": [
            {"id": "inspect", "kind": "tool", "tool_name": "aieng.inspect_package", "status": "pending"},
            {"id": "audit", "kind": "tool", "tool_name": "aieng.read_audit_log", "status": "pending"},
        ],
    },
    {
        "id": "safe_geometry_inspection",
        "title": "Safe geometry inspection",
        "description": "Inspect source CAD geometry through FreeCADCmd without mutating CAD or package state.",
        "required_context": ["project_id", "source STEP"],
        "steps": [
            {"id": "preview", "kind": "artifact", "status": "pending"},
            {"id": "inspect", "kind": "tool", "tool_name": "freecad.inspect_geometry", "status": "pending"},
        ],
    },
    {
        "id": "guarded_cad_patch",
        "title": "Guarded CAD patch",
        "description": "Parse a proposed .aieng patch, preview side effects, require approval, then execute.",
        "required_context": ["project_id", ".aieng package", "patch proposal"],
        "steps": [
            {"id": "parse", "kind": "mcp_tool", "tool_name": "aieng_parse_patch", "status": "pending"},
            {"id": "preview", "kind": "tool", "tool_name": "preview_operation", "status": "pending"},
            {"id": "approval", "kind": "approval", "status": "pending", "approval_required": True},
            {"id": "execute", "kind": "mcp_tool", "tool_name": "aieng_execute_patch", "status": "pending"},
        ],
    },
    {
        "id": "cae_postprocess_refresh",
        "title": "CAE post-process refresh",
        "description": "Import external computed metrics and regenerate CAE result summaries without running a solver.",
        "required_context": ["project_id", ".aieng package", "metrics file"],
        "steps": [
            {
                "id": "metrics",
                "kind": "tool",
                "tool_name": "postprocess.generate_computed_metrics",
                "status": "pending",
            },
            {
                "id": "summary",
                "kind": "tool",
                "tool_name": "postprocess.refresh_cae_summary",
                "status": "pending",
            },
        ],
    },
    {
        "id": "ai_usefulness_benchmark",
        "title": "AI usefulness benchmark",
        "description": "Run or dry-run the A/B benchmark using the shared aieng benchmark provider layer.",
        "required_context": ["benchmark scenario", "LLM provider config"],
        "steps": [
            {"id": "estimate", "kind": "benchmark", "status": "pending"},
            {"id": "llm_a", "kind": "llm", "status": "pending"},
            {"id": "llm_b", "kind": "llm", "status": "pending"},
            {"id": "score", "kind": "benchmark", "status": "pending"},
        ],
    },
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inject_path(path: Path) -> tuple[str, bool]:
    candidate = str(path)
    if candidate in sys.path:
        return candidate, False
    sys.path.insert(0, candidate)
    return candidate, True


def _remove_path(candidate: str, injected: bool) -> None:
    if not injected:
        return
    try:
        sys.path.remove(candidate)
    except ValueError:
        pass


def _clear_stale_module(module_name: str, expected_root: Path) -> None:
    module = sys.modules.get(module_name)
    if module is None:
        return
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        return
    try:
        Path(module_file).resolve().relative_to(expected_root.resolve())
    except ValueError:
        sys.modules.pop(module_name, None)


def _base_descriptor(
    *,
    name: str,
    source: str,
    category: str,
    purpose: str,
    available: bool = True,
    required_inputs: list[str] | None = None,
    optional_inputs: list[str] | None = None,
    side_effects: list[str] | None = None,
    mutates_cad: bool = False,
    mutates_package: bool = False,
    may_update_claim_map: bool = False,
    runtime_requirements: list[str] | None = None,
    dry_run_support: str = "none",
    claim_policy: dict[str, Any] | None = None,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "category": category,
        "purpose": purpose,
        "required_inputs": required_inputs or [],
        "optional_inputs": optional_inputs or [],
        "mutates_cad": mutates_cad,
        "mutates_package": mutates_package,
        "may_update_claim_map": may_update_claim_map,
        "runtime_requirements": runtime_requirements or [],
        "dry_run_support": dry_run_support,
        "side_effects": side_effects or [],
        "claim_policy": claim_policy
        or {"claims_advanced_default": False, "requires_explicit_update_claim": True},
        "available": available,
        "unavailable_reason": unavailable_reason,
    }


def list_capabilities(settings: Any) -> list[dict[str, Any]]:
    capabilities: list[dict[str, Any]] = []

    # Local runtime tools are already registered in app.runtime.
    for tool in _rt.registered_tools_info():
        requires_approval = bool(tool.get("requires_approval"))
        capabilities.append(
            _base_descriptor(
                name=str(tool["name"]),
                source="aieng-ui-runtime",
                category="runtime",
                purpose=str(tool.get("description") or ""),
                optional_inputs=["project_id", "tool_input"],
                side_effects=["Runtime event and audit records are written"],
                dry_run_support="partial" if not requires_approval else "none",
                claim_policy={"claims_advanced_default": False, "requires_explicit_update_claim": True},
            )
        )

    # Full MCP transparency registry, when the sibling repo is available.
    mcp_src = settings.freecad_mcp_root / "src"
    if mcp_src.exists():
        candidate, injected = _inject_path(mcp_src)
        try:
            _clear_stale_module("freecad_mcp.tool_registry", mcp_src)
            _clear_stale_module("freecad_mcp", mcp_src)
            from freecad_mcp.tool_registry import default_registry

            for entry in default_registry().list_all():
                payload = entry.model_dump(mode="json")
                capabilities.append(
                    _base_descriptor(
                        name=payload["tool_name"],
                        source="freecad-mcp",
                        category=payload["category"],
                        purpose=payload["purpose"],
                        required_inputs=payload.get("required_inputs") or [],
                        optional_inputs=payload.get("optional_inputs") or [],
                        side_effects=payload.get("side_effects") or [],
                        mutates_cad=bool(payload.get("mutates_cad")),
                        mutates_package=bool(payload.get("mutates_package")),
                        may_update_claim_map=bool(payload.get("may_update_claim_map")),
                        runtime_requirements=payload.get("runtime_requirements") or [],
                        dry_run_support=payload.get("dry_run_support") or "none",
                        claim_policy=payload.get("claim_policy") or {},
                    )
                )
        except Exception as exc:
            capabilities.append(
                _base_descriptor(
                    name="freecad_mcp.tool_registry",
                    source="freecad-mcp",
                    category="runtime",
                    purpose="Unified MCP tool registry could not be imported.",
                    available=False,
                    unavailable_reason=f"{type(exc).__name__}: {exc}",
                    dry_run_support="none",
                )
            )
        finally:
            _remove_path(candidate, injected)
    else:
        capabilities.append(
            _base_descriptor(
                name="freecad_mcp.tool_registry",
                source="freecad-mcp",
                category="runtime",
                purpose="Unified MCP tool registry is unavailable.",
                available=False,
                unavailable_reason=f"missing path: {mcp_src}",
            )
        )

    for item in AIENG_PACKAGE_MCP_TOOLS:
        capabilities.append(
            _base_descriptor(
                name=item["name"],
                source="aieng-package-mcp",
                category=item.get("category", "reference"),
                purpose=item.get("purpose", ""),
                required_inputs=item.get("required_inputs") or [],
                optional_inputs=item.get("optional_inputs") or ["package_path"],
                side_effects=item.get("side_effects") or [],
                mutates_package=bool(item.get("mutates_package")),
                dry_run_support=item.get("dry_run_support", "full"),
            )
        )

    capabilities.extend(
        [
            _base_descriptor(
                name="benchmark.ai_usefulness.run",
                source="aieng-benchmark",
                category="orchestration",
                purpose="Run or dry-run the AI usefulness A/B benchmark.",
                required_inputs=["scenario_id", "llm_config"],
                optional_inputs=["condition", "dry_run", "output_path"],
                side_effects=["Writes benchmark result files for real runs"],
                mutates_package=False,
                dry_run_support="full",
            ),
            _base_descriptor(
                name="benchmark.ai_usefulness.list_scenarios",
                source="aieng-benchmark",
                category="runtime",
                purpose="List benchmark scenarios discoverable in the aieng repo.",
                dry_run_support="full",
            ),
        ]
    )
    return capabilities


def preview_capability(settings: Any, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("operation_name") or payload.get("name") or "").strip()
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    approved = bool(payload.get("approved") or payload.get("approval_granted"))
    caps = list_capabilities(settings)
    matches = [item for item in caps if item["name"] == name]
    cap = next(
        (
            item for item in matches
            if item.get("mutates_cad")
            or item.get("mutates_package")
            or item.get("may_update_claim_map")
            or item.get("source") == "freecad-mcp"
        ),
        matches[0] if matches else None,
    )
    if cap is None:
        return {
            "status": "rejected",
            "operation_name": name,
            "preview": None,
            "errors": [f"Unknown capability: {name}"],
        }

    would_write: list[str] = []
    for side_effect in cap.get("side_effects", []):
        lowered = str(side_effect).lower()
        if "write" not in lowered and "writes" not in lowered:
            continue
        for key in ("file_path", "output_path", "outputPath", "run_dir", "artifact_output_dir", "output_dir"):
            value = inputs.get(key)
            if value:
                would_write.append(str(value))
                break

    runtime_blocks = _runtime_blocks(settings, cap.get("runtime_requirements") or [])
    guard_checks: list[str] = []
    if cap.get("mutates_cad") or cap.get("mutates_package"):
        guard_checks.append("package_context_load")
    if cap.get("mutates_cad"):
        guard_checks.extend(["feature_graph_existence", "parameter_editability", "protected_region_check"])
    if cap.get("may_update_claim_map"):
        guard_checks.extend(["claim_id_validity", "evidence_ids_present"])

    mutating = bool(cap.get("mutates_cad") or cap.get("mutates_package") or cap.get("may_update_claim_map"))
    approval_required = mutating and not approved
    return {
        "status": "approval_required" if approval_required else "success",
        "operation_name": name,
        "capability": cap,
        "approval_required": approval_required,
        "blocked": approval_required or bool(runtime_blocks),
        "preview": {
            "operation_name": name,
            "would_write_artifacts": _dedupe(would_write),
            "would_update_evidence": cap.get("mutates_package") and "evidence" in str(cap.get("side_effects", "")).lower(),
            "would_update_traces": cap.get("mutates_package") and "trace" in str(cap.get("side_effects", "")).lower(),
            "would_touch_claims": bool(cap.get("may_update_claim_map")),
            "guard_checks_required": _dedupe(guard_checks),
            "unavailable_runtime_blocks": runtime_blocks,
            "expected_duration_estimate": "blocked" if runtime_blocks else "fast",
            "warnings": [
                "Mutating operations require explicit user approval before execution."
            ]
            if approval_required
            else [],
        },
    }


def _runtime_blocks(settings: Any, requirements: list[str]) -> list[str]:
    reqs = {r for r in requirements if r and r != "none"}
    if not reqs:
        return []
    blocks: list[str] = []
    if "freecad" in reqs and not settings.freecad_cmd.exists():
        blocks.append("freecad")
    # The UI backend can only directly detect FreeCAD paths today; FEM/mesher/solver
    # stay explicit as unavailable until the MCP runtime reports them.
    for req in ("fem", "mesher", "solver"):
        if req in reqs:
            blocks.append(req)
    return _dedupe(blocks)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def list_workflows() -> list[dict[str, Any]]:
    return [dict(item) for item in WORKFLOW_DEFINITIONS]


def list_benchmark_scenarios(settings: Any) -> list[dict[str, Any]]:
    root = settings.aieng_root / "benchmarks" / "ai_usefulness" / "scenarios"
    if not root.exists():
        return []
    scenarios: list[dict[str, Any]] = []
    for path in sorted(p for p in root.iterdir() if p.is_dir()):
        scenarios.append(_scenario_descriptor(settings, path))
    return scenarios


def _scenario_descriptor(settings: Any, path: Path) -> dict[str, Any]:
    condition_b_package = path / "condition_b.aieng"
    condition_b_contents = path / "condition_b_contents"
    return {
        "id": path.name,
        "name": path.name.replace("_", " "),
        "path": str(path),
        "question_file": str(path / "questions.md"),
        "condition_a_path": str(path / "condition_a.md"),
        "condition_b_index": str(path / "condition_b_index.md"),
        "condition_b_source": str(condition_b_package if condition_b_package.exists() else condition_b_contents),
        "has_condition_b_package": condition_b_package.exists(),
        "has_condition_b_contents": condition_b_contents.exists(),
        "rubric_file": str(settings.aieng_root / "benchmarks" / "ai_usefulness" / "scoring_rubric.md"),
        "schema_file": str(settings.aieng_root / "benchmarks" / "ai_usefulness" / "results.schema.json"),
    }


@dataclass(frozen=True)
class BenchmarkRunEnvelope:
    run_id: str
    status: str
    scenario_id: str
    dry_run: bool
    created_at: str
    result: dict[str, Any]
    result_path: str | None
    events: list[dict[str, Any]]
    warnings: list[str]


def benchmark_runs_root(settings: Any) -> Path:
    return settings.data_root / "benchmarks" / "runs"


def run_benchmark_from_payload(settings: Any, payload: dict[str, Any]) -> dict[str, Any]:
    scenarios = {item["id"]: item for item in list_benchmark_scenarios(settings)}
    scenario_id = str(payload.get("scenario_id") or next(iter(scenarios), "")).strip()
    if not scenario_id or scenario_id not in scenarios:
        return _benchmark_error("scenario_not_found", f"Benchmark scenario not found: {scenario_id}")

    llm_config = payload.get("llm_config") if isinstance(payload.get("llm_config"), dict) else {}
    if "api_key" in llm_config:
        llm_config = {k: v for k, v in llm_config.items() if k != "api_key"}
    dry_run = bool(payload.get("dry_run", True))
    condition = str(payload.get("condition") or "both")
    events = [_event("benchmark_started", {"scenario_id": scenario_id, "dry_run": dry_run})]
    run_id = "bench_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
    output_path = benchmark_runs_root(settings) / f"{run_id}.result.json"

    aieng_src = settings.aieng_root / "src"
    if not aieng_src.exists():
        return _benchmark_error("aieng_unavailable", f"missing path: {aieng_src}", scenario_id=scenario_id)
    candidate, injected = _inject_path(aieng_src)
    try:
        from aieng.benchmarking import BenchmarkPaths, BenchmarkRunConfig, ProviderConfig, run_benchmark

        scenario = scenarios[scenario_id]
        provider_config = ProviderConfig(
            provider=str(llm_config.get("provider") or "openai-compatible"),
            model=str(llm_config.get("model") or "configured-model"),
            api_key_env=llm_config.get("api_key_env") or None,
            base_url=llm_config.get("base_url") or None,
            input_price_per_million_tokens=llm_config.get("input_price_per_million_tokens"),
            output_price_per_million_tokens=llm_config.get("output_price_per_million_tokens"),
            max_output_tokens=int(llm_config.get("max_output_tokens") or 8192),
            temperature=float(llm_config.get("temperature") or 0.0),
            top_p=float(llm_config.get("top_p") or 1.0),
            seed=llm_config.get("seed"),
        )
        paths = BenchmarkPaths(
            benchmark_scenario=scenario_id,
            question_file=Path(scenario["question_file"]),
            rubric_file=Path(scenario["rubric_file"]),
            condition_a_path=Path(scenario["condition_a_path"]),
            condition_b_index_file=Path(scenario["condition_b_index"]),
            condition_b_source=Path(scenario["condition_b_source"]),
            results_dir=benchmark_runs_root(settings),
            schema_file=Path(scenario["schema_file"]),
        )
        progress_messages: list[str] = []

        def _progress(message: str) -> None:
            progress_messages.append(message)
            events.append(_event("benchmark_progress", {"message": message}))

        result = run_benchmark(
            paths=paths,
            config=BenchmarkRunConfig(
                condition=condition,
                provider=provider_config,
                dry_run=dry_run,
                output_path=output_path if not dry_run else None,
            ),
            provider=None,
            prepare_condition_b=None,
            progress=_progress,
        )
        if dry_run:
            result_path: str | None = None
        else:
            result_path = str(output_path)
        envelope = BenchmarkRunEnvelope(
            run_id=run_id,
            status="completed",
            scenario_id=scenario_id,
            dry_run=dry_run,
            created_at=_now_iso(),
            result=result,
            result_path=result_path,
            events=events + [_event("benchmark_completed", {"run_id": run_id})],
            warnings=list(result.get("warnings") or []) + list(result.get("dry_run_notes") or []),
        )
        return _store_benchmark_run(settings, envelope)
    except Exception as exc:
        envelope = BenchmarkRunEnvelope(
            run_id=run_id,
            status="failed",
            scenario_id=scenario_id,
            dry_run=dry_run,
            created_at=_now_iso(),
            result={},
            result_path=None,
            events=events + [_event("benchmark_failed", {"error": f"{type(exc).__name__}: {exc}"})],
            warnings=[],
        )
        data = _store_benchmark_run(settings, envelope)
        data["errors"] = [f"{type(exc).__name__}: {exc}"]
        return data
    finally:
        _remove_path(candidate, injected)


def get_benchmark_run(settings: Any, run_id: str) -> dict[str, Any] | None:
    path = benchmark_runs_root(settings) / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _store_benchmark_run(settings: Any, envelope: BenchmarkRunEnvelope) -> dict[str, Any]:
    root = benchmark_runs_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": envelope.run_id,
        "status": envelope.status,
        "scenario_id": envelope.scenario_id,
        "dry_run": envelope.dry_run,
        "created_at": envelope.created_at,
        "result": envelope.result,
        "result_path": envelope.result_path,
        "events": envelope.events,
        "warnings": envelope.warnings,
    }
    (root / f"{envelope.run_id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10],
        "type": event_type,
        "timestamp": _now_iso(),
        "payload": payload,
    }


def _benchmark_error(code: str, message: str, *, scenario_id: str = "") -> dict[str, Any]:
    return {
        "run_id": "bench_error_" + uuid.uuid4().hex[:6],
        "status": "failed",
        "scenario_id": scenario_id,
        "dry_run": True,
        "created_at": _now_iso(),
        "result": {},
        "result_path": None,
        "events": [_event("benchmark_failed", {"code": code, "message": message})],
        "warnings": [],
        "errors": [message],
    }
