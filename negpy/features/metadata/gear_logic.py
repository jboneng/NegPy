"""Resolve gear library selections into MetadataConfig updates."""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from negpy.features.metadata.gear_models import GearLibrary
from negpy.features.metadata.models import MetadataConfig


def metadata_from_gear(
    config: MetadataConfig,
    library: GearLibrary,
    *,
    gear_preset_id: str = "",
    camera_id: str = "",
    lens_id: str = "",
    film_stock_id: str = "",
    clear_preset: bool = False,
) -> MetadataConfig:
    """Build updated MetadataConfig from gear library selections."""
    preset_id = "" if clear_preset else (gear_preset_id or config.gear_preset_id)
    cam_id = camera_id if camera_id != "" else config.camera_id
    lens_id_val = lens_id if lens_id != "" else config.lens_id
    film_id = film_stock_id if film_stock_id != "" else config.film_stock_id

    if preset_id:
        preset = library.get_gear_preset(preset_id)
        if preset:
            cam_id = preset.camera_id or cam_id
            lens_id_val = preset.lens_id or lens_id_val
            film_id = preset.film_stock_id or film_id

    camera_make = ""
    camera_model = ""
    lens_make = ""
    lens_model = ""
    focal_length: Optional[float] = None
    max_aperture: Optional[float] = None
    film = config.film
    film_manufacturer = ""
    film_iso: Optional[int] = None
    film_format = config.format
    film_color_type = ""

    if cam_id:
        cam = library.get_camera(cam_id)
        if cam:
            camera_make = cam.make
            camera_model = cam.model

    if lens_id_val:
        lens = library.get_lens(lens_id_val)
        if lens:
            lens_make = lens.make
            lens_model = lens.lens_model or lens.resolved_display_name
            focal_length = lens.focal_length_mm
            max_aperture = lens.max_aperture

    if film_id:
        stock = library.get_film_stock(film_id)
        if stock:
            film = stock.full_film_label
            film_manufacturer = stock.manufacturer
            film_iso = stock.iso
            film_format = stock.format.value
            film_color_type = stock.color_type.value

    return replace(
        config,
        gear_preset_id=preset_id,
        camera_id=cam_id,
        lens_id=lens_id_val,
        film_stock_id=film_id,
        camera_make=camera_make,
        camera_model=camera_model,
        lens_make=lens_make,
        lens_model=lens_model,
        focal_length_mm=focal_length,
        max_aperture=max_aperture,
        film=film,
        film_manufacturer=film_manufacturer,
        film_iso=film_iso,
        format=film_format if film_id else config.format,
        film_color_type=film_color_type,
    )
