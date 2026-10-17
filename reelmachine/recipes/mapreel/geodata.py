# reelmachine/recipes/mapreel/geodata.py
"""Map data, fetched once into the shared cache: Natural Earth borders, NASA Blue Marble."""

from __future__ import annotations

from pathlib import Path

import httpx

from ...config import Settings
from ...core.assets import Licence
from ...core.errors import ProviderUnavailable

NE_VERSION = "v5.1.2"
_NE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_VERSION}/geojson"
_BM = "https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73909"
USER_AGENT = "reel-machine/mapreel"

#: key -> (file name in the cache, URL)
FILES: dict[str, tuple[str, str]] = {
    "countries": ("ne_10m_admin_0_countries.geojson", f"{_NE}/ne_10m_admin_0_countries.geojson"),
    "world": ("blue-marble-5400.jpg", f"{_BM}/world.topo.bathy.200412.3x5400x2700.jpg"),
    "detail": ("blue-marble-21600.jpg", f"{_BM}/world.topo.bathy.200412.3x21600x10800.jpg"),
}

NATURAL_EARTH = Licence(name="Public domain", url="https://www.naturalearthdata.com/about/terms-of-use/",
                        attribution=f"Made with Natural Earth ({NE_VERSION})")
BLUE_MARBLE = Licence(name="Public domain", url="https://visibleearth.nasa.gov/image-use-policy",
                      attribution="NASA Earth Observatory, Blue Marble: Next Generation (Reto Stöckli)")
LICENCES: dict[str, Licence] = {"countries": NATURAL_EARTH, "world": BLUE_MARBLE, "detail": BLUE_MARBLE}


def geo_cache(settings: Settings) -> Path:
    """Public data, so shared by every caller."""
    return (settings.shared_cache or settings.cache_dir) / "mapreel" / "geo"


def _download(url: str, dest: Path) -> None:
    with httpx.Client(timeout=300, follow_redirects=True) as client:
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)


def ensure(root: Path, key: str) -> Path:
    name, url = FILES[key]
    path = Path(root) / name
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(name + ".partial")
    try:
        _download(url, partial)
    except (httpx.HTTPError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise ProviderUnavailable(
            f"could not fetch the map data {name!r}: {exc}",
            hint=f"check the network; it is fetched once into {path.parent}",
            details={"url": url},
        ) from exc
    partial.replace(path)
    return path
