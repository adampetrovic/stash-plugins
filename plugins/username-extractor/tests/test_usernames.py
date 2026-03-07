"""Tests for username extraction from OCR text."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from username_extractor import find_usernames_in_text, is_noise, normalize_username


# ---------------------------------------------------------------------------
# Real OCR output from test screenshots (captured during development)
# ---------------------------------------------------------------------------

# TikTok @jazmynmajors — original frame, PSM 3
TIKTOK_JAZMYN = "ob TikTok\n\n@jazmynmajors\n"

# Instagram Reel — miraclegymnast + @gipson_tegan
INSTAGRAM_REEL = (
    "DP) F10%m\n\n16:00 Quarts-feirs 31 de sgowte\"\n\n"
    "< fae\n\n$B Minnetonka\nSom 9:07\n\n"
    "miraclegymnast © 1\nnjoying our time tagether 7 @ PP:\n"
    "\"2 zz\n\n@gipson_tegan ah\n\n'ler ¢rmiearie\n"
)

# Instagram Live — steli_acro (from threshold preprocessing)
INSTAGRAM_LIVE_THRESH = "steli_acro\n\nIn diretta\n\ne\n\n.\n\n*\n\n@ |\n"

# Device recording — joellegymnast (from threshold preprocessing)
DEVICE_RECORDING_THRESH = (
    "2229\n\nAO VIVO |\"! e3 ,x«\n\njoellegymnast\n\n=\n\n1s,\n\n"
    "_~\n\n~*~\n\ntv\n\n0:20\n"
)

# TikTok with moved watermark — partial OCR (threshold 170)
TIKTOK_MOVED_PARTIAL = "To,\n\nalepa\n\n533210\n"


class TestAtUsernameExtraction:
    """Tests for @username pattern matching."""

    def test_tiktok_at_username(self):
        results = find_usernames_in_text(TIKTOK_JAZMYN)
        names = [u for u, _ in results]
        assert "jazmynmajors" in names

    def test_tiktok_at_username_high_confidence(self):
        """@username near TikTok keyword should get boosted score."""
        results = find_usernames_in_text(TIKTOK_JAZMYN)
        scores = {u: s for u, s in results}
        # Should have both a base @match (10) and a TikTok-proximity match (15)
        assert scores.get("jazmynmajors", 0) >= 10

    def test_instagram_at_mention(self):
        results = find_usernames_in_text(INSTAGRAM_REEL)
        names = [u for u, _ in results]
        assert "gipson_tegan" in names

    def test_at_with_space(self):
        """OCR sometimes inserts a space after @."""
        results = find_usernames_in_text("@ some_user more text")
        names = [u for u, _ in results]
        assert "some_user" in names

    def test_at_with_dots(self):
        results = find_usernames_in_text("@amelie_gym.xx")
        names = [u for u, _ in results]
        assert "amelie_gym.xx" in names

    def test_multiple_at_usernames(self):
        text = "@user_one text @user_two more text @user_three"
        results = find_usernames_in_text(text)
        names = [u for u, _ in results]
        assert "user_one" in names
        assert "user_two" in names
        assert "user_three" in names


class TestStandaloneUsernameExtraction:
    """Tests for Instagram-style standalone username detection."""

    def test_username_with_underscore(self):
        results = find_usernames_in_text(INSTAGRAM_LIVE_THRESH)
        names = [u for u, _ in results]
        assert "steli_acro" in names

    def test_username_from_device_recording(self):
        results = find_usernames_in_text(DEVICE_RECORDING_THRESH)
        names = [u for u, _ in results]
        assert "joellegymnast" in names

    def test_instagram_reel_primary_username(self):
        results = find_usernames_in_text(INSTAGRAM_REEL)
        names = [u for u, _ in results]
        assert "miraclegymnast" in names

    def test_underscore_gives_higher_score_than_plain(self):
        results = find_usernames_in_text("steli_acro plainword")
        scores = {u: s for u, s in results}
        assert scores.get("steli_acro", 0) > scores.get("plainword", 0)


class TestNoisyOCRText:
    """Ensure noise is filtered and real usernames survive."""

    def test_timestamps_filtered(self):
        results = find_usernames_in_text("16:00 @real_user 0:12")
        names = [u for u, _ in results]
        assert "real_user" in names
        # Timestamps should not appear
        assert all("16:00" not in u and "0:12" not in u for u in names)

    def test_platform_words_filtered(self):
        results = find_usernames_in_text("TikTok Instagram @actual_user")
        names = [u for u, _ in results]
        assert "actual_user" in names
        assert "tiktok" not in [n.lower() for n in names]
        assert "instagram" not in [n.lower() for n in names]

    def test_ui_words_filtered(self):
        text = "followers following share @the_real_user comment"
        results = find_usernames_in_text(text)
        names = [u for u, _ in results]
        assert "the_real_user" in names
        assert "followers" not in names
        assert "following" not in names

    def test_percentage_filtered(self):
        results = find_usernames_in_text("88% @real_user")
        names = [u for u, _ in results]
        assert "real_user" in names

    def test_empty_text(self):
        assert find_usernames_in_text("") == []

    def test_only_noise(self):
        results = find_usernames_in_text("the and for you 12:00 88%")
        assert results == []


class TestIsNoise:
    def test_short_words(self):
        assert is_noise("ab")
        assert is_noise("x")
        assert is_noise("tHa")
        assert is_noise("abc")

    def test_timestamps(self):
        assert is_noise("12:00")
        assert is_noise("0:12")

    def test_percentages(self):
        assert is_noise("88%")

    def test_pure_numbers(self):
        assert is_noise("12345")

    def test_ui_words(self):
        assert is_noise("follow")
        assert is_noise("LIVE")
        assert is_noise("entrou")

    def test_all_caps_short(self):
        assert is_noise("VIVO")
        assert is_noise("AO")

    def test_valid_usernames_pass(self):
        assert not is_noise("jazmynmajors")
        assert not is_noise("steli_acro")
        assert not is_noise("amelie_gym.xx")
        assert not is_noise("gipson_tegan")
        assert not is_noise("miraclegymnast")

    def test_empty(self):
        assert is_noise("")

    def test_too_long(self):
        assert is_noise("a" * 31)


class TestNormalizeUsername:
    def test_strip_at(self):
        assert normalize_username("@jazmynmajors") == "jazmynmajors"

    def test_strip_trailing_punctuation(self):
        assert normalize_username("user123...") == "user123"
        assert normalize_username("user!?") == "user"

    def test_strip_leading_dots(self):
        assert normalize_username("..user") == "user"
        assert normalize_username(".user") == "user"

    def test_preserves_leading_underscore(self):
        """Leading underscores are valid (e.g. _username)."""
        assert normalize_username("@_username") == "_username"
        assert normalize_username("_user_name") == "_user_name"

    def test_preserves_trailing_underscore(self):
        """Trailing underscores are valid (e.g. kaylenmorgan_)."""
        assert normalize_username("@kaylenmorgan_") == "kaylenmorgan_"
        assert normalize_username("user_") == "user_"

    def test_clean_username_unchanged(self):
        assert normalize_username("steli_acro") == "steli_acro"

    def test_at_with_space(self):
        assert normalize_username("@ user") == "user"
