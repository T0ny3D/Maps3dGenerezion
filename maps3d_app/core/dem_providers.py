# maps3d_app/core/dem_providers.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

LogFn = Optional[Callable[[str], None]]

@dataclass(frozen=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

class DemProviderError(RuntimeError):
    pass

class DemProvider:
    name: str = "base"
    def get_dem(self, bbox: BBox, out_path: Path, log: LogFn = None) -> Path:
        raise NotImplementedError
