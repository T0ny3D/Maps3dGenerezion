# maps3d_app/core/settings.py
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

APP_NAME = "Maps3DGen"

def _config_dir() -> Path:
    # Windows: %APPDATA%\Maps3DGen
    base = os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d

def config_path() -> Path:
    return _config_dir() / "config.json"

@dataclass
class AppConfig:
    dem_provider: str = "auto"   # auto | srtm | opentopo
    opentopo_api_key: str = ""   # vuota -> disabilitato
    opentopo_dataset: str = "SRTMGL1"  # es: SRTMGL1, SRTMGL3, COP90, ALOS...

def load_config() -> AppConfig:
    p = config_path()
    if not p.exists():
        return AppConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cfg = AppConfig()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg
    except Exception:
        return AppConfig()

def save_config(cfg: AppConfig) -> None:
    p = config_path()
    p.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
