You are working in a Python + PySide6 project that generates 3D printable terrain maps from GPX.

Primary goal:
- Zero-manual workflow: user selects GPX -> app auto-detects area -> downloads DEM -> generates outputs.
- Fixed model size: ALWAYS 120x120 mm unless explicitly changed by code (no manual user sizing needed).
- Preferred output: a single 3MF for Bambu Studio, containing separate objects (base/track/frame etc).

Rules:
- Do not duplicate methods. Keep one canonical implementation.
- Keep UI responsive: long tasks MUST run in a QThread worker and report progress/log signals.
- Prefer Blender backend for final geometry:
  - inlay groove + separate inlay part
  - recessed frame
  - optional OSM layers (water/green/detail)
- Pure Python pipeline is fallback only when Blender is unavailable.

Architecture rules:
- Keep non-UI logic out of `main_window.py`.
- Add new features via small modules (e.g. export_3mf.py, blender_backend.py helpers).
- Use type hints and dataclasses for configuration.
- Keep file naming and conventions consistent with SPEC.md.

Quality:
- Add minimal validation and clear user-facing errors (invalid GPX, DEM too large, Blender missing).
- Avoid breaking existing behaviour; prefer small safe refactors.
