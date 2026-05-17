# Quickstart: Vertical CAE MVP demo

A 60-second entry point to the workspace's end-to-end CAE flow. For the full
walkthrough see [`../../docs/demo-vertical-cae-workflow.md`](../../docs/demo-vertical-cae-workflow.md).

---

## What the demo proves

A single pytest exercises the full vertical CAE workflow through the
`aieng-ui` runtime REST API:

> evidence read → preflight plan → approval-gated external CalculiX run →
> FRD scalar extraction → computed-metrics write-back → summary refresh →
> evidence-backed report

The test confirms that an agent (or human caller) can drive a real solver
adapter through an approval gate, parse real per-node DISP and S fields
from CalculiX FRD output, and have the resulting scalar extrema land
inside a `.aieng` package — without bypassing any honesty boundary
(`converged: null`, explicit `limitations` array, no physical
correctness claim).

---

## Run it

One command, from a clean Python environment:

```powershell
cd /path/to/workspace_aieng\aieng-ui\backend
python -m pytest -c NUL tests/test_api.py::test_vertical_cae_workflow_end_to_end -v
```

Expected success signal:

```text
tests/test_api.py::test_vertical_cae_workflow_end_to_end PASSED
```

No FreeCAD installation, no `ccx` executable, and no network are required.
The test patches `shutil.which` and `subprocess.run` so the CalculiX
subprocess adapter exercises its real code path against a synthetic FRD
fixture.

---

## What is real

| Capability | Real |
|-----------|------|
| Runtime tool registry and `POST /api/runtime/runs` orchestration | ✅ |
| Approval gate (`cae.run_solver` pauses at `awaiting_approval`) | ✅ |
| External CalculiX subprocess adapter (`subprocess.run`, shell=False, timeout) | ✅ |
| Pure-Python CalculiX FRD text parser (DISP + S → max displacement, max von Mises) | ✅ |
| Atomic `.aieng` ZIP rewrite (temp file + `shutil.move`) | ✅ |
| Audit/event timeline (`run_started → tool_started → approval_required → tool_succeeded → run_completed`) | ✅ |
| Pre-processing, simulation-run, and post-processing summaries | ✅ |
| Schema-version drift warning surfaced through `aieng_bridge` | ✅ |

## What is mocked or limited

| Capability | Status | Why |
|-----------|--------|-----|
| `ccx` executable | Patched | `shutil.which` returns `/fake/ccx`; `subprocess.run` writes a fixture FRD |
| `.inp` input deck | Pre-built fixture inside the test package | No mesh-to-deck generator exists yet |
| Mesh generation | Not implemented | FreeCAD FEM workbench integration is future work |
| Binary FRD | Not supported | UTF-8 text FRD only |
| VTU / ODB parsing | Not supported | Only CalculiX FRD scalar extraction |
| Field visualization | Synthetic | Frontend colormap is `y_normalized`; no real per-node field serving |
| Convergence claim | Explicitly avoided | `converged: null` in `solver_run.json`; exit code alone is not reliable evidence |
| Physical correctness | Not validated | No experimental correlation, mesh convergence study, or independent validation |

---

## Honesty boundary (repeated, on purpose)

- **AIENG is not a solver.** External CalculiX is invoked as a subprocess by the workbench runtime; the result is treated as evidence, not as a validated claim.
- **AIENG is not a CAD kernel.** Geometry edits happen in FreeCAD via the workbench bridge, not inside the evidence layer.
- **FRD parsing is scalar extraction only.** Max displacement and max von Mises are computed from per-node DISP and S fields; full-field post-processing is not implemented.
- **No physical correctness validation.** Setup readiness is artifact-presence; result extraction is numerical without correlation.
- **Convergence is unknown unless reliable evidence exists.** Exit code 0 is not evidence; `converged` stays `null`.

---

## Where each repo fits

| Repo | Role in this demo |
|------|-------------------|
| [`aieng`](../../aieng/README.md) | `.aieng` package format; CAE artifact detection; pre/post/run summaries; FRD scalar extractor; canonical `schema_version` constants. |
| [`aieng-ui`](../README.md) | Runtime tool registry; approval gate; external CalculiX adapter; artifact write-back; audit/event timeline. |
| [`aieng_freecad_mcp`](../../aieng_freecad_mcp/README.md) | Agent-facing MCP bridge — thin HTTP wrappers around `aieng-ui` so external agents (Claude Code, Codex, MCP clients) drive the same runtime tools. |

---

## Deeper docs

- [Vertical CAE demo walkthrough](../../docs/demo-vertical-cae-workflow.md) — line-by-line test breakdown, agent prompt, inspecting the produced `.aieng` package.
- [AIENG agent workflow pattern](../../docs/aieng-agent-workflow.md) — reusable pattern for evidence → action → write-back loops.
- [Runtime architecture](runtime_architecture.md) — orchestration layer, tool adapters, FreeCAD bridge.
- [MCP runtime tools](../../aieng_freecad_mcp/docs/mcp_runtime_tools.md) — every MCP tool, contract, and limitation.
- [Repo boundaries](../../docs/repo_boundaries.md) — ownership, designed coupling points, what must not cross.
