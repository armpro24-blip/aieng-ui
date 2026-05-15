from __future__ import annotations

from typing import Any

from .freecad.adapter import FreeCADAdapter
from .protocols import CadExecutionProvider


def get_provider(settings: Any, config: dict[str, str]) -> CadExecutionProvider:
    provider = config.get("provider", "freecad")
    if provider == "freecad":
        return FreeCADAdapter(settings=settings, config=config)
    raise ValueError(f"unsupported CAD provider: {provider}")
