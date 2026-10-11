"""Units, the stage, anchors and shape sizes. Metres, Y up; an object's origin is its
bottom centre. The JS runtime receives every number from here through shot.json, so the
two sides cannot disagree about where something is."""

from __future__ import annotations

from .models import ShapeSpec

Vec3 = tuple[float, float, float]

STAGE_TOP = {"stone-slab": 0.9, "table": 0.75, "none": 0.0}
WIDTH_RATIO = {
    "bowl": 2.2, "cup": 0.8, "pot": 1.3, "jar": 0.7, "bottle": 0.32,
    "ball": 1.0, "box": 1.0, "sack": 0.75, "pile": 2.5, "plinth": 2.0,
}
CONTAINERS = frozenset({"bowl", "cup", "pot", "jar"})
FLOOR = 0.15  # a container's floor, as a fraction of its height
SKY_HEIGHT = 3.0


def dims(spec: ShapeSpec) -> Vec3:
    width = spec.width if spec.width is not None else spec.height * WIDTH_RATIO[spec.shape]
    return (round(width, 4), round(spec.height, 4), round(width, 4))


def anchors(stage: str) -> dict[str, Vec3]:
    top = STAGE_TOP[stage]
    return {
        "stage.center": (0.0, top, 0.0),
        "stage.left": (-0.45, top, 0.0),
        "stage.right": (0.45, top, 0.0),
        # close marks: a vertical frame is narrow (about +-0.3 m wide at 1.5 m)
        "stage.near_left": (-0.24, top, 0.12),
        "stage.near_right": (0.24, top, 0.12),
        "stage.front": (0.0, top, 0.3),  # the slab's near edge, toward the camera
        "stage.back": (0.0, top, -0.28),
        "ground": (0.0, 0.0, 0.9),
        "sky": (0.0, top + SKY_HEIGHT, 0.0),
        # the lake set: water at y = 0, out beyond the slab (the camera sits at +z)
        "lake.near": (0.0, 0.0, -1.6),
        "lake.far": (0.3, 0.0, -3.6),
        "lake.left": (-1.4, 0.0, -2.4),
        "lake.right": (1.4, 0.0, -2.4),
        # the underwater set, below the lake
        "underwater.center": (0.0, -1.2, -3.0),
        "underwater.surface": (0.0, -0.15, -3.0),
        "underwater.floor": (0.0, -2.3, -3.0),
    }


def fill_y(height: float, level: float) -> float:
    """Height of a container's fill surface above the container's origin."""
    return height * (FLOOR + (1.0 - FLOOR) * 0.9 * level)
