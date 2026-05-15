from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class CadExecutionProvider(Protocol):
    provider: str

    def probe_capabilities(self, *, whitelisted_tools: list[str]) -> dict[str, Any]:
        """Return provider health and runtime capabilities."""

    def import_step_to_package(self, *, step_path: Path, out_path: Path) -> dict[str, Any]:
        """Create a package from a STEP source."""

    def enrich_package(self, *, package_path: Path, topology_backend: str) -> dict[str, Any]:
        """Generate topology/features/summaries for an existing package."""

    def validate_package(self, *, package_path: Path) -> dict[str, Any]:
        """Validate an existing package."""

    def package_summary_snapshot(self, *, package_path: Path) -> dict[str, Any]:
        """Return package summary payload used by the platform."""

    def check_mcp_operation(
        self,
        *,
        package_path: str | None,
        payload: dict[str, Any],
        whitelisted_tools: list[str],
    ) -> dict[str, Any]:
        """Run provider-specific MCP guard checks."""

    def parse_patch_proposal(self, *, patch_json: dict[str, Any]) -> dict[str, Any]:
        """Parse a patch proposal without executing it."""

    def prepare_patch_preflight(self, *, package_path: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        """Prepare a dry-run execution summary for a patch."""

    def export_step_preview_to_stl(self, *, step_path: Path, stl_path: Path) -> dict[str, Any]:
        """Export an STL preview from a STEP source."""
