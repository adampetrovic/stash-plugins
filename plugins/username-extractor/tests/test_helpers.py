"""Tests for utility / helper functions."""

import collections
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from username_extractor import (
    build_details,
    get_sample_timestamps,
    pick_winner,
    _existing_tag_ids,
    _existing_performer_ids,
    USERNAME_MARKER,
)


class TestBuildDetails:
    def test_empty_existing(self):
        result = build_details("", "jazmynmajors")
        assert result == f"{USERNAME_MARKER} @jazmynmajors"

    def test_none_existing(self):
        result = build_details(None, "jazmynmajors")
        assert result == f"{USERNAME_MARKER} @jazmynmajors"

    def test_preserves_existing(self):
        result = build_details("some existing notes", "user_1")
        assert result.startswith(f"{USERNAME_MARKER} @user_1")
        assert "some existing notes" in result

    def test_double_newline_separator(self):
        result = build_details("old", "user")
        assert "\n\n" in result


class TestGetSampleTimestamps:
    def test_zero_duration(self):
        ts = get_sample_timestamps(0)
        assert ts == [0.5]

    def test_negative_duration(self):
        ts = get_sample_timestamps(-5)
        assert ts == [0.5]

    def test_none_duration(self):
        ts = get_sample_timestamps(None)
        assert ts == [0.5]

    def test_short_video(self):
        ts = get_sample_timestamps(2)
        assert len(ts) == 1
        assert 0 < ts[0] < 2

    def test_medium_video(self):
        ts = get_sample_timestamps(30)
        assert len(ts) >= 3
        assert all(0 < t < 30 for t in ts)

    def test_timestamps_are_sorted(self):
        ts = get_sample_timestamps(60)
        assert ts == sorted(ts)

    def test_timestamps_avoid_edges(self):
        """Timestamps should not be at 0 or at the exact duration."""
        ts = get_sample_timestamps(60)
        assert all(t > 0 for t in ts)
        assert all(t < 60 for t in ts)

    def test_num_frames_respected(self):
        ts = get_sample_timestamps(120, num_frames=3)
        assert len(ts) == 3


class TestPickWinner:
    def test_empty_counter(self):
        assert pick_winner(collections.Counter()) is None

    def test_single_candidate(self):
        c = collections.Counter({"user": 10})
        assert pick_winner(c) == "user"

    def test_highest_wins(self):
        c = collections.Counter({"user_a": 5, "user_b": 20, "user_c": 3})
        assert pick_winner(c) == "user_b"

    def test_threshold_filters(self):
        c = collections.Counter({"user": 5})
        assert pick_winner(c, threshold=10) is None

    def test_threshold_passes(self):
        c = collections.Counter({"user": 15})
        assert pick_winner(c, threshold=10) == "user"

    def test_zero_threshold(self):
        c = collections.Counter({"user": 1})
        assert pick_winner(c, threshold=0) == "user"


class TestExistingIds:
    def test_tag_ids(self):
        item = {"tags": [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]}
        assert _existing_tag_ids(item) == {"1", "2"}

    def test_empty_tags(self):
        assert _existing_tag_ids({"tags": []}) == set()

    def test_no_tags_key(self):
        assert _existing_tag_ids({}) == set()

    def test_none_tags(self):
        assert _existing_tag_ids({"tags": None}) == set()

    def test_performer_ids(self):
        item = {"performers": [{"id": "10", "name": "A"}, {"id": "20", "name": "B"}]}
        assert _existing_performer_ids(item) == {"10", "20"}

    def test_empty_performers(self):
        assert _existing_performer_ids({"performers": []}) == set()

    def test_no_performers_key(self):
        assert _existing_performer_ids({}) == set()
