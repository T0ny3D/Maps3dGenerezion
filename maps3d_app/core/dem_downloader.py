from __future__ import annotations

import math
import os
import platform
import subprocess
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


def _kill_process_tree_windows(pid: int) -> None:
    # /T = termina child processes, /F = forza
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
    )


def _run_elevation_clip_hard(
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
    Run `eio clip` with a HARD timeout that kills the full process tree on Windows.
    Returns (ok, diag).
    """
    # CLI ufficiale del pacchetto elevation è: eio
    # eio --product SRTM1 clip -o out.tif --bounds west south east north
    cmd = [
        "eio",
        "--product",
        product,
        "clip",
        "-o",
        str(out_path),
        "--bounds",
        str(min_lon),
        str(min_lat),
        str(max_lon),
        str(max_lat),
    ]

    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Comando 'eio' non trovato. Nella build EXE deve essere presente il tool del pacchetto 'elevation'."
        ) from exc

    try:
        stdout, stderr = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # kill tree (important on Windows)
        if platform.system() == "Windows":
            _kill_process_tree_windows(p.pid)
        else:
            p.kill()
        try:
            stdout, stderr = p.communicate(timeout=10)
        except Exception:
            stdout, stderr = "", ""
        diag = f"[timeout {timeout_s}s] eio clip product={product}\n{stdout}\n{stderr}".strip()
        return False, diag

    out = (stdout or "") + ("\n" + stderr if stderr else "")
    ok = (p.returncode == 0) and out_path.exists() and out_path.stat().st_size > 0
    return ok, out.strip()


def download_srtm_dem_for_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_tif_path: str | Path,
    timeout_s: int = 120,
    retries: int = 1,
    log: LogFn = None,
) -> Path:
    """
    Download+clip SRTM DEM for bbox using `eio clip`.

    Robust:
    - HARD timeout per attempt (kills process tree on Windows)
    - retry
    - fallback SRTM1 -> SRTM3
    - optional live logging via `log(str)`
    """
    _validate_area(min_lon, min_lat, max_lon, max_lat)

    out_path = Path(out_tif_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    cache_dir = out_path.parent / ".elevation_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env["ELEVATION_DATA"] = str(cache_dir)

    if log:
        log(f"SRTM DEM: cache={cache_dir}")
        log(f"SRTM DEM: bbox=[{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}]")
        log(f"SRTM DEM: output={out_path}")

    if out_path.exists() and out_path.stat().st_size > 0:
        if log:
            log("SRTM DEM: file già presente, salto download.")
        return out_path

    attempts: list[tuple[str, int]] = []
    for _ in range(retries + 1):
        attempts.append(("SRTM1", timeout_s))
    attempts.append(("SRTM3", max(timeout_s, 180)))

    last_diag = ""
    for product, to_s in attempts:
        # cleanup broken file
        if out_path.exists() and out_path.stat().st_size == 0:
            try:
                out_path.unlink()
            except OSError:
                pass

        if log:
            log(f"SRTM DEM: tentativo product={product} timeout={to_s}s (cmd: eio clip)")

        ok, diag = _run_elevation_clip_hard(
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
            if log:
                log(f"SRTM DEM: OK ({product}) -> {out_path.name}")
            return out_path

        if log:
            short = (diag[:400] + "…") if len(diag) > 400 else diag
            log(f"SRTM DEM: fallito ({product}). {short}")

        time.sleep(1.0)

    raise RuntimeError(
        "Download DEM SRTM fallito.\n"
        f"bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}]\n"
        f"output atteso: {out_path}\n"
        f"cache: {cache_dir}\n"
        f"diagnostica:\n{last_diag}"
    )
