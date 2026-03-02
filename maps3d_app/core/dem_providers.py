# maps3d_app/core/dem_providers.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .dem_downloader import download_srtm_dem_for_bbox  # <-- qui

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

class SrtmProvider(DemProvider):
    name = "srtm"

    def get_dem(self, bbox: BBox, out_path: Path, log: LogFn = None) -> Path:
        # Se la tua funzione NON supporta log=..., rimuovi log=log
        dem_path = download_srtm_dem_for_bbox(
            (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat),
            str(out_path),
            log=log,
        )
        return Path(dem_path)
