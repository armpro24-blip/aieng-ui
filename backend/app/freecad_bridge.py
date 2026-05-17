"""Bridge: delegates geometry inspection and STEP export to aieng_freecad_mcp.

This module is the sole point of contact between aieng-ui and the
aieng_freecad_mcp package. Imports happen at call time (not at module load)
so the service starts normally even when aieng_freecad_mcp is not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import asyncio


def _load_src(freecad_mcp_root: str | Path) -> None:
    """Inject aieng_freecad_mcp/src into sys.path if not already present."""
    src_path = str(Path(freecad_mcp_root) / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)


def inspect_geometry(
    input_path: str | Path,
    *,
    freecad_cmd: str | Path,
    freecad_mcp_root: str | Path,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run geometry inspection via FreeCADCmd subprocess.

    Imports ``freecad_mcp.geometry_inspector.run_geometry_inspection`` from
    ``freecad_mcp_root/src``.  Raises RuntimeError if the package cannot be
    found, FileNotFoundError if ``input_path`` or ``freecad_cmd`` do not
    exist, or RuntimeError if FreeCADCmd does not produce output.

    Args:
        input_path: Path to a .step, .stp, or .fcstd file.
        freecad_cmd: Path to the FreeCADCmd executable.
        freecad_mcp_root: Root of the aieng_freecad_mcp repo checkout.
        timeout: Seconds before FreeCADCmd is considered hung.
    """
    src_path = str(Path(freecad_mcp_root) / "src")
    _load_src(freecad_mcp_root)

    try:
        from freecad_mcp.geometry_inspector import run_geometry_inspection  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import freecad_mcp.geometry_inspector from {src_path!r}. "
            f"Ensure aieng_freecad_mcp is checked out at {freecad_mcp_root!r}. "
            f"Detail: {exc}"
        ) from exc

    return run_geometry_inspection(input_path, freecad_cmd, timeout=timeout)


def export_step(
    input_path: str | Path,
    output_path: str | Path,
    *,
    freecad_cmd: str | Path,
    freecad_mcp_root: str | Path,
    timeout: int = 120,
) -> dict[str, Any]:
    """Export a CAD file to STEP format via FreeCADCmd subprocess.

    Imports ``freecad_mcp.step_exporter.run_step_export`` from
    ``freecad_mcp_root/src``. Raises RuntimeError if the package cannot be
    found, FileNotFoundError if ``input_path`` or ``freecad_cmd`` do not
    exist, or RuntimeError if FreeCADCmd does not produce output.

    The returned dict includes an ``artifacts`` list with at minimum one entry:
    ``{"path": str, "kind": "step", "role": "primary_geometry"}``.

    Args:
        input_path: Path to a .step, .stp, or .fcstd file.
        output_path: Destination path for the exported STEP file.
        freecad_cmd: Path to the FreeCADCmd executable.
        freecad_mcp_root: Root of the aieng_freecad_mcp repo checkout.
        timeout: Seconds before FreeCADCmd is considered hung.
    """
    src_path = str(Path(freecad_mcp_root) / "src")
    _load_src(freecad_mcp_root)

    try:
        from freecad_mcp.step_exporter import run_step_export  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import freecad_mcp.step_exporter from {src_path!r}. "
            f"Ensure aieng_freecad_mcp is checked out at {freecad_mcp_root!r}. "
            f"Detail: {exc}"
        ) from exc

    return run_step_export(input_path, output_path, freecad_cmd, timeout=timeout)


def run_macro(
    macro_path: str | Path,
    *,
    freecad_cmd: str | Path,
    freecad_mcp_root: str | Path,
    document_path: str | Path | None = None,
    save_document: bool = False,
    timeout: int = 300,
) -> dict[str, Any]:
    """Execute a FreeCAD macro via FreeCADCmd subprocess.

    Imports ``freecad_mcp.macro_runner.run_macro`` from
    ``freecad_mcp_root/src``. Raises RuntimeError if the package cannot be
    found, FileNotFoundError if ``macro_path`` or ``freecad_cmd`` do not
    exist, or RuntimeError if FreeCADCmd does not produce output.

    Args:
        macro_path: Path to the macro file (.FCMacro or .py).
        freecad_cmd: Path to the FreeCADCmd executable.
        freecad_mcp_root: Root of the aieng_freecad_mcp repo checkout.
        document_path: Optional working document (.FCStd or .step) to open
            before executing the macro.
        save_document: If True, save the document after macro execution.
        timeout: Seconds before FreeCADCmd is considered hung.

    Returns:
        A dict with ``status``, ``stdout``, ``stderr``, ``return_code``,
        ``freecad_version``, and optionally ``error`` / ``error_type``.
    """
    src_path = str(Path(freecad_mcp_root) / "src")
    _load_src(freecad_mcp_root)

    try:
        from freecad_mcp.macro_runner import run_macro as _run_macro  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import freecad_mcp.macro_runner from {src_path!r}. "
            f"Ensure aieng_freecad_mcp is checked out at {freecad_mcp_root!r}. "
            f"Detail: {exc}"
        ) from exc

    return _run_macro(
        macro_path,
        freecad_cmd,
        document_path=document_path,
        save_document=save_document,
        timeout=timeout,
    )


def export_computed_metrics(
    input_path: str | Path,
    output_path: str | Path,
    *,
    freecad_mcp_root: str | Path,
    load_case_id: str = "load_case_001",
    software: str | None = None,
    source_files: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize external metrics into ``computed_metrics.json``.

    Imports ``freecad_mcp.computed_metrics_exporter.export_computed_metrics``
    from ``freecad_mcp_root/src``. No FreeCAD or solver is required.

    Args:
        input_path: Path to input JSON or CSV with raw metrics.
        output_path: Destination path for ``computed_metrics.json``.
        freecad_mcp_root: Root of the aieng_freecad_mcp repo checkout.
        load_case_id: Load case identifier written into the output.
        software: Name of the software that produced the original metrics.
        source_files: Original solver result files the metrics were derived from.

    Returns:
        The normalized ``computed_metrics`` dict.
    """
    src_path = str(Path(freecad_mcp_root) / "src")
    _load_src(freecad_mcp_root)

    try:
        from freecad_mcp.computed_metrics_exporter import (  # type: ignore[import]
            export_computed_metrics as _export_computed_metrics,
        )
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import freecad_mcp.computed_metrics_exporter from {src_path!r}. "
            f"Ensure aieng_freecad_mcp is checked out at {freecad_mcp_root!r}. "
            f"Detail: {exc}"
        ) from exc

    return _export_computed_metrics(
        input_path,
        output_path,
        load_case_id=load_case_id,
        software=software,
        source_files=source_files or [],
    )


def edit_parameter(
    package_path: str | Path,
    *,
    feature_id: str,
    parameter_name: str,
    new_value: Any,
    freecad_mcp_root: str | Path,
    input_fcstd: str | Path | None = None,
    artifact_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Execute one guarded parameter edit through the freecad-mcp patch bridge.

    The bridge validates the semantic feature/parameter mapping against the
    package and executes via freecad-mcp's deterministic patch executor. The UI
    runtime remains responsible for approval gating and package write-back.
    """
    _load_src(freecad_mcp_root)

    try:
        from freecad_mcp.aieng_bridge.context import load_aieng_context  # type: ignore[import]
        from freecad_mcp.aieng_bridge.patch import (  # type: ignore[import]
            execute_patch_plan,
            parse_patch_proposal,
        )
        from freecad_mcp.aieng_bridge.stub_executor import StubFreecadExecutor  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import freecad_mcp parameter-edit bridge from {Path(freecad_mcp_root) / 'src'!r}: {exc}"
        ) from exc

    patch = {
        "patch_id": "cad_edit_parameter_runtime",
        "operations": [
            {
                "operation": "modify_parameter",
                "target_feature_id": feature_id,
                "parameter_name": parameter_name,
                "new_value": new_value,
            }
        ],
    }
    context = load_aieng_context(Path(package_path))
    plan = parse_patch_proposal(patch)
    executor = StubFreecadExecutor(context.feature_graph or {})

    async def _run() -> Any:
        return await execute_patch_plan(
            plan,
            executor,
            context=context,
            package_path=Path(package_path),
            input_fcstd=Path(input_fcstd) if input_fcstd else None,
            artifact_output_dir=Path(artifact_output_dir) if artifact_output_dir else None,
            dry_run=False,
            export_modified_step=True,
            export_modified_fcstd=False,
            persist_to_aieng=True,
        )

    summary = asyncio.run(_run())
    payload = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
    return {
        "status": payload.get("status"),
        "summary": payload,
        "artifacts_written": payload.get("artifacts_written", []),
        "warnings": payload.get("warnings", []),
        "errors": payload.get("errors", []),
    }
