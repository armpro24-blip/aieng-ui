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
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng-ui\backend
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

## Honest limitations

| Limitation | Why |
|-----------|-----|
| **No mesh generation** | The `.inp` deck must already contain mesh. AIENG does not generate nodes or elements. |
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
