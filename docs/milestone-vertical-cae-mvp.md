# Milestone: Vertical CAE MVP

The current MVP positions AIENG as an evidence layer that converts CAD/CAE
lifecycle artifacts into LLM-readable engineering evidence, while the
workbench runtime executes approved actions and writes artifacts back into the
package, the MCP bridge exposes those capabilities to external agents, and the
Skill layer teaches the LLM the correct evidence-first order of operations.

## Repo roles

- aieng: evidence package structure, artifact detection, summary generation,
  schema version constants, and FRD scalar extraction.
- aieng-ui: workbench frontend plus local backend/runtime, approval flow,
  audit events, external CalculiX adapter, artifact inspector, and setup patch
  diff review.
- aieng_freecad_mcp: MCP bridge, FreeCAD adapter surface, and runtime tool
  wrappers.
- aieng-agent-skills: reusable agent workflow instructions plus Skill
  validation checklist.

## Current vertical CAE MVP loop

1. Read AIENG evidence.
2. Apply setup patch if needed.
3. Prepare solver run.
4. Execute approval-gated external CalculiX run.
5. Capture FRD output.
6. Extract scalar metrics.
7. Refresh summaries.
8. Inspect artifacts and diffs.
9. Produce an evidence-backed report.

## What is real in this MVP

- Runtime tool chain for the end-to-end CAE loop.
- External solver adapter in the workbench runtime.
- FRD scalar extraction pipeline.
- computed_metrics to result_summary refresh pipeline.
- Artifact inspector and JSON diff review flow.
- MCP wrappers for agent access.
- Vertical benchmark test covering preflight, approval, run, extraction,
  refresh, and honest reporting behavior.

## Current limits (intentional)

- No mesh generation.
- No input deck generation unless a deck already exists.
- No field visualization workflow.
- No automatic convergence proof.
- No physical correctness validation.
- No arbitrary CAD mutation path.

## How to run and check the MVP

- Quickstart: [docs/quickstart-vertical-cae-demo.md](quickstart-vertical-cae-demo.md)
- Vertical benchmark command:

```powershell
cd C:\Users\RL_Carla\Desktop\workspace_aieng\aieng-ui\backend
python -m pytest -c NUL tests/test_api.py::test_vertical_cae_workflow_end_to_end -v
```

- Skill validation checklist:
  - [aieng-agent-skills/skills/aieng-cad-cae-copilot/validation.md](../../aieng-agent-skills/skills/aieng-cad-cae-copilot/validation.md)

## Next-phase candidates (not implemented here)

- Real CalculiX environment quickstart.
- Input deck generation or import bridge.
- UI evidence graph and richer diff viewer.
- CAD parameter edit prototype.
- DRY/refactor of large runtime files.
- Live runtime events.
