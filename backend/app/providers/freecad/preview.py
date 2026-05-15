from __future__ import annotations

import textwrap


FREECAD_PREVIEW_SCRIPT = textwrap.dedent(
    """
    import json
    import os

    import FreeCAD
    import Mesh
    import MeshPart
    import Part

    step_input = os.environ["AIENG_PLATFORM_STEP_INPUT"]
    stl_output = os.environ["AIENG_PLATFORM_STL_OUTPUT"]
    result_output = os.environ["AIENG_PLATFORM_RESULT_OUTPUT"]
    linear_deflection = float(os.environ.get("AIENG_PLATFORM_LINEAR_DEFLECTION", "0.1"))
    angular_deflection = float(os.environ.get("AIENG_PLATFORM_ANGULAR_DEFLECTION", "0.35"))

    doc = FreeCAD.newDocument("AiengPreview")
    Part.insert(step_input, doc.Name)
    doc.recompute()

    objects = [obj for obj in doc.Objects if hasattr(obj, "Shape")]
    if not objects:
        raise ValueError("No exportable shape objects found after STEP import.")

    shapes = [obj.Shape for obj in objects]
    compound = shapes[0] if len(shapes) == 1 else Part.makeCompound(shapes)
    bbox = compound.BoundBox

    meshes = []
    for obj in objects:
        meshes.append(
            MeshPart.meshFromShape(
                Shape=obj.Shape,
                LinearDeflection=linear_deflection,
                AngularDeflection=angular_deflection,
                Relative=False,
            )
        )

    final_mesh = meshes[0] if len(meshes) == 1 else Mesh.Mesh()
    if len(meshes) > 1:
        for mesh in meshes:
            final_mesh.addMesh(mesh)

    final_mesh.write(stl_output)
    result = {
        "object_count": len(objects),
        "object_names": [obj.Name for obj in objects],
        "stl_output": stl_output,
        "bounds": {
            "xmin": float(bbox.XMin),
            "xmax": float(bbox.XMax),
            "ymin": float(bbox.YMin),
            "ymax": float(bbox.YMax),
            "zmin": float(bbox.ZMin),
            "zmax": float(bbox.ZMax),
        },
    }
    with open(result_output, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    """
).strip()
