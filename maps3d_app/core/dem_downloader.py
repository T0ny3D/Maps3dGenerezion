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
        raise ValueError(
            "Area GPX troppo estesa per download automatico SRTM: supera 1x1 grado. "
            "Riduci il GPX o usa un DEM manuale."
        )

    w_km = _haversine_km(min_lon, min_lat, max_lon, min_lat)
    h_km = _haversine_km(min_lon, min_lat, min_lon, max_lat)
    if max(w_km, h_km) > 200.0:
        raise ValueError(
            f"Area GPX troppo estesa ({w_km:.1f}x{h_km:.1f} km). Limite automatico: 200 km."
        )


def _run_elevation_clip(
    *,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_path: Path,
    product: str,
    timeout_s: int,
    idle_timeout_s: int,
    env: dict[str, str],
) -> tuple[int, str]:
    """
    Run `elevation clip` streaming output to avoid infinite hangs.
    Returns: (returncode, combined_output)
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

    start = time.time()
    last_output = time.time()
    lines: list[str] = []

    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    except OSError as exc:
        raise RuntimeError(
            "Comando 'elevation' non disponibile. Installa dependencies da requirements.txt"
        ) from exc

    assert p.stdout is not None

    while True:
        # Legge una riga se disponibile (non blocca troppo)
        line = p.stdout.readline()
        if line:
            last_output = time.time()
            lines.append(line.rstrip())
        else:
            # Se il processo è terminato, usciamo
            rc = p.poll()
            if rc is not None:
                break

            # Check idle timeout / total timeout
            now = time.time()
            if now - last_output > idle_timeout_s:
                p.kill()
                lines.append(
                    f"[maps3d] elevation clip idle-timeout after {idle_timeout_s}s (product={product})"
                )
                return 124, "\n".join(lines)

            if now - start > timeout_s:
                p.kill()
                lines.append(
                    f"[maps3d] elevation clip total-timeout after {timeout_s}s (product={product})"
                )
                return 124, "\n".join(lines)

            time.sleep(0.2)

    output = "\n".join(lines)
    return p.returncode or 0, output


def download_srtm_dem_for_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_tif_path: str | Path,
    timeout_s: int = 420,
    idle_timeout_s: int = 45,
    retries: int = 1,
) -> Path:
    """
    Download+clip SRTM DEM for bbox using `elevation clip`.

    Improvements vs previous version:
    - streaming output (no silent hangs)
    - idle-timeout (abort if no output for a while)
    - retry + fallback product (SRTM1 -> SRTM3)
    """
    _validate_area(min_lon, min_lat, max_lon, max_lat)

    out_path = Path(out_tif_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    cache_dir = out_path.parent / ".elevation_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env["ELEVATION_DATA"] = str(cache_dir)

    # Se esiste già ed è non vuoto, riusa
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    # Proviamo in ordine: SRTM1 (retry), poi fallback SRTM3
    products = ["SRTM1"] * (retries + 1) + ["SRTM3"]

    last_output = ""
    last_rc = 1

    for product in products:
        rc, out_text = _run_elevation_clip(
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            out_path=out_path,
            product=product,
            timeout_s=timeout_s,
            idle_timeout_s=idle_timeout_s,
            env=env,
        )
        last_rc = rc
        last_output = out_text

        if rc == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return out_path

        # pulizia file vuoti/rotti prima di retry
        if out_path.exists() and out_path.stat().st_size == 0:
            try:
                out_path.unlink()
            except OSError:
                pass

    # Se arriviamo qui: fallito
    msg = (
        "Download DEM SRTM fallito.\n"
        f"bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}]\n"
        f"output atteso: {out_path}\n"
        f"returncode: {last_rc}\n"
        "output:\n"
        f"{last_output.strip()}"
    )
    raise RuntimeError(msg)
