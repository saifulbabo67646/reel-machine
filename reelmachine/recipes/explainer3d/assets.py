"""CC0 assets for cinematic reels: models, sky images and textures.

Everything is fetched once into a shared cache (`<cache>/explainer3d/assets`), never at
render time: shot projects hard-link the files. Models are measured here from their glTF
bounds, so Python knows the size of every actor exactly as the runtime will draw it.

Sources: Poly Haven (models, HDRIs, textures — CC0) and the Khronos glTF sample assets
(the BarramundiFish — CC0).
"""

from __future__ import annotations

import json
import math
import re
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ...core.assets import Licence, Provenance
from ...core.errors import InvalidInput, ProviderUnavailable
from .models import ModelSpec

POLYHAVEN_API = "https://api.polyhaven.com"
USER_AGENT = "reel-machine/explainer3d (+https://github.com/)"
CATALOGUE_TTL_S = 7 * 24 * 3600
DEFAULT_SIZE = 0.3

POLYHAVEN_LICENCE = Licence(name="CC0-1.0", url="https://polyhaven.com/license", attribution="Poly Haven")
KHRONOS_LICENCE = Licence(name="CC0-1.0", url="https://creativecommons.org/publicdomain/zero/1.0/",
                          attribution="Khronos glTF Sample Assets")
THREE_LICENCE = Licence(name="MIT", url="https://github.com/mrdoob/three.js/blob/r181/LICENSE",
                        attribution="three.js authors")

KHRONOS = {
    "BarramundiFish": {
        "url": "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/BarramundiFish/glTF-Binary/BarramundiFish.glb",
        "name": "Barramundi fish (photoreal, textured)",
        "tags": ["fish", "carp", "bass", "animal", "water", "underwater", "leap"],
    },
}
WATER_NORMALS = "https://raw.githubusercontent.com/mrdoob/three.js/r181/examples/textures/waternormals.jpg"


def http_client() -> httpx.Client:
    return httpx.Client(timeout=120, follow_redirects=True)


def _get(client: httpx.Client, url: str, **kw: Any) -> httpx.Response:
    return client.get(url, headers={"User-Agent": USER_AGENT}, **kw)


# ---------------------------------------------------------------- glTF bounds
def read_gltf_json(path: Path) -> dict[str, Any]:
    data = Path(path).read_bytes()
    if data[:4] == b"glTF":
        length, kind = struct.unpack("<II", data[12:20])
        if kind != 0x4E4F534A:
            raise ValueError("GLB without a JSON chunk")
        return json.loads(data[20:20 + length])
    return json.loads(data)


def _mat_mul(a: list[float], b: list[float]) -> list[float]:  # column-major 4x4
    return [sum(a[r + 4 * k] * b[k + 4 * c] for k in range(4)) for c in range(4) for r in range(4)]


def _node_matrix(node: dict[str, Any]) -> list[float]:
    if "matrix" in node:
        return [float(v) for v in node["matrix"]]
    tx, ty, tz = node.get("translation", [0, 0, 0])
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = node.get("scale", [1, 1, 1])
    r = [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
         2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
         2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
         0, 0, 0, 1]
    for i in range(3):
        r[i] *= sx
        r[4 + i] *= sy
        r[8 + i] *= sz
    r[12], r[13], r[14] = tx, ty, tz
    return r


def gltf_bounds(doc: dict[str, Any]) -> tuple[list[float], list[float]]:
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    nodes, meshes, accessors = doc.get("nodes", []), doc.get("meshes", []), doc.get("accessors", [])

    def visit(index: int, parent: list[float]) -> None:
        node = nodes[index]
        m = _mat_mul(parent, _node_matrix(node))
        if "mesh" in node:
            for prim in meshes[node["mesh"]].get("primitives", []):
                acc = accessors[prim["attributes"]["POSITION"]]
                a, b = acc["min"], acc["max"]
                for cx in (a[0], b[0]):
                    for cy in (a[1], b[1]):
                        for cz in (a[2], b[2]):
                            p = [m[0] * cx + m[4] * cy + m[8] * cz + m[12],
                                 m[1] * cx + m[5] * cy + m[9] * cz + m[13],
                                 m[2] * cx + m[6] * cy + m[10] * cz + m[14]]
                            for i in range(3):
                                lo[i], hi[i] = min(lo[i], p[i]), max(hi[i], p[i])
        for child in node.get("children", []):
            visit(child, m)

    identity = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    scenes = doc.get("scenes") or [{"nodes": list(range(len(nodes)))}]
    for root in scenes[doc.get("scene", 0)].get("nodes", []):
        visit(root, identity)
    if lo[0] == math.inf:
        raise ValueError("model has no geometry")
    return lo, hi


def model_dims(spec: ModelSpec, lo: list[float], hi: list[float]) -> tuple[float, float, float]:
    """The actor's size as drawn: scaled so its height (or longest side) is as asked."""
    size = [hi[i] - lo[i] for i in range(3)]
    if spec.height is not None:
        k = spec.height / max(size[1], 1e-6)
    else:
        k = (spec.size or DEFAULT_SIZE) / max(max(size), 1e-6)
    return (round(size[0] * k, 4), round(size[1] * k, 4), round(size[2] * k, 4))


# ---------------------------------------------------------------- fetching
@dataclass(frozen=True)
class ModelFile:
    source: str
    root: Path
    main: str  # relative to root
    bounds: tuple[list[float], list[float]]
    licence: Licence
    provenance: Provenance
    files: tuple[str, ...] = field(default_factory=tuple)


def _store(root: Path, write: Any) -> None:
    """Fill `root` atomically: concurrent jobs never see half a download."""
    root.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".partial-", dir=root.parent))
    try:
        write(tmp)
        if root.exists():
            return
        tmp.rename(root)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _files(root: Path) -> tuple[str, ...]:
    return tuple(sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != "meta.json"))


def _refuse(source: str, path: str, why: str) -> InvalidInput:
    return InvalidInput(f"model {source!r} {why}", hint="search the catalogue with find_models",
                        details={"path": path} if path else {})


def fetch_model(source: str, cache_root: Path, *, path: str = "", res: str = "1k") -> ModelFile:
    provider, _, ident = source.partition(":")
    root = Path(cache_root) / "models" / provider / ident
    meta = root / "meta.json"
    if not meta.is_file():
        with http_client() as client:
            if provider == "polyhaven":
                response = _get(client, f"{POLYHAVEN_API}/files/{ident}")
                if response.status_code == 404:
                    raise _refuse(source, path, "is not in the Poly Haven catalogue")
                response.raise_for_status()
                entry = (response.json().get("gltf") or {}).get(res, {}).get("gltf")
                if not entry:
                    raise _refuse(source, path, "has no glTF download")

                def write(tmp: Path) -> None:
                    main = Path(entry["url"]).name
                    (tmp / main).write_bytes(_get(client, entry["url"]).raise_for_status().content)
                    for rel, inc in (entry.get("include") or {}).items():
                        target = tmp / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(_get(client, inc["url"]).raise_for_status().content)
                    bounds = gltf_bounds(read_gltf_json(tmp / main))
                    (tmp / "meta.json").write_text(json.dumps({"main": main, "bounds": bounds, "url": entry["url"]}))
            elif provider == "khronos":
                item = KHRONOS.get(ident)
                if item is None:
                    raise _refuse(source, path, "is not a Khronos sample model this recipe knows")

                def write(tmp: Path) -> None:
                    main = Path(item["url"]).name
                    (tmp / main).write_bytes(_get(client, item["url"]).raise_for_status().content)
                    bounds = gltf_bounds(read_gltf_json(tmp / main))
                    (tmp / "meta.json").write_text(json.dumps({"main": main, "bounds": bounds, "url": item["url"]}))
            else:
                raise _refuse(source, path, "has an unknown provider")
            try:
                _store(root, write)
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"could not download {source}", hint=str(exc),
                                          details={"path": path} if path else {}) from exc
    data = json.loads(meta.read_text())
    licence = POLYHAVEN_LICENCE if provider == "polyhaven" else KHRONOS_LICENCE
    return ModelFile(source=source, root=root, main=data["main"], bounds=(data["bounds"][0], data["bounds"][1]),
                     licence=licence, provenance=Provenance(provider=provider, source=data["url"]), files=_files(root))


def fetch_url(url: str, dest: Path) -> Path:
    dest = Path(dest)
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with http_client() as client:
        data = _get(client, url).raise_for_status().content
    tmp = dest.with_name(f".{dest.name}.partial")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def fetch_hdri(ident: str, cache_root: Path, res: str = "4k") -> Path:
    dest = Path(cache_root) / "hdri" / f"{ident}_{res}.hdr"
    if dest.is_file():
        return dest
    with http_client() as client:
        files = _get(client, f"{POLYHAVEN_API}/files/{ident}").raise_for_status().json()
    return fetch_url(files["hdri"][res]["hdr"]["url"], dest)


_MAPS = {"Diffuse": "diff", "nor_gl": "nor", "Rough": "rough"}


def fetch_texture(ident: str, cache_root: Path, res: str = "2k") -> dict[str, Path]:
    folder = Path(cache_root) / "textures" / ident / res
    out = {name: folder / f"{name}.jpg" for name in _MAPS.values()}
    if all(p.is_file() for p in out.values()):
        return out
    with http_client() as client:
        files = _get(client, f"{POLYHAVEN_API}/files/{ident}").raise_for_status().json()
    for key, name in _MAPS.items():
        fetch_url(files[key][res]["jpg"]["url"], out[name])
    return out


# ---------------------------------------------------------------- the catalogue
def _catalogue(cache_root: Path) -> dict[str, Any]:
    path = Path(cache_root) / "polyhaven-models.json"
    if path.is_file() and time.time() - path.stat().st_mtime < CATALOGUE_TTL_S:
        return json.loads(path.read_text())
    with http_client() as client:
        data = _get(client, f"{POLYHAVEN_API}/assets", params={"t": "models"}).raise_for_status().json()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def find_models(query: str, cache_root: Path, *, limit: int = 12) -> list[dict[str, Any]]:
    """CC0 models whose id, name, tags or categories match the query words."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    rows = [(f"khronos:{k}", v["name"], v["tags"], ["animals"]) for k, v in KHRONOS.items()]
    rows += [(f"polyhaven:{k}", v.get("name", k), v.get("tags", []), v.get("categories", []))
             for k, v in _catalogue(cache_root).items()]
    scored = []
    for source, name, tags, cats in rows:
        hay_id, hay_name = source.lower(), name.lower()
        hay_tags = " ".join(tags + cats).lower()
        score = sum(3 * (w in hay_id) + 2 * (w in hay_name) + (w in hay_tags) for w in words)
        if score:
            scored.append((score, source, name, tags))
    scored.sort(key=lambda r: (-r[0], r[1]))
    return [{"source": s, "name": n, "tags": t[:8], "licence": "CC0-1.0"} for _, s, n, t in scored[:limit]]


# ---------------------------------------------------------------- the cast
def resolve_cast(inputs: Any, cache_root: Path) -> dict[str, Any]:
    """Every cast member as drawn: shapes from their numbers, models fetched and measured."""
    from .geometry import dims as shape_dims
    from .models import ResolvedModel, ShapeSpec

    generated = Licence(name="CC0-1.0", url="https://creativecommons.org/publicdomain/zero/1.0/",
                        attribution="generated by reel-machine")
    out: dict[str, Any] = {}
    for key, member in inputs.cast.items():
        asset = member.asset
        if isinstance(asset, ShapeSpec):
            out[key] = ResolvedModel(kind="shape", shape=asset, dims=shape_dims(asset), licence=generated,
                                     provenance=Provenance(provider="shape", source=asset.shape))
            continue
        found = fetch_model(asset.model, Path(cache_root), path=f"cast.{key}.asset.model")
        lo, hi = found.bounds
        out[key] = ResolvedModel(kind="model", model=asset, root=str(found.root), main=found.main,
                                 files=list(found.files), bounds=(lo, hi), dims=model_dims(asset, lo, hi),
                                 licence=found.licence, provenance=found.provenance)
    return out


# ---------------------------------------------------------------- cinematic sets
# where the dressing sits around the stone slab (x right, z toward the camera; water y = 0)
ENVIRONMENTS: dict[str, dict[str, Any]] = {
    "lake": {
        "hdri": "small_harbour_sunset",
        "textures": {"rock": "rock_boulder_dry"},
        "water_normals": True,
        "dressing": [
            {"source": "polyhaven:rock_moss_set_01", "size": 1.5, "at": [-1.1, -0.3, 0.1], "yaw": 0.4},
            {"source": "polyhaven:rock_moss_set_01", "size": 1.2, "at": [1.15, -0.32, -0.25], "yaw": 2.1},
            {"source": "polyhaven:grass_medium_01", "height": 0.5, "at": [-0.8, -0.02, 0.55], "yaw": 0.0},
            {"source": "polyhaven:grass_medium_01", "height": 0.45, "at": [0.85, -0.02, 0.5], "yaw": 1.5},
            {"source": "polyhaven:shrub_01", "height": 0.9, "at": [-1.55, -0.05, 0.3], "yaw": 0.3},
        ],
    },
    "underwater": {
        "hdri": "small_harbour_sunset",  # light only: the underwater background is fog
        "textures": {"sand": "coast_sand_01"},
        "water_normals": True,
        "dressing": [],
    },
}


@dataclass(frozen=True)
class Dressing:
    model: ModelFile
    dims: tuple[float, float, float]
    at: list[float]
    yaw: float


@dataclass(frozen=True)
class EnvPack:
    environment: str
    files: dict[str, Path]  # published path inside the shot project's assets/env/<environment>/
    dressing: list[Dressing]
    licences: list[Licence]


def environment_packs(environments: list[str], cache_root: Path) -> dict[str, EnvPack]:
    cache_root = Path(cache_root)
    packs: dict[str, EnvPack] = {}
    for env in dict.fromkeys(environments):
        spec = ENVIRONMENTS.get(env)
        if spec is None:
            continue
        files: dict[str, Path] = {}
        licences: list[Licence] = []
        if spec.get("hdri"):
            files["sky.hdr"] = fetch_hdri(spec["hdri"], cache_root)
            licences.append(POLYHAVEN_LICENCE)
        for name, ident in spec.get("textures", {}).items():
            for kind, path in fetch_texture(ident, cache_root).items():
                files[f"{name}/{kind}.jpg"] = path
            licences.append(POLYHAVEN_LICENCE)
        if spec.get("water_normals"):
            files["waternormals.jpg"] = fetch_url(WATER_NORMALS, cache_root / "three" / "waternormals.jpg")
            licences.append(THREE_LICENCE)
        dressing = []
        for item in spec.get("dressing", []):
            found = fetch_model(item["source"], cache_root)
            spec_model = ModelSpec(model=item["source"], height=item.get("height"), size=item.get("size"))
            dressing.append(Dressing(model=found, dims=model_dims(spec_model, *found.bounds),
                                     at=list(item["at"]), yaw=float(item["yaw"])))
            licences.append(found.licence)
        packs[env] = EnvPack(environment=env, files=files, dressing=dressing, licences=licences)
    return packs
