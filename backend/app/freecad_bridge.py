"""Bridge: delegates geometry inspection and STEP export to aieng_freecad_mcp.

This module is the sole point of contact between aieng-ui and the
aieng_freecad_mcp package. Imports happen at call time (not at module load)
so the service starts normally even when aieng_freecad_mcp is not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


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
