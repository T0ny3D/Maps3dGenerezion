from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph.opengl as gl
import trimesh
from PySide6.QtWidgets import QVBoxLayout, QWidget


class Preview3DWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor((18, 18, 20, 255))
        self.view.opts["distance"] = 300

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self._items: list[gl.GLMeshItem] = []
        self._mins: list[np.ndarray] = []
        self._maxs: list[np.ndarray] = []

        grid = gl.GLGridItem()
        grid.setSize(200, 200)
        grid.setSpacing(10, 10)
        self.view.addItem(grid)

        axis = gl.GLAxisItem()
        axis.setSize(50, 50, 50)
        self.view.addItem(axis)

    def clear(self) -> None:
        for item in self._items:
            self.view.removeItem(item)
        self._items.clear()
        self._mins.clear()
        self._maxs.clear()

    def load_stl(self, path: str | Path, color: tuple[float, float, float, float], smooth: bool = True) -> None:
        mesh = trimesh.load(path, force="mesh")
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError(f"File STL non valido: {path}")
        vertices = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces, dtype=np.uint32)
        md = gl.MeshData(vertexes=vertices, faces=faces)
        item = gl.GLMeshItem(meshdata=md, smooth=smooth, shader="shaded", color=color, drawEdges=False)
        self.view.addItem(item)
        self._items.append(item)
        self._mins.append(vertices.min(axis=0))
        self._maxs.append(vertices.max(axis=0))

    def frame_all(self) -> None:
        if not self._mins:
            return
        mins = np.min(np.vstack(self._mins), axis=0)
        maxs = np.max(np.vstack(self._maxs), axis=0)
        center = (mins + maxs) / 2.0
        size = max(float(np.max(maxs - mins)), 1.0)
        self.view.opts["center"] = gl.Vector(center[0], center[1], center[2])
        self.view.opts["distance"] = size * 2.2
        self.view.update()
