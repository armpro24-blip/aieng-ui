# Release Checklist — v0.2.0-alpha

This checklist covers the runtime-honesty, approval-gate, artifact-integrity, frontend-honesty, test, and known-limitation criteria for tagging `v0.2.0-alpha`.

---

## A. Runtime honesty

- [x] **`field_summary` missing core module returns `skipped`**
  - `aieng_bridge.write_field_summary()` catches `ModuleNotFoundError` containing `"cae_field_summary"`
  - Returns `{"status": "skipped", "reason": "...", "artifacts": []}` instead of crashing
  - `_tool_cae_extract_field_regions` passes `field_summary_status` through to the result

- [x] **No fake mesh success when FreeCAD unavailable**
  - `_tool_cae_generate_mesh` catches `RuntimeError` / `FileNotFoundError` from `freecad_bridge.generate_mesh()`
  - Returns `{"ok": false, "status": "error", "code": "freecad_unavailable", ...}`
  - Does **not** write `simulation/mesh/*.inp` or `mesh_metadata.json` into the `.aieng` package

- [x] **`cad.edit_parameter` stub mode returns `partial` / `source=stub_mock`**
  - `AIENG_FREECAD_EXECUTOR=stub` is explicit-only; returns `source="stub_mock"`, `status="partial"`
  - `auto` checks `settings.freecad_cmd.exists()`; honest `RuntimeError` if missing (no silent stub fallback)
  - Real executor (`macro` or `rpc`) returns `source="freecad_real"`

- [x] **Synthetic field overlay labeled as not for engineering judgment**
  - Frontend `fieldNote` logic shows three distinct labels:
    - `FRD真实数据` — real FRD data, bbox aligned
    - `FRD数据存在，但几何坐标可能不一致` — FRD data present but bbox suspicious
    - `合成预览，不可用于工程判断` — synthetic / no real data

---

## B. Approval gates

All mutation or expensive operations require explicit approval:

- [x] `cad.edit_parameter` — `requires_approval=True`
- [x] `cae.generate_mesh` — `requires_approval=True`
- [x] `cae.run_solver` — `requires_approval=True` (existing)
- [x] `freecad.run_macro` — `requires_approval=True` (existing)

Runtime behavior:
- Plan builder routes intents to these tools only when present in `_INTENT_MAP`
- `POST /api/runtime/runs/{id}/approve` resumes execution
- `POST /api/runtime/runs/{id}/reject` marks run rejected

---

## C. Artifact integrity

- [x] **No ZIP append duplicate entries for mesh writeback**
  - `_write_mesh_into_package_atomic()` reads all existing members, writes new temp ZIP, moves atomically
  - No `zipfile.ZIP_APPEND` used

- [x] **`mesh_metadata.json` present after real mesh generation**
  - Generated metadata includes: `schema_version`, `mesh_size_mm`, `element_type`, `output_format`, `source_geometry`, `mesh_file`, `generated_at`

- [x] **No STEP writeback if real STEP file missing**
  - `cad.edit_parameter` checks executor result; missing STEP blocks package write and returns `status="error"`

- [x] **Stale downstream artifacts listed after CAD/mesh mutation**
  - `cad.edit_parameter` returns `stale_artifacts: ["results/computed_metrics.json", ...]`
  - `cae.generate_mesh` returns `stale_artifacts: ["results/computed_metrics.json", "simulation/solver_input.inp", ...]`

---

## D. Frontend honesty

- [x] **FRD overlay shows real source when `source=frd`**
  - `applyFieldColors` returns `{applied, bboxStatus, warnings}`
  - `fieldDescriptor.source === "frd"` triggers real-data label path

- [x] **Bbox mismatch warning visible**
  - `checkBboxAlignment` handles near-zero dimensions (thin plate / 2D)
  - Returns `"suspicious"` with reason when mesh bbox ≠ FRD bbox

- [x] **Synthetic overlay warning visible**
  - When `source !== "frd"`, label reads `合成预览，不可用于工程判断`

- [x] **LLM `config_ready` and `connection_verified` are separate**
  - `POST /api/llm/test` with `verify_connection=false` returns `config_ready`
  - `POST /api/llm/test` with `verify_connection=true` attempts real API ping and returns `connection_verified`
  - Never returns the API key in the response body

---

## E. Tests

### Backend

```powershell
cd aieng-ui\backend
python -m pytest tests/test_api.py -v -k "cae_generate_mesh"
```

**Expected (no FreeCAD):**
```
test_cae_generate_mesh_registered_with_approval PASSED
test_cae_generate_mesh_requires_approval PASSED
test_cae_generate_mesh_unpacks_geometry_from_zip PASSED
test_cae_generate_mesh_missing_geometry_returns_error PASSED
test_cae_generate_mesh_real_freecad_integration SKIPPED
test_cae_generate_mesh_no_freecad_returns_error PASSED
```

**Expected (with FreeCAD + `AIENG_TEST_REAL_FREECAD=1`):**
```
test_cae_generate_mesh_real_freecad_integration PASSED
```

### Frontend

```powershell
cd aieng-ui\frontend
npx tsc --noEmit   # must pass
npm run build      # must produce dist/ without errors
```

### Backend LLM tests

```powershell
cd aieng-ui\backend
python -m pytest tests/test_api.py -v -k "llm"
```

**Expected:** `test_llm_test_endpoint_*` PASSED.

---

## F. Known limitations

| Limitation | Impact | Next step |
|---|---|---|
| Real mesh test requires local FreeCAD/Gmsh | CI cannot run mesh integration test | Install FreeCAD on CI runner or keep skipif |
| `field_summary` ownership unresolved | `aieng.cae_field_summary` was reverted in core; `aieng validate` does not recognize `field_summary.json` | Decide whether to restore module or keep skipped |
| Benchmark CLI reverted / not currently active | `aieng benchmark` commands may be stale | Re-enable after benchmark schema stabilizes |
| Full CAD parameter edit real path depends on FreeCAD executor mode | `macro` mode macro generation is placeholder | Implement `_MacroRunnerCadExecutor` for real macro generation |
| Mesh quality metrics not yet implemented | No `aspect_ratio`, `skewness`, `jacobian` in `mesh_metadata.json` | Add Gmsh quality report parsing |
| Only `.inp` mesh output prioritized | `.vtk`, `.unv`, `.stl` mesh export not wired | Add format switch in `generate_mesh()` macro |
| FRD field serving is scalar-only | No per-node JSON / VTU / HDF5 export | Implement `GET /projects/{id}/fields/{f}` real data endpoint |
| Convergence claim remains null | `solver_run.json` always sets `converged: null` | Add convergence heuristics (residual drop, energy norm) |

---

## G. Release decision

### Criteria for tagging `v0.2.0-alpha`

All of the following must be true:

1. [x] Runtime honesty: no fake success, no silent stub fallback, explicit `skipped`/`partial`/`error` semantics
2. [x] Approval gates: all mutation/expensive ops are gated
3. [x] Artifact integrity: atomic ZIP rewrite, no fake artifacts, stale warnings present
4. [x] Frontend honesty: FRD source labels distinguish real/suspicious/synthetic
5. [x] Tests pass: backend mesh tests 5 passed + 1 skipped; frontend tsc/build pass
6. [x] Documentation synced: README, runtime architecture, package contract, quickstart, demo checklist updated

### Blocking issues

_None at this time._

### Non-blocking follow-ups

- Run `test_cae_generate_mesh_real_freecad_integration` on a machine with FreeCAD 1.0+ and confirm PASSED
- Add mesh quality metrics to `mesh_metadata.json`
- Implement `_MacroRunnerCadExecutor` for real CAD parameter edit macro generation
- Restore or formally deprecate `field_summary` ownership
- Re-enable benchmark CLI after schema stabilizes

---

*Last updated: 2026-05-17*
