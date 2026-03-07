"""Tests for scene/image processing with mocked Stash API."""

import collections
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from username_extractor import (
    process_scene,
    process_image,
    build_details,
    USERNAME_MARKER,
    PLATFORM_TIKTOK,
    PLATFORM_INSTAGRAM,
    PLATFORM_CONFIDENCE_THRESHOLD,
)


def _make_scene(
    scene_id="123",
    title="test.mp4",
    details="",
    video_path="/media/test.mp4",
    duration=30,
    studio_name=None,
    tags=None,
    performers=None,
):
    scene = {
        "id": scene_id,
        "title": title,
        "details": details,
        "files": [{"path": video_path, "duration": duration}],
        "studio": {"id": "s1", "name": studio_name} if studio_name else None,
        "tags": tags or [],
        "performers": performers or [],
    }
    return scene


def _make_image(
    image_id="456",
    title="test.jpg",
    details="",
    image_path="/media/test.jpg",
    studio_name=None,
    tags=None,
    performers=None,
):
    return {
        "id": image_id,
        "title": title,
        "details": details,
        "visual_files": [{"path": image_path}],
        "studio": {"id": "s1", "name": studio_name} if studio_name else None,
        "tags": tags or [],
        "performers": performers or [],
    }


class TestProcessScenePlatformDetection:
    """Test that process_scene correctly sets studio based on platform detection."""

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.find_or_create_studio", return_value="studio_tiktok")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_sets_tiktok_studio(
        self, mock_exists, mock_analyze, mock_studio, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter({PLATFORM_TIKTOK: 30}),
            collections.Counter({"jazmynmajors": 25}),
        )
        scene = _make_scene()
        studio_cache = {}

        result = process_scene(MagicMock(), scene, studio_cache, "tag_id")

        assert "TikTok" in result
        assert "jazmynmajors" in result
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[1]["studio_id"] == "studio_tiktok"

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.find_or_create_studio", return_value="studio_ig")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_sets_instagram_studio(
        self, mock_exists, mock_analyze, mock_studio, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter({PLATFORM_INSTAGRAM: 20}),
            collections.Counter({"steli_acro": 14}),
        )
        scene = _make_scene()
        studio_cache = {}

        result = process_scene(MagicMock(), scene, studio_cache, "tag_id")

        assert "Instagram" in result
        assert "steli_acro" in result

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_no_platform_no_studio_set(
        self, mock_exists, mock_analyze, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter(),  # no platform
            collections.Counter({"someuser": 5}),
        )
        scene = _make_scene()

        result = process_scene(MagicMock(), scene, {}, "tag_id")

        mock_update.assert_called_once()
        assert mock_update.call_args[1]["studio_id"] is None

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_does_not_override_existing_studio(
        self, mock_exists, mock_analyze, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter({PLATFORM_TIKTOK: 30}),
            collections.Counter({"user": 10}),
        )
        scene = _make_scene(studio_name="YouTube")

        result = process_scene(MagicMock(), scene, {}, "tag_id")

        mock_update.assert_called_once()
        # Should NOT set studio because one already exists
        assert mock_update.call_args[1]["studio_id"] is None

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_below_threshold_no_studio(
        self, mock_exists, mock_analyze, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter({PLATFORM_INSTAGRAM: 3}),  # below threshold
            collections.Counter({"user": 5}),
        )
        scene = _make_scene()

        result = process_scene(MagicMock(), scene, {}, "tag_id")

        mock_update.assert_called_once()
        assert mock_update.call_args[1]["studio_id"] is None


class TestProcessScenePerformerLinking:
    """Test performer auto-linking when username matches performer URLs."""

    @patch("username_extractor.find_or_create_studio", return_value="studio_tiktok")
    @patch("username_extractor.update_scene")
    @patch("username_extractor.find_performers_by_url")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_links_matching_performer(
        self, mock_exists, mock_analyze, mock_performers, mock_update, mock_studio
    ):
        mock_analyze.return_value = (
            collections.Counter({PLATFORM_TIKTOK: 20}),
            collections.Counter({"jazmynmajors": 25}),
        )
        mock_performers.return_value = [
            {"id": "p1", "name": "Jazmyn Majors", "urls": ["https://tiktok.com/@jazmynmajors"]}
        ]
        scene = _make_scene()

        result = process_scene(MagicMock(), scene, {}, "tag_id")

        assert "Jazmyn Majors" in result
        mock_update.assert_called_once()
        assert "p1" in mock_update.call_args[1]["performer_ids"]

    @patch("username_extractor.update_scene")
    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_no_matching_performer(
        self, mock_exists, mock_analyze, mock_performers, mock_update
    ):
        mock_analyze.return_value = (
            collections.Counter(),
            collections.Counter({"unknownuser": 10}),
        )
        scene = _make_scene()

        process_scene(MagicMock(), scene, {}, "tag_id")

        mock_update.assert_called_once()
        assert mock_update.call_args[1]["performer_ids"] is None

    @patch("username_extractor.update_scene")
    @patch("username_extractor.find_performers_by_url")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_preserves_existing_performers(
        self, mock_exists, mock_analyze, mock_performers, mock_update
    ):
        mock_analyze.return_value = (
            collections.Counter(),
            collections.Counter({"newuser": 10}),
        )
        mock_performers.return_value = [
            {"id": "p2", "name": "New Performer", "urls": ["https://instagram.com/newuser"]}
        ]
        scene = _make_scene(
            performers=[{"id": "p_existing", "name": "Existing"}]
        )

        process_scene(MagicMock(), scene, {}, "tag_id")

        mock_update.assert_called_once()
        perf_ids = mock_update.call_args[1]["performer_ids"]
        assert "p_existing" in perf_ids
        assert "p2" in perf_ids

    @patch("username_extractor.update_scene")
    @patch("username_extractor.find_performers_by_url")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_no_duplicate_performer(
        self, mock_exists, mock_analyze, mock_performers, mock_update
    ):
        """If performer is already linked, don't add again."""
        mock_analyze.return_value = (
            collections.Counter(),
            collections.Counter({"user": 10}),
        )
        mock_performers.return_value = [
            {"id": "p1", "name": "Already Linked", "urls": ["https://tiktok.com/@user"]}
        ]
        scene = _make_scene(performers=[{"id": "p1", "name": "Already Linked"}])

        process_scene(MagicMock(), scene, {}, "tag_id")

        mock_update.assert_called_once()
        # No new performers → performer_ids should be None
        assert mock_update.call_args[1]["performer_ids"] is None


class TestProcessSceneDetails:
    """Test details field updating."""

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_sets_details_with_username(
        self, mock_exists, mock_analyze, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter(),
            collections.Counter({"testuser": 10}),
        )
        scene = _make_scene(details="")

        process_scene(MagicMock(), scene, {}, "tag_id")

        new_details = mock_update.call_args[1]["details"]
        assert USERNAME_MARKER in new_details
        assert "@testuser" in new_details

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_does_not_overwrite_existing_marker(
        self, mock_exists, mock_analyze, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter(),
            collections.Counter({"user": 10}),
        )
        existing = f"{USERNAME_MARKER} @olduser"
        scene = _make_scene(details=existing)

        process_scene(MagicMock(), scene, {}, "tag_id")

        # details should be None (not updated)
        assert mock_update.call_args[1]["details"] is None


class TestProcessSceneTags:
    """Test that the processed tag is always added."""

    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_adds_processed_tag(
        self, mock_exists, mock_analyze, mock_update, mock_perf
    ):
        mock_analyze.return_value = (
            collections.Counter(),
            collections.Counter(),
        )
        scene = _make_scene(tags=[{"id": "t1", "name": "existing"}])

        process_scene(MagicMock(), scene, {}, "new_tag_id")

        tag_ids = mock_update.call_args[0][2]  # positional arg
        assert "t1" in tag_ids
        assert "new_tag_id" in tag_ids


class TestProcessSceneEdgeCases:
    def test_no_files(self):
        scene = {"id": "1", "title": "", "details": "", "files": [],
                 "studio": None, "tags": [], "performers": []}
        result = process_scene(MagicMock(), scene, {}, "tag_id")
        assert result == "no_files"

    @patch("os.path.exists", return_value=False)
    def test_file_missing(self, mock_exists):
        scene = _make_scene(video_path="/nonexistent.mp4")
        result = process_scene(MagicMock(), scene, {}, "tag_id")
        assert result == "file_missing"


class TestProcessSceneDryRun:
    @patch("username_extractor.find_or_create_studio", return_value="studio_tiktok")
    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.update_scene")
    @patch("username_extractor.analyze_video")
    @patch("os.path.exists", return_value=True)
    def test_dry_run_does_not_update(
        self, mock_exists, mock_analyze, mock_update, mock_perf, mock_studio
    ):
        mock_analyze.return_value = (
            collections.Counter({PLATFORM_TIKTOK: 20}),
            collections.Counter({"user": 15}),
        )
        scene = _make_scene()

        result = process_scene(MagicMock(), scene, {}, "tag_id", dry_run=True)

        assert "TikTok" in result
        assert "user" in result
        mock_update.assert_not_called()


class TestProcessImage:
    @patch("username_extractor.find_or_create_studio", return_value="studio_ig")
    @patch("username_extractor.update_image")
    @patch("username_extractor.find_performers_by_url", return_value=[])
    @patch("username_extractor.analyze_image_file")
    @patch("os.path.exists", return_value=True)
    def test_basic_image_processing(
        self, mock_exists, mock_analyze, mock_perf, mock_update, mock_studio
    ):
        mock_analyze.return_value = (
            collections.Counter({PLATFORM_INSTAGRAM: 20}),
            collections.Counter({"img_user": 10}),
        )
        image = _make_image()

        result = process_image(MagicMock(), image, {}, "tag_id")

        assert "Instagram" in result
        assert "img_user" in result
        mock_update.assert_called_once()

    def test_image_no_files(self):
        image = {"id": "1", "title": "", "details": "", "visual_files": [],
                 "studio": None, "tags": [], "performers": []}
        result = process_image(MagicMock(), image, {}, "tag_id")
        assert result == "no_files"
