from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ModelSpace:
    """Canonical XY model space in millimeters."""

    src_min_x: float
    src_min_y: float
    src_span_x: float
    src_span_y: float
    model_width_mm: float
    model_height_mm: float

    @classmethod
    def from_source_bounds(
        cls,
        src_min_x: float,
        src_max_x: float,
        src_min_y: float,
        src_max_y: float,
        model_width_mm: float,
        model_height_mm: float,
    ) -> "ModelSpace":
        span_x = max(abs(src_max_x - src_min_x), 1e-6)
        span_y = max(abs(src_max_y - src_min_y), 1e-6)
        return cls(
            src_min_x=min(src_min_x, src_max_x),
            src_min_y=min(src_min_y, src_max_y),
            src_span_x=span_x,
            src_span_y=span_y,
            model_width_mm=model_width_mm,
            model_height_mm=model_height_mm,
        )

    def to_model_xy(self, src_xy: np.ndarray) -> np.ndarray:
        out = np.empty_like(src_xy, dtype=np.float64)
        out[:, 0] = (src_xy[:, 0] - self.src_min_x) / self.src_span_x * self.model_width_mm
        out[:, 1] = (src_xy[:, 1] - self.src_min_y) / self.src_span_y * self.model_height_mm
        return out

    def to_model_x(self, x: np.ndarray) -> np.ndarray:
        return (x - self.src_min_x) / self.src_span_x * self.model_width_mm

    def to_model_y(self, y: np.ndarray) -> np.ndarray:
        return (y - self.src_min_y) / self.src_span_y * self.model_height_mm
