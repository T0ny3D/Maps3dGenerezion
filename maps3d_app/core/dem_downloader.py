from __future__ import annotations

import gzip
import math
import os
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Iterable

import rasterio
from rasterio.merge import merge

SRTM1_BASE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
SRTM3_BASE_URL = "https://srtm.csi.cgiar.org/wp-content/uploads/files/srtm_5x5/TIFF"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
USER_AGENT = "Maps3dGenerezion/1.0"


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


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


def _srtm1_tile_ilonlat(lon: float, lat: float) -> tuple[int, int]:
    return int(math.floor(lon)), int(math.floor(lat))


def _srtm3_tile_ilonlat(lon: float, lat: float) -> tuple[int, int]:
    ilon, ilat = _srtm1_tile_ilonlat(lon, lat)
    return (ilon + 180) // 5 + 1, (64 - ilat) // 5


def _iter_srtm1_tiles(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> Iterable[tuple[str, str]]:
    ileft, itop = _srtm1_tile_ilonlat(min_lon, max_lat)
    iright, ibottom = _srtm1_tile_ilonlat(max_lon, min_lat)
    if float(max_lat).is_integer():
        itop -= 1
    if float(max_lon).is_integer():
        iright -= 1
    for ilon in range(ileft, iright + 1):
        slon = f"{'E' if ilon >= 0 else 'W'}{abs(ilon):03d}"
        for ilat in range(ibottom, itop + 1):
            slat = f"{'N' if ilat >= 0 else 'S'}{abs(ilat):02d}"
            yield slat, slon


def _iter_srtm3_tiles(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> Iterable[tuple[int, int]]:
    ileft, itop = _srtm3_tile_ilonlat(min_lon, max_lat)
    iright, ibottom = _srtm3_tile_ilonlat(max_lon, min_lat)
    for ilon in range(ileft, iright + 1):
        for ilat in range(itop, ibottom + 1):
            yield ilon, ilat


def _download_url(url: str, dest: Path, timeout_s: int, log: Callable[[str], None] | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            total_header = resp.getheader("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else None
            downloaded = 0
            last_log = time.monotonic()
            with open(tmp, "wb") as out_file:
                while True:
                    chunk = resp.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)
                    if log and time.monotonic() - last_log > 2:
                        if total:
                            log(
                                f"Scaricati {downloaded / 1024 / 1024:.1f} MB di {total / 1024 / 1024:.1f} MB..."
                            )
                        else:
                            log(f"Scaricati {downloaded / 1024 / 1024:.1f} MB...")
                        last_log = time.monotonic()
        os.replace(tmp, dest)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Errore HTTP {exc.code} durante download {url}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            raise RuntimeError(f"Errore SSL durante download {url}: {reason}") from exc
        if isinstance(reason, socket.timeout):
            raise RuntimeError(f"Timeout rete durante download {url}") from exc
        raise RuntimeError(f"Errore rete/proxy durante download {url}: {reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"Timeout rete durante download {url}") from exc
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _gunzip_file(src: Path, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with gzip.open(src, "rb") as src_file, open(tmp, "wb") as dest_file:
        shutil.copyfileobj(src_file, dest_file)
    os.replace(tmp, dest)


def _extract_zip_member(src: Path, member: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with zipfile.ZipFile(src) as zf, zf.open(member) as member_file, open(tmp, "wb") as dest_file:
        shutil.copyfileobj(member_file, dest_file)
    os.replace(tmp, dest)


def _merge_tiles_to_bbox(tile_paths: list[Path], bounds: tuple[float, float, float, float], out_path: Path) -> None:
    if not tile_paths:
        raise RuntimeError("Nessun tile DEM disponibile per il bbox richiesto.")
    datasets: list[rasterio.DatasetReader] = []
    try:
        for path in tile_paths:
            datasets.append(rasterio.open(path))
        mosaic, transform = merge(datasets, bounds=bounds)
        profile = datasets[0].profile
        profile.update(
            driver="GTiff",
            height=mosaic.shape[1],
            width=mosaic.shape[2],
            transform=transform,
            count=mosaic.shape[0],
        )
        with rasterio.open(out_path, "w", **profile) as dest:
            dest.write(mosaic)
    finally:
        for ds in datasets:
            ds.close()


def _download_srtm1_tiles(
    *,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    cache_dir: Path,
    deadline: float,
    log: Callable[[str], None] | None,
) -> list[Path]:
    tiles: list[Path] = []
    for slat, slon in _iter_srtm1_tiles(min_lon, min_lat, max_lon, max_lat):
        tile_dir = cache_dir / "SRTM1" / slat
        tile_path = tile_dir / f"{slat}{slon}.hgt"
        if tile_path.exists() and tile_path.stat().st_size > 0:
            tiles.append(tile_path)
            continue
        url = f"{SRTM1_BASE_URL}/{slat}/{slat}{slon}.hgt.gz"
        gz_path = tile_dir / f"{slat}{slon}.hgt.gz"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Timeout download DEM (SRTM1).")
        _log(log, f"Download tile {slat}{slon} (SRTM1)...")
        _download_url(url, gz_path, timeout_s=max(5, int(remaining)), log=log)
        _gunzip_file(gz_path, tile_path)
        gz_path.unlink(missing_ok=True)
        tiles.append(tile_path)
    return tiles


def _download_srtm3_tiles(
    *,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    cache_dir: Path,
    deadline: float,
    log: Callable[[str], None] | None,
) -> list[Path]:
    tiles: list[Path] = []
    tile_dir = cache_dir / "SRTM3"
    for ilon, ilat in _iter_srtm3_tiles(min_lon, min_lat, max_lon, max_lat):
        base_name = f"srtm_{ilon:02d}_{ilat:02d}"
        tif_path = tile_dir / f"{base_name}.tif"
        if tif_path.exists() and tif_path.stat().st_size > 0:
            tiles.append(tif_path)
            continue
        zip_path = tile_dir / f"{base_name}.zip"
        url = f"{SRTM3_BASE_URL}/{base_name}.zip"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Timeout download DEM (SRTM3).")
        _log(log, f"Download tile {base_name} (SRTM3)...")
        _download_url(url, zip_path, timeout_s=max(5, int(remaining)), log=log)
        _extract_zip_member(zip_path, f"{base_name}.tif", tif_path)
        zip_path.unlink(missing_ok=True)
        tiles.append(tif_path)
    return tiles


def _download_and_merge(
    *,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_path: Path,
    cache_dir: Path,
    product: str,
    timeout_s: int,
    log: Callable[[str], None] | None,
) -> None:
    deadline = time.monotonic() + timeout_s
    if product == "SRTM1":
        tiles = list(_iter_srtm1_tiles(min_lon, min_lat, max_lon, max_lat))
    else:
        tiles = list(_iter_srtm3_tiles(min_lon, min_lat, max_lon, max_lat))
    _log(log, f"Prodotto {product}: {len(tiles)} tile da scaricare.")
    if product == "SRTM1":
        tile_paths = _download_srtm1_tiles(
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            cache_dir=cache_dir,
            deadline=deadline,
            log=log,
        )
    else:
        tile_paths = _download_srtm3_tiles(
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            cache_dir=cache_dir,
            deadline=deadline,
            log=log,
        )
    _log(log, "Merge e clip DEM...")
    _merge_tiles_to_bbox(tile_paths, (min_lon, min_lat, max_lon, max_lat), out_path)


def download_srtm_dem_for_bbox(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    out_tif_path: str | Path,
    timeout_s: int = 120,
    retries: int = 1,
    log: Callable[[str], None] | None = None,
) -> Path:
    """
    Download+clip SRTM DEM for bbox using pure Python download + rasterio merge/clip.

    Robust:
    - timeout per attempt
    - retry
    - fallback SRTM1 -> SRTM3
    """
    _validate_area(min_lon, min_lat, max_lon, max_lat)

    out_path = Path(out_tif_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cache_dir = Path(os.environ.get("ELEVATION_DATA") or out_path.parent / ".elevation_cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and out_path.stat().st_size > 0:
        _log(log, f"DEM già presente: {out_path}")
        return out_path

    attempts: list[tuple[str, int]] = []
    for _ in range(retries + 1):
        attempts.append(("SRTM1", timeout_s))
    attempts.append(("SRTM3", max(timeout_s, 180)))

    last_diag = ""
    _log(
        log,
        f"Avvio download DEM bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}] cache={cache_dir}",
    )
    for idx, (product, to_s) in enumerate(attempts, start=1):
        # cleanup broken file
        if out_path.exists() and out_path.stat().st_size == 0:
            out_path.unlink(missing_ok=True)

        attempt_start = time.monotonic()
        try:
            _log(log, f"Tentativo {idx}/{len(attempts)}: {product} (timeout {to_s}s)")
            _download_and_merge(
                min_lon=min_lon,
                min_lat=min_lat,
                max_lon=max_lon,
                max_lat=max_lat,
                out_path=out_path,
                cache_dir=cache_dir,
                product=product,
                timeout_s=to_s,
                log=log,
            )
            if out_path.exists() and out_path.stat().st_size > 0:
                _log(log, f"DEM pronto in {time.monotonic() - attempt_start:.1f}s.")
                return out_path
            raise RuntimeError("DEM generato vuoto.")
        except Exception as exc:  # noqa: BLE001
            last_diag = str(exc)
            _log(log, f"Tentativo {product} fallito: {exc}")
            time.sleep(1.0)

    raise RuntimeError(
        "Download DEM SRTM fallito.\n"
        f"bbox=[{min_lon:.4f},{min_lat:.4f},{max_lon:.4f},{max_lat:.4f}]\n"
        f"output atteso: {out_path}\n"
        f"diagnostica:\n{last_diag}"
    )
