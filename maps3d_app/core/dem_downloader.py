from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Callable, Optional

LogFn = Optional[Callable[[str], None]]


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
        raise ValueError("Area troppo estesa (>1°). Riduci GPX o usa DEM manuale.")

    w_km = _haversine_km(min_lon, min_lat, max_lon, min_lat)
    h_km = _haversine_km(min_lon, min_lat, min_lon, max_lat)
    if max(w_km, h_km) > 200.0:
        raise ValueError(f"Area troppo estesa ({w_km:.1f}x{h_km:.1f} km). Limite 200 km.")


def download_srtm_dem_for_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_tif_path: str | Path,
    timeout_s: int = 120,  # tenuto per compatibilità (l’API non sempre lo usa)
    retries: int = 1,
    log: LogFn = None,
) -> Path:
    """
    Download+clip SRTM DEM for bbox usando l'API Python del pacchetto `elevation`
    (NO subprocess / NO CLI `eio`), quindi compatibile con PyInstaller onefile.
    """
    _validate_area(min_lon, min_lat, max_lon, max_lat)

    out_path = Path(out_tif_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # cache elevation (stile elevation)
    cache_dir = out_path.parent / ".elevation_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ELEVATION_DATA"] = str(cache_dir)

    if log:
        log(f"SRTM DEM: cache={cache_dir}")
        log(f"SRTM DEM: bbox=[{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}]")
        log(f"SRTM DEM: output={out_path}")

    if out_path.exists() and out_path.stat().st_size > 0:
        if log:
            log("SRTM DEM: file già presente, salto download.")
        return out_path

    # Import qui per evitare costi/errore startup se manca
    try:
        import elevation  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Modulo 'elevation' non importabile. Verifica che sia in requirements e incluso in PyInstaller."
        ) from exc

    attempts: list[str] = ["SRTM1"] * (retries + 1) + ["SRTM3"]

    last_err: str = ""
    for product in attempts:
        if log:
            log(f"SRTM DEM: tentativo product={product} (Python API elevation.clip)")

        tmp = out_path.with_suffix(out_path.suffix + ".part")
        try:
            # alcuni setup accettano product=..., altri usano 'dem'/'product': gestiamo con try
            try:
                elevation.clip(bounds=(min_lon, min_lat, max_lon, max_lat), output=str(tmp), product=product)
            except TypeError:
                elevation.clip(bounds=(min_lon, min_lat, max_lon, max_lat), output=str(tmp))

            if tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(out_path)
                if log:
                    log(f"SRTM DEM: OK ({product}) -> {out_path.name}")
                return out_path

            last_err = f"Output vuoto dopo elevation.clip (product={product})"
            if log:
                log(f"SRTM DEM: fallito ({product}). {last_err}")

        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            if log:
                log(f"SRTM DEM: fallito ({product}). {last_err}")

        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

        time.sleep(1.0)

    raise RuntimeError(
        "Download DEM SRTM fallito (Python elevation API).\n"
        f"bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}]\n"
        f"output atteso: {out_path}\n"
        f"cache: {cache_dir}\n"
        f"ultimo errore: {last_err}"
    )
