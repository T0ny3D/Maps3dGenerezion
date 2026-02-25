You are working in a Python PySide6 project.

Rules:
- Do not duplicate methods. Keep one canonical implementation.
- Keep UI responsive: long tasks must run in QThread worker.
- Prefer Blender backend for final geometry (inlay + frame + OSM layers).
- The app must be zero-manual after GPX selection: auto-bbox, auto-DEM download, default 120x120.
- Add new features via small modules (export_3mf.py etc), avoid bloating main_window.py.
- Use type hints and dataclasses for configuration.
