# Quickstart: Real CalculiX Solver Smoke Test

This quickstart proves that the AIENG workbench can execute a **real**
external CalculiX solver through the runtime approval gate, capture the
output, and write artifacts back into the `.aieng` package.

For the mocked benchmark used in CI, see
[`quickstart-vertical-cae-demo.md`](quickstart-vertical-cae-demo.md).

---

## What this proves

- `cae.run_solver` finds a real `ccx` executable on the host system.
- The runtime executes `ccx` as a subprocess (`shell=False`) with timeout
  and captures stdout, stderr, and return code.
- After the run completes, `solver_run.json`, `solver_log.txt`,
  `solver_input.inp`, and `outputs/result.frd` are written into the
  `.aieng` package.
- The approval gate pauses the run before execution; explicit approval is
  required.

---

## Prerequisites

- Python 3.10+ with `aieng-ui` backend dependencies installed
- `aieng` and `aieng_freecad_mcp` sibling repos present
- **CalculiX (`ccx`) executable available on PATH**

---

## Install or locate CalculiX / ccx on Windows

### Option 1 — Pre-built Windows binaries

1. Download the CalculiX Windows binaries from the official source or a
   trusted mirror (e.g. the CalculiX forum or GitHub releases).
2. Extract the archive to a folder such as `C:\Program Files\CalculiX`.
3. Add the folder containing `ccx.exe` to your system PATH.

### Option 2 — WSL (Windows Subsystem for Linux)

```bash
# Inside WSL
sudo apt update
sudo apt install calculix-ccx
```

If you use WSL, ensure the `ccx` command is available in the WSL shell and
that the Python backend can reach it (run pytest inside WSL or ensure the
Windows Python can call WSL binaries).

### Option 3 — Conda / conda-forge

```bash
conda install -c conda-forge calculix
```

---

## Confirm ccx is available

In PowerShell:

```powershell
Get-Command ccx
ccx -h
```

Expected: help text or version info from CalculiX. If `ccx` is not found,
adjust your PATH and retry.

---

## Run the real-ccx smoke test

One command from the `aieng-ui` backend directory:

```powershell
cd /path/to/workspace_aieng\aieng-ui\backend
python -m pytest -c NUL tests/test_api.py::test_run_solver_real_ccx_skipped_if_unavailable -v
```

### Expected success signal

If `ccx` is **available**:

```
tests\test_api.py::test_run_solver_real_ccx_skipped_if_unavailable PASSED
```

If `ccx` is **not available**, the test skips cleanly:

```
tests\test_api.py::test_run_solver_real_ccx_skipped_if_unavailable SKIPPED
```

The skip is intentional — CI environments without CalculiX remain green.

---

## What artifacts appear after a successful run

Inside the project's `.aieng` package:

```text
simulation/runs/run_001/solver_input.inp   # copy of the input deck
simulation/runs/run_001/solver_log.txt     # ccx stdout + stderr
simulation/runs/run_001/solver_run.json    # execution metadata
simulation/runs/run_001/outputs/result.frd # CalculiX result file
```

`solver_run.json` contains:
- `run_id`, `solver`, `state`, `solved`
- `converged: null` — honest, no reliable convergence evidence
- `return_code`, `duration_seconds`, timestamps
- `warnings`, `errors`

If `extract_results=True` and a `.frd` is produced, the runtime may also
write:

```text
results/computed_metrics.json              # max displacement, max von Mises
```

---

## Run the full real-pipeline smoke test (STEP → mesh → solver → summary)

The smoke test above runs `ccx` against a hand-written `.inp` fixture, so the
**meshing** half of the pipeline is not exercised. A second, doubly-gated test
goes further: it generates a tiny cube via FreeCADCmd, runs **real** FreeCAD +
Gmsh mesh generation, completes the resulting mesh-only deck with material /
boundary / load / output blocks in-test, runs **real** `ccx`, parses the **real**
FRD output, and refreshes the CAE result summary. No mocks anywhere.

### Prerequisites

- All prerequisites for the basic smoke test above.
- **FreeCADCmd available** (either on PATH, at `FREECAD_CMD`, or at one of the
  standard Windows install paths probed by `_resolve_freecad_cmd`).
- **Opt-in env var:** `AIENG_TEST_REAL_FREECAD=1`. The test skips unless both
  this flag is set *and* both binaries are reachable. This keeps casual local
  runs and CI green when only one binary is installed.

### Command

From `aieng-ui/backend/`:

```powershell
$env:AIENG_TEST_REAL_FREECAD = "1"
python -m pytest -c NUL tests/test_api.py::test_full_real_pipeline_step_to_summary -v -s
```

### Expected output

**PASSED** — when both binaries are present. Runtime is dominated by FreeCAD
meshing (typically 10–30 seconds for a 10×1×1 mm cube at 2 mm element size).
The actual `ccx` solve completes in well under one second.

**SKIPPED** — when `AIENG_TEST_REAL_FREECAD` is unset, FreeCADCmd is missing,
*or* `ccx` is missing. Exit code 0; CI stays green.

### What was proven

After a successful run the `.aieng` package on disk contains, in order:

```text
geometry/source.step                              # cube generated by FreeCADCmd
simulation/mesh/mesh_2.0mm.inp                    # real FreeCAD/Gmsh mesh
simulation/mesh/mesh_metadata.json
simulation/runs/run_001/solver_input.inp          # mesh + completed deck blocks
simulation/runs/run_001/outputs/result.frd        # real CalculiX output
simulation/runs/run_001/solver_run.json           # converged: null, honest
simulation/runs/run_001/solver_log.txt
results/computed_metrics.json                     # real max stress + max displacement
results/result_summary.json
results/evidence_index.json
results/postprocessing_summary.md
```

The `GET /api/projects/{id}/cae-result-summary` endpoint then reports
`computed_values.extrema_computed: true` with non-zero, real-number stress and
displacement — proving that the four runtime tools compose end-to-end against
real binaries.

### Caveats

- The test generates its boundary and load NSETs by node-coordinate (min-x is
  fixed, max-x is loaded). This works for the cube fixture but is **not** a
  general boundary-condition synthesizer — production work uses authored load
  cases, not coordinate-based heuristics.
- If `ccx` is in WSL but `FreeCADCmd.exe` is Windows-native (or vice versa),
  run pytest from whichever subsystem has *both* on PATH. Crossing the boundary
  does not work.

---

## Evidence artifacts vs. engineering claims

Running the solver and refreshing the summary **creates or updates evidence
artifacts** inside the `.aieng` package:

| Artifact | Written by | Evidence role |
|----------|-----------|---------------|
| `simulation/runs/<id>/solver_run.json` | `cae.run_solver` | solver execution metadata, audit |
| `simulation/runs/<id>/outputs/result.frd` | `cae.run_solver` | raw numerical result source |
| `results/computed_metrics.json` | `cae.run_solver` (with `extract_results=True`) | FRD-extracted scalar extrema |
| `results/result_summary.json` | `postprocess.refresh_cae_summary` | LLM-readable summary |
| `results/evidence_index.json` | `postprocess.refresh_cae_summary` | auditable artifact catalog |

**Neither step validates or advances engineering claims automatically.**
`ai/claim_map.json` and `results/claim_map.json` are not written by
`cae.run_solver` or `postprocess.refresh_cae_summary`. Claim advancement
is an explicit, separate workflow that requires a deliberate
claim-update step.

This boundary is verified by `test_evidence_claim_contract_after_cae_run`
in `aieng-ui/backend/tests/test_api.py`.

---

## Honest limitations

| Limitation | Why |
|-----------|-----|
| **No mesh generation** | The basic `.inp` deck must already contain mesh. AIENG does not generate nodes or elements for the basic smoke test. (The full-pipeline test above does generate a mesh, but its boundary-condition synthesis is coordinate-based and not a general feature.) |
| **No input deck generation** | The input deck must be prepared externally or imported. AIENG does not create `.inp` files from geometry. |
| **No field visualization** | The frontend colormap is synthetic (`y_normalized`). Real per-node field serving is future work. |
| **No automatic convergence proof** | `converged` remains `null` because CalculiX exit codes alone are not reliable evidence of convergence. |
| **No physical correctness validation** | No experimental correlation, mesh convergence study, or independent validation is performed. |
| **Only CalculiX (`ccx`) supported** | The adapter looks for `ccx`, `ccx_linux`, `ccx2.21`, `ccx_static`. Other solvers are not in scope. |

---

## References

- [`quickstart-vertical-cae-demo.md`](quickstart-vertical-cae-demo.md) — mocked benchmark (no ccx install required)
- [`../../docs/demo-vertical-cae-workflow.md`](../../docs/demo-vertical-cae-workflow.md) — full walkthrough
- [`../../docs/aieng-agent-workflow.md`](../../docs/aieng-agent-workflow.md) — reusable agent pattern
