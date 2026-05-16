# aieng local runtime — architecture note

## Why chat is treated as an orchestration layer

The chat UI is not a chatbot. It is a natural-language entry point into a
structured engineering workbench. Each user message is parsed into a plan of
discrete, auditable tool calls. Responses carry structured plan steps, event
timelines, and error records — not free-form text.

This design makes the system debuggable, replayable, and connectable to
external agents (Claude Code, Codex, MCP clients) without retrofitting.

---

## Why API calls are only one tool adapter

The previous design called backend REST endpoints directly from the
orchestration layer. That conflates the transport layer with the business
logic layer. The runtime treats the backend API as *one adapter among many*:

```
Web UI / Chat UI
        │
        ▼
aieng local runtime          ← this module (backend/app/runtime.py)
        │
        ├── aieng tools       ← wraps existing package_summary / validate / convert
        ├── audit tools       ← wraps existing write_audit_log / recent_logs
        ├── FreeCAD adapter   ← skeleton; bridge path TBD (see below)
        └── future adapters   ← solver, MCP, CLI
```

The runtime module (`backend/app/runtime.py`) has *no imports from main.py*.
Tool handlers are registered at app startup via closures that capture the
active `Settings` instance. This keeps the dependency graph one-directional.

---

## How FreeCAD should connect

`freecad.inspect_geometry` and `freecad.run_macro` are registered as skeleton
tools. Four integration paths are viable; pick the one that fits the
deployment environment:

| Path | How | When to use |
|------|-----|-------------|
| **A — FreeCAD Python API** | `import FreeCAD` inside a FreeCAD-hosted subprocess via `FreeCADCmd --run script.py` | Simplest; works when FreeCADCmd is on PATH |
| **B — Headless subprocess** | Spawn `FreeCADCmd --run macro.py`, capture stdout/stderr | Good for stateless one-shot operations |
| **C — Local socket bridge** | POST to `freecad-mcp` running on `localhost:PORT` | Best for interactive sessions; freecad-mcp already exists in this repo |
| **D — Workbench extension** | Named pipe or stdout capture from a running FreeCAD GUI instance | Needed for UI-driven workflows |

`freecad.run_macro` is gated with `requires_approval=True`. The runtime
executor will pause and emit an `approval_required` event before any macro
executes, regardless of which bridge path is used.

---

## How MCP can wrap runtime tools later

The runtime's tool registry is a flat dict. Wrapping it as an MCP server
requires only a thin adapter:

```
Claude Code / Codex
        │  (MCP protocol)
        ▼
aieng MCP server             ← future: backend/app/mcp_server.py
        │
        ▼
aieng local runtime          ← backend/app/runtime.py  (already exists)
        │
        ├── aieng.inspect_package
        ├── aieng.refresh_semantics
        ├── aieng.generate_preview
        ├── aieng.read_audit_log
        ├── freecad.inspect_geometry
        ├── freecad.export_step
        ├── cae.apply_setup_patch
        ├── cae.extract_solver_results
        ├── cae.prepare_solver_run
        ├── cae.run_solver  (approval-gated)
        └── freecad.run_macro  (approval-gated)
```

Each tool in `_REGISTRY` maps directly to one MCP tool definition. The MCP
server would iterate `runtime.registered_tool_names()`, expose them as MCP
tools, and forward calls to `runtime.execute_run()` (or call handlers
directly for single-tool requests).

The approval gate (`requires_approval=True`) maps naturally to MCP's
human-in-the-loop confirmation pattern.

---

## What is implemented now (Phase 0 + Phase 1 + Phase 2 + Phase 2.5)

| Component | Status |
|-----------|--------|
| `RunRecord` / `ToolCall` / `ToolResult` / `RuntimeEvent` models | ✅ |
| `ToolError` structured error payload | ✅ |
| File-backed run persistence (`data/runtime/runs/`) | ✅ configurable via `AIENG_RUNTIME_STATE_DIR` |
| In-memory + disk run store; reloads on restart | ✅ |
| Intent-based plan builder | ✅ |
| `execute_run()` with event emission and approval gate | ✅ |
| `resume_run()` — executes pending tool after approval | ✅ |
| `reject_run()` — marks run rejected, tool not executed | ✅ |
| Statuses: `pending`, `running`, `completed`, `failed`, `awaiting_approval`, `rejected`, `cancelled` | ✅ |
| `aieng.inspect_package` tool | ✅ wraps `package_summary()` |
| `aieng.refresh_semantics` tool | ✅ wraps `validate_aieng_file()` |
| `aieng.generate_preview` tool | ✅ wraps `convert_asset()` |
| `aieng.read_audit_log` tool | ✅ wraps `recent_logs()` |
| `freecad.inspect_geometry` tool | ✅ real bridge via `freecad_bridge.inspect_geometry()` → `FreeCADCmd` |
| `freecad.export_step` tool | ✅ real bridge via `freecad_bridge.export_step()` → `FreeCADCmd`; returns artifact refs |
| `freecad.run_macro` tool | ✅ skeleton (approval-gated) |
| `ToolResult.artifacts` hoisting | ✅ `_execute_steps()` extracts `artifacts` list from tool output dict |
| Per-project artifact audit log | ✅ `write_audit_log(..., "freecad_export", {...})` on each export |
| `POST /api/runtime/runs` endpoint | ✅ |
| `GET /api/runtime/runs` endpoint | ✅ listing (slim summaries, up to 50) |
| `GET /api/runtime/runs/{id}` endpoint | ✅ |
| `GET /api/runtime/runs/{id}/events` endpoint | ✅ |
| `POST /api/runtime/runs/{id}/approve` endpoint | ✅ resumes awaiting_approval run |
| `POST /api/runtime/runs/{id}/reject` endpoint | ✅ rejects awaiting_approval run |
| `GET /api/runtime/tools` endpoint | ✅ tool registry introspection |
| Tool `description` field in registry | ✅ |
| Audit log on each run + approval/rejection events | ✅ |
| Frontend approve/reject buttons (conditional on awaiting_approval) | ✅ |
| Frontend events shown as plan steps in chat | ✅ |
| Frontend geometry result summary line | ✅ compact human-readable output for `freecad.inspect_geometry` |
| Frontend artifact changed-files section | ✅ `变更文件:` block from `ToolResult.artifacts` |

**Phase 2 / 2.5 bridge files:**
- `aieng_freecad_mcp/src/freecad_mcp/geometry_inspector.py` — `FREECAD_INSPECT_SCRIPT` + `run_geometry_inspection()` launcher
- `aieng_freecad_mcp/src/freecad_mcp/step_exporter.py` — `FREECAD_EXPORT_SCRIPT` + `run_step_export()` launcher; returns `artifacts` list
- `aieng-ui/backend/app/freecad_bridge.py` — thin sys.path-injection wrapper; exports `inspect_geometry()` and `export_step()`

**Input resolution for `freecad.inspect_geometry` and `freecad.export_step`** (first match wins):
1. `inputPath` or `input_path` key in tool input
2. `project_id` key → reads `metadata.json` → resolves `source_step` relative path

**Output path for `freecad.export_step`:** If no `outputPath` provided, auto-generates `{stem}_export.step` alongside the input file (never overwrites source).

---

## What remains future work

| Item | Notes |
|------|-------|
| Streaming events | Poll-based; SSE or WebSocket would enable live updates |
| Real FreeCAD macro bridge | `freecad.run_macro` is still a skeleton; needs approval gate wired to a real execution path |
| Solver field data endpoint | Extend `GET /projects/{id}/fields/{f}` to serve real VTK/HDF5 data |
| MCP server adapter | `backend/app/mcp_server.py` wrapping `runtime.registered_tool_names()` |
| Multi-step plan with dependencies | Steps execute sequentially; parallel/conditional is future |
| Per-run project scoping | Tool handlers accept `project_id`; run-level scoping can be tightened |
