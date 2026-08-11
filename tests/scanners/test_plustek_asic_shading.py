# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour ASIC shading: home measure/apply before acquire (SilverFast order)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from negpy.infrastructure.scanners.plustek.device.model_8200i_se import MODEL_8200I_SE
from negpy.infrastructure.scanners.plustek.exceptions import CalibrationError
from negpy.infrastructure.scanners.plustek.scan.calibrate import CalibEntry, Calibrator
from negpy.infrastructure.scanners.plustek.scan.geometry import compute_geometry
from negpy.infrastructure.scanners.plustek.scan.session import ScanSession


def _geometry(dpi: int = 1200):
    return compute_geometry(dpi, model=MODEL_8200I_SE, area=(0.0, 0.0, 1.0, 0.5))


def _blob_entry(geometry, *, blob: bytes = b"\x01\x02\x03\x04") -> CalibEntry:
    return CalibEntry(
        method="transparency",
        resolution=geometry.resolution,
        startx=geometry.startx,
        pixels=geometry.pixels,
        dark=np.zeros((geometry.pixels, 3), dtype=np.uint16),
        white=np.full((geometry.pixels, 3), 65535, dtype=np.uint16),
        asic_shading=True,
        shading_blob=blob,
        afe_offsets=(38, 30, 36),
        afe_gains=(19, 31, 23),
    )


def _asic(*, shading_ready: bool = False, motor: bool = True) -> MagicMock:
    asic = MagicMock()
    asic._initialized = True
    asic._motor_moves_enabled = motor
    asic.asic_shading_ready = shading_ready
    asic.last_color_shading_host_ok = False
    asic.last_host_calib_dark = None
    asic.last_host_calib_white = None
    asic.usb_planar_rgb = False
    asic._reg_cache = {}
    asic.is_at_home = MagicMock(return_value=True)
    asic.home = MagicMock()
    asic.search_afe = MagicMock()
    asic.run_asic_shading = MagicMock(return_value=b"\x00" * 16)
    asic.upload_shading_table = MagicMock()
    asic.apply_frontend = MagicMock()
    asic.last_afe = None
    asic.last_color_shading_reject_reason = None
    return asic


def test_measure_disarms_motor_and_persists_blob(tmp_path: Path):
    asic = _asic(motor=True)
    order: list[str] = []

    def search_afe(**_kw):
        order.append("afe")
        assert asic._motor_moves_enabled is False

    def run_asic_shading(**_kw):
        order.append("shade")
        assert asic._motor_moves_enabled is False
        asic.asic_shading_ready = True
        asic.last_afe = MagicMock(offsets=(38, 30, 36), gains=(19, 31, 23))
        return b"BLOBDATA"

    asic.search_afe.side_effect = search_afe
    asic.run_asic_shading.side_effect = run_asic_shading

    cal = Calibrator(asic, cache_path=tmp_path / "calib.json", model=MODEL_8200I_SE)
    entry = cal.measure_colour_asic_shading(_geometry())

    assert order == ["afe", "shade"]
    assert asic._motor_moves_enabled is True
    assert entry.has_asic_blob
    assert entry.shading_blob == b"BLOBDATA"
    assert cal.prefer_asic_shading is True


def test_apply_cached_blob_skips_strip_acquire(tmp_path: Path):
    asic = _asic(shading_ready=False)
    cal = Calibrator(asic, cache_path=tmp_path / "calib.json", model=MODEL_8200I_SE)
    geometry = _geometry()
    entry = _blob_entry(geometry, blob=b"CACHED")
    cal.cache.upsert(entry)
    cal.cache.save()

    asic.run_asic_shading.side_effect = AssertionError("must not re-measure")
    asic.search_afe.side_effect = AssertionError("must not re-search")

    cal.apply_colour_asic_shading(entry)

    asic.upload_shading_table.assert_called_once_with(b"CACHED")
    asic.apply_frontend.assert_called_once()
    assert asic.asic_shading_ready is True


def test_cache_roundtrip_blob(tmp_path: Path):
    path = tmp_path / "calib.json"
    geometry = _geometry()
    cal = Calibrator(_asic(), cache_path=path, model=MODEL_8200I_SE)
    cal.cache.upsert(_blob_entry(geometry, blob=b"XYZ"))
    cal.cache.save()

    cal2 = Calibrator(_asic(), cache_path=path, model=MODEL_8200I_SE)
    hit = cal2.find_for_scan(method="transparency", geometry=geometry)
    assert hit is not None
    assert hit.has_asic_blob
    assert hit.shading_blob == b"XYZ"


def test_dummy_marker_without_blob_is_cache_miss(tmp_path: Path):
    geometry = _geometry()
    cal = Calibrator(_asic(), cache_path=tmp_path / "calib.json", model=MODEL_8200I_SE)
    cal.cache.upsert(
        CalibEntry(
            method="transparency",
            resolution=geometry.resolution,
            startx=geometry.startx,
            pixels=geometry.pixels,
            dark=np.zeros((geometry.pixels, 3), dtype=np.uint16),
            white=np.full((geometry.pixels, 3), 65535, dtype=np.uint16),
            asic_shading=True,
        )
    )
    assert cal.find_for_scan(method="transparency", geometry=geometry) is None


def test_session_ensures_before_acquire(tmp_path: Path, monkeypatch):
    asic = _asic()
    cal = Calibrator(asic, cache_path=tmp_path / "calib.json", model=MODEL_8200I_SE)
    geometry = _geometry()
    order: list[str] = []

    def fake_ensure(geo):
        order.append("ensure")
        asic.asic_shading_ready = True
        entry = _blob_entry(geo)
        cal._active = entry
        cal.prefer_asic_shading = True
        return entry

    monkeypatch.setattr(cal, "ensure_colour_asic_shading", fake_ensure)
    session = ScanSession(asic, MODEL_8200I_SE, cal)
    monkeypatch.setattr(
        session,
        "acquire_raw",
        lambda *_a, **_k: (order.append("acquire") or b"\x00" * 64),
    )
    monkeypatch.setattr(
        session.pipeline,
        "assemble",
        lambda *_a, **_k: np.zeros((4, geometry.pixels, 3), dtype=np.uint16),
    )
    session.run(resolution=geometry.resolution, mode="color", geometry=geometry)
    assert order == ["ensure", "acquire"]


def test_measure_retries_home_then_raises(tmp_path: Path):
    asic = _asic()
    calls = {"n": 0}

    def shade(**_kw):
        calls["n"] += 1
        asic.asic_shading_ready = False
        asic.last_color_shading_reject_reason = "ASIC shading validation failed"
        return b"bad"

    asic.run_asic_shading.side_effect = shade
    asic.search_afe.side_effect = lambda **_k: None
    cal = Calibrator(asic, cache_path=tmp_path / "calib.json", model=MODEL_8200I_SE)
    with pytest.raises(CalibrationError, match="Retry the scan"):
        cal.measure_colour_asic_shading(_geometry())
    assert calls["n"] == 2
    asic.home.assert_called()


def test_measure_raises_lamp_off_dark_message(tmp_path: Path):
    asic = _asic()

    def shade(**_kw):
        asic.asic_shading_ready = False
        asic.last_color_shading_reject_reason = "lamp-off dark still bright (dark mean 22936 > 3000)"
        return b"bad"

    asic.run_asic_shading.side_effect = shade
    asic.search_afe.side_effect = lambda **_k: None
    cal = Calibrator(asic, cache_path=tmp_path / "calib.json", model=MODEL_8200I_SE)
    with pytest.raises(CalibrationError, match="AFE black level"):
        cal.measure_colour_asic_shading(_geometry())


def test_colour_shading_failure_message_mapping():
    from negpy.infrastructure.scanners.plustek.scan.calibrate import (
        colour_shading_failure_message,
    )

    assert "AFE black level" in colour_shading_failure_message("dark mean 12000 > 3000")
    assert "illumination" in colour_shading_failure_message("ch0 white≈dark (w=100 d=200); lamp-off dark still bright (dark mean 22000)")
    assert "clear home field" in colour_shading_failure_message("white strip looks like film (not clear home field)")
    assert "park at home" in colour_shading_failure_message("carriage not at home")
    assert "power-cycle" in colour_shading_failure_message("ch0 range 10 < 500")


def test_pick_shading_dark_layout_keeps_equal_mean():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        pick_shading_dark_layout,
    )

    # Same total energy, different col0 balance — must not flip for balance alone.
    primary = [(1000, 2000, 3000), (30000, 20000, 10000)]
    alternate = [(7000, 7000, 7000), (15000, 15000, 15000)]
    assert sum(sum(c) for c in primary) == sum(sum(c) for c in alternate)
    chosen, planar = pick_shading_dark_layout(primary, alternate, primary_is_planar=False)
    assert planar is False
    assert chosen[0] == (1000, 2000, 3000)


def test_pick_shading_dark_layout_flips_when_alt_truly_dark():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        pick_shading_dark_layout,
    )

    primary = [(20000, 21000, 19000)] * 4
    alternate = [(800, 900, 850)] * 4
    chosen, planar = pick_shading_dark_layout(primary, alternate, primary_is_planar=False)
    assert planar is True
    assert chosen[0] == (800, 900, 850)


def test_shading_settle_constants_match_silverfast():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        COLOR_SHADING_DARK_MEAN_MAX,
        COLOR_SHADING_DARK_SETTLE_S,
        COLOR_SHADING_WHITE_MEAN_MIN,
        IR_SHADING_DARK_SETTLE_S,
        IR_SHADING_WHITE_MEAN_MIN,
    )

    assert COLOR_SHADING_DARK_SETTLE_S == 0.5
    assert IR_SHADING_DARK_SETTLE_S == 0.5
    assert COLOR_SHADING_DARK_MEAN_MAX == 3000
    assert IR_SHADING_WHITE_MEAN_MIN == 10000
    # Post-unity DVDSET whites read ~50-57k, so the colour floor sits well above IR.
    assert COLOR_SHADING_WHITE_MEAN_MIN == 20000


def test_shading_table_covers_the_full_image_window():
    """The table spans every acquired column — no narrower AHB cap (sessions 03/04)."""
    from negpy.infrastructure.scanners.plustek.scan.bringup import (
        bringup_scan_geometry,
        preview_safe_scan_area,
    )
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        declared_shading_size,
        shading_acquire_width,
        shading_entry_count,
    )
    from negpy.infrastructure.scanners.plustek.scan.geometry import compute_geometry

    for dpi in (1800, 3600):
        area, _ = preview_safe_scan_area(MODEL_8200I_SE, dpi, y1=0.0)
        raw = compute_geometry(dpi, model=MODEL_8200I_SE, area=area)
        raw_n = shading_acquire_width(
            strpixel=raw.pixel_startx,
            endpixel=raw.pixel_endx,
            dpiset=raw.register_dpiset,
            optical_resolution=MODEL_8200I_SE.optical_resolution,
        )
        assert shading_entry_count(declared_shading_size(raw_n)) == raw_n

        geometry, _ = bringup_scan_geometry(MODEL_8200I_SE, dpi, profile="preview_safe")
        bring_n = shading_acquire_width(
            strpixel=geometry.pixel_startx,
            endpixel=geometry.pixel_endx,
            dpiset=geometry.register_dpiset,
            optical_resolution=MODEL_8200I_SE.optical_resolution,
        )
        assert bring_n == raw_n
        assert geometry.pixels == raw.pixels
        assert geometry.pixel_endx == raw.pixel_endx


def test_declared_shading_size_matches_capture_sessions():
    """Declared AHB size = 4 x pairs with two pad pairs per full 512-byte block."""
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        SHADING_WIDTH_BY_DPI,
        declared_shading_size,
        shading_entry_count,
    )

    assert declared_shading_size(1728) == 21064  # session 03, 1200 dpi
    assert declared_shading_size(2478) == 30208  # session 04, 1800 dpi
    assert declared_shading_size(4956) == 60416  # session 06, 3600 dpi
    assert SHADING_WIDTH_BY_DPI[1200] == 1728
    assert SHADING_WIDTH_BY_DPI[1800] == 2478
    for size in (21064, 30208, 60416):
        assert declared_shading_size(shading_entry_count(size)) == size


def test_pack_shading_table_pads_every_512_byte_block():
    """DVDSET skips the last two pairs of each block; data must not fill them."""
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        SHADING_BLOCK_BYTES,
        SHADING_BLOCK_DATA_PAIRS,
        SHADING_GAIN_UNITY,
        declared_shading_size,
        make_unity_white_table,
        unpack_shading_table,
    )

    n = 2478
    dark = [(927, 1039, 1171)] * n
    blob = make_unity_white_table(dark, declared_size=declared_shading_size(n))
    assert len(blob) == 30208

    gains = [int.from_bytes(blob[off + 2 : off + 4], "little") for off in range(0, len(blob), 4)]
    for i, gain in enumerate(gains):
        in_pad = i % (SHADING_BLOCK_BYTES // 4) >= SHADING_BLOCK_DATA_PAIRS
        assert gain == (0 if in_pad else SHADING_GAIN_UNITY), f"pair {i}"
    assert gains.count(SHADING_GAIN_UNITY) == n * 3

    rows = unpack_shading_table(blob)[:n]
    assert len(rows) == n
    assert rows[0] == {"dark": [927, 1039, 1171], "gain": [SHADING_GAIN_UNITY] * 3}
    assert rows[-1]["dark"] == [927, 1039, 1171]


def test_shading_gain_inverts_the_white_profile():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        SHADING_GAIN_MAX,
        SHADING_GAIN_MIN,
        SHADING_GAIN_TARGET,
        SHADING_GAIN_UNITY,
        shading_gain_for_white,
        shading_gains_from_white,
    )

    assert SHADING_GAIN_UNITY == 0x2000
    assert SHADING_GAIN_TARGET == 0xFFFF
    # White already at target needs unity; a dimmer column needs more gain.
    assert shading_gain_for_white(SHADING_GAIN_TARGET) == SHADING_GAIN_UNITY
    assert shading_gain_for_white(SHADING_GAIN_TARGET / 2) == SHADING_GAIN_UNITY * 2
    assert shading_gain_for_white(50000) > shading_gain_for_white(57000)
    # Clamps hold at both extremes.
    assert shading_gain_for_white(1) == SHADING_GAIN_MAX
    assert shading_gain_for_white(1_000_000) == SHADING_GAIN_MIN

    gains = shading_gains_from_white([(50000, 55000, 56000)])
    assert gains[0][0] > gains[0][1] > gains[0][2]


def test_measured_shading_table_stores_gains_not_whites():
    """The gain slot must hold ~1.2x, never the 50k measurement (that inverts it)."""
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        SHADING_GAIN_UNITY,
        build_measured_shading_table,
        declared_shading_size,
        unpack_shading_table,
    )

    n = 256
    dark = [(927, 1039, 1171)] * n
    white = [(49610, 55067, 56009)] * n
    blob = build_measured_shading_table(dark, white, declared_size=declared_shading_size(n))
    rows = unpack_shading_table(blob)[:n]
    assert rows[0]["dark"] == [927, 1039, 1171]
    for gain, expected in zip(rows[0]["gain"], (10822, 9750, 9586), strict=True):
        assert abs(gain - expected) <= 2
        assert 1.05 <= gain / SHADING_GAIN_UNITY <= 2.0
    assert rows[-1]["gain"] == rows[0]["gain"]


def test_session04_capture_gains_match_silverfast():
    """Golden: SF's own coefficients from the white strip it measured."""
    import json

    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        SHADING_GAIN_TARGET,
        SHADING_GAIN_UNITY,
        shading_gain_for_white,
    )

    fixture = json.loads((Path(__file__).parent / "data" / "session04_shading_gain.json").read_text(encoding="utf-8"))
    assert fixture["unity_gain"] == SHADING_GAIN_UNITY
    assert fixture["sf_target"][0] == SHADING_GAIN_TARGET
    for column in fixture["columns"]:
        for channel, target in enumerate(fixture["sf_target"]):
            got = shading_gain_for_white(column["white_unity"][channel], target=target)
            assert abs(got - column["coeff"][channel]) <= 16, (column["x"], channel, got)


def test_validate_color_shading_rejects_film_like_white():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        validate_color_shading_strip,
    )

    dark = [(800, 900, 850)] * 32
    # R≪G/B is the orange-mask tell.
    white = [(20000, 52000, 50000)] * 32
    ok, reason = validate_color_shading_strip(dark, white)
    assert ok is False
    assert "film" in reason


def test_validate_color_shading_accepts_session04_whites():
    """Post-unity DVDSET whites (~50-57k) are the expected reading, not a fault."""
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        validate_color_shading_strip,
    )

    dark = [(927, 1039, 1171)] * 32
    white = [(49610, 55067, 56009)] * 32
    ok, reason = validate_color_shading_strip(dark, white)
    assert ok is True
    assert reason == "ok"


def test_validate_color_shading_rejects_railed_white():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        validate_color_shading_strip,
    )

    dark = [(800, 900, 850)] * 32
    white = [(65535, 65535, 65535)] * 32
    ok, reason = validate_color_shading_strip(dark, white)
    assert ok is False
    assert "rail" in reason


def test_validate_color_shading_rejects_dim_white():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        validate_color_shading_strip,
    )

    dark = [(800, 900, 850)] * 32
    white = [(12000, 11500, 11000)] * 32
    ok, reason = validate_color_shading_strip(dark, white)
    assert ok is False
    assert "white mean" in reason


def test_validate_color_shading_soft_accepts_high_dark():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        validate_color_shading_strip,
    )

    dark = [(5000, 5500, 4800)] * 32
    white = [(50000, 52000, 51000)] * 32
    ok, reason = validate_color_shading_strip(dark, white)
    assert ok is True
    assert "soft dark" in reason


def test_validate_color_shading_gains_band():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        SHADING_GAIN_MAX,
        SHADING_GAIN_UNITY,
        shading_gains_from_white,
        validate_color_shading_gains,
    )

    ok, reason = validate_color_shading_gains(shading_gains_from_white([(49610, 55067, 56009)] * 32))
    assert ok is True
    assert "median gain" in reason

    ok, reason = validate_color_shading_gains([(SHADING_GAIN_UNITY,) * 3] * 32)
    assert ok is False
    assert "already at target" in reason

    ok, reason = validate_color_shading_gains([(SHADING_GAIN_MAX,) * 3] * 32)
    assert ok is False
    assert "too dim" in reason


def test_adaptive_afe_gain_target_bright_probe_aims_sf_white():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        AFE_GAIN_TARGET_SF_WHITE,
        adaptive_afe_gain_target,
    )

    assert adaptive_afe_gain_target((33000, 35000, 38000)) == float(AFE_GAIN_TARGET_SF_WHITE)
    assert adaptive_afe_gain_target((13000, 12000, 12500)) == float(AFE_GAIN_TARGET_SF_WHITE)


def test_adaptive_afe_gain_target_dim_probe_raises():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        AFE_GAIN_TARGET_SF_WHITE,
        adaptive_afe_gain_target,
    )

    target = adaptive_afe_gain_target((2000, 2100, 1900))
    assert target > 2100
    assert target < float(AFE_GAIN_TARGET_SF_WHITE)


def test_coarse_offsets_from_wide_means_session_scale():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        coarse_offsets_from_wide_means,
    )

    # Invert session-04-ish coarse (~38/30/36): mean ≈ 65535 - code*1155.
    means = tuple(65535 - c * 1155 for c in (38, 30, 36))
    assert coarse_offsets_from_wide_means(means) == (38, 30, 36)


def test_search_afe_codes_respects_code_min():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        AFE_GAIN_MIN,
        search_afe_codes,
    )

    def apply(codes: tuple[int, int, int]) -> tuple[float, float, float]:
        # Always above target so dichotomy keeps lowering toward code_min.
        del codes
        return (40000.0, 40000.0, 40000.0)

    gains = search_afe_codes(
        initial=(0x80, 0x80, 0x80),
        code_max=0x1FF,
        code_min=AFE_GAIN_MIN,
        target=0x3000,
        iterations=9,
        tolerance=512.0,
        code_increases_mean=True,
        apply=apply,
    )
    assert gains == (AFE_GAIN_MIN, AFE_GAIN_MIN, AFE_GAIN_MIN)


def test_colour_afe_uses_coarse_only_lamp_stays_on(monkeypatch):
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        AFE_GAIN_MIN,
        AFE_STRIP_PIXELS,
        AFE_WIDE_PIXELS,
        AfeSearchConfig,
        coarse_offsets_from_wide_means,
    )

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    calls: list[dict] = []

    def fake_search(**kwargs):
        calls.append(kwargs)
        apply = kwargs["apply"]
        apply(kwargs["initial"])
        return (AFE_GAIN_MIN, AFE_GAIN_MIN + 1, AFE_GAIN_MIN + 2)

    monkeypatch.setattr(gl128_mod, "search_afe_codes", fake_search)

    sample = (30000).to_bytes(2, "little")
    strip = (sample * 3) * AFE_STRIP_PIXELS
    wide = (sample * 3) * AFE_WIDE_PIXELS
    expected_coarse = coarse_offsets_from_wide_means((30000.0, 30000.0, 30000.0))

    def _strip(size=AFE_STRIP_PIXELS * 6, **_k):
        return wide if size >= len(wide) else strip

    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._initialized = True
    asic.acquire_afe_strip = MagicMock(side_effect=_strip)
    asic.apply_frontend = MagicMock()
    asic.lamp_on = MagicMock()
    asic.lamp_off = MagicMock()

    result = asic.search_afe(config=AfeSearchConfig(iterations=1))
    assert result.offsets == expected_coarse
    assert result.gains == (AFE_GAIN_MIN, AFE_GAIN_MIN + 1, AFE_GAIN_MIN + 2)
    assert len(calls) == 1
    assert calls[0]["code_min"] == AFE_GAIN_MIN
    assert calls[0]["code_max"] > 0xFF
    asic.lamp_on.assert_called()
    asic.lamp_off.assert_not_called()


def test_colour_afe_zero_collapse_restores_coarse_and_session04(monkeypatch):
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        AFE_STRIP_PIXELS,
        AFE_WIDE_PIXELS,
        COLOR_AFE_SESSION04_GAINS,
        AfeSearchConfig,
        coarse_offsets_from_wide_means,
    )

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    def fake_search(**kwargs):
        apply = kwargs["apply"]
        apply(kwargs["initial"])
        return (0, 0, 0)

    monkeypatch.setattr(gl128_mod, "search_afe_codes", fake_search)

    sample = (28000).to_bytes(2, "little")
    strip = (sample * 3) * AFE_STRIP_PIXELS
    wide = (sample * 3) * AFE_WIDE_PIXELS
    expected_coarse = coarse_offsets_from_wide_means((28000.0, 28000.0, 28000.0))

    def _strip(size=AFE_STRIP_PIXELS * 6, **_k):
        return wide if size >= len(wide) else strip

    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._initialized = True
    asic.acquire_afe_strip = MagicMock(side_effect=_strip)
    asic.apply_frontend = MagicMock()
    asic.lamp_on = MagicMock()
    asic.lamp_off = MagicMock()

    result = asic.search_afe(config=AfeSearchConfig(iterations=1))
    assert result.offsets == expected_coarse
    assert result.gains == COLOR_AFE_SESSION04_GAINS
    asic.lamp_off.assert_not_called()


def test_colour_afe_peg_near_max_falls_back_and_skips_strike(monkeypatch):
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        AFE_STRIP_PIXELS,
        AFE_WIDE_PIXELS,
        AfeSearchConfig,
        coarse_offsets_from_wide_means,
    )

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    def fake_search(**kwargs):
        apply = kwargs["apply"]
        apply(kwargs["initial"])
        return (510, 510, 509)

    monkeypatch.setattr(gl128_mod, "search_afe_codes", fake_search)

    sample = (32000).to_bytes(2, "little")
    strip = (sample * 3) * AFE_STRIP_PIXELS
    wide = (sample * 3) * AFE_WIDE_PIXELS
    expected_coarse = coarse_offsets_from_wide_means((32000.0, 32000.0, 32000.0))

    def _strip(size=AFE_STRIP_PIXELS * 6, **_k):
        return wide if size >= len(wide) else strip

    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._initialized = True
    asic.acquire_afe_strip = MagicMock(side_effect=_strip)
    asic.apply_frontend = MagicMock()
    asic.lamp_on = MagicMock()
    asic.lamp_off = MagicMock()
    asic._strike_lamp_on = MagicMock()

    result = asic.search_afe(config=AfeSearchConfig(iterations=1))
    assert result.offsets == expected_coarse
    assert result.gains == (0x80, 0x80, 0x80)
    assert asic.last_afe is result
    asic.lamp_on.assert_called()
    asic.lamp_off.assert_not_called()
    asic._strike_lamp_on.assert_not_called()


def test_stationary_data_ready_ignores_busy_accepts_home_not_empty(monkeypatch):
    """Ready = not BUFEMPTY at home (0xa9/0x9c/…); reject motor-busy 0xa5."""
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.asic.status import ScannerStatus
    from negpy.infrastructure.scanners.plustek.exceptions import ScanError

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    # Discard read, then busy 0xa5 (no HOME), then live ready 0x9c.
    asic.read_status = MagicMock(
        side_effect=[
            ScannerStatus.from_reg41(0xDC),
            ScannerStatus.from_reg41(0xA5),
            ScannerStatus.from_reg41(0x9C),
        ]
    )
    asic._wait_stationary_data_ready(1.0, where="test")
    assert asic.read_status.call_count == 3

    asic.read_status = MagicMock(return_value=ScannerStatus.from_reg41(0xA5))
    with pytest.raises(ScanError, match="stationary data ready"):
        asic._wait_stationary_data_ready(0.0, where="test")


def test_motorized_shading_data_ready_accepts_busy_without_home(monkeypatch):
    """Colour white (DVDSET+MTRPWR) may leave home — accept not BUFEMPTY / 0xa5."""
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.asic.status import ScannerStatus
    from negpy.infrastructure.scanners.plustek.exceptions import ScanError

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic.read_status = MagicMock(
        side_effect=[
            ScannerStatus.from_reg41(0xDC),  # discard
            ScannerStatus.from_reg41(0xE5),  # empty, no home
            ScannerStatus.from_reg41(0xA5),  # not empty, motor busy, no home
        ]
    )
    asic._wait_shading_data_ready(1.0, where="white", motorized=True)
    assert asic.read_status.call_count == 3

    # Stationary path still rejects the same busy code.
    asic.read_status = MagicMock(return_value=ScannerStatus.from_reg41(0xA5))
    with pytest.raises(ScanError, match="stationary data ready"):
        asic._wait_shading_data_ready(0.0, where="dark", motorized=False)


def test_apply_stationary_scan_regs_writes_capture_mode_block():
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128

    proto = MagicMock()
    asic = Gl128(proto, MODEL_8200I_SE)
    asic._apply_stationary_scan_regs()
    written = dict(proto.write_registers_batched.call_args_list[0][0][0])
    written.update(dict(proto.write_registers_batched.call_args_list[1][0][0]))
    assert written[0x04] == 0x42
    assert written[0x05] == 0x40
    assert 0xD0 in written
    assert 0x03 not in written


def test_shading_strip_setup_sets_dvdset_when_requested():
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.asic.registers import Gl128Registers

    r = Gl128Registers()
    proto = MagicMock()
    proto.write_u24 = MagicMock()
    proto.write_u16 = MagicMock()
    proto.read_register = MagicMock(
        side_effect=lambda addr: {
            0x01: 0x22,
            0x02: 0x10,
            0x04: 0x42,
            0x05: 0x40,
            0x2B: 0x02,
            0xA5: 0x02,
            0xAB: 0x02,
        }.get(addr, 0)
    )
    asic = Gl128(proto, MODEL_8200I_SE)
    asic._apply_stationary_scan_regs = MagicMock()
    asic._setup_shading_strip_regs(pixels=100, lines=128, resolution=1800, dvdset=True)
    asic._apply_stationary_scan_regs.assert_not_called()
    # Geometry is written on both strips so the white pass never inherits a stale window.
    assert proto.write_u24.call_count >= 4
    assert asic._reg_cache[0x01] & 0x20  # DVDSET
    # SF white uses MTRPWR; NegPy ORs AGOHOME so SCAN-clear parks on live HW.
    assert asic._reg_cache[r.REG_0x02] & r.MTRPWR
    assert asic._reg_cache[r.REG_0x02] & r.AGOHOME
    # SF white clears 0x3B/0xA3; _SCAN_REGS alone would leave 0x3B=1.
    assert asic._reg_cache[0x3B] == 0x00
    assert asic._reg_cache[0xA3] == 0x00
    assert asic._reg_cache[0x2B] == MODEL_8200I_SE.dummy_by_dpi[1800]
    assert asic._reg_cache[0xA5] == MODEL_8200I_SE.pixel_clock_by_dpi[1800]
    assert asic._reg_cache[0xAB] == MODEL_8200I_SE.pixel_clock_by_dpi[1800]
    proto.write_u24.reset_mock()
    asic._setup_shading_strip_regs(pixels=100, lines=128, resolution=1800, dvdset=False)
    asic._apply_stationary_scan_regs.assert_called_once()
    assert proto.write_u24.call_count >= 4  # LINCNT, STR, END, FEEDL, ...
    assert not (asic._reg_cache[0x01] & 0x20)
    assert asic._reg_cache[r.REG_0x02] == 0x00
    assert asic._reg_cache[0xA3] == 0x01  # SF dark
    dark_dummy, dark_a, dark_b = MODEL_8200I_SE.shading_strip_clocks(1800, dvdset=False)
    assert asic._reg_cache[0x2B] == dark_dummy
    assert asic._reg_cache[0xA5] == dark_a
    assert asic._reg_cache[0xAB] == dark_b


def test_capture_lperiod_matches_session_03_04():
    assert MODEL_8200I_SE.line_period_for(1200) == 11283
    assert MODEL_8200I_SE.line_period_for(1800) == 11470


def test_upload_tables_slope_false_skips_slope_ahb():
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.asic.registers import Gl128Registers

    r = Gl128Registers()
    proto = MagicMock()
    proto.write_ahb = MagicMock()
    asic = Gl128(proto, MODEL_8200I_SE)
    asic.upload_tables(resolution=1800, shading=False, slope=False)
    addrs = [int(c.args[0]) for c in proto.write_ahb.call_args_list]
    assert r.AHB_SLOPE_SCAN not in addrs
    assert r.AHB_SLOPE_FAST not in addrs
    assert r.AHB_CHANNEL_R in addrs
    assert r.AHB_CHANNEL_G in addrs
    assert r.AHB_CHANNEL_B in addrs


def test_run_asic_shading_calls_exposure_only_after_unity(monkeypatch):
    """Unity→white window must not push slow slope (session 03/04)."""
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import AfeFrontend

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    proto = MagicMock()
    asic = Gl128(proto, MODEL_8200I_SE)
    asic._initialized = True
    asic._motor_moves_enabled = False
    asic.last_afe = AfeFrontend(offsets=(19, 16, 18), gains=(16, 16, 16))
    asic.apply_frontend = MagicMock()
    asic._require_carriage_at_home = MagicMock()
    asic.lamp_off = MagicMock()
    asic.lamp_on = MagicMock()
    asic._reassert_lamp_off = MagicMock()
    asic._log_lamp_off_state = MagicMock()
    asic._log_lamp_state = MagicMock()
    asic._await_colour_optical_dark = MagicMock()
    asic.upload_shading_table = MagicMock()
    n = 8
    strip = b"\x00\x04" * (n * 128 * 3)
    white = b"\x00\xd0" * (n * 128 * 3)
    asic.acquire_shading_strip = MagicMock(side_effect=[strip, white])
    calls: list[dict] = []

    def _upload(*, resolution: int, shading: bool = False, slope: bool = True):
        calls.append({"resolution": resolution, "shading": shading, "slope": slope})

    asic.upload_tables = MagicMock(side_effect=_upload)
    monkeypatch.setattr(
        gl128_mod,
        "average_rgb16_columns",
        MagicMock(
            side_effect=[
                [(900, 970, 990)] * n,
                [(50000, 52000, 51000)] * n,
            ]
        ),
    )

    asic.run_asic_shading(resolution=1800, strpixel=240, endpixel=240 + n * 4, dpiset=300)
    assert calls, "expected upload_tables after unity"
    assert calls[0]["slope"] is False
    # One unity upload, one measured upload — no re-upload after arming 0x22.
    assert asic.upload_shading_table.call_count == 2
    assert asic.asic_shading_ready is True


def test_measure_colour_falls_back_to_host_calib_when_dvdset_white_raw():
    """Host-fake ASIC whites cause diamond — calibrator must host-stretch instead."""
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import AfeFrontend

    geo = _geometry(1800)
    asic = _asic(shading_ready=False)
    asic.search_afe = MagicMock(return_value=None)
    asic.last_afe = AfeFrontend(offsets=(18, 15, 18), gains=(16, 16, 16))
    asic.asic_shading_ready = False
    asic.last_color_shading_host_ok = True
    asic.last_host_calib_dark = [(900, 970, 990)] * 64
    asic.last_host_calib_white = [(40000, 45000, 42000)] * 64
    asic.last_color_shading_reject_reason = "white mean hot"
    asic.run_asic_shading = MagicMock(return_value=b"\x00" * 100)

    cal = Calibrator(asic, cache_path=None, model=MODEL_8200I_SE)
    entry = cal.measure_colour_asic_shading(geo)
    assert entry.asic_shading is False
    assert entry.shading_blob is None
    assert entry.dark.shape[0] == geo.pixels
    assert int(entry.white[0, 0]) == 40000
    assert cal.prefer_asic_shading is False
    assert cal.should_apply_host_calib() is True


def test_await_colour_optical_dark_retries_once_then_raises(monkeypatch):
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.exceptions import ScanError
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import AFE_STRIP_PIXELS

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    sample = (20000).to_bytes(2, "little")
    strip = (sample * 3) * AFE_STRIP_PIXELS
    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._initialized = True
    asic.usb_planar_rgb = False
    asic.last_afe = None
    asic.acquire_afe_strip = MagicMock(return_value=strip)
    asic.apply_frontend = MagicMock()
    asic._reassert_lamp_off = MagicMock()
    asic._log_lamp_off_state = MagicMock()
    asic.read_status = MagicMock()

    with pytest.raises(ScanError, match="lamp-off strip still bright"):
        asic._await_colour_optical_dark()
    assert asic.acquire_afe_strip.call_count == 2


def test_await_colour_optical_dark_accepts_dark_probe(monkeypatch):
    import negpy.infrastructure.scanners.plustek.asic.gl128 as gl128_mod
    from negpy.infrastructure.scanners.plustek.asic.gl128 import Gl128
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import AFE_STRIP_PIXELS

    monkeypatch.setattr(gl128_mod.time, "sleep", lambda *_a, **_k: None)

    sample = (800).to_bytes(2, "little")
    strip = (sample * 3) * AFE_STRIP_PIXELS
    asic = Gl128(MagicMock(), MODEL_8200I_SE)
    asic._initialized = True
    asic.usb_planar_rgb = False
    asic.last_afe = None
    asic.acquire_afe_strip = MagicMock(return_value=strip)
    asic.apply_frontend = MagicMock()

    asic._await_colour_optical_dark()
    assert asic.acquire_afe_strip.call_count == 1


def test_validate_rejects_starved_channel():
    from negpy.infrastructure.scanners.plustek.scan.calib_gl128 import (
        COLOR_SHADING_CHANNEL_WHITE_MIN,
        validate_color_shading_strip,
    )

    assert COLOR_SHADING_CHANNEL_WHITE_MIN >= 8000
    dark = [(900, 1000, 1100)] * 32
    white = [(55000, 56000, 4000)] * 32
    ok, reason = validate_color_shading_strip(dark, white)
    assert ok is False
    assert "ch2 white" in reason


def test_infrared_cache_miss_does_not_force_colour_calib(tmp_path: Path, monkeypatch):
    asic = _asic()
    cal = Calibrator(asic, cache_path=tmp_path / "calib.json", model=MODEL_8200I_SE)
    geometry = _geometry()
    order: list[str] = []
    monkeypatch.setattr(cal, "ensure_colour_asic_shading", lambda *_a, **_k: order.append("ensure"))
    session = ScanSession(asic, MODEL_8200I_SE, cal)
    monkeypatch.setattr(
        session,
        "acquire_raw",
        lambda *_a, **_k: (order.append("acquire") or b"\x00" * 64),
    )
    monkeypatch.setattr(
        session.pipeline,
        "assemble",
        lambda *_a, **_k: np.zeros((4, geometry.pixels, 3), dtype=np.uint16),
    )
    session.run(resolution=geometry.resolution, mode="infrared", geometry=geometry)
    assert order == ["acquire"]


def test_host_calib_exposure_makeup_lifts_dim_film_window():
    """Home-chrome stretch leaves film-window peaks low — makeup to near white."""
    from negpy.infrastructure.scanners.plustek.scan.pipeline import (
        HOST_CALIB_PEAK_TARGET,
        ImagePipeline,
    )

    pipe = ImagePipeline(MODEL_8200I_SE)
    w = 32
    # Raw: dark~1000, mid film~8000, bright film~16000; home white~50000.
    rgb = np.full((16, w, 3), 8000, dtype=np.uint16)
    rgb[:, w // 4 : 3 * w // 4, :] = 16000
    dark = np.full((w, 3), 1000, dtype=np.uint16)
    white = np.full((w, 3), 50000, dtype=np.uint16)
    out = pipe.apply_host_calib(rgb, dark=dark, white=white)
    peak = float(np.percentile(out, 99.7))
    assert peak >= HOST_CALIB_PEAK_TARGET * 0.95
    # Midtones must lift with the peak (not only clip edges).
    assert float(np.median(out)) > 20000


def test_host_calib_border_clamp_caps_hot_margins():
    """Holder chrome brighter than film inset must not stay above film peak."""
    from negpy.infrastructure.scanners.plustek.scan.pipeline import ImagePipeline

    pipe = ImagePipeline(MODEL_8200I_SE)
    h, w = 100, 100
    rgb = np.full((h, w, 3), 20000, dtype=np.uint16)
    rgb[10:90, 10:90, :] = 25000
    rgb[:4, :, :] = 65535
    rgb[-4:, :, :] = 65535
    rgb[:, :4, :] = 65535
    rgb[:, -4:, :] = 65535
    out = pipe.clamp_border_highlights(rgb)
    assert int(out[:4].max()) <= int(out[10:90, 10:90].max())
    assert int(out[40:60, 40:60].mean()) == 25000


def test_border_clamp_ceiling_is_per_channel():
    """A joint ceiling leaves the margin above a dim channel's own film peak.

    Session 004: green's film window peaked at 19306 while the joint p99.7 was
    27432, so the clamped chrome became green's brightest content and auto Dmin
    metered it — a 1.42x green lift in the positive.
    """
    from negpy.infrastructure.scanners.plustek.scan.pipeline import ImagePipeline

    pipe = ImagePipeline(MODEL_8200I_SE)
    h, w = 200, 200
    rgb = np.zeros((h, w, 3), dtype=np.uint16)
    rgb[:, :, 0] = 27000
    rgb[:, :, 1] = 19300
    rgb[:, :, 2] = 27600
    rgb[:6, :, :] = 65535
    rgb[-6:, :, :] = 65535
    rgb[:, :6, :] = 65535
    rgb[:, -6:, :] = 65535
    out = pipe.clamp_border_highlights(rgb)
    film = out[20:180, 20:180]
    for c, expected in enumerate((27000, 19300, 27600)):
        assert int(film[:, :, c].max()) == expected
        assert int(out[:6, :, c].max()) <= expected + 1


def test_assemble_exposes_and_clamps_on_the_asic_shading_path():
    """DVDSET references home chrome, so both makeup and clamp must still run."""
    from negpy.infrastructure.scanners.plustek.scan.pipeline import ImagePipeline

    pipe = ImagePipeline(MODEL_8200I_SE)
    seen: list[str] = []
    pipe.expose_film_base = lambda rgb, **_kw: (seen.append("expose") or rgb)  # type: ignore[method-assign]
    pipe.clamp_border_highlights = lambda rgb, **_kw: (seen.append("clamp") or rgb)  # type: ignore[method-assign]
    geometry = _geometry(1800)
    rgb = np.zeros((geometry.lines, geometry.pixels, 3), dtype=np.uint16)
    pipe.decode_rgb = lambda *_a, **_k: rgb  # type: ignore[method-assign]
    pipe.assemble(b"", geometry, dark=None, white=None, planar=False)
    assert seen == ["expose", "clamp"]


def test_expose_film_base_keeps_the_orange_mask_ratios():
    """The makeup must be scalar — a per-channel lift neutralizes the mask."""
    from negpy.infrastructure.scanners.plustek.scan.pipeline import (
        HOST_CALIB_PEAK_TARGET,
        ImagePipeline,
    )

    pipe = ImagePipeline(MODEL_8200I_SE)
    h, w = 120, 120
    rgb = np.zeros((h, w, 3), dtype=np.uint16)
    # Session 004 film-window levels: base at 42% of scale, green 0.71x of red.
    rgb[:, :, 0] = 27046
    rgb[:, :, 1] = 19306
    rgb[:, :, 2] = 27695
    out = pipe.expose_film_base(rgb, source="test")
    peak = float(out.max())
    assert 0.98 <= peak / HOST_CALIB_PEAK_TARGET <= 1.02
    before = 19306 / 27046
    after = float(out[:, :, 1].mean()) / float(out[:, :, 0].mean())
    assert abs(after - before) < 0.005
