"""Tests for platform detection from OCR text."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from username_extractor import detect_platforms_in_text, PLATFORM_TIKTOK, PLATFORM_INSTAGRAM


class TestTikTokDetection:
    def test_tiktok_keyword(self):
        scores = detect_platforms_in_text("ob TikTok\n@jazmynmajors")
        assert scores[PLATFORM_TIKTOK] >= 10

    def test_tiktok_split_keyword(self):
        scores = detect_platforms_in_text("Tik Tok\n@someuser")
        assert scores[PLATFORM_TIKTOK] >= 10

    def test_tiktok_lowercase(self):
        scores = detect_platforms_in_text("tiktok @user123")
        assert scores[PLATFORM_TIKTOK] >= 10

    def test_tiktok_in_noisy_ocr(self):
        text = "TN\nww\nTik Tok\n@ haile987\n0:00 / 0:12"
        scores = detect_platforms_in_text(text)
        assert scores[PLATFORM_TIKTOK] >= 10

    def test_no_tiktok_in_random_text(self):
        scores = detect_platforms_in_text("hello world some random text")
        assert scores.get(PLATFORM_TIKTOK, 0) == 0


class TestInstagramDetection:
    def test_ao_vivo(self):
        scores = detect_platforms_in_text("amelie_gym.xx  AO VIVO  5")
        assert scores[PLATFORM_INSTAGRAM] >= 10

    def test_in_diretta(self):
        scores = detect_platforms_in_text("steli_acro\nIn diretta  4")
        assert scores[PLATFORM_INSTAGRAM] >= 10

    def test_en_vivo(self):
        scores = detect_platforms_in_text("username123 En vivo")
        assert scores[PLATFORM_INSTAGRAM] >= 10

    def test_en_direct(self):
        scores = detect_platforms_in_text("user.name En direct")
        assert scores[PLATFORM_INSTAGRAM] >= 10

    def test_weak_signals_not_enough_alone(self):
        """A single weak signal (e.g. 'entrou') scores 3, below threshold."""
        scores = detect_platforms_in_text("tallulah.rose10 entrou")
        assert scores.get(PLATFORM_INSTAGRAM, 0) < 10

    def test_multiple_weak_signals_accumulate(self):
        text = "entrou seguidores seguindo curtir"
        scores = detect_platforms_in_text(text)
        assert scores[PLATFORM_INSTAGRAM] == 12  # 4 × 3

    def test_no_instagram_in_random_text(self):
        scores = detect_platforms_in_text("nothing platform related here")
        assert scores.get(PLATFORM_INSTAGRAM, 0) == 0


class TestMixedPlatform:
    def test_tiktok_only(self):
        scores = detect_platforms_in_text("TikTok @user")
        assert PLATFORM_TIKTOK in scores
        assert PLATFORM_INSTAGRAM not in scores

    def test_instagram_only(self):
        scores = detect_platforms_in_text("joellegymnast AO VIVO")
        assert PLATFORM_INSTAGRAM in scores
        assert PLATFORM_TIKTOK not in scores

    def test_empty_text(self):
        scores = detect_platforms_in_text("")
        assert len(scores) == 0

    def test_whitespace_only(self):
        scores = detect_platforms_in_text("   \n\n  ")
        assert len(scores) == 0
