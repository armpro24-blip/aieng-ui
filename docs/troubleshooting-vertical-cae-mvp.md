# Troubleshooting — Vertical CAE MVP

Practical diagnosis for demo/runtime failures in the AIENG vertical CAE MVP. Use this when something breaks and you need to decide whether the demo can continue.

> This is a **docs-only** artifact. No code, frontend, runtime tools, MCP tools, or schemas are modified by this guide.

---

## Fast triage order

Run these in sequence. Stop when you find the problem.

1. **Check repo status** — `git status` in `aieng-ui`. Uncommitted changes or stale branches can produce inconsistent behavior.
2. **Run backend tests** — `pytest tests/test_api.py -v`. If tests fail, fix the environment first.
3. **Check ccx availability** — `Get-Command ccx` (PowerShell) or `ccx -h`. If missing, the real-ccx smoke test will skip; mocked benchmarks still work.
4. **Run real-ccx smoke test** — `pytest tests/test_api.py::test_run_solver_real_ccx_skipped_if_unavailable -v`. Confirm whether a real solver is available.
5. **Inspect package artifacts** — use the Artifact Inspector or `unzip -l solver.aieng` to verify the input deck, solver settings, and mesh metadata exist.
6. **Inspect runtime warnings** — read `solver_log.txt` and `solver_run.json` inside the package. Runtime warnings are honest signals, not noise.

---

## Failure modes

| # | Symptom | Likely cause | How to check | Suggested fix | Can demo continue? |
|---|---|---|---|---|---|
| 1 | `solver_not_found` — ccx / CalculiX not found | `ccx` not on PATH; wrong install path; WSL/ccx name mismatch | `Get-Command ccx` or `ccx -h` in terminal | Install CalculiX or add its directory to PATH. Common Windows paths: `C:\Program Files\CalculiX\ccx.exe`, `C:\ccx_2.21\ccx.exe`. See [`quickstart-real-ccx.md`](quickstart-real-ccx.md). | **Yes** — switch to the mocked benchmark (`test_vertical_cae_workflow_end_to_end`). |
| 2 | ccx found but solver run fails (return code ≠ 0) | Bad `.inp` file; incompatible ccx version; missing material/section data | Read `solver_log.txt` in the package for stdout/stderr/return code | Fix the input deck or use the `minimal_cantilever.inp` fixture. Verify ccx version compatibility. | **Yes** — show the honest failure in `solver_run.json` (`solved: false`, `converged: null`) and explain that AIENG reports what it sees. |
| 3 | `input_deck_not_found` — input deck missing from `.aieng` package | `.inp` was never added; path mismatch between package contents and `input_deck_path` argument | Artifact Inspector or `unzip -l` to list package contents | Add the `.inp` to the package before calling `cae.run_solver`. Use `cae.prepare_solver_run` to verify artifact presence. | **Yes** — explain preflight as a safety gate. |
| 4 | `forbidden_path` or `invalid_input_deck` — input deck path rejected | Path contains `..`, starts with `/`, or does not end with `.inp` | Inspect the `input_deck_path` string in the tool input | Pass a relative path inside the package that ends with `.inp`. No traversal characters. | **Yes** — fix the path and retry. |
| 5 | Solver run times out | Solver hung; mesh too large; timeout too short | `solver_run.json` shows `return_code: -1` and timeout warning | Increase `timeout_seconds` in the tool input (default 120). Or reduce model size. | **Yes** — explain timeout as a safety boundary. |
| 6 | FRD file not produced | Solver failed before writing FRD; wrong `.inp` stem; ccx wrote `.frd` to unexpected name | Check `solver_log.txt` and temp-directory contents. Verify the `.inp` stem matches the expected FRD name. | Fix the input deck or verify ccx behavior manually outside AIENG. | **Yes** — show `solver_run.json` with empty `output_files` and explain the honest boundary. |
| 7 | FRD file produced but extraction fails | Binary FRD (not supported); malformed FRD; missing `aieng` bridge dependency | Check `solver_log.txt` and runtime warnings for `FRD extraction failed` | Use UTF-8 text FRD only. Binary FRD is not supported. Verify the FRD contains per-node `DISP` and `S` blocks. | **Yes** — show the FRD exists but extraction failed. Scalar extraction is a best-effort pipeline. |
| 8 | `computed_metrics.json` missing | FRD extraction was skipped or failed; `extract_results` was `false` | Check `solver_run.json` for `output_files`. Check runtime result for `extracted_metrics`. | Re-run with `extract_results: true`. Verify FRD is valid text. | **Yes** — explain that computed metrics are a post-processing artifact, not a solver output. |
| 9 | `result_summary` still shows `extrema_computed: false` | `refresh_summary` was skipped; `computed_metrics.json` missing or empty; summary refresh failed | Inspect `computed_metrics.json` and runtime warnings. | Re-run with `refresh_summary: true`. Verify `computed_metrics.json` exists and contains `max_displacement` and `max_von_mises`. | **Yes** — show the evidence chain and explain why the summary is stale. |
| 10 | Schema-version drift warning appears | `aieng` schema version changed since package was created; package imported with older tooling | Read the warning text. Compare `manifest.json` `schema_version` against `aieng` constants. | Run `aieng.refresh_semantics` or re-import the model to regenerate the package with the current schema. | **Yes** — explain schema drift as a version-safety signal. |
| 11 | Setup patch succeeds but `stale_artifacts` are present | `cae.apply_setup_patch` changed setup files (mesh, settings, load case) but the solver was not re-run | Inspect chat bubble diff panel for patch operations. Check CAE panel for stale-artifact warnings. | Re-run the solver after setup changes. Or explain that old results no longer reflect the new setup. | **Yes** — this is expected behavior. The stale-artifact warning is honest evidence. |
| 12 | Artifact Inspector shows `exists: false` | Path typo; artifact not yet written; package not saved after runtime action | Double-check the path. List package contents with `unzip -l` or Artifact Inspector on a known-good path. | Correct the path. Re-run the tool that produces the artifact. Save/refresh the project. | **Yes** — explain artifact presence as evidence of tool success. |
| 13 | Artifact Inspector says artifact is oversized or binary | JSON > 2 MB; text > 256 KB; or file is binary (e.g. `.frd`, `.step`) | Check `size_bytes` in the response. Verify file extension. | For large JSON/text, download externally. For binary, use FreeCAD or a dedicated viewer. The inspector is for small evidence files. | **Yes** — explain the inspector's scope. |
| 14 | MCP runtime unavailable — AI agent cannot reach `aieng-ui` | Backend not running; wrong port; CORS/firewall; MCP bridge misconfigured | `curl http://localhost:8000/api/health` from the agent host | Start the backend (`uvicorn app.main:app --reload`). Verify port and network path. Check MCP bridge config points to the right URL. | **Yes** — if the backend starts. Otherwise demonstrate directly through the workbench REST API. |
| 15 | Approval-gated run remains `awaiting_approval` | Human/agent did not call `POST /api/runtime/runs/{run_id}/approve` | Check run status via `GET /api/runtime/runs/{run_id}` or chat panel | Call the approve endpoint, or reject if the preflight report looks wrong. The approval gate is intentional. | **Yes** — the approval gate is a feature, not a bug. Explain it. |
| 16 | `converged` is `null` | AIENG deliberately does not claim convergence. Exit code 0 is not reliable evidence. | Read `solver_run.json`. `converged` is always `null`. | None required. This is the honest boundary. If you have independent convergence evidence, add it to `computed_metrics.json` manually. | **Yes** — say out loud: "Convergence is unknown unless we have reliable evidence." |
| 17 | Frontend build fails | Missing `node_modules`; `lightningcss` error; TypeScript error; stale `dist/` | `npm run build` and read the error. Check for CSS syntax errors (e.g. stray `}`). | `rm -rf node_modules && npm install`. Fix CSS/TS errors. Remove stale `dist/`. | **Yes** — if the backend and runtime work, demo via API/Postman/curl. |
| 18 | Backend tests fail because dependencies are missing | Virtual env not activated; `pip install -e ".[dev]"` not run; `aieng` package not installed in editable mode | Read the import error. Check `pip list` for `aieng`, `fastapi`, `pytest`. | Activate `.venv` or install deps: `pip install -e ".[dev]"` from `aieng-ui/backend`. Also `pip install -e .` from `aieng`. | **No** — fix the environment before demoing. |

---

## When to stop and not trust results

Do not claim success if any of the following are true. Explain the honest boundary instead.

- **Setup changed but solver not rerun.** Old results do not reflect the new mesh, load case, or solver settings. `stale_artifacts` warnings are present.
- **Result summary is stale or missing.** `extrema_computed: false` means no extracted metrics reached the summary. The evidence chain is broken.
- **FRD extraction failed.** The solver may have run, but AIENG could not parse the output. Scalar metrics are missing.
- **Solver return code is nonzero.** `solved: false` in `solver_run.json`. The subprocess failed; any downstream artifacts are from an earlier run or are missing.
- **Convergence is unsupported.** `converged: null` is expected. Do not say "the model converged" unless you have independent evidence (residual history, mesh study, experimental correlation).

---

## Links

| Doc | Purpose |
|---|---|
| [Demo readiness checklist](demo-readiness-checklist.md) | Pre-demo verification steps and talking points. |
| [Quickstart: real ccx](quickstart-real-ccx.md) | Installing CalculiX, running the real-ccx smoke test, inspecting artifacts. |
| [Quickstart: vertical CAE demo](quickstart-vertical-cae-demo.md) | One-command benchmark, what is real vs. mocked. |
| [Milestone: vertical CAE MVP](milestone-vertical-cae-mvp.md) | MVP positioning, capabilities, intentional limits. |
| [Runtime architecture](runtime_architecture.md) | Orchestration layer, tool adapters, event timeline. |
| [README](../README.md) | Project overview, tool registry, API summary. |
