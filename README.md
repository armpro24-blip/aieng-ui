# aieng-ui

Web workbench and FastAPI service for the `.aieng` engineering platform.

## What This Is

`aieng-ui` provides:

- **FastAPI service layer** — project/file management, preview generation, semantic package inspection, CAE artifact detection (`GET /api/projects/{project_id}/cae-artifacts`)
- **React SPA** — STEP upload, Three.js viewer (GLB/STL), semantic summary panel, honest CAE lifecycle panel (setup / simulation runs / results) with one-click refresh and external metrics import, chat/orchestration panel, audit log, settings drawer
- **Local orchestration runtime** — `RunRecord`, `ToolCall`, `ToolResult`, `RuntimeEvent` types; intent-based plan builder; synchronous executor with approval gate
- **CAD provider registry** — pluggable `CadProvider` interface; FreeCAD is the first implementation

Nine registered runtime tools (7 working + 2 skeleton):

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
| `freecad.run_macro` | Skeleton, approval-gated |

External agents (Claude Code, Codex, custom MCP clients) can access all runtime tools via the MCP bridge in `aieng_freecad_mcp`. See [`../docs/runtime_and_agents.md`](../docs/runtime_and_agents.md).

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

## Documentation

Repo-level docs:

- [Runtime architecture](docs/runtime_architecture.md) — orchestration layer, tool adapters, FreeCAD bridge paths

Workspace-level docs (covers all three repos):

- [System architecture](../docs/system_architecture.md) — three-repo overview and data flow
- [Repo boundaries](../docs/repo_boundaries.md) — ownership, coupling points, what must not cross
- [Runtime and agents](../docs/runtime_and_agents.md) — run lifecycle, REST API, future MCP integration
- [CAD adapter strategy](../docs/cad_adapter_strategy.md) — provider interface, adding new backends
- [Package contract](../docs/package_contract.md) — `.aieng` ZIP format and package states
- [Roadmap](../docs/roadmap.md) — phases 1–5
