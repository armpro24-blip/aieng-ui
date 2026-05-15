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

        summary = generate_cae_result_summary(result_path)
        return {
            "status": "ok",
            "package_path": str(result_path),
            "schema_version": summary.get("schema_version"),
            "artifacts": list(_REFRESH_ARTIFACTS),
        }
    except Exception as exc:
        raise RuntimeError(f"Failed to refresh CAE result summary: {exc}") from exc
    finally:
        if injected:
            try:
                sys.path.remove(candidate)
            except ValueError:
                pass
