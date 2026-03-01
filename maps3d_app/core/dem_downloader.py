from __future__ import annotations

import math
import os
import subprocess
import time
from pathlib import Path


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


def _try_elevation_clip(
    *,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_path: Path,
    product: str,
    timeout_s: int,
    env: dict[str, str],
) -> tuple[bool, str]:
    """
    Run elevation clip with a HARD timeout.
    Returns: (success, diagnostic_output)
    """
    cmd = [
        "elevation",
        "clip",
        f"-b={min_lon}",
        str(min_lat),
        str(max_lon),
        str(max_lat),
        f"-o={out_path}",
        "--product",
        product,
    ]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_s,
        )
        out = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        ok = (res.returncode == 0) and out_path.exists() and out_path.stat().st_size > 0
        return ok, out.strip()

    except subprocess.TimeoutExpired as exc:
        out = ""
        if exc.stdout:
            out += exc.stdout
        if exc.stderr:
            out += "\n" + exc.stderr
        return False, f"[timeout {timeout_s}s] elevation clip product={product}\n{out.strip()}"

    except FileNotFoundError as exc:
        raise RuntimeError(
            "Comando 'elevation' non trovato. Assicurati che la dependency 'elevation' sia installata."
        ) from exc


def download_srtm_dem_for_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_tif_path: str | Path,
    timeout_s: int = 90,
    retries: int = 2,
) -> Path:
    """
    Download+clip SRTM DEM for bbox using `elevation clip`.

    Robust behavior:
    - HARD timeout per attempt (default 90s)
    - retry
    - fallback SRTM1 -> SRTM3
    - clean partial outputs
    """
    _validate_area(min_lon, min_lat, max_lon, max_lat)

    out_path = Path(out_tif_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    cache_dir = out_path.parent / ".elevation_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env["ELEVATION_DATA"] = str(cache_dir)

    # reuse if already exists
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    attempts: list[tuple[str, int]] = []
    # SRTM1 attempts then fallback to SRTM3
    for _ in range(retries + 1):
        attempts.append(("SRTM1", timeout_s))
    attempts.append(("SRTM3", max(timeout_s, 120)))

    last_diag = ""
    for product, to_s in attempts:
        # cleanup broken file
        if out_path.exists() and out_path.stat().st_size == 0:
            try:
                out_path.unlink()
            except OSError:
                pass

        ok, diag = _try_elevation_clip(
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            out_path=out_path,
            product=product,
            timeout_s=to_s,
            env=env,
        )
        last_diag = diag

        if ok:
            return out_path

        # small backoff before retry
        time.sleep(1.0)

    raise RuntimeError(
        "Download DEM SRTM fallito.\n"
        f"bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}]\n"
        f"output atteso: {out_path}\n"
        f"diagnostica:\n{last_diag}"
    )
