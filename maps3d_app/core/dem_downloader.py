from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _validate_area(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> None:
    lon_span = abs(max_lon - min_lon)
    lat_span = abs(max_lat - min_lat)
    if lon_span > 1.0 or lat_span > 1.0:
        raise ValueError(
            "Area GPX troppo estesa per download automatico SRTM: supera 1x1 grado. "
            "Riduci il GPX o usa un DEM manuale."
        )

    w_km = _haversine_km(min_lon, min_lat, max_lon, min_lat)
    h_km = _haversine_km(min_lon, min_lat, min_lon, max_lat)
    if max(w_km, h_km) > 200.0:
        raise ValueError(
            f"Area GPX troppo estesa ({w_km:.1f}x{h_km:.1f} km). "
            "Limite automatico: 200 km."
        )


def download_srtm_dem_for_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_tif_path: str | Path,
    timeout_s: int = 600,
) -> Path:
    """Download+clip SRTM DEM (30m) for bbox using elevation CLI.

    Returns output tif path.
    """
    _validate_area(min_lon, min_lat, max_lon, max_lat)

    out_path = Path(out_tif_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    cache_dir = out_path.parent / ".elevation_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env["ELEVATION_DATA"] = str(cache_dir)

    cmd = [
        "elevation",
        "clip",
        f"-b={min_lon}",
        str(min_lat),
        str(max_lon),
        str(max_lat),
        f"-o={out_path}",
        "--product",
        "SRTM1",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, env=env)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timeout download DEM SRTM. Riprova con area più piccola.") from exc
    except OSError as exc:
        raise RuntimeError("Comando 'elevation' non disponibile. Installa dependencies da requirements.txt") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").lower()
        if "network" in stderr or "ssl" in stderr or "http" in stderr or "download" in stderr:
            raise RuntimeError(f"Errore rete durante download DEM SRTM:\n{result.stderr.strip()}")
        raise RuntimeError(f"Download DEM SRTM fallito:\n{result.stderr.strip() or result.stdout.strip()}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Download DEM terminato ma file output mancante/vuoto.")

    return out_path
