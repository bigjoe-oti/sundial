"""Tests for decay.rank_memories() and _salience_tag()."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import decay  # noqa: E402


class TestSalienceTag(unittest.TestCase):
    """Boundary tests for the salience classifier."""

    def test_active_positive_score(self) -> None:
        self.assertEqual(decay._salience_tag(0.1), "active")
        self.assertEqual(decay._salience_tag(5.0), "active")

    def test_fading_zero_boundary(self) -> None:
        # score == 0 is NOT > 0, so it should be "fading" (the middle band).
        self.assertEqual(decay._salience_tag(0.0), "fading")

    def test_fading_negative_above_dormant(self) -> None:
        self.assertEqual(decay._salience_tag(-0.5), "fading")
        self.assertEqual(decay._salience_tag(-1.99), "fading")

    def test_fading_at_dormant_boundary(self) -> None:
        # score == -2.0 is NOT < -2.0, so it should be "fading".
        self.assertEqual(decay._salience_tag(-2.0), "fading")

    def test_dormant_below_threshold(self) -> None:
        self.assertEqual(decay._salience_tag(-2.01), "dormant")
        self.assertEqual(decay._salience_tag(-10.0), "dormant")


class TestRankMemories(unittest.TestCase):
    """rank_memories() composability and sorting tests."""

    def test_empty_weights(self) -> None:
        self.assertEqual(decay.rank_memories({}), [])
        self.assertEqual(decay.rank_memories(None), [])  # type: ignore[arg-type]

    def test_sorted_descending_by_score(self) -> None:
        weights = {
            "a.md": {"score": -3.0, "accesses": 1, "last_seen": "2026-01-01T00:00:00+00:00"},
            "b.md": {"score": 2.0, "accesses": 5, "last_seen": "2026-01-01T00:00:00+00:00"},
            "c.md": {"score": 0.5, "accesses": 3, "last_seen": "2026-01-01T00:00:00+00:00"},
        }
        ranked = decay.rank_memories(weights)
        self.assertEqual([e["file"] for e in ranked], ["b.md", "c.md", "a.md"])

    def test_salience_tags_applied(self) -> None:
        weights = {
            "active.md": {"score": 1.5, "accesses": 4, "last_seen": "2026-01-01T00:00:00+00:00"},
            "fading.md": {"score": -1.0, "accesses": 2, "last_seen": "2026-01-01T00:00:00+00:00"},
            "dormant.md": {"score": -5.0, "accesses": 1, "last_seen": "2026-01-01T00:00:00+00:00"},
        }
        ranked = decay.rank_memories(weights)
        by_file = {e["file"]: e["salience"] for e in ranked}
        self.assertEqual(by_file["active.md"], "active")
        self.assertEqual(by_file["fading.md"], "fading")
        self.assertEqual(by_file["dormant.md"], "dormant")

    def test_top_k_truncation(self) -> None:
        weights = {f"mem{i}.md": {"score": float(i), "accesses": 1,
                                   "last_seen": "2026-01-01T00:00:00+00:00"}
                   for i in range(20)}
        ranked = decay.rank_memories(weights, top_k=5)
        self.assertEqual(len(ranked), 5)
        # Highest scores first
        self.assertEqual(ranked[0]["file"], "mem19.md")
        self.assertEqual(ranked[4]["file"], "mem15.md")

    def test_malformed_entries_skipped(self) -> None:
        weights = {
            "good.md": {"score": 1.0, "accesses": 2, "last_seen": "2026-01-01T00:00:00+00:00"},
            "no_score.md": {"accesses": 1, "last_seen": "2026-01-01T00:00:00+00:00"},
            "bad_type.md": "not a dict",
            "null_score.md": {"score": None, "accesses": 1, "last_seen": "2026-01-01T00:00:00+00:00"},
        }
        ranked = decay.rank_memories(weights)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["file"], "good.md")

    def test_output_shape(self) -> None:
        weights = {
            "test.md": {"score": 0.5, "accesses": 3, "last_seen": "2026-01-01T00:00:00+00:00"},
        }
        ranked = decay.rank_memories(weights)
        self.assertEqual(len(ranked), 1)
        entry = ranked[0]
        self.assertIn("file", entry)
        self.assertIn("score", entry)
        self.assertIn("salience", entry)
        self.assertIn("accesses", entry)
        self.assertIsInstance(entry["score"], float)
        self.assertIsInstance(entry["accesses"], int)


if __name__ == "__main__":
    unittest.main()
