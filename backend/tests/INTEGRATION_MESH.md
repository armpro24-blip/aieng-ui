# PR 3B Integration Test: `cae.generate_mesh` with Real FreeCAD/Gmsh

This document describes how to run the real-integration test for `cae.generate_mesh`.

## Overview

The integration test verifies the full pipeline:

```
.aieng ZIP 内 geometry 解包
    → FreeCAD/Gmsh mesh 生成
    → .inp 产物
    → mesh_metadata.json
    → 原子写回 .aieng
    → 返回 completed
```

## Prerequisites

### 1. Install FreeCAD

Download and install FreeCAD from https://www.freecad.org/downloads.php.

Recommended version: **FreeCAD 1.0** or later (includes Gmsh tools).

On Windows the default install path is:

```
C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe
```

### 2. Verify Gmsh availability

FreeCAD 1.0 bundles Gmsh. Confirm it is importable inside FreeCAD:

```powershell
& "C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe" -c "import femmesh.gmshtools; print('Gmsh OK')"
```

If you see `Gmsh OK`, the toolchain is ready.

### 3. Environment variable

Set `AIENG_TEST_REAL_FREECAD=1` to opt-in to the integration test.

```powershell
$env:AIENG_TEST_REAL_FREECAD = "1"
```

Or permanently via System Properties → Environment Variables.

The test also accepts an optional `FREECAD_CMD` override:

```powershell
$env:FREECAD_CMD = "C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe"
```

If `FREECAD_CMD` is not set, the test searches common installation directories and the system `PATH`.

## Running the Test

### Single test (fastest)

```powershell
cd aieng-ui\backend
$env:AIENG_TEST_REAL_FREECAD = "1"
python -m pytest tests\test_api.py::test_cae_generate_mesh_real_freecad_integration -v
```

### All mesh-related tests

```powershell
cd aieng-ui\backend
$env:AIENG_TEST_REAL_FREECAD = "1"
python -m pytest tests\test_api.py -v -k "cae_generate_mesh"
```

Expected output when FreeCAD is available:

```
tests/test_api.py::test_cae_generate_mesh_registered_with_approval PASSED
tests/test_api.py::test_cae_generate_mesh_requires_approval PASSED
tests/test_api.py::test_cae_generate_mesh_unpacks_geometry_from_zip PASSED
tests/test_api.py::test_cae_generate_mesh_missing_geometry_returns_error PASSED
tests/test_api.py::test_cae_generate_mesh_real_freecad_integration PASSED   <-- real run
tests/test_api.py::test_cae_generate_mesh_no_freecad_returns_error PASSED
```

Expected output when FreeCAD is **not** available:

```
tests/test_api.py::test_cae_generate_mesh_real_freecad_integration SKIPPED
```

## Inspecting the Output

The integration test writes artifacts into a temporary `.aieng` package. To inspect it:

### 1. Add a `print` inside the test

Temporarily add the following line near the end of the test to see the package path:

```python
print("Integration package:", pkg_path)
```

### 2. List ZIP contents

```powershell
python -c "import zipfile, sys; zf = zipfile.ZipFile(sys.argv[1]); print('\n'.join(zf.namelist()))" "<pkg_path>"
```

You should see entries like:

```
manifest.json
simulation/solver_settings.json
simulation/cae_imports/parsed_loads.json
geometry/source.step
simulation/mesh/mesh_3.0mm.inp
simulation/mesh/mesh_metadata.json
```

### 3. Extract and inspect metadata

```powershell
python -c "import zipfile, json, sys; zf = zipfile.ZipFile(sys.argv[1]); print(json.dumps(json.loads(zf.read('simulation/mesh/mesh_metadata.json')), indent=2))" "<pkg_path>"
```

Example output:

```json
{
  "schema_version": "0.1",
  "mesh_size_mm": 3.0,
  "element_type": "tetrahedral",
  "output_format": "inp",
  "source_geometry": "C:\\...\\geometry\\source.step",
  "mesh_file": "simulation/mesh/mesh_3.0mm.inp",
  "generated_at": "2026-05-17T04:30:00+00:00"
}
```

### 4. Extract the mesh

```powershell
python -c "import zipfile, sys; zf = zipfile.ZipFile(sys.argv[1]); open('mesh.inp','wb').write(zf.read('simulation/mesh/mesh_3.0mm.inp'))" "<pkg_path>"
```

You can then open `mesh.inp` in CalculiX CAE, Gmsh, or ParaView.

## Failure-Path Guarantees

When FreeCAD is **not** installed:

- The tool returns `{"ok": false, "status": "error", "code": "freecad_unavailable"}`
- No fake `simulation/mesh/*.inp` is written into the `.aieng` package
- No fake `mesh_metadata.json` is written
- The existing `test_cae_generate_mesh_no_freecad_returns_error` enforces this contract

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SKIPPED` | `AIENG_TEST_REAL_FREECAD` not set | `export AIENG_TEST_REAL_FREECAD=1` |
| `SKIPPED` | FreeCADCmd not found | Install FreeCAD or set `FREECAD_CMD` |
| `error: freecad_unavailable` | FreeCADCmd path wrong | Verify path and set `FREECAD_CMD` |
| `ModuleNotFoundError: femmesh.gmshtools` | Gmsh workbench missing | Use FreeCAD 1.0+ or install Gmsh add-on |
| Macro timeout | Mesh too coarse/fine | Adjust `mesh_size_mm` in test input |
