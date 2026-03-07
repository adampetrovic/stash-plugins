"""Integration tests: run real OCR on screenshot fixtures.

These require tesseract and ffmpeg to be installed.
Skipped automatically if either is missing (e.g. in CI).
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from username_extractor import (
    PLATFORM_INSTAGRAM,
    PLATFORM_TIKTOK,
    analyze_image_file,
    pick_winner,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed"
)
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)

requires_ocr = pytest.mark.usefixtures()  # grouping marker
pytestmark = [needs_tesseract, needs_ffmpeg]


def _run(filename):
    """Run analyze_image_file on a fixture, return (platform, username)."""
    path = os.path.join(FIXTURES, filename)
    assert os.path.exists(path), f"Fixture missing: {path}"
    pv, uv = analyze_image_file(path)
    platform = pick_winner(pv, threshold=10)
    username = pick_winner(uv, threshold=2)
    return platform, username, pv, uv


# -------------------------------------------------------------------------
# TikTok screenshots
# -------------------------------------------------------------------------


class TestTikTokOCR:
    def test_jazmynmajors(self):
        """Clear TikTok watermark: logo + @jazmynmajors."""
        platform, username, pv, uv = _run("tiktok_jazmynmajors.png")
        assert platform == PLATFORM_TIKTOK, f"Expected TikTok, got {platform} ({pv})"
        assert username == "jazmynmajors", f"Expected jazmynmajors, got {username} ({uv})"

    def test_sydmcghee(self):
        """Small cropped TikTok watermark: @sydmcghee."""
        platform, username, pv, uv = _run("tiktok_sydmcghee.png")
        assert platform == PLATFORM_TIKTOK, f"Expected TikTok, got {platform} ({pv})"
        assert username == "sydmcghee", f"Expected sydmcghee, got {username} ({uv})"

    @pytest.mark.xfail(
        reason="Moved/semi-transparent watermark too garbled for tesseract",
        strict=False,
    )
    def test_haile987_moved_watermark(self):
        """TikTok with moved/semi-transparent watermark — harder case.

        The watermark text is partially obscured.  We accept either a
        correct username or at least correct platform detection.
        """
        platform, username, pv, uv = _run("tiktok_haile987_moved.png")
        assert platform == PLATFORM_TIKTOK, f"Expected TikTok, got {platform} ({pv})"
        # Username may be garbled — accept partial match
        if username:
            assert "haile" in username or "987" in username, (
                f"Expected haile*987*, got {username} ({uv})"
            )


# -------------------------------------------------------------------------
# Instagram Live screenshots
# -------------------------------------------------------------------------


class TestInstagramLiveOCR:
    def test_steli_acro(self):
        """Instagram Live (Italian 'In diretta'): steli_acro."""
        platform, username, pv, uv = _run("ig_live_steli_acro.png")
        assert platform == PLATFORM_INSTAGRAM, f"Expected Instagram, got {platform} ({pv})"
        assert username == "steli_acro", f"Expected steli_acro, got {username} ({uv})"

    def test_amelie_gym_xx(self):
        """Instagram Live (Portuguese 'AO VIVO') with chat: amelie_gym.xx."""
        platform, username, pv, uv = _run("ig_live_amelie_gym_xx.png")
        assert platform == PLATFORM_INSTAGRAM, f"Expected Instagram, got {platform} ({pv})"
        assert username == "amelie_gym.xx", f"Expected amelie_gym.xx, got {username} ({uv})"

    def test_joellegymnast_device_recording(self):
        """Device recording of Instagram Live (AO VIVO): joellegymnast."""
        platform, username, pv, uv = _run("ig_live_joellegymnast_device.png")
        assert platform == PLATFORM_INSTAGRAM, f"Expected Instagram, got {platform} ({pv})"
        assert username == "joellegymnast", f"Expected joellegymnast, got {username} ({uv})"


# -------------------------------------------------------------------------
# Instagram Reel screenshot
# -------------------------------------------------------------------------


class TestInstagramReelOCR:
    def test_miraclegymnast(self):
        """Instagram Reel with poster + @mention: miraclegymnast."""
        platform, username, pv, uv = _run("ig_reel_miraclegymnast.png")
        # This may detect as Instagram or no platform (Reels don't always
        # have the same Live indicators).  The key assertion is username.
        assert username in (
            "miraclegymnast",
            "gipson_tegan",
        ), f"Expected miraclegymnast or gipson_tegan, got {username} ({uv})"
