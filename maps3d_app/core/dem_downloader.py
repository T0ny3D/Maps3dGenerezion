from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
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
    # Limiti “di sicurezza” (puoi alzarli dopo)
    lon_span = abs(max_lon - min_lon)
    lat_span = abs(max_lat - min_lat)
    if lon_span > 2.0 or lat_span > 2.0:
        raise ValueError("Area troppo estesa (>2°). Riduci GPX o usa DEM manuale.")

    w_km = _haversine_km(min_lon, min_lat, max_lon, min_lat)
    h_km = _haversine_km(min_lon, min_lat, min_lon, max_lat)
    if max(w_km, h_km) > 300.0:
        raise ValueError(f"Area troppo estesa ({w_km:.1f}x{h_km:.1f} km). Limite 300 km.")


def _download_url_to_file(url: str, out_path: Path, timeout_s: int, log: LogFn = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Maps3DGen"})
    if log:
        log(f"OpenTopo: GET {url[:120]}...")

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        data = resp.read()

    # OpenTopography in caso di errore spesso ritorna JSON
    if "application/json" in ctype or (data[:1] == b"{" and data[-1:] in (b"}", b"\n")):
        try:
            msg = json.loads(data.decode("utf-8", errors="ignore"))
        except Exception:
            msg = data.decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenTopo error: {msg}")

    out_path.write_bytes(data)


def download_srtm_dem_for_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_tif_path: str | Path,
    timeout_s: int = 120,
    retries: int = 1,
    log: LogFn = None,
    api_key: str | None = None,
    demtype: str = "SRTMGL1",
) -> Path:
    """
    Download DEM (GeoTIFF) da OpenTopography Global DEM API.
    Compatibile con PyInstaller (no subprocess, no make, no CLI).

    demtype comuni:
      - SRTMGL1 (SRTM 30m)
      - SRTMGL3 (SRTM 90m)
      - COP90 (Copernicus 90m)
      - ALOS (se disponibile via OpenTopo)

    Serve API key: la passi con api_key=... oppure via env OPENTOPO_API_KEY.
    """
    _validate_area(min_lon, min_lat, max_lon, max_lat)

    out_path = Path(out_tif_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and out_path.stat().st_size > 0:
        if log:
            log("DEM: file già presente, salto download.")
        return out_path

    key = (api_key or os.environ.get("OPENTOPO_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "OpenTopography: API key mancante. Inseriscila nel campo UI oppure imposta OPENTOPO_API_KEY."
        )

    if log:
        log(f"OpenTopo: demtype={demtype}")
        log(f"OpenTopo: bbox=[{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}]")
        log(f"OpenTopo: output={out_path.name}")

    # Endpoint ufficiale “globaldem”
    params = {
        "demtype": demtype,
        "south": str(min_lat),
        "north": str(max_lat),
        "west": str(min_lon),
        "east": str(max_lon),
        "outputFormat": "GTiff",
        "API_Key": key,
    }
    url = "https://portal.opentopography.org/API/globaldem?" + urllib.parse.urlencode(params)

    last_err = ""
    tmp = out_path.with_suffix(out_path.suffix + ".part")

    for attempt in range(retries + 1):
        try:
            if log:
                log(f"OpenTopo: download tentativo {attempt+1}/{retries+1} (timeout={timeout_s}s)")
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass

            _download_url_to_file(url, tmp, timeout_s=timeout_s, log=log)

            if not tmp.exists() or tmp.stat().st_size == 0:
                raise RuntimeError("OpenTopo: file scaricato vuoto.")

            tmp.replace(out_path)
            if log:
                log(f"OpenTopo: OK -> {out_path}")
            return out_path

        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            if log:
                log(f"OpenTopo: fallito tentativo {attempt+1}: {last_err}")
            time.sleep(1.0)

    raise RuntimeError(
        "Download DEM fallito (OpenTopography).\n"
        f"bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}]\n"
        f"output atteso: {out_path}\n"
        f"ultimo errore: {last_err}"
    )
