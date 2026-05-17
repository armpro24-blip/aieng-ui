from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


MCP_BRIDGE_TOOLS = {"mcp.check", "mcp.parse_patch", "mcp.prepare_execution"}


def sanitize_llm_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if k != "api_key"}


def _inject_path(path: Path) -> tuple[str, bool]:
    candidate = str(path)
    if candidate in sys.path:
        return candidate, False
    sys.path.insert(0, candidate)
    return candidate, True


def _remove_path(candidate: str, injected: bool) -> None:
    if injected:
        try:
            sys.path.remove(candidate)
        except ValueError:
            pass


def _coerce_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed


def _build_provider(settings: Any, llm_config: dict[str, Any]) -> Any:
    src = settings.aieng_root / "src"
    candidate, injected = _inject_path(src)
    try:
        from aieng.benchmarking.providers import ProviderConfig, build_provider

        config = ProviderConfig(
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
        return build_provider(config)
    finally:
        _remove_path(candidate, injected)


def _compact_context(project_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(project_summary, dict):
        return {}
    cae = project_summary.get("cae") if isinstance(project_summary.get("cae"), dict) else {}
    return {
        "project": project_summary.get("project"),
        "manifest": project_summary.get("manifest"),
        "validation": project_summary.get("validation"),
        "derived": project_summary.get("derived"),
        "viewer": project_summary.get("viewer"),
        "cae": {
            "present": cae.get("present"),
            "results_available": cae.get("results_available"),
            "available_fields": cae.get("available_fields") or [],
            "constraints_count": cae.get("constraints_count"),
            "loads_count": cae.get("loads_count"),
            "evidence_count": cae.get("evidence_count"),
        },
    }


def _tool_names(runtime_tools: list[dict[str, Any]]) -> set[str]:
    return {str(tool.get("name")) for tool in runtime_tools if tool.get("name")}


def _tool_requires_approval(runtime_tools: list[dict[str, Any]], tool_name: str) -> bool:
    for tool in runtime_tools:
        if tool.get("name") == tool_name:
            return bool(tool.get("requires_approval"))
    return False


def _step(
    step_id: str,
    kind: str,
    tool_name: str,
    description: str,
    inputs: dict[str, Any] | None = None,
    approval_required: bool = False,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "kind": kind,
        "tool_name": tool_name,
        "name": tool_name,
        "description": description,
        "input": inputs or {},
        "status": "pending",
        "approval_required": approval_required,
    }


def heuristic_agent_plan(
    *,
    message: str,
    project_id: str | None,
    patch_json: dict[str, Any] | None,
    runtime_tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], str]:
    text = message.lower()
    tools = _tool_names(runtime_tools)
    base_input = {"project_id": project_id} if project_id else {}
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []

    if not project_id:
        warnings.append("No project_id was provided; agent can explain a plan but cannot inspect or execute project tools.")

    if project_id and "aieng.inspect_package" in tools:
        steps.append(_step("inspect", "tool", "aieng.inspect_package", "Read current .aieng package context.", base_input))

    wants_geometry = any(token in text for token in ["geometry", "几何", "cad", "建模", "模型", "减重", "孔", "厚度", "inspect"])
    if project_id and wants_geometry and "freecad.inspect_geometry" in tools:
        steps.append(_step("geometry", "tool", "freecad.inspect_geometry", "Inspect CAD geometry before proposing edits.", base_input))

    wants_modification = patch_json is not None or any(
        token in text
        for token in ["建模", "修改", "改", "减重", "加孔", "打孔", "厚度", "apply", "patch", "edit", "model"]
    )
    if wants_modification:
        if project_id and "mcp.check" in tools:
            steps.append(
                _step(
                    "mcp_check",
                    "tool",
                    "mcp.check",
                    "Check MCP guardrails and capability gaps for the requested CAD operation.",
                    {
                        **base_input,
                        "operation": "cad_set_parameter" if patch_json else "cad_modeling_request",
                        "is_modification": True,
                        "requested_outputs": ["preview", "modified_artifact", "tool_trace"],
                    },
                )
            )
        if project_id and patch_json:
            if "mcp.parse_patch" in tools:
                steps.append(
                    _step(
                        "parse_patch",
                        "tool",
                        "mcp.parse_patch",
                        "Parse the provided .aieng patch proposal without executing it.",
                        {**base_input, "patch_json": patch_json},
                    )
                )
            if "mcp.prepare_execution" in tools:
                steps.append(
                    _step(
                        "preflight_patch",
                        "tool",
                        "mcp.prepare_execution",
                        "Dry-run patch execution using the MCP bridge and return side effects.",
                        {**base_input, "patch_json": patch_json},
                    )
                )
        else:
            warnings.append(
                "Modeling request detected, but no executable patch_json was provided. "
                "The agent can inspect and preflight capability gaps, then ask for a concrete patch proposal."
            )

    if project_id and any(token in text for token in ["preview", "预览", "glb", "stl", "刷新"]) and "aieng.generate_preview" in tools:
        steps.append(_step("preview", "tool", "aieng.generate_preview", "Refresh the web preview artifact.", base_input))

    if project_id and any(token in text for token in ["export", "导出", "step"]) and "freecad.export_step" in tools:
        steps.append(_step("export_step", "tool", "freecad.export_step", "Export a STEP artifact.", base_input))

    if project_id and not steps and "aieng.inspect_package" in tools:
        steps.append(_step("inspect", "tool", "aieng.inspect_package", "Default safe inspection.", base_input))

    reply = (
        "I built a guarded agent plan. Mutating CAD work is limited to MCP preflight unless a concrete, supported patch is supplied."
    )
    return steps, warnings, reply


def llm_agent_plan(
    *,
    settings: Any,
    message: str,
    project_id: str | None,
    project_summary: dict[str, Any] | None,
    runtime_tools: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    llm_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str], str, str | None]:
    provider = _build_provider(settings, llm_config)
    executable_tools = [
        {
            "name": tool.get("name"),
            "requires_approval": bool(tool.get("requires_approval")),
            "description": tool.get("description"),
        }
        for tool in runtime_tools
    ]
    capability_brief = [
        {
            "name": cap.get("name"),
            "source": cap.get("source"),
            "category": cap.get("category"),
            "mutates_cad": cap.get("mutates_cad"),
            "mutates_package": cap.get("mutates_package"),
            "available": cap.get("available"),
        }
        for cap in capabilities[:80]
    ]
    system_prompt = (
        "You are an engineering CAD/CAE planning agent. Return only JSON. "
        "You may propose steps only using the provided executable runtime tools. "
        "Do not invent tools. For CAD mutation, prefer mcp.check, mcp.parse_patch, "
        "and mcp.prepare_execution; do not execute unsupported arbitrary modeling. "
        "Never update claims unless an explicit claim update tool is provided."
    )
    user_prompt = json.dumps(
        {
            "user_message": message,
            "project_id": project_id,
            "project_context": _compact_context(project_summary),
            "executable_runtime_tools": executable_tools,
            "capabilities": capability_brief,
            "response_schema": {
                "reply": "short human-readable response",
                "warnings": ["list of warnings or missing inputs"],
                "steps": [
                    {
                        "id": "stable id",
                        "tool_name": "one executable_runtime_tools.name",
                        "description": "what this step does",
                        "input": {"project_id": project_id},
                    }
                ],
            },
        },
        ensure_ascii=False,
    )
    raw = provider.generate(system_prompt=system_prompt, user_prompt=user_prompt)
    parsed = _coerce_json_object(raw)
    raw_steps = parsed.get("steps") if isinstance(parsed.get("steps"), list) else []
    tool_set = _tool_names(runtime_tools)
    mcp_tool_names = MCP_BRIDGE_TOOLS | {
        str(cap.get("name"))
        for cap in capabilities
        if str(cap.get("source") or "").lower().endswith("mcp")
    }
    steps: list[dict[str, Any]] = []
    warnings = [str(item) for item in parsed.get("warnings") or []]
    base_input = {"project_id": project_id} if project_id else {}

    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or item.get("name") or "")
        if tool_name not in tool_set:
            warnings.append(f"LLM proposed unavailable tool and it was dropped: {tool_name}")
            continue
        step_input = item.get("input") if isinstance(item.get("input"), dict) else {}
        merged = {**base_input, **step_input}
        steps.append(
            _step(
                str(item.get("id") or f"step_{index + 1}"),
                "mcp_tool" if tool_name in mcp_tool_names else "tool",
                tool_name,
                str(item.get("description") or tool_name),
                merged,
                approval_required=_tool_requires_approval(runtime_tools, tool_name),
            )
        )
    return steps, warnings, str(parsed.get("reply") or "I built a guarded agent plan."), raw


def build_agent_plan(
    *,
    settings: Any,
    message: str,
    project_id: str | None,
    project_summary: dict[str, Any] | None,
    runtime_tools: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    llm_config: dict[str, Any],
    patch_json: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    llm_raw: str | None = None
    mode = "heuristic"
    if llm_config and not dry_run:
        try:
            steps, warnings, reply, llm_raw = llm_agent_plan(
                settings=settings,
                message=message,
                project_id=project_id,
                project_summary=project_summary,
                runtime_tools=runtime_tools,
                capabilities=capabilities,
                llm_config=llm_config,
            )
            mode = "llm"
        except Exception as exc:
            steps, warnings, reply = heuristic_agent_plan(
                message=message,
                project_id=project_id,
                patch_json=patch_json,
                runtime_tools=runtime_tools,
            )
            errors.append(f"LLM planning unavailable; used heuristic planner: {type(exc).__name__}: {exc}")
    else:
        steps, warnings, reply = heuristic_agent_plan(
            message=message,
            project_id=project_id,
            patch_json=patch_json,
            runtime_tools=runtime_tools,
        )

    runtime_tool_names = _tool_names(runtime_tools)
    filtered: list[dict[str, Any]] = []
    for step in steps:
        tool_name = str(step.get("tool_name") or step.get("name") or "")
        if tool_name not in runtime_tool_names:
            warnings.append(f"Dropped unavailable runtime tool: {tool_name}")
            continue
        step["approval_required"] = bool(step.get("approval_required")) or _tool_requires_approval(runtime_tools, tool_name)
        filtered.append(step)

    mutating = any(step.get("approval_required") for step in filtered)
    return {
        "reply": reply,
        "mode": mode,
        "message": message,
        "project_id": project_id,
        "steps": filtered,
        "requires_approval": mutating,
        "preview": {
            "step_count": len(filtered),
            "tools": [step.get("tool_name") for step in filtered],
            "would_execute": [step.get("tool_name") for step in filtered if not step.get("approval_required")],
            "approval_gated": [step.get("tool_name") for step in filtered if step.get("approval_required")],
            "side_effects": [
                "Runtime events and audit records are written.",
                "MCP bridge steps are dry-run/preflight unless an explicit execution tool is wired.",
            ],
            "warnings": warnings,
        },
        "warnings": warnings,
        "errors": errors,
        "llm_raw": llm_raw,
        "llm_config": sanitize_llm_config(llm_config),
    }
