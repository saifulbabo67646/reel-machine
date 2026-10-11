"""Characters: stylised CC0 humans that walk, talk, sit and handle props.

Bodies come from Quaternius's Universal Base Characters and motion from his Universal
Animation Library (both CC0, one 65-joint rig, so every clip plays on every body). They are
free downloads on itch.io: `fetch_packs` walks the same "download for free" flow a browser
does, and `import_packs` installs zips a person downloaded by hand when that flow changes.
Only what renders is kept — the glTF bodies, their textures and one animation file.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from ...core.assets import Licence
from ...core.errors import ProviderUnavailable

LICENCE = Licence(name="CC0-1.0", url="https://creativecommons.org/publicdomain/zero/1.0/", attribution="Quaternius")
ITCH = {
    "base": "https://quaternius.itch.io/universal-base-characters",
    "animations": "https://quaternius.itch.io/universal-animation-library",
}
BODY_DIR = "Base Characters/Godot - UE/"
HAIR_DIR = "Hairstyles/Rigged to Head Bone/glTF (Godot -Unreal)/"
HAIR_TEXTURES = "Hairstyles/Textures/"
HAIR = {"long": "Hair_Long.gltf", "buns": "Hair_Buns.gltf", "parted": "Hair_SimpleParted.gltf",
        "buzzed": "Hair_Buzzed.gltf", "buzzed_female": "Hair_BuzzedFemale.gltf", "none": ""}
BEARD = "Hair_Beard.gltf"

# The base bodies ship undressed, meant for outfit packs. We dress them by bone: every vertex
# takes the region of the bone that moves it most, and each region gets a colour.
BONE_REGIONS = {
    "skin": ("Head", "neck_01", "hand_", "index_", "middle_", "ring_", "pinky_", "thumb_"),
    "sleeve": ("lowerarm_",),
    "top": ("spine_", "clavicle_", "upperarm_"),
    "bottom": ("root", "pelvis", "thigh_", "calf_"),
    "shoes": ("foot_", "ball_"),
}


def region_of(bone: str) -> str:
    for region, prefixes in BONE_REGIONS.items():
        if any(bone.startswith(p) for p in prefixes):
            return region
    return "top"
ANIMATION_FILE = "UAL1_Standard.glb"

# source -> (folder in the install, file)
CHARACTERS: dict[str, tuple[str, str]] = {
    "quaternius:male": ("male", "Superhero_Male_FullBody.gltf"),
    "quaternius:female": ("female", "Superhero_Female_FullBody.gltf"),
    "mannequin": ("", "animations.glb"),  # the grey rig the animation library ships with
}

# friendly name -> clip in the animation library
CLIPS: dict[str, str] = {
    "idle": "Idle_Loop", "talk": "Idle_Talking_Loop", "walk": "Walk_Loop", "walk_formal": "Walk_Formal_Loop",
    "jog": "Jog_Fwd_Loop", "sprint": "Sprint_Loop", "crouch": "Crouch_Idle_Loop", "crouch_walk": "Crouch_Fwd_Loop",
    "dance": "Dance_Loop", "jump": "Jump_Start", "land": "Jump_Land", "pick_up": "PickUp_Table",
    "interact": "Interact", "push": "Push_Loop", "fix": "Fixing_Kneeling", "sit_down": "Sitting_Enter",
    "sit": "Sitting_Idle_Loop", "sit_talk": "Sitting_Talking_Loop", "stand_up": "Sitting_Exit",
    "punch": "Punch_Cross", "jab": "Punch_Jab", "hit": "Hit_Chest", "roll": "Roll", "swim": "Swim_Fwd_Loop",
    "drive": "Driving_Loop", "fall": "Death01", "cast": "Spell_Simple_Shoot", "hold_torch": "Idle_Torch_Loop",
}
# metres per second each locomotion clip covers at its natural speed (feet match the ground)
GAIT_SPEED = {"walk": 1.35, "jog": 3.0, "sprint": 5.5}


def install_root(cache_root: Path) -> Path:
    return Path(cache_root) / "characters" / "quaternius"


def installed(cache_root: Path) -> bool:
    root = install_root(cache_root)
    return (root / "animations.glb").is_file() and all((root / d / f).is_file() for d, f in CHARACTERS.values() if d)


def _fix_image_names(gltf: Path) -> None:
    """The pack's glTF names a few textures `X_png.png` but ships them as `X.png`."""
    doc = json.loads(gltf.read_text(encoding="utf-8"))
    changed = False
    for image in doc.get("images", []):
        uri = image.get("uri", "")
        if uri and not (gltf.parent / uri).is_file():
            alt = re.sub(r"_png\.png$", ".png", uri)
            if (gltf.parent / alt).is_file():
                image["uri"] = alt
                changed = True
    if changed:
        gltf.write_text(json.dumps(doc), encoding="utf-8")


def import_packs(base_zip: Path, animations_zip: Path, cache_root: Path) -> Path:
    root = install_root(cache_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".partial-", dir=root.parent))
    try:
        with zipfile.ZipFile(base_zip) as z:
            names = [n for n in z.namelist() if BODY_DIR in n and not n.endswith("/")]
            shared = [n for n in names if not re.search(r"(Male|Female)", Path(n).name)]
            for folder, file in (v for v in CHARACTERS.values() if v[0]):
                who = file.split("_")[1]  # Male / Female
                dest = tmp / folder
                dest.mkdir(parents=True, exist_ok=True)
                for n in names:
                    if who in Path(n).name:  # case-sensitive: "Female" does not contain "Male"
                        (dest / Path(n).name).write_bytes(z.read(n))
                for n in shared:
                    (dest / Path(n).name).write_bytes(z.read(n))
                if not (dest / file).is_file():
                    raise ProviderUnavailable(f"the character pack has no {file}", hint="is this Universal Base Characters?")
                _fix_image_names(dest / file)
            hair = tmp / "hair"
            hair.mkdir()
            for n in z.namelist():
                if (HAIR_DIR in n or HAIR_TEXTURES in n) and not n.endswith("/"):
                    (hair / Path(n).name).write_bytes(z.read(n))
            for gltf in hair.glob("*.gltf"):
                _fix_image_names(gltf)
            _keep_licences(z, tmp, "base")
        with zipfile.ZipFile(animations_zip) as z:
            glb = next((n for n in z.namelist() if n.endswith(ANIMATION_FILE)), None)
            if glb is None:
                raise ProviderUnavailable(f"the animation pack has no {ANIMATION_FILE}",
                                          hint="is this Universal Animation Library?")
            (tmp / "animations.glb").write_bytes(z.read(glb))
            _keep_licences(z, tmp, "animations")
        if root.exists():
            shutil.rmtree(root)
        tmp.rename(root)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return root


def _keep_licences(z: zipfile.ZipFile, tmp: Path, pack: str) -> None:
    """The pack's own licence words travel with the install (the packs say CC0)."""
    (tmp / "licences").mkdir(exist_ok=True)
    for n in z.namelist():
        if Path(n).name.lower().startswith("licen"):
            (tmp / "licences" / f"{pack}-{Path(n).name}").write_bytes(z.read(n))


def _itch_download(page: str, dest: Path) -> Path:
    """itch.io's free-download flow: the page's CSRF token buys a download page whose
    single upload hands back a signed file URL."""
    import httpx

    with httpx.Client(timeout=300, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        html = client.get(page).raise_for_status().text
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if not token:
            raise ProviderUnavailable("itch.io page changed: no download token", hint=f"download {page} by hand and import it")
        download = client.post(f"{page}/download_url", data={"csrf_token": token.group(1)}).raise_for_status().json()["url"]
        listing = client.get(download).raise_for_status().text
        upload = re.search(r'data-upload_id="(\d+)"', listing)
        if not upload:
            raise ProviderUnavailable("itch.io download page changed", hint=f"download {page} by hand and import it")
        file_url = client.post(f"{page}/file/{upload.group(1)}", params={"source": "game_download"},
                               data={"csrf_token": token.group(1)}).raise_for_status().json()["url"]
        with client.stream("GET", file_url) as response, open(dest, "wb") as out:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                out.write(chunk)
    return dest


def fetch_packs(cache_root: Path) -> Path:
    with tempfile.TemporaryDirectory(dir=Path(cache_root)) as tmp:
        base = _itch_download(ITCH["base"], Path(tmp) / "base.zip")
        animations = _itch_download(ITCH["animations"], Path(tmp) / "animations.zip")
        return import_packs(base, animations, cache_root)


def ensure_installed(cache_root: Path) -> Path:
    Path(cache_root).mkdir(parents=True, exist_ok=True)
    if not installed(cache_root):
        try:
            fetch_packs(cache_root)
        except Exception as exc:  # noqa: BLE001 - any failure leaves the manual route
            if isinstance(exc, ProviderUnavailable):
                raise
            raise ProviderUnavailable(
                "could not download the character packs", hint=f"download {ITCH['base']} and {ITCH['animations']}, then "
                "run `reel explainer3d import-characters <base.zip> <animations.zip>`", details={"error": str(exc)}) from exc
    return install_root(cache_root)


def character_file(source: str, root: Path) -> Path:
    """The body's glTF inside an install `root` (see ensure_installed)."""
    folder, file = CHARACTERS[source]
    return Path(root) / folder / file if folder else Path(root) / file


def character_files(source: str) -> list[str]:
    """Paths (inside the install) a shot project needs for this character."""
    folder, _ = CHARACTERS[source]
    return [folder] if folder else []


def describe() -> dict[str, Any]:
    return {"characters": sorted(CHARACTERS), "clips": sorted(CLIPS), "gaits": GAIT_SPEED}
