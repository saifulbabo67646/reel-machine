"""Turn a script into resolved shots, or refuse with the exact field that is wrong."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from ...core.errors import InvalidInput
from .camera import CAMERA_MOVES
from .geometry import CONTAINERS
from .models import (
    Action, CharacterSpec, Explainer3DInputs, ModelSpec, ResolvedAction, ResolvedCamera, ResolvedShot, ShapeSpec,
)
from .compile import load_look
from .moves import MOVES
from .sets import reel_sets, stage_anchors
from .timing import auto_layout

LOOKS = frozenset(p.stem for p in (Path(__file__).resolve().parent / "looks").glob("*.json"))


def _refuse(path: str, message: str, hint: str = "") -> InvalidInput:
    return InvalidInput(message, hint=hint, details={"path": path})


def _params(model, raw: dict[str, Any], path: str) -> dict[str, Any]:
    try:
        return model.model_validate(raw).model_dump(by_alias=True)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(str(part) for part in error["loc"]) if error["loc"] else ""
        raise _refuse(f"{path}.{field}" if field else path, error["msg"]) from None


def resolve_script(inputs: Explainer3DInputs) -> list[ResolvedShot]:
    if inputs.look not in LOOKS:
        raise _refuse("look", f"unknown look {inputs.look!r}", hint=f"one of {sorted(LOOKS)}")
    if inputs.music and inputs.music != "auto" and not Path(inputs.music).expanduser().is_file():
        raise _refuse("music", f"music file not found: {inputs.music}",
                      hint="give a local audio file, or leave music out")
    reel_set, shot_sets = reel_sets(inputs)  # refuses an unknown preset with its path
    marks = stage_anchors(inputs)
    cinematic = load_look(inputs.look).get("renderer") == "cinematic"
    for key, member in inputs.cast.items():
        if isinstance(member.asset, ModelSpec) and not cinematic:
            raise _refuse(f"cast.{key}.asset", "models are drawn by the cinematic renderer",
                          hint='use look "cinematic", or a shape / a compound of parts')
        if member.at is not None and member.at not in marks:
            raise _refuse(f"cast.{key}.at", f"unknown anchor {member.at!r}", hint=f"one of {sorted(marks)}")

    def is_container(ref: str) -> bool:
        asset = inputs.cast[ref].asset
        return asset.shape in CONTAINERS if isinstance(asset, ShapeSpec) else asset.container

    def check_ref(ref: Any, path: str, *, container: bool = False) -> None:
        if ref is None:
            return
        if container:
            if ref not in inputs.cast:
                raise _refuse(path, f"{ref!r} is not a cast member")
            if not is_container(ref):
                raise _refuse(path, f"{ref!r} is not a container", hint=f"containers: {sorted(CONTAINERS)}")
        elif ref not in inputs.cast and ref not in marks:
            raise _refuse(path, f"{ref!r} is neither a cast member nor an anchor")

    resolved: list[ResolvedShot] = []
    for index, shot in enumerate(inputs.shot_list()):
        base = f"shots[{index}]"
        if shot.actions == "auto":
            for j, action in enumerate(shot.auto):
                if action.at is not None:
                    raise _refuse(f"{base}.auto[{j}].at", "auto actions are laid out for you; drop `at`")
            raw_actions, key = list(shot.auto), "auto"
        else:
            raw_actions, key = list(shot.actions), "actions"

        missing = [j for j, action in enumerate(raw_actions) if action.at is None]
        layout = dict(zip(missing, auto_layout(len(missing))))
        actions = [
            _resolve_action(action, j, f"{base}.{key}[{j}]", layout.get(j), inputs, check_ref, is_container)
            for j, action in enumerate(raw_actions)
        ]
        for j, later in enumerate(actions):
            for earlier in actions[:j]:
                overlaps = later.at[0] < earlier.at[1] and earlier.at[0] < later.at[1]
                if later.actor and later.actor == earlier.actor and overlaps:
                    raise _refuse(
                        f"{base}.{key}[{j}].at",
                        f"{later.actor!r} is already moving in action {earlier.index} at this time",
                        hint="one move per actor at a time; shift `at` so they do not overlap",
                    )

        camera = shot.camera
        if camera is None:
            cam = ResolvedCamera(move="static", target="stage.center", continues=index > 0)
        else:
            if camera.move not in CAMERA_MOVES:
                raise _refuse(f"{base}.camera.move", f"unknown camera move {camera.move!r}",
                              hint=f"one of {sorted(CAMERA_MOVES)}")
            if camera.continue_ and index == 0:
                raise _refuse(f"{base}.camera.continue", "the first shot has no camera to continue")
            target = camera.target or "stage.center"
            check_ref(target, f"{base}.camera.target")
            params = _params(CAMERA_MOVES[camera.move].params, camera.params(), f"{base}.camera")
            cam = ResolvedCamera(move=camera.move, target=target, params=params, continues=camera.continue_)

        if not actions and cam.move == "static":
            raise _refuse(base, "this shot does not move", hint="give it an action or a camera move")
        resolved.append(ResolvedShot(
            id=shot.id, index=index, narration=shot.narration, label=shot.label, actions=actions, camera=cam,
            duration_ms=int(shot.duration * 1000) if shot.duration is not None else None, card=shot.card,
            environment=shot_sets[index].name, set_key=shot_sets[index].key))
    return resolved


def _resolve_action(
    action: Action, j: int, path: str, laid_out, inputs: Explainer3DInputs,
    check_ref: Callable[..., None], is_container: Callable[[str], bool],
) -> ResolvedAction:
    move = MOVES.get(action.move)
    if move is None:
        raise _refuse(f"{path}.move", f"unknown move {action.move!r}", hint=f"one of {sorted(MOVES)}")
    if move.actor == "required":
        if action.actor is None:
            raise _refuse(f"{path}.actor", f"{move.name} needs an actor")
        if action.actor not in inputs.cast:
            raise _refuse(f"{path}.actor", f"{action.actor!r} is not a cast member")
        if move.actor_container and not is_container(action.actor):
            raise _refuse(f"{path}.actor", f"{move.name} needs a container actor")
        if move.character and not isinstance(inputs.cast[action.actor].asset, CharacterSpec):
            raise _refuse(f"{path}.actor", f"{move.name} needs a character",
                          hint='cast a person: {"asset": {"character": "quaternius:female"}}')
    elif action.actor is not None:
        raise _refuse(f"{path}.actor", f"{move.name} takes no actor")
    params = _params(move.params, action.params(), path)
    for name in move.refs:
        check_ref(params.get(name), f"{path}.{name}", container=name in move.container_refs)
    at = action.at if action.at is not None else laid_out
    return ResolvedAction(move=move.name, actor=action.actor, params=params, at=tuple(at), index=j)
