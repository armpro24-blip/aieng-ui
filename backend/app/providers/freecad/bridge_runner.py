from __future__ import annotations

import textwrap


BRIDGE_RUNNER_SOURCE = textwrap.dedent(
    """
    from __future__ import annotations

    import asyncio
    import json
    import os
    import sys
    import traceback
    import types
    import zipfile
    from collections import Counter
    from pathlib import Path
    from typing import Any


    def _bootstrap_paths() -> None:
        aieng_root = Path(os.environ["AIENG_ROOT"]).resolve()
        freecad_mcp_root = Path(os.environ["FREECAD_MCP_ROOT"]).resolve()
        for path in (aieng_root / "src", freecad_mcp_root / "src"):
            candidate = str(path)
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
        freecad_pkg_root = freecad_mcp_root / "src" / "freecad_mcp"
        if "freecad_mcp" not in sys.modules:
            namespace = types.ModuleType("freecad_mcp")
            namespace.__path__ = [str(freecad_pkg_root)]
            sys.modules["freecad_mcp"] = namespace
        if "freecad_mcp.contracts" not in sys.modules:
            contracts_namespace = types.ModuleType("freecad_mcp.contracts")
            contracts_namespace.__path__ = [str(freecad_pkg_root / "contracts")]
            class ToolExecutionError(RuntimeError):
                pass
            contracts_namespace.ToolExecutionError = ToolExecutionError
            sys.modules["freecad_mcp.contracts"] = contracts_namespace
        if "freecad_mcp.aieng_bridge" not in sys.modules:
            bridge_namespace = types.ModuleType("freecad_mcp.aieng_bridge")
            bridge_namespace.__path__ = [str(freecad_pkg_root / "aieng_bridge")]
            sys.modules["freecad_mcp.aieng_bridge"] = bridge_namespace


    def _read_payload(path_arg: str | None) -> dict[str, Any]:
        if not path_arg:
            return {}
        path = Path(path_arg)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


    def _read_json_member(package_path: Path, member: str) -> Any | None:
        try:
            with zipfile.ZipFile(package_path) as archive:
                if member not in archive.namelist():
                    return None
                return json.loads(archive.read(member))
        except Exception:
            return None


    def _read_text_member(package_path: Path, member: str) -> str | None:
        try:
            with zipfile.ZipFile(package_path) as archive:
                if member not in archive.namelist():
                    return None
                return archive.read(member).decode("utf-8", errors="replace")
        except Exception:
            return None


    def _feature_stats(feature_graph: Any) -> dict[str, Any]:
        features: list[dict[str, Any]] = []
        if isinstance(feature_graph, dict):
            raw = feature_graph.get("features", [])
            if isinstance(raw, list):
                features = [item for item in raw if isinstance(item, dict)]
            elif isinstance(raw, dict):
                features = [item for item in raw.values() if isinstance(item, dict)]
        editable = 0
        parameter_count = 0
        preview: list[dict[str, Any]] = []
        for feature in features:
            editability = feature.get("editability", {})
            if isinstance(editability, dict) and editability.get("executable") is not False:
                editable += 1
            params = feature.get("parameters", [])
            if isinstance(params, list):
                parameter_count += len(params)
            if len(preview) < 12:
                preview.append(
                    {
                        "id": feature.get("id"),
                        "name": feature.get("name"),
                        "type": feature.get("type"),
                        "freecad_object_name": feature.get("freecad_object_name"),
                    }
                )
        return {
            "count": len(features),
            "editable_count": editable,
            "parameter_count": parameter_count,
            "preview": preview,
        }


    def _topology_stats(topology: Any) -> dict[str, Any]:
        entities = []
        if isinstance(topology, dict):
            raw = topology.get("entities", [])
            if isinstance(raw, list):
                entities = [item for item in raw if isinstance(item, dict)]
        counts = Counter(entity.get("type", "unknown") for entity in entities)
        return {
            "count": len(entities),
            "by_type": dict(counts),
            "preview": entities[:20],
        }


    def _interface_stats(interface_graph: Any) -> dict[str, Any]:
        interfaces = []
        if isinstance(interface_graph, dict):
            raw = interface_graph.get("interfaces", [])
            if isinstance(raw, list):
                interfaces = [item for item in raw if isinstance(item, dict)]
        roles = Counter()
        for item in interfaces:
            for role in item.get("roles", []) if isinstance(item.get("roles"), list) else []:
                roles[str(role)] += 1
        return {
            "count": len(interfaces),
            "roles": dict(roles),
            "preview": interfaces[:12],
        }


    def command_runtime(payload: dict[str, Any]) -> dict[str, Any]:
        from aieng.geometry.step_importer import import_step_package
        from aieng.mcp import server as aieng_server
        from aieng.validate import validate_package
        from freecad_mcp.aieng_bridge.guards import check_operation_allowed
        from freecad_mcp.aieng_bridge.patch import execute_patch_plan, parse_patch_proposal
        from freecad_mcp.freecad_runtime import detect_freecad_runtime

        caps = detect_freecad_runtime().model_dump(mode="json")
        return {
            "python_executable": sys.executable,
            "python_version": sys.version,
            "freecad_runtime": caps,
            "aieng_import_available": callable(import_step_package),
            "aieng_validate_available": callable(validate_package),
            "aieng_mcp_reader_available": callable(aieng_server.tool_get_manifest),
            "mcp_patch_parse_available": callable(parse_patch_proposal),
            "mcp_patch_prepare_available": callable(execute_patch_plan),
            "mcp_guard_available": callable(check_operation_allowed),
            "whitelisted_tools": payload.get("whitelisted_tools", []),
        }


    def command_import_step(payload: dict[str, Any]) -> dict[str, Any]:
        from aieng.geometry.step_importer import import_step_package

        step_path = Path(payload["step_path"])
        out_path = Path(payload["out_path"])
        created = import_step_package(step_path, out_path, overwrite=True)
        return {
            "status": "ok",
            "package_path": str(created),
            "package_size": created.stat().st_size,
        }


    def _resolve_topology_backend(requested_backend: str | None) -> str:
        from aieng.geometry.backend import detect_occ_runtime

        backend = (requested_backend or "auto").strip().lower()
        if backend in {"mock", "occ"}:
            return backend
        if backend != "auto":
            return backend

        runtime = detect_occ_runtime()
        if runtime.get("available") and runtime.get("provider") == "OCP":
            return "occ"
        return "mock"


    def command_enrich_package(payload: dict[str, Any]) -> dict[str, Any]:
        from aieng.ai.summary_writer import summarize_package
        from aieng.geometry.topology_extractor import extract_topology_package
        from aieng.graph.aag import build_aag_package
        from aieng.graph.feature_graph import recognize_features_package
        from aieng.validation.completeness_writer import write_completeness_report_package
        from aieng.validation.status_writer import update_validation_status_package

        package_path = Path(payload["package_path"])
        topology_backend = _resolve_topology_backend(payload.get("topology_backend"))
        generated_resources: list[str] = []

        extract_topology_package(package_path, overwrite=True, backend=topology_backend)
        generated_resources.append("geometry/topology_map.json")
        build_aag_package(package_path, overwrite=True)
        generated_resources.append("graph/aag.json")
        recognize_features_package(package_path, overwrite=True)
        generated_resources.append("graph/feature_graph.json")
        update_validation_status_package(package_path, overwrite=True)
        generated_resources.append("validation/status.yaml")
        write_completeness_report_package(package_path, overwrite=True)
        generated_resources.append("validation/completeness_report.json")
        summarize_package(package_path, overwrite=True)
        generated_resources.extend(["README_FOR_AI.md", "ai/summary.md"])

        return {
            "status": "ok",
            "package_path": str(package_path),
            "package_size": package_path.stat().st_size,
            "topology_backend": topology_backend,
            "generated_resources": generated_resources,
        }


    def command_validate_package(payload: dict[str, Any]) -> dict[str, Any]:
        from aieng.validate import validate_package

        package_path = Path(payload["package_path"])
        report = validate_package(package_path)
        messages = [
            {
                "level": getattr(message.level, "value", str(message.level)),
                "text": message.text,
            }
            for message in getattr(report, "messages", [])
        ]
        counts = Counter(item["level"] for item in messages)
        return {
            "ok": bool(getattr(report, "ok", False)),
            "messages": messages,
            "counts": dict(counts),
        }


    def command_package_summary(payload: dict[str, Any]) -> dict[str, Any]:
        from aieng.mcp.server import (
            tool_get_claim_map,
            tool_get_completeness_report,
            tool_get_evidence_index,
            tool_get_evidence_report,
            tool_get_external_tool_requirements,
            tool_get_interfaces,
            tool_get_manifest,
            tool_get_task_spec,
            tool_get_tool_trace,
            tool_get_topology,
            tool_get_validation_status,
        )

        package_path = Path(payload["package_path"])
        validation = command_validate_package(payload)

        with zipfile.ZipFile(package_path) as archive:
            members = sorted(archive.namelist())

        manifest = tool_get_manifest(package_path)
        topology = tool_get_topology(package_path)
        validation_status = tool_get_validation_status(package_path)
        interfaces = tool_get_interfaces(package_path)
        task_spec = tool_get_task_spec(package_path)
        external_tools = tool_get_external_tool_requirements(package_path)
        evidence_index = tool_get_evidence_index(package_path)
        claim_map = tool_get_claim_map(package_path)
        tool_trace = tool_get_tool_trace(package_path)
        completeness = tool_get_completeness_report(package_path)
        evidence_report = tool_get_evidence_report(package_path)
        feature_graph = _read_json_member(package_path, "graph/feature_graph.json")
        ai_summary = _read_text_member(package_path, "ai/summary.md")

        return {
            "members": members,
            "member_count": len(members),
            "manifest": manifest,
            "feature_graph": feature_graph,
            "topology": topology,
            "interfaces": interfaces,
            "task_spec": task_spec,
            "external_tool_requirements": external_tools,
            "claim_map": claim_map,
            "evidence_index": evidence_index,
            "tool_trace": tool_trace,
            "completeness_report": completeness,
            "evidence_report": evidence_report,
            "validation_status": validation_status,
            "validation_report": validation,
            "ai_summary": ai_summary,
            "derived": {
                "feature_graph": _feature_stats(feature_graph),
                "topology": _topology_stats(topology),
                "interfaces": _interface_stats(interfaces),
            },
        }


    def command_mcp_check(payload: dict[str, Any]) -> dict[str, Any]:
        from freecad_mcp.aieng_bridge.context import load_aieng_context
        from freecad_mcp.aieng_bridge.guards import check_operation_allowed
        from freecad_mcp.freecad_runtime import detect_freecad_runtime

        package_path = payload.get("package_path")
        context = load_aieng_context(package_path)
        guard = check_operation_allowed(
            context,
            payload.get("operation", "cad_export_step"),
            target_feature_id=payload.get("target_feature_id"),
            requested_outputs=payload.get("requested_outputs"),
            is_modification=bool(payload.get("is_modification", False)),
        )
        return {
            "guard": guard.model_dump(mode="json"),
            "context": {
                "available": context.available,
                "mode": context.mode,
                "warnings": context.warnings,
                "has_manifest": context.manifest is not None,
                "has_task_spec": context.task_spec is not None,
                "has_feature_graph": context.feature_graph is not None,
                "has_constraints": context.constraints is not None,
                "has_claim_map": context.claim_map is not None,
                "has_reference_map": context.reference_map is not None,
            },
            "runtime": detect_freecad_runtime().model_dump(mode="json"),
            "whitelisted_tools": payload.get("whitelisted_tools", []),
        }


    def command_parse_patch(payload: dict[str, Any]) -> dict[str, Any]:
        from freecad_mcp.aieng_bridge.patch import parse_patch_proposal

        patch_json = payload.get("patch_json") or {}
        plan = parse_patch_proposal(patch_json)
        return {
            "status": "success",
            "plan": plan.model_dump(mode="json"),
            "supported_operation_count": len(plan.operations),
            "unsupported_operation_count": len(plan.unsupported_operations),
        }


    def command_prepare_execution(payload: dict[str, Any]) -> dict[str, Any]:
        from freecad_mcp.aieng_bridge.patch import execute_patch_plan, parse_patch_proposal
        from freecad_mcp.aieng_bridge.stub_executor import StubFreecadExecutor

        patch_json = payload.get("patch_json") or {}
        package_path = payload.get("package_path")
        plan = parse_patch_proposal(patch_json)
        summary = asyncio.run(
            execute_patch_plan(
                plan,
                StubFreecadExecutor(),
                package_path=package_path,
                dry_run=True,
                export_modified_step=bool(payload.get("export_modified_step", False)),
                export_modified_fcstd=bool(payload.get("export_modified_fcstd", False)),
                input_fcstd=payload.get("input_fcstd"),
            )
        )
        return {
            "status": "success",
            "plan": plan.model_dump(mode="json"),
            "preflight": summary.model_dump(mode="json"),
        }


    COMMANDS = {
        "runtime": command_runtime,
        "import_step": command_import_step,
        "enrich_package": command_enrich_package,
        "validate_package": command_validate_package,
        "package_summary": command_package_summary,
        "mcp_check": command_mcp_check,
        "parse_patch": command_parse_patch,
        "prepare_execution": command_prepare_execution,
    }


    def main() -> int:
        try:
            _bootstrap_paths()
            if len(sys.argv) < 2:
                raise ValueError("missing command")
            command = sys.argv[1]
            payload = _read_payload(sys.argv[2] if len(sys.argv) > 2 else None)
            if command not in COMMANDS:
                raise ValueError(f"unknown command: {command}")
            result = COMMANDS[command](payload)
            print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                )
            )
            return 1


    if __name__ == "__main__":
        raise SystemExit(main())
    """
).strip()
