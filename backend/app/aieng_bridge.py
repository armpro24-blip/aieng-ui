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


def validate_package(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
) -> dict[str, Any]:
    """Validate a .aieng package against AIENG schemas and rules.

    Imports ``aieng.validate.validate_package`` from ``aieng_root/src``.
    Raises RuntimeError if the import fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.

    Returns:
        Dict with status, ok, messages (list of {level, text}), and summary counts.
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
        from aieng.validate import validate_package as _validate_package  # type: ignore[import]

        report = _validate_package(pkg)
        messages = [
            {"level": msg.level.value, "text": msg.text}
            for msg in report.messages
        ]
        counts: dict[str, int] = {}
        for msg in report.messages:
            level = msg.level.value
            counts[level] = counts.get(level, 0) + 1
        return {
            "status": "ok",
            "package_path": str(pkg),
            "ok": report.ok,
            "messages": messages,
            "counts": counts,
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to validate package: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def extract_field_regions(
    package_path: str | Path,
    frd_path: str | Path,
    *,
    aieng_root: str | Path,
    field: str = "S",
    metric: str = "von_mises",
    max_clusters: int = 3,
    threshold_percentile: float = 90.0,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Extract high-magnitude field regions from a CalculiX FRD file.

    Imports ``aieng.simulation.field_region_extractor.extract_field_regions_package``
    from ``aieng_root/src``. Raises RuntimeError if the import or extraction fails.

    Args:
        package_path: Path to the .aieng package.
        frd_path: Path to the CalculiX .frd result file.
        aieng_root: Root of the aieng repo checkout.
        field: FRD field name to analyse (``'S'`` or ``'DISP'``).
        metric: Metric to compute per node (``'von_mises'`` or ``'magnitude'``).
        max_clusters: Maximum number of clusters to return.
        threshold_percentile: Percentile cutoff for high-magnitude nodes.
        overwrite: Whether to overwrite an existing field_regions.json.

    Returns:
        Dict with status, out_path, cluster_count, clusters, and warnings.
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
        from aieng.simulation.field_region_extractor import (  # type: ignore[import]
            FieldRegionError,
            extract_field_regions_package,
        )

        result = extract_field_regions_package(
            pkg,
            frd,
            field=field,
            metric=metric,
            max_clusters=max_clusters,
            threshold_percentile=threshold_percentile,
            overwrite=overwrite,
        )
        return {
            "status": "ok",
            "package_path": str(pkg),
            "out_path": result.get("out_path"),
            "cluster_count": result.get("cluster_count", 0),
            "clusters": result.get("clusters", []),
            "warnings": result.get("warnings", []),
        }
    except FieldRegionError as exc:
        raise ValueError(str(exc)) from exc
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to extract field regions: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def write_field_summary(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write field summary artifacts derived from results/field_regions.json."""
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
        from aieng.cae_field_summary import write_field_summary_package  # type: ignore[import]

        write_field_summary_package(pkg, overwrite=overwrite)
        return {
            "status": "ok",
            "package_path": str(pkg),
            "artifacts": [
                {"path": "results/field_summary.json", "kind": "field_summary", "role": "llm_field_summary"},
                {"path": "results/field_summary.md", "kind": "markdown", "role": "llm_field_summary"},
            ],
        }
    except ModuleNotFoundError as exc:
        if "cae_field_summary" in str(exc):
            return {
                "status": "skipped",
                "package_path": str(pkg),
                "reason": "aieng.cae_field_summary is not available (removed in core)",
                "artifacts": [],
            }
        raise
    except FileNotFoundError as exc:
        if "field_regions" in str(exc):
            return {
                "status": "skipped",
                "package_path": str(pkg),
                "reason": f"Field regions artifact missing: {exc}",
                "artifacts": [],
            }
        raise
    except (FileExistsError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to write field summary: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def generate_solver_input(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    run_id: str = "run_001",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Generate a runnable CalculiX solver input deck from a .aieng package.

    Imports ``aieng.simulation.deck_generator.generate_solver_input_package``
    from ``aieng_root/src``. Raises RuntimeError if the import or generation fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.
        run_id: Solver run identifier.
        overwrite: Whether to overwrite an existing solver input deck.

    Returns:
        Dict with status, out_path, missing_items, and warnings.
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
        from aieng.simulation.deck_generator import (  # type: ignore[import]
            MissingSetupError,
            generate_solver_input_package,
        )

        result = generate_solver_input_package(
            pkg,
            run_id=run_id,
            overwrite=overwrite,
        )
        return {
            "status": "ok",
            "package_path": str(pkg),
            "out_path": result.get("out_path"),
            "missing_items": result.get("missing_items", []),
            "warnings": result.get("warnings", []),
        }
    except MissingSetupError as exc:
        raise ValueError(str(exc)) from exc
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to generate solver input: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def write_completeness_report(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a completeness report into a .aieng package.

    Imports ``aieng.validation.completeness_writer.write_completeness_report_package``
    from ``aieng_root/src``. Raises RuntimeError if the import or write fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.
        overwrite: Whether to overwrite an existing completeness report.

    Returns:
        Dict with status, package_path, and completeness report artifact.
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
        from aieng.validation.completeness_writer import write_completeness_report_package  # type: ignore[import]

        result_path = write_completeness_report_package(pkg, overwrite=overwrite)
        return {
            "status": "ok",
            "package_path": str(result_path),
            "artifacts": [
                {
                    "path": "validation/completeness_report.json",
                    "kind": "completeness_report",
                    "role": "package_completeness_assessment",
                }
            ],
        }
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to write completeness report: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def update_validation_status(
    package_path: str | Path,
    *,
    aieng_root: str | Path,
    overwrite: bool = False,
    extra_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update validation status inside a .aieng package.

    Imports ``aieng.validation.status_writer.update_validation_status_package``
    from ``aieng_root/src``. Raises RuntimeError if the import or write fails.

    Args:
        package_path: Path to the .aieng package.
        aieng_root: Root of the aieng repo checkout.
        overwrite: Whether to overwrite an existing validation status.
        extra_status: Optional extra status fields to merge.

    Returns:
        Dict with status, package_path, and validation status artifact.
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
        from aieng.validation.status_writer import update_validation_status_package  # type: ignore[import]

        result_path = update_validation_status_package(pkg, overwrite=overwrite, extra_status=extra_status)
        return {
            "status": "ok",
            "package_path": str(result_path),
            "artifacts": [
                {
                    "path": "validation/status.yaml",
                    "kind": "validation_status",
                    "role": "package_validation_status",
                }
            ],
        }
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to update validation status: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass


def convert_source_to_package(
    source_path: str | Path,
    out_path: str | Path,
    *,
    aieng_root: str | Path,
    model_id: str | None = None,
    converter_id: str | None = None,
    overwrite: bool = False,
    runtime_mode: str = "auto",
) -> dict[str, Any]:
    """Convert a CAD source file (.FCStd or .step) to a .aieng package.

    Imports ``aieng.converters.cli_runners.convert_source`` and
    ``aieng.geometry.step_importer.import_step_package`` from
    ``aieng_root/src``. Raises RuntimeError if the import or conversion fails.

    Args:
        source_path: Path to the source CAD file (.FCStd or .step/.stp).
        out_path: Path for the output .aieng package.
        aieng_root: Root of the aieng repo checkout.
        model_id: Optional model ID; inferred from out_path stem if omitted.
        converter_id: Optional converter ID for FCStd sources.
        overwrite: Whether to overwrite an existing output package.
        runtime_mode: Runtime mode for FCStd conversion ("auto", "offline", "runtime").

    Returns:
        Dict with status, out_path, converter_id, and source_type.
    """
    src = Path(source_path)
    out = Path(out_path)

    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")
    if not src.is_file():
        raise ValueError(f"Source path is not a file: {src}")

    aieng_src = Path(aieng_root) / "src"
    if not aieng_src.exists():
        raise RuntimeError(f"aieng src not found at {aieng_src}")

    injected = False
    try:
        candidate = str(aieng_src)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            injected = True

        if model_id is None:
            from aieng.package import model_id_from_package_path  # type: ignore[import]

            model_id = model_id_from_package_path(out)

        suffix = src.suffix.lower()
        if suffix in {".fcstd"}:
            from aieng.converters.cli_runners import convert_source  # type: ignore[import]

            result_path = convert_source(
                source_path=src,
                out=out,
                model_id=model_id,
                converter_id=converter_id,
                overwrite=overwrite,
                runtime_mode=runtime_mode,
            )
            return {
                "status": "ok",
                "out_path": str(result_path),
                "converter_id": converter_id or "freecad_reference",
                "source_type": "fcstd",
            }
        elif suffix in {".step", ".stp"}:
            from aieng.geometry.step_importer import import_step_package  # type: ignore[import]

            result_path = import_step_package(src, out, overwrite=overwrite)
            return {
                "status": "ok",
                "out_path": str(result_path),
                "converter_id": converter_id or "step_importer",
                "source_type": "step",
            }
        else:
            raise ValueError(
                f"Unsupported source file extension: {suffix!r}. "
                f"Supported: .FCStd, .step, .stp"
            )
    except (FileNotFoundError, ValueError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to convert source: {exc}") from exc
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
