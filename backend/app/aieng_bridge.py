"""Bridge: delegates CAE summary generation to aieng.

This module is the sole point of contact between aieng-ui and the
aieng package for CAE result summary operations. Imports happen at call time
so the service starts normally even when aieng is not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


_REFRESH_ARTIFACTS = [
    {"path": "results/result_summary.json", "kind": "cae_result_summary", "role": "llm_readable_postprocessing_summary"},
    {"path": "results/evidence_index.json", "kind": "evidence_index", "role": "cae_evidence_catalog"},
    {"path": "results/postprocessing_summary.md", "kind": "markdown_summary", "role": "human_llm_readable_summary"},
]


def _check_schema_version(
    actual: str | None,
    expected: str,
    artifact: str,
) -> list[str]:
    """Compare an on-disk schema_version against the expected constant.

    Returns a list with one human-readable warning if there's a mismatch or
    missing version, otherwise an empty list. The frontend surfaces the
    warnings array verbatim in the chat panel.
    """
    if actual is None:
        return [f"{artifact}: schema_version missing on disk; regenerate to refresh."]
    if actual != expected:
        return [
            f"{artifact}: schema_version {actual!r} on disk, "
            f"expected {expected!r}; regenerate."
        ]
    return []

_PREPROCESSING_ARTIFACTS = [
    {"path": "simulation/preprocessing_summary.json", "kind": "cae_preprocessing_summary", "role": "preprocessing_readiness_summary"},
    {"path": "simulation/preprocessing_summary.md", "kind": "markdown_summary", "role": "preprocessing_markdown_summary"},
]


def refresh_cae_result_summary(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Regenerate CAE result summary artifacts inside a .aieng package.

    Imports ``aieng.cae_result_summary.write_cae_result_summary_package``
    from ``aieng_root/src``. Raises RuntimeError if the package cannot be
    found or the write fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.
        overwrite: Whether to overwrite existing summary files.

    Returns:
        Dict with status, package_path, schema_version, and artifacts list.
    """
    path = Path(package_path)
    if not path.exists():
        raise FileNotFoundError(f"Package not found: {path}")

    aieng_src = Path(aieng_root) / "src"
    if not aieng_src.exists():
        raise RuntimeError(f"aieng src not found at {aieng_src}")

    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_result_summary import write_cae_result_summary_package  # type: ignore[import]

        result_path = write_cae_result_summary_package(path, overwrite=overwrite)
        # Re-read the generated summary to return its schema version
        from aieng.cae_result_summary import generate_cae_result_summary  # type: ignore[import]
        from aieng.schema_versions import CAE_RESULT_SUMMARY_SCHEMA  # type: ignore[import]

        summary = generate_cae_result_summary(result_path)
        warnings = _check_schema_version(
            summary.get("schema_version"),
            CAE_RESULT_SUMMARY_SCHEMA,
            "cae_result_summary",
        )
        return {
            "status": "ok",
            "package_path": str(result_path),
            "schema_version": summary.get("schema_version"),
            "artifacts": list(_REFRESH_ARTIFACTS),
            "warnings": warnings,
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to refresh CAE result summary: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def refresh_preprocessing_summary(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Regenerate preprocessing summary artifacts inside a .aieng package.

    Imports ``aieng.cae_preprocessing_summary.write_preprocessing_summary_package``
    from ``aieng_root/src``. Raises RuntimeError if the package cannot be
    found or the write fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.
        overwrite: Whether to overwrite existing summary files.

    Returns:
        Dict with status, package_path, schema_version, and artifacts list.
    """
    path = Path(package_path)
    if not path.exists():
        raise FileNotFoundError(f"Package not found: {path}")

    aieng_src = Path(aieng_root) / "src"
    if not aieng_src.exists():
        raise RuntimeError(f"aieng src not found at {aieng_src}")

    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.cae_preprocessing_summary import write_preprocessing_summary_package  # type: ignore[import]

        result_path = write_preprocessing_summary_package(path, overwrite=overwrite)
        from aieng.cae_preprocessing_summary import generate_preprocessing_summary  # type: ignore[import]
        from aieng.schema_versions import CAE_PREPROCESSING_SUMMARY_SCHEMA  # type: ignore[import]

        summary = generate_preprocessing_summary(result_path)
        warnings = _check_schema_version(
            summary.get("schema_version"),
            CAE_PREPROCESSING_SUMMARY_SCHEMA,
            "cae_preprocessing_summary",
        )
        return {
            "status": "ok",
            "package_path": str(result_path),
            "schema_version": summary.get("schema_version"),
            "artifacts": list(_PREPROCESSING_ARTIFACTS),
            "warnings": warnings,
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to refresh preprocessing summary: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def extract_frd_solver_results(
    package_path: str | Path,
    frd_path: str | Path,
    *,
    aieng_root: str | Path,
    load_case_id: str = "load_case_001",
    software: str = "CalculiX",
    overwrite: bool = True,
) -> dict[str, Any]:
    """Parse a CalculiX FRD file and write computed_metrics.json into a package.

    Imports ``aieng.simulation.frd_result_extractor.write_computed_metrics_package``
    from ``aieng_root/src``. Raises RuntimeError if the import fails.

    Args:
        package_path: Path to the .aieng package.
        frd_path: Path to the CalculiX .frd result file.
        aieng_root: Root of the aieng repo checkout.
        load_case_id: Load case identifier.
        software: Solver software name for metrics_source.
        overwrite: Whether to overwrite an existing computed_metrics.json.

    Returns:
        Dict with status, package_path, metrics (the computed_metrics dict),
        and artifacts list.
    """
    pkg = Path(package_path)
    frd = Path(frd_path)
    if not pkg.exists():
        raise FileNotFoundError(f"Package not found: {pkg}")
    if not frd.exists():
        raise FileNotFoundError(f"FRD file not found: {frd}")

    aieng_src = Path(aieng_root) / "src"
    if not aieng_src.exists():
        raise RuntimeError(f"aieng src not found at {aieng_src}")

    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.simulation.frd_result_extractor import write_computed_metrics_package  # type: ignore[import]

        metrics = write_computed_metrics_package(
            pkg,
            frd,
            load_case_id=load_case_id,
            software=software,
            overwrite=overwrite,
        )
        return {
            "status": "ok",
            "package_path": str(pkg),
            "metrics": metrics,
            "artifacts": [
                {
                    "path": "results/computed_metrics.json",
                    "kind": "computed_metrics",
                    "role": "frd_extracted_postprocessing_metrics",
                }
            ],
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to extract FRD solver results: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def write_mesh_handoff(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    overwrite: bool = False,
    handoff_id: str = "mesh_handoff_001",
) -> dict[str, Any]:
    """Write a mesh handoff contract into a .aieng package.

    Imports ``aieng.simulation.mesh_handoff_writer.write_mesh_handoff_package``
    from ``aieng_root/src``. Raises RuntimeError if the import or write fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.
        overwrite: Whether to overwrite an existing mesh handoff contract.
        handoff_id: Identifier for this handoff contract.

    Returns:
        Dict with status, package_path, and the handoff contract artifact.
    """
    pkg = Path(package_path)
    if not pkg.exists():
        raise FileNotFoundError(f"Package not found: {pkg}")

    aieng_src = Path(aieng_root) / "src"
    if not aieng_src.exists():
        raise RuntimeError(f"aieng src not found at {aieng_src}")

    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.simulation.mesh_handoff_writer import write_mesh_handoff_package  # type: ignore[import]

        result_path = write_mesh_handoff_package(
            pkg,
            overwrite=overwrite,
            handoff_id=handoff_id,
        )
        return {
            "status": "ok",
            "package_path": str(result_path),
            "artifacts": [
                {
                    "path": "simulation/mesh_handoff_contract.json",
                    "kind": "mesh_handoff_contract",
                    "role": "external_mesher_handoff_spec",
                }
            ],
        }
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to write mesh handoff contract: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def import_solver_evidence(
    package_path: str | Path,
    result_file: str | Path,
    *,
    aieng_root: str | Path,
    result_format: str = "calculix_dat",
    producer_tool: str = "calculix",
    claim_support: list[str] | None = None,
    verification_status: str = "unverified",
    evidence_id: str | None = None,
) -> dict[str, Any]:
    """Import external solver result evidence into a .aieng package.

    Imports ``aieng.simulation.solver_evidence_importer.import_solver_evidence_package``
    from ``aieng_root/src``. Raises RuntimeError if the import or write fails.

    Args:
        package_path: Path to the .aieng package.
        result_file: Path to the solver result file.
        aieng_root: Root of the aieng repo checkout.
        result_format: Format of the result file (e.g. "calculix_dat").
        producer_tool: Name of the solver tool that produced the result.
        claim_support: List of claim IDs this evidence supports.
        verification_status: Verification status for the evidence.
        evidence_id: Optional explicit evidence ID.

    Returns:
        Dict with status, package_path, evidence_id, and summary.
    """
    pkg = Path(package_path)
    result = Path(result_file)
    if not pkg.exists():
        raise FileNotFoundError(f"Package not found: {pkg}")
    if not result.exists():
        raise FileNotFoundError(f"Result file not found: {result}")

    aieng_src = Path(aieng_root) / "src"
    if not aieng_src.exists():
        raise RuntimeError(f"aieng src not found at {aieng_src}")

    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.simulation.solver_evidence_importer import import_solver_evidence_package  # type: ignore[import]

        out_path, summary = import_solver_evidence_package(
            pkg,
            result_file=result,
            result_format=result_format,
            producer_tool=producer_tool,
            claim_support=claim_support or ["claim_solver_result_001"],
            verification_status=verification_status,
            evidence_id=evidence_id,
        )
        return {
            "status": "ok",
            "package_path": str(out_path),
            "evidence_id": summary.get("evidence_id", evidence_id),
            "summary": summary,
            "artifacts": [
                {
                    "path": "results/evidence_index.json",
                    "kind": "evidence_index",
                    "role": "solver_evidence_catalog",
                }
            ],
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to import solver evidence: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def write_evidence_scaffold(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write evidence scaffold (evidence_index.json + claim_map.json) into a .aieng package.

    Imports ``aieng.results.evidence_writer.write_evidence_scaffold_package``
    from ``aieng_root/src``. Raises RuntimeError if the import or write fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.
        overwrite: Whether to overwrite existing evidence scaffold files.

    Returns:
        Dict with status, package_path, and scaffold artifact paths.
    """
    pkg = Path(package_path)
    if not pkg.exists():
        raise FileNotFoundError(f"Package not found: {pkg}")

    aieng_src = Path(aieng_root) / "src"
    if not aieng_src.exists():
        raise RuntimeError(f"aieng src not found at {aieng_src}")

    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True
        from aieng.results.evidence_writer import write_evidence_scaffold_package  # type: ignore[import]

        result_path = write_evidence_scaffold_package(pkg, overwrite=overwrite)
        return {
            "status": "ok",
            "package_path": str(result_path),
            "artifacts": [
                {"path": "results/evidence_index.json", "kind": "evidence_index", "role": "evidence_catalog"},
                {"path": "results/claim_map.json", "kind": "claim_map", "role": "claim_evidence_map"},
            ],
        }
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to write evidence scaffold: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass
