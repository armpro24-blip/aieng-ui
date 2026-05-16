# aieng-ui

Web workbench and FastAPI service for the `.aieng` engineering platform.

## What This Is

`aieng-ui` provides:

- **FastAPI service layer** — project/file management, preview generation, semantic package inspection, CAE artifact detection (`GET /api/projects/{project_id}/cae-artifacts`)
- **React SPA** — STEP upload, Three.js viewer (GLB/STL), semantic summary panel, honest CAE lifecycle panel (setup / simulation runs / results) with one-click refresh and external metrics import, artifact inspector (read-only JSON/text evidence review), chat/orchestration panel, audit log, settings drawer
- **Local orchestration runtime** — `RunRecord`, `ToolCall`, `ToolResult`, `RuntimeEvent` types; intent-based plan builder; synchronous executor with approval gate
- **CAD provider registry** — pluggable `CadProvider` interface; FreeCAD is the first implementation

## Role in the vertical CAE MVP

`aieng-ui` is the **workbench**: the local FastAPI runtime + React SPA where the vertical CAE MVP actually executes. It owns the moving parts that `aieng` deliberately does not:

- The runtime tool registry (table below) and the `POST /api/runtime/runs` orchestration entry point.
- The **approval gate** — `cae.run_solver` is `requires_approval=True`; the runtime pauses before subprocess execution and exposes explicit `approve`/`reject` REST endpoints.
- The **external CalculiX subprocess adapter** — `subprocess.run([ccx, …], shell=False)` with timeout, captured stdout/stderr/return code, and honest `converged: null` semantics. AIENG does not host a solver.
- **Artifact write-back** into the `.aieng` package (atomic ZIP rewrite via temp file + `shutil.move`).
- The **audit/event timeline** (`RuntimeEvent` sequence).
- The schema-version drift warning surfaced through the `aieng_bridge` to the chat panel.

External agents (Claude Code, Codex, MCP clients) reach the workbench through `aieng_freecad_mcp`. For the reproducible end-to-end demo see [`docs/quickstart-vertical-cae-demo.md`](docs/quickstart-vertical-cae-demo.md).

Sixteen registered runtime tools (15 working + 1 skeleton; `cae.run_solver`
and `freecad.run_macro` are approval-gated):

| Tool | Status |
|------|--------|
| `aieng.inspect_package` | Working |
| `aieng.refresh_semantics` | Working |
| `aieng.generate_preview` | Working |
| `aieng.read_audit_log` | Working |
| `freecad.inspect_geometry` | Working — FreeCADCmd bridge |
| `freecad.export_step` | Working — FreeCADCmd bridge; writes `{stem}_export.step` |
| `postprocess.generate_computed_metrics` | Working — normalizes external metrics into `computed_metrics.json` and writes it back into the `.aieng` package |
| `postprocess.refresh_cae_summary` | Working — regenerates CAE result summary, evidence index, and markdown |
| `mcp.check` | Working — checks MCP guardrails, capability gaps, operation policy |
| `mcp.parse_patch` | Working — parses an `.aieng` patch proposal without executing |
| `mcp.prepare_execution` | Working — dry-run `.aieng` patch proposal; returns preflight side effects |
| `cae.apply_setup_patch` | Working — controlled patches to CAE setup artifacts |
| `cae.extract_solver_results` | Working — parses CalculiX FRD and writes `computed_metrics.json` |
| `cae.prepare_solver_run` | Working — preflight inspection, no solver execution |
| `cae.run_solver` | Working — external CalculiX execution adapter MVP, approval-gated |
| `freecad.run_macro` | Skeleton, approval-gated |

External agents (Claude Code, Codex, custom MCP clients) can access all runtime tools via the MCP bridge in `aieng_freecad_mcp`. See [`../docs/runtime_and_agents.md`](../docs/runtime_and_agents.md).

## Evidence review API

Read-only endpoints for human review of artifacts inside a project's `.aieng`
package. These do NOT execute solvers, mutate packages, or advance claims —
they exist so a reviewer (or agent) can inspect what the runtime wrote.

| Endpoint | Purpose |
|---|---|
| `GET /api/projects/{project_id}/artifact?path=...` | Read a single artifact from the project's `.aieng` package. Returns `{path, exists, media_type, size_bytes?, parsed_json?, text?, warnings}`. JSON files are parsed when ≤ 2 MB; text files are inlined when ≤ 256 KB. Missing artifacts return `exists: false` with 200. Path traversal, absolute paths, and backslashes are rejected with 400. |
| `POST /api/projects/{project_id}/artifact/diff` | Compute RFC-6901 JSON Pointer paths for differences between two JSON values supplied in the body as `{before, after}`. Returns `{changed_paths, added_paths, removed_paths}`. Pure computation; no package access. |

Pair the two: capture a JSON artifact before an action, capture it again
after, then POST both to `/artifact/diff` to surface the structural delta.

The artifact inspector is exposed in the CAE panel of the React SPA: enter an
artifact path (e.g. `results/computed_metrics.json`) to view parsed JSON or
text inline. Clickable artifact paths appear in the CAE artifact grid and in
runtime chat history for low-risk file types (`.json`, `.txt`, `.md`, `.yaml`,
`.yml`, `.inp`, `.csv`, `.log`).

When `cae.apply_setup_patch` changes setup artifacts, the runtime chat bubble
shows an **artifact diff** panel: path, operation, JSON pointer, changed/added/
removed RFC-6901 paths, and compact before/after values. This is evidence review
metadata only — it does not prove physical correctness or mean the solver was
rerun. Stale-artifact warnings remain visible.

## Quickstart

```bash
# Backend
cd backend
pip install -e ".[dev]"
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend
"C:/Users/RL_Carla/anaconda3/python.exe" -m pytest -c NUL tests/test_api.py -v
```

A generic end-to-end post-processing smoke test (`test_postprocessing_smoke_metrics_import_and_summary_refresh`) validates the full metrics-import → summary-refresh workflow without solver execution or part-family fixtures.

A vertical CAE workflow benchmark (`test_vertical_cae_workflow_end_to_end`) demonstrates the full agent-run lifecycle through the runtime REST API: preflight → approval-gated external solver execution (mocked ccx) → FRD scalar extraction → computed metrics write-back → result summary refresh, with honest limitations enforced (`converged=null`, explicit warnings, no physical correctness claim). See [`../docs/aieng-agent-workflow.md`](../docs/aieng-agent-workflow.md) for the reusable agent workflow pattern, and [`../docs/demo-vertical-cae-workflow.md`](../docs/demo-vertical-cae-workflow.md) for a step-by-step walkthrough with agent prompt.

## Documentation

Repo-level docs:

- [Runtime architecture](docs/runtime_architecture.md) — orchestration layer, tool adapters, FreeCAD bridge paths
- [Vertical CAE MVP milestone](docs/milestone-vertical-cae-mvp.md) — current MVP positioning, real capabilities, boundaries, and check commands

Workspace-level docs (covers all three repos):

- [System architecture](../docs/system_architecture.md) — three-repo overview and data flow
- [Repo boundaries](../docs/repo_boundaries.md) — ownership, coupling points, what must not cross
- [Runtime and agents](../docs/runtime_and_agents.md) — run lifecycle, REST API, future MCP integration
- [CAD adapter strategy](../docs/cad_adapter_strategy.md) — provider interface, adding new backends
- [Package contract](../docs/package_contract.md) — `.aieng` ZIP format and package states
- [Roadmap](../docs/roadmap.md) — phases 1–5
