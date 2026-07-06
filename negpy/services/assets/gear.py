"""JSON-backed gear library (cameras, lenses, film stocks, gear presets)."""

from __future__ import annotations

import json
import os
import shutil
from typing import TypeVar

from negpy.features.metadata.gear_models import (
    Camera,
    FilmStock,
    GearLibrary,
    GearPreset,
    Lens,
)
from negpy.kernel.system.config import APP_CONFIG
from negpy.kernel.system.paths import get_resource_path

T = TypeVar("T")

_CAMERAS_FILE = "cameras.json"
_LENSES_FILE = "lenses.json"
_FILM_STOCKS_FILE = "film_stocks.json"
_GEAR_PRESETS_FILE = "gear_presets.json"


class GearProfiles:
    """
    JSON I/O for analog gear libraries under APP_CONFIG.gear_dir.
    Disk I/O on dropdown/dialog open and on save — never per render.
    """

    @staticmethod
    def _gear_dir() -> str:
        path = APP_CONFIG.gear_dir
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _read_list(path: str, factory: type[T]) -> list[T]:
        if not os.path.isfile(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                return []
            return [factory.from_dict(item) for item in raw if isinstance(item, dict)]  # type: ignore[attr-defined]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []

    @staticmethod
    def _write_list(path: str, items: list) -> None:
        tmp = path + ".tmp"
        data = [item.to_dict() for item in items]
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)

    @staticmethod
    def load_library() -> GearLibrary:
        base = GearProfiles._gear_dir()
        return GearLibrary(
            cameras=GearProfiles._read_list(os.path.join(base, _CAMERAS_FILE), Camera),
            lenses=GearProfiles._read_list(os.path.join(base, _LENSES_FILE), Lens),
            film_stocks=GearProfiles._read_list(os.path.join(base, _FILM_STOCKS_FILE), FilmStock),
            gear_presets=GearProfiles._read_list(os.path.join(base, _GEAR_PRESETS_FILE), GearPreset),
        )

    @staticmethod
    def save_cameras(cameras: list[Camera]) -> None:
        GearProfiles._write_list(os.path.join(GearProfiles._gear_dir(), _CAMERAS_FILE), cameras)

    @staticmethod
    def save_lenses(lenses: list[Lens]) -> None:
        GearProfiles._write_list(os.path.join(GearProfiles._gear_dir(), _LENSES_FILE), lenses)

    @staticmethod
    def save_film_stocks(film_stocks: list[FilmStock]) -> None:
        GearProfiles._write_list(os.path.join(GearProfiles._gear_dir(), _FILM_STOCKS_FILE), film_stocks)

    @staticmethod
    def save_gear_presets(presets: list[GearPreset]) -> None:
        GearProfiles._write_list(os.path.join(GearProfiles._gear_dir(), _GEAR_PRESETS_FILE), presets)

    @staticmethod
    def save_library(library: GearLibrary) -> None:
        GearProfiles.save_cameras(library.cameras)
        GearProfiles.save_lenses(library.lenses)
        GearProfiles.save_film_stocks(library.film_stocks)
        GearProfiles.save_gear_presets(library.gear_presets)

    @staticmethod
    def seed_example() -> None:
        """Copy bundled starter gear JSON into the user folder when missing."""
        gear_dir = APP_CONFIG.gear_dir
        bundled_dir = get_resource_path("gear")
        try:
            os.makedirs(gear_dir, exist_ok=True)
            if not os.path.isdir(bundled_dir):
                return
            for fname in (_CAMERAS_FILE, _LENSES_FILE, _FILM_STOCKS_FILE, _GEAR_PRESETS_FILE):
                dest = os.path.join(gear_dir, fname)
                if os.path.exists(dest):
                    continue
                src = os.path.join(bundled_dir, fname)
                if os.path.isfile(src):
                    shutil.copyfile(src, dest)
        except OSError:
            pass
