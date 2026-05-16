# Demo Readiness Checklist — Vertical CAE MVP

Use this checklist before demoing or reviewing the AIENG vertical CAE MVP. It covers pre-demo verification, the demo flow, talking points, and common failure modes.

> This is a **docs-only** artifact. No code, frontend, runtime tools, MCP tools, or schemas are modified by this checklist.

---

## 1. What this checklist is for

The vertical CAE MVP demonstrates an end-to-end CAE lifecycle through the `aieng-ui` workbench runtime:

> evidence read → preflight plan → approval-gated external CalculiX run →
> FRD scalar extraction → computed-metrics write-back → summary refresh →
> evidence-backed report

This checklist ensures the demonstrator can verify the environment, run the right tests, speak accurately about boundaries, and recover from common failures.

---

## 2. Required pre-demo checks

Run these in order. If any required check fails, stop and fix before demoing.

### 2.1 aieng-ui backend tests (required)
```powershell
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng-ui\backend
python -m pytest -c NUL tests/test_api.py -v
```
**Expected:** `111 passed, 1 skipped, 2 warnings` (the skip is the real-ccx smoke test when ccx is absent).

### 2.2 Vertical workflow benchmark (required)
```powershell
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng-ui\backend
python -m pytest -c NUL tests/test_api.py::test_vertical_cae_workflow_end_to_end -v
```
**Expected:** `PASSED`.

### 2.3 Frontend build (required if UI is being shown)
```powershell
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng-ui\frontend
npm install
npm run build
```
**Expected:** `dist/` produced without `lightningcss` or TypeScript errors.

### 2.4 aieng tests (required if evidence/schema code changed)
```powershell
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng
python -m pytest -v
```
**Expected:** All tests pass.

### 2.5 aieng_freecad_mCP runtime MCP tests (required if MCP is being shown)
```powershell
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng_freecad_mcp
python -m pytest -v
```
**Expected:** All tests pass.

---

## 3. Optional real-ccx check

Only if you intend to show a real CalculiX execution rather than the mocked benchmark.

### 3.1 Check ccx is installed
```powershell
Get-Command ccx
# or
ccx -h
```
**Expected:** Path to `ccx.exe` or usage text. If not found, the smoke test will skip.

### 3.2 Run the real ccx smoke test
```powershell
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng-ui\backend
python -m pytest -c NUL tests/test_api.py::test_run_solver_real_ccx_skipped_if_unavailable -v
```
**If ccx is installed:** `PASSED`. The test runs the fixture `minimal_cantilever.inp` through a real ccx process, then asserts `solver_run.json`, `solver_log.txt`, and `outputs/result.frd` exist in the package.

**If ccx is not installed:** `SKIPPED` with reason `CalculiX executable (ccx) not found on PATH — skipping real solver smoke test.`

> This skip is **not a failure**. CI and demo laptops without ccx skip cleanly.

---

## 4. Demo flow checklist

Use this sequence during the demo. Tick each step as you go.

- [ ] **Open workbench** — `aieng-ui` frontend at `http://localhost:5173` (dev) or served `dist/`.
- [ ] **Inspect CAE lifecycle evidence** — show the CAE panel: setup / simulation runs / results. Point out pre-existing artifacts (mesh metadata, solver settings, load case) or their absence.
- [ ] **Run or explain `cae.prepare_solver_run`** — show the preflight report: missing artifacts, readiness score, honest limitations. Do not claim the solver will converge.
- [ ] **Run solver** — choose one of:
  - *Mocked benchmark*: run `test_vertical_cae_workflow_end_to_end` and narrate each stage.
  - *Real ccx*: run `test_run_solver_real_ccx_skipped_if_unavailable` (only if ccx is installed).
- [ ] **Inspect `result_summary` / `computed_metrics`** — open the produced `.aieng` package artifacts. Show `computed_metrics.json` with max displacement and max von Mises.
- [ ] **Inspect artifact paths with Artifact Inspector** — click an artifact path in the CAE grid or chat bubble (e.g. `simulation/runs/run_001/solver_run.json`). Show parsed JSON or text inline.
- [ ] **Show setup patch diff if relevant** — if a setup patch was applied earlier, open the chat bubble diff panel: operation, JSON pointer, changed/added/removed paths, compact before/after values.

---

## 5. What to say clearly during demo

Say these boundaries out loud. They protect the team from overclaiming and the audience from misunderstanding.

- **"AIENG is an evidence/grounding layer."** It reads, validates, and summarizes engineering artifacts. It does not compute physics.
- **"Solver execution is external CalculiX through the workbench runtime."** The runtime invokes `ccx` as a subprocess with timeout and captured output. AIENG itself is not a solver.
- **"FRD parsing is scalar extraction, not full field post-processing."** We extract max displacement and max von Mises from per-node DISP and S fields. We do not serve per-node fields for contour plots or VTU/ODB export.
- **"No mesh generation."** Mesh must exist in the package or be produced outside AIENG (e.g. FreeCAD FEM workbench, future work).
- **"No input deck generation unless a fixture or pre-existing deck exists."** The `.inp` file must already be in the package or supplied externally.
- **"Convergence remains unknown unless reliable evidence exists."** Exit code 0 is not evidence of convergence. `solver_run.json` sets `converged: null`.
- **"No physical correctness validation."** No experimental correlation, mesh convergence study, or independent verification is performed.

---

## 6. Common failure modes

| Symptom | Likely cause | Quick fix |
|---|---|---|
| `solver_not_found` | `ccx` not on PATH | Install CalculiX or add its directory to PATH; on Windows check `C:\Program Files\CalculiX\ccx.exe` or `C:\ccx_2.21\ccx.exe`. |
| `input_deck_not_found` | Package missing `.inp` | Ensure `cae.prepare_solver_run` was called or the `.inp` was imported manually. |
| `FRD not produced` | ccx failed or wrote to unexpected filename | Check `solver_log.txt` for stdout/stderr/return code. Verify the `.inp` stem matches what ccx expects. |
| Schema drift warning | `aieng` schema version changed since package was created | Re-run `aieng.refresh_semantics` or re-import the model to regenerate the package with current schema. |
| Stale artifacts after setup patch | `cae.apply_setup_patch` changed setup files but did not re-run the solver | This is expected behavior. The UI shows a stale-artifact warning. Explain that the old results no longer reflect the new setup. |
| Artifact too large/binary for inspector | File > 2 MB (JSON) or > 256 KB (text), or binary format | The inspector returns `size_bytes` without content. Download or inspect the file externally. |

---

## 7. Links to relevant docs

| Doc | What it covers |
|---|---|
| [Quickstart: vertical CAE demo](quickstart-vertical-cae-demo.md) | One-command benchmark, what is real vs. mocked, honesty boundary. |
| [Quickstart: real ccx](quickstart-real-ccx.md) | Installing/locating CalculiX on Windows, running the real-ccx smoke test, inspecting artifacts. |
| [Milestone: vertical CAE MVP](milestone-vertical-cae-mvp.md) | MVP positioning, current capabilities, intentional limits, next-phase candidates. |
| [Runtime architecture](runtime_architecture.md) | Orchestration layer, tool adapters, FreeCAD bridge paths, event timeline. |
| [Vertical CAE demo walkthrough](../../docs/demo-vertical-cae-workflow.md) | Line-by-line test breakdown, agent prompt, inspecting the produced `.aieng` package. |
| [AIENG agent workflow pattern](../../docs/aieng-agent-workflow.md) | Reusable evidence → action → write-back loop for agent authors. |
| [Repo boundaries](../../docs/repo_boundaries.md) | Ownership, designed coupling points, what must not cross between repos. |
| [README](../README.md) | Project overview, tool registry, evidence review API, test commands. |
