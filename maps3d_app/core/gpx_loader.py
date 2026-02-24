from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def load_gpx_points(gpx_path: str | Path) -> np.ndarray:
    """Return GPX track points as Nx2 array [lon, lat]."""
    tree = ET.parse(gpx_path)
    root = tree.getroot()

    points: list[tuple[float, float]] = []
    for trkpt in root.findall(".//{*}trkpt"):
        lat = trkpt.attrib.get("lat")
        lon = trkpt.attrib.get("lon")
        if lat is None or lon is None:
            continue
        points.append((float(lon), float(lat)))

    if len(points) < 2:
        raise ValueError("Il file GPX deve contenere almeno 2 punti traccia.")

    return np.asarray(points, dtype=np.float64)
