"""Scene authoring: deterministic specs, raster line art, and both renderers."""

from .program import ProgramRenderer
from .spec import GEOMETRY, choose_structure, program_scene, split_words, stroke_scene
from .stroke import Annotation, StrokeRenderer, build_annotation

__all__ = [
    "Annotation",
    "GEOMETRY",
    "ProgramRenderer",
    "StrokeRenderer",
    "build_annotation",
    "choose_structure",
    "program_scene",
    "split_words",
    "stroke_scene",
]
