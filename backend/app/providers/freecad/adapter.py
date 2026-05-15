from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .bridge_runner import BRIDGE_RUNNER_SOURCE
from .preview import FREECAD_PREVIEW_SCRIPT


class FreeCADAdapter:
    provider = "freecad"

    def __init__(self, *, settings: Any, config: dict[str, str]) -> None:
        self.settings = settings
        self.config = config

    @staticmethod
    def _parse_process_json(output: str) -> dict[str, Any]:
        for line in reversed([item.strip() for item in output.splitlines() if item.strip()]):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        raise ValueError(f"command did not produce JSON output: {output[:400]}")

    @staticmethod
    def _run_process(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    @staticmethod
    def _resolve_topology_backend_choice(requested_backend: str | None) -> str:
        backend = (requested_backend or "auto").strip().lower()
        if backend in {"mock", "occ"}:
            return backend
        if backend != "auto":
            return backend
        if importlib.util.find_spec("OCP") is not None:
            return "occ"
        return "mock"

    def _run_bridge(self, command: str, payload: dict[str, Any], *, timeout: int = 180) -> dict[str, Any]:
        self.settings.data_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aieng-platform-bridge-") as temp_dir:
            temp_root = Path(temp_dir)
            payload_path = temp_root / "payload.json"
            runner_path = temp_root / "bridge_runner.py"
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            runner_path.write_text(BRIDGE_RUNNER_SOURCE + "\n", encoding="utf-8")
            env = {
                **os.environ,
                "AIENG_ROOT": str(self.settings.aieng_root),
                "FREECAD_MCP_ROOT": str(self.settings.freecad_mcp_root),
                "FREECAD_MCP_FREECAD_PATH": str(self.settings.freecad_home),
                "PYTHONIOENCODING": "utf-8",
            }
            completed = self._run_process(
                [str(self.settings.freecad_python), str(runner_path), command, str(payload_path)],
                env=env,
                cwd=self.settings.platform_root,
                timeout=timeout,
            )
            envelope = self._parse_process_json(completed.stdout)
            if completed.returncode != 0 or not envelope.get("ok"):
                raise RuntimeError(
                    f"bridge command '{command}' failed: {envelope.get('error') or completed.stderr or completed.stdout}"
                )
            return envelope["result"]

    def probe_capabilities(self, *, whitelisted_tools: list[str]) -> dict[str, Any]:
        topology_backend_requested = self.config.get("topology_backend", "auto")
        topology_backend_resolved = self._resolve_topology_backend_choice(topology_backend_requested)
        probe: dict[str, Any] = {
            "provider": self.config["provider"],
            "topology_backend_requested": topology_backend_requested,
            "topology_backend_resolved": topology_backend_resolved,
            "aieng_root": str(self.settings.aieng_root),
            "aieng_src_exists": (self.settings.aieng_root / "src").exists(),
            "freecad_mcp_root": str(self.settings.freecad_mcp_root),
            "freecad_mcp_src_exists": (self.settings.freecad_mcp_root / "src").exists(),
            "freecad_home": str(self.settings.freecad_home),
            "freecad_cmd": str(self.settings.freecad_cmd),
            "freecad_python": str(self.settings.freecad_python),
            "freecad_cmd_exists": self.settings.freecad_cmd.exists(),
            "freecad_python_exists": self.settings.freecad_python.exists(),
            "whitelisted_tools": whitelisted_tools,
        }
        issues: list[str] = []
        if not probe["aieng_src_exists"]:
            issues.append("AIENG_ROOT/src 不存在")
        if not probe["freecad_mcp_src_exists"]:
            issues.append("FREECAD_MCP_ROOT/src 不存在")
        if not probe["freecad_cmd_exists"]:
            issues.append("FreeCADCmd.exe 不存在")
        if not probe["freecad_python_exists"]:
            issues.append("FreeCAD python.exe 不存在")
        try:
            probe["bridge"] = self._run_bridge("runtime", {"whitelisted_tools": whitelisted_tools}, timeout=120)
        except Exception as exc:
            probe["bridge_error"] = f"{type(exc).__name__}: {exc}"
            issues.append("bridge runtime 探测失败")
        probe["issues"] = issues
        probe["ready"] = not issues
        return probe

    def import_step_to_package(self, *, step_path: Path, out_path: Path) -> dict[str, Any]:
        return self._run_bridge(
            "import_step",
            {"step_path": str(step_path), "out_path": str(out_path)},
            timeout=240,
        )

    def enrich_package(self, *, package_path: Path, topology_backend: str) -> dict[str, Any]:
        return self._run_bridge(
            "enrich_package",
            {"package_path": str(package_path), "topology_backend": topology_backend},
            timeout=300,
        )

    def validate_package(self, *, package_path: Path) -> dict[str, Any]:
        return self._run_bridge("validate_package", {"package_path": str(package_path)}, timeout=240)

    def package_summary_snapshot(self, *, package_path: Path) -> dict[str, Any]:
        return self._run_bridge("package_summary", {"package_path": str(package_path)}, timeout=300)

    def check_mcp_operation(
        self,
        *,
        package_path: str | None,
        payload: dict[str, Any],
        whitelisted_tools: list[str],
    ) -> dict[str, Any]:
        return self._run_bridge(
            "mcp_check",
            {
                "package_path": package_path,
                "operation": payload.get("operation", "cad_export_step"),
                "target_feature_id": payload.get("target_feature_id"),
                "requested_outputs": payload.get("requested_outputs"),
                "is_modification": bool(payload.get("is_modification", False)),
                "whitelisted_tools": whitelisted_tools,
            },
            timeout=180,
        )

    def parse_patch_proposal(self, *, patch_json: dict[str, Any]) -> dict[str, Any]:
        return self._run_bridge("parse_patch", {"patch_json": patch_json}, timeout=180)

    def prepare_patch_preflight(self, *, package_path: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_bridge(
            "prepare_execution",
            {
                "package_path": package_path,
                "patch_json": payload.get("patch_json") or {},
                "export_modified_step": bool(payload.get("export_modified_step", True)),
                "export_modified_fcstd": bool(payload.get("export_modified_fcstd", False)),
                "input_fcstd": payload.get("input_fcstd"),
            },
            timeout=240,
        )

    def export_step_preview_to_stl(self, *, step_path: Path, stl_path: Path) -> dict[str, Any]:
        if not self.settings.freecad_cmd.exists():
            raise RuntimeError(f"FreeCADCmd not found: {self.settings.freecad_cmd}")
        stl_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aieng-platform-preview-") as temp_dir:
            temp_root = Path(temp_dir)
            script_path = temp_root / "preview.py"
            result_path = temp_root / "result.json"
            script_path.write_text(FREECAD_PREVIEW_SCRIPT + "\n", encoding="utf-8")
            env = {
                **os.environ,
                "AIENG_PLATFORM_STEP_INPUT": str(step_path),
                "AIENG_PLATFORM_STL_OUTPUT": str(stl_path),
                "AIENG_PLATFORM_RESULT_OUTPUT": str(result_path),
                "AIENG_PLATFORM_LINEAR_DEFLECTION": "0.08",
                "AIENG_PLATFORM_ANGULAR_DEFLECTION": "0.35",
            }
            completed = self._run_process(
                [str(self.settings.freecad_cmd), str(script_path)],
                env=env,
                cwd=self.settings.platform_root,
                timeout=300,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "FreeCADCmd preview export failed")
            if not result_path.exists():
                raise RuntimeError("FreeCADCmd preview export did not write a result file")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["stdout"] = completed.stdout.strip()
            result["stderr"] = completed.stderr.strip()
            result["stl_size"] = stl_path.stat().st_size if stl_path.exists() else 0
            return result
