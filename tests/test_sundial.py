#!/usr/bin/env python3
"""Sundial test suite. Stdlib only (unittest), zero dependencies.

Run:  python3 tests/test_sundial.py
Pins the load-bearing logic so it cannot silently rot: quiet-hours edges,
the ACT-R decay ordering, the due-horizon boundary, the token parser, and the
ledger's idempotent-start / finalize-previous behavior.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))

import core  # noqa: E402
import decay  # noqa: E402
import tzutil  # noqa: E402
import policy  # noqa: E402
import estimator  # noqa: E402

WATCHER_DIR = Path(__file__).resolve().parent.parent / "watcher"
sys.path.insert(0, str(WATCHER_DIR))

import watcher  # noqa: E402
import opportunities  # noqa: E402
import owner_model  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import prompt_submit  # noqa: E402
import session_start  # noqa: E402

import presence  # noqa: E402  (watcher dir already on sys.path)


_ORIG_MENUBAR_SPAWN = core._menubar_spawn


def setUpModule():
    core._menubar_spawn = lambda cmd: None


def tearDownModule():
    core._menubar_spawn = _ORIG_MENUBAR_SPAWN


class TestTz(unittest.TestCase):
    def test_quiet_non_wrapping(self):
        self.assertTrue(tzutil.in_quiet_hours(10, 9, 18))
        self.assertFalse(tzutil.in_quiet_hours(8, 9, 18))
        self.assertTrue(tzutil.in_quiet_hours(9, 9, 18))    # start inclusive
        self.assertFalse(tzutil.in_quiet_hours(18, 9, 18))  # end exclusive

    def test_quiet_wrapping(self):
        self.assertTrue(tzutil.in_quiet_hours(23, 21, 8))
        self.assertTrue(tzutil.in_quiet_hours(2, 21, 8))
        self.assertFalse(tzutil.in_quiet_hours(12, 21, 8))
        self.assertTrue(tzutil.in_quiet_hours(21, 21, 8))   # start inclusive
        self.assertFalse(tzutil.in_quiet_hours(8, 21, 8))   # end exclusive

    def test_quiet_degenerate(self):
        self.assertFalse(tzutil.in_quiet_hours(5, 0, 0))

    def test_working_hours(self):
        self.assertTrue(tzutil.is_working_hours(9))
        self.assertTrue(tzutil.is_working_hours(17))
        self.assertFalse(tzutil.is_working_hours(18))
        self.assertFalse(tzutil.is_working_hours(8))
        self.assertFalse(tzutil.is_working_hours(22))

    def test_country_tz(self):
        self.assertEqual(tzutil.country_to_timezone("AE"), "Asia/Dubai")
        self.assertEqual(tzutil.country_to_timezone("eg"), "Africa/Cairo")
        self.assertIsNone(tzutil.country_to_timezone("US"))   # multi-tz omitted
        self.assertIsNone(tzutil.country_to_timezone(None))
        self.assertIsNone(tzutil.country_to_timezone(""))


class TestDecay(unittest.TestCase):
    def test_recency_and_frequency(self):
        with tempfile.TemporaryDirectory() as d:
            md = Path(d)
            (md / "recent.md").write_text("x")
            old = md / "old.md"
            old.write_text("y")
            old_t = time.time() - 60 * 60 * 24 * 30  # 30 days ago
            os.utime(old, (old_t, old_t))
            (md / "MEMORY.md").write_text("index")

            w = decay.compute_weights(md, {})
            self.assertIn("recent.md", w)
            self.assertIn("old.md", w)
            self.assertNotIn("MEMORY.md", w)  # index file excluded
            # recency: recently touched scores higher (less negative)
            self.assertGreater(w["recent.md"]["score"], w["old.md"]["score"])
            # frequency: more accesses lifts the score
            prior = {"recent.md": {"accesses": 10, "last_seen": w["recent.md"]["last_seen"]}}
            w2 = decay.compute_weights(md, prior)
            self.assertGreater(w2["recent.md"]["score"], w["recent.md"]["score"])

    def test_nothing_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.md"
            f.write_text("data")
            decay.compute_weights(Path(d), {})
            self.assertTrue(f.exists())  # decay never deletes

    def test_missing_dir(self):
        self.assertEqual(decay.compute_weights(Path("/nonexistent/xyz"), {}), {})


class TestCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._orig = (core.DATA, core.COMMITMENTS, core.LEDGER, core.BIRTH, core.WEIGHTS)
        core.DATA = d
        core.COMMITMENTS = d / "commitments.json"
        core.LEDGER = d / "session-ledger.json"
        core.BIRTH = d / "birth.json"
        core.WEIGHTS = d / "memory-weights.json"

    def tearDown(self):
        (core.DATA, core.COMMITMENTS, core.LEDGER, core.BIRTH, core.WEIGHTS) = self._orig
        self.tmp.cleanup()

    def test_add_commitment_stores_weight(self):
        rec = core.add_commitment("q?", "+0m", kind="awaiting-reply", weight="high")
        self.assertEqual(rec["weight"], "high")
        self.assertNotIn("weight",
                         core.add_commitment("q2?", "+0m", kind="awaiting-reply",
                                             weight="normal"))
        self.assertNotIn("weight",
                         core.add_commitment("q3?", "+0m", kind="awaiting-reply"))

    def test_add_commitment_stores_policy_fields(self):
        rec = core.add_commitment("drop col?", "+0m", kind="awaiting-reply",
                                  confidence=0.9, irreversible=True,
                                  default_action="back up then halt")
        self.assertEqual(rec["confidence"], 0.9)
        self.assertTrue(rec["irreversible"])
        self.assertEqual(rec["default_action"], "back up then halt")
        bare = core.add_commitment("q?", "+0m", kind="awaiting-reply")
        for k in ("confidence", "irreversible", "default_action"):
            self.assertNotIn(k, bare)

    def test_add_commitment_stores_rungs(self):
        rec = core.add_commitment("q?", "+0m", kind="awaiting-reply",
                                  rungs=["knock one", "knock two", "final call"])
        self.assertEqual(rec["rungs"], ["knock one", "knock two", "final call"])
        self.assertNotIn("rungs",
                         core.add_commitment("q2?", "+0m", kind="awaiting-reply"))

    # --- birth ---
    def test_birth_written_once(self):
        b1 = core.get_or_create_birth()
        b2 = core.get_or_create_birth()
        self.assertEqual(b1["created_at"], b2["created_at"])

    # --- due dates ---
    def test_parse_due_date_only_is_utc(self):
        due = core.parse_due("2026-06-26")
        self.assertIsNotNone(due)
        self.assertEqual(due.tzinfo, timezone.utc)

    def test_parse_due_invalid(self):
        self.assertIsNone(core.parse_due("not-a-date"))
        self.assertIsNone(core.parse_due(None))
        self.assertIsNone(core.parse_due(""))

    def test_parse_due_relative_minutes(self):
        before = core.now_utc()
        due = core.parse_due("+10m")
        self.assertIsNotNone(due)
        secs = (due - before).total_seconds()
        self.assertTrue(595 <= secs <= 605, f"expected ~600s, got {secs}")

    def test_parse_due_relative_hours(self):
        before = core.now_utc()
        due = core.parse_due("+2h")
        secs = (due - before).total_seconds()
        self.assertTrue(7195 <= secs <= 7205, f"expected ~7200s, got {secs}")

    def test_parse_due_relative_invalid(self):
        self.assertIsNone(core.parse_due("+m"))
        self.assertIsNone(core.parse_due("+5d"))   # only m/h supported
        self.assertIsNone(core.parse_due("10m"))   # must start with +
        self.assertIsNone(core.parse_due("+99999999999999m"))  # overflows timedelta

    def test_due_horizon_boundary(self):
        now = core.now_utc()
        items = [
            {"id": "a", "created_at": now.isoformat(),
             "due_at": (now - timedelta(hours=2)).isoformat(),
             "text": "overdue", "source": "t", "status": "open"},
            {"id": "b", "created_at": now.isoformat(),
             "due_at": (now + timedelta(hours=5)).isoformat(),
             "text": "soon", "source": "t", "status": "open"},
            {"id": "c", "created_at": now.isoformat(),
             "due_at": (now + timedelta(hours=48)).isoformat(),
             "text": "far", "source": "t", "status": "open"},
            {"id": "d", "created_at": now.isoformat(),
             "due_at": (now - timedelta(hours=2)).isoformat(),
             "text": "done", "source": "t", "status": "done"},
            {"id": "e", "created_at": now.isoformat(),
             "due_at": None, "text": "nodate", "source": "t", "status": "open"},
        ]
        core.write_json(core.COMMITMENTS, items)
        ids = [c["id"] for c, _ in core.due_commitments(24)]
        self.assertIn("a", ids)        # overdue surfaces
        self.assertIn("b", ids)        # within horizon
        self.assertNotIn("c", ids)     # beyond horizon
        self.assertNotIn("d", ids)     # not open
        self.assertNotIn("e", ids)     # no due date
        self.assertEqual(ids[0], "a")  # sorted soonest/overdue first

    # --- humanizers ---
    def test_humanize_age(self):
        self.assertEqual(core.humanize_age(core.now_utc().isoformat()), "born today")
        past = (core.now_utc() - timedelta(days=5)).isoformat()
        self.assertIn("5d", core.humanize_age(past))

    def test_humanize_delta(self):
        self.assertEqual(core.humanize_delta(30), "30s")
        self.assertEqual(core.humanize_delta(120), "2m")
        self.assertTrue(core.humanize_delta(3600).startswith("1h"))
        self.assertIn("1d", core.humanize_delta(90000))  # 25h -> 1d 1h

    # --- token parser ---
    def test_token_parser_sums_output(self):
        tp = Path(self.tmp.name) / "t.jsonl"
        tp.write_text("\n".join([
            json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 100, "input_tokens": 50}}}),
            json.dumps({"type": "user", "message": {"content": "hi"}}),
            json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 56}}}),
            "not json at all",
        ]))
        self.assertEqual(core.best_effort_tokens(str(tp)), 156)

    def test_token_parser_missing_or_empty(self):
        self.assertIsNone(core.best_effort_tokens(""))
        self.assertIsNone(core.best_effort_tokens(None))
        self.assertIsNone(core.best_effort_tokens(str(Path(self.tmp.name) / "nope.jsonl")))

    def test_token_parser_no_usage_is_none(self):
        tp = Path(self.tmp.name) / "nu.jsonl"
        tp.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}))
        self.assertIsNone(core.best_effort_tokens(str(tp)))

    # --- ledger ---
    def test_session_start_idempotent(self):
        _row, prev, created = core.start_session("s1", "startup", None)
        self.assertTrue(created)
        self.assertIsNone(prev)
        _row2, _prev2, created2 = core.start_session("s1", "resume", None)
        self.assertFalse(created2)
        self.assertEqual(len(core.load_ledger()), 1)

    def test_finalize_previous_on_new_session(self):
        tp = Path(self.tmp.name) / "prev.jsonl"
        tp.write_text(json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 42}}}))
        core.start_session("s1", "startup", str(tp))
        core.start_session("s2", "startup", None)  # boots -> finalizes s1
        rows = {r["session_id"]: r for r in core.load_ledger()}
        self.assertEqual(rows["s1"]["tokens"], 42)
        self.assertIsNotNone(rows["s1"]["end_ts"])
        self.assertIsNotNone(rows["s1"]["wall_ms"])
        self.assertIsNone(rows["s2"]["tokens"])  # current session not yet finalized

    # --- commitments (with awaiting-reply kind) ---
    def test_add_commitment_awaiting_kind(self):
        rec = core.add_commitment("q?", "+10m", "agent-blocked",
                                  kind="awaiting-reply", session_id="sess-1")
        self.assertEqual(rec["kind"], "awaiting-reply")
        self.assertEqual(rec["session_id"], "sess-1")
        self.assertEqual(rec["status"], "open")

    def test_add_commitment_plain_shape_unchanged(self):
        rec = core.add_commitment("plain thing", "+10m")
        self.assertNotIn("kind", rec)
        self.assertNotIn("session_id", rec)

    def test_close_awaiting_closes_all_and_only_awaiting(self):
        core.add_commitment("q1?", "+10m", kind="awaiting-reply")
        core.add_commitment("q2?", "+10m", kind="awaiting-reply")
        plain = core.add_commitment("plain", "+10m")
        n = core.close_awaiting()
        self.assertEqual(n, 2)
        by_id = {c["id"]: c for c in core.load_commitments()}
        self.assertEqual(by_id[plain["id"]]["status"], "open")
        statuses = {c["status"] for c in core.load_commitments() if c.get("kind") == "awaiting-reply"}
        self.assertEqual(statuses, {"answered"})
        self.assertEqual(core.close_awaiting(), 0)  # idempotent, nothing left

    # --- read_json corruption quarantine ---
    def test_read_json_quarantines_corruption(self):
        garbage = b"{not valid json at all"
        core.COMMITMENTS.write_bytes(garbage)
        self.assertEqual(core.load_commitments(), [])  # default, not a crash
        quarantined = list(core.DATA.glob("commitments.json.corrupt-*"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), garbage)
        # the original path is gone (renamed away), so a fresh write starts clean
        self.assertFalse(core.COMMITMENTS.exists())
        core.add_commitment("fresh", "+10m")
        # next write must not touch/destroy the quarantine file
        still_there = list(core.DATA.glob("commitments.json.corrupt-*"))
        self.assertEqual(still_there, quarantined)
        self.assertEqual(still_there[0].read_bytes(), garbage)

    def test_read_json_missing_file_no_quarantine(self):
        self.assertFalse(core.COMMITMENTS.exists())
        self.assertEqual(core.load_commitments(), [])
        self.assertEqual(list(core.DATA.glob("commitments.json.corrupt-*")), [])

    def test_read_json_toctou_healed_file_not_quarantined(self):
        # Narrow race: an unlocked reader parses garbage, then a locked
        # writer replaces the path with GOOD bytes before the reader's
        # quarantine rename lands. The rename would move a good file away.
        # read_json must re-check the file's current bytes and only
        # quarantine what is STILL corrupt.
        target = core.DATA / "healed.json"
        core.DATA.mkdir(parents=True, exist_ok=True)
        target.write_text('{"ok": true}', encoding="utf-8")

        class RacyPath:
            """First read_text returns garbage (the stale read); every later
            access proxies to the real, now-healthy file."""
            def __init__(self, real):
                object.__setattr__(self, "_real", real)
                object.__setattr__(self, "_reads", 0)

            def read_text(self, *a, **k):
                object.__setattr__(self, "_reads", self._reads + 1)
                if self._reads == 1:
                    return "{garbage not json"
                return self._real.read_text(*a, **k)

            def __getattr__(self, name):
                return getattr(self._real, name)

        result = core.read_json(RacyPath(target), "DEFAULT")
        self.assertEqual(result, "DEFAULT")  # that call still degrades
        # ...but the healed file must NOT have been quarantined away
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")),
                         {"ok": True})
        self.assertEqual(list(core.DATA.glob("healed.json.corrupt-*")), [])

    # --- write_json atomicity ---
    def test_write_json_sequential_overwrites_cleanly(self):
        target = core.DATA / "seq.json"
        core.write_json(target, {"a": 1})
        core.write_json(target, {"a": 2})
        self.assertEqual(core.read_json(target, None), {"a": 2})
        self.assertEqual(list(core.DATA.glob("*.tmp")), [])  # no stray tmp

    def test_write_json_concurrent_writers_never_corrupt(self):
        target = core.DATA / "concurrent.json"
        errors = []

        def writer(tag):
            for i in range(200):
                try:
                    core.write_json(target, {"tag": tag, "i": i})
                except Exception as e:  # pragma: no cover - failure path
                    errors.append(e)

        t1 = threading.Thread(target=writer, args=("a",))
        t2 = threading.Thread(target=writer, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(errors, [])
        # Whatever the last write was, the file must always parse as valid
        # JSON -- never truncated or interleaved between the two writers.
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn(data.get("tag"), ("a", "b"))
        self.assertEqual(list(core.DATA.glob("*.tmp")), [])  # no strays left

    # --- ledger lock: load->mutate->write serialization ---
    def test_ledger_lock_no_lost_update_between_add_and_close(self):
        core.add_commitment("seed", "+10m", kind="awaiting-reply")  # starts OPEN

        # Widen the load->mutate->write window deterministically: a fast local
        # tempdir write is too quick for the GIL to reliably interleave two
        # threads, so without this the race would be flaky-invisible rather
        # than proven. The sleep happens inside load_commitments, i.e. INSIDE
        # whatever critical section the fix wraps -- so a correct lock must
        # serialize across it (blocking thread) while a missing lock lets the
        # two loads race and one writer's update clobbers the other's.
        orig_load = core.load_commitments

        def slow_load():
            items = orig_load()
            time.sleep(0.003)
            return items

        errors = []

        def adder():
            for i in range(50):
                try:
                    core.add_commitment(f"q{i}", "+10m", kind="awaiting-reply")
                except Exception as e:  # pragma: no cover - failure path
                    errors.append(e)

        def closer():
            for _ in range(50):
                try:
                    core.close_awaiting()
                except Exception as e:  # pragma: no cover - failure path
                    errors.append(e)

        core.load_commitments = slow_load
        try:
            t1 = threading.Thread(target=adder)
            t2 = threading.Thread(target=closer)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        finally:
            core.load_commitments = orig_load

        self.assertEqual(errors, [])
        items = core.load_commitments()
        self.assertEqual(len(items), 51)  # seed + 50 adds, none lost
        seed_after = next(c for c in items if c["text"] == "seed")
        # the seeded item must never have regressed back to "open" because
        # an add_commitment's stale in-memory snapshot clobbered close's write
        self.assertEqual(seed_after["status"], "answered")


class TestWatcherLadder(unittest.TestCase):
    def _c(self, minutes_past_due, kind="awaiting-reply"):
        now = core.now_utc()
        c = {"id": "x1", "created_at": now.isoformat(),
             "due_at": (now - timedelta(minutes=minutes_past_due)).isoformat(),
             "text": "q?", "source": "t", "status": "open"}
        if kind != "plain":
            c["kind"] = kind
        return c, now

    def test_migrate_entry(self):
        self.assertEqual(watcher.migrate_entry("2026-01-01T00:00:00+00:00"),
                         {"count": 1, "last": "2026-01-01T00:00:00+00:00",
                          "unseen_s": 0.0, "here_s": 0.0, "last_cycle": None,
                          "ripe_here_cycles": 0})
        d = {"count": 2, "last": "x", "unseen_s": 5.0, "here_s": 1.0,
             "last_cycle": "t", "ripe_here_cycles": 4}
        self.assertEqual(watcher.migrate_entry(d), d)
        self.assertEqual(watcher.migrate_entry(None),
                         {"count": 0, "last": None, "unseen_s": 0.0,
                          "here_s": 0.0, "last_cycle": None,
                          "ripe_here_cycles": 0})

    def test_migrate_entry_repairs_bad_ripe_here_cycles(self):
        for bad in ("3", None, 2.5, [], True):
            e = watcher.migrate_entry({"count": 1, "last": "x",
                                       "ripe_here_cycles": bad})
            self.assertEqual(e["ripe_here_cycles"], 0, f"bad={bad!r}")

    def test_rung_selection_by_elapsed(self):
        # Old RUNG_OFFSETS-based (wall-elapsed-since-due) ladder is now the
        # unseen-time ladder (UNSEEN_OFFSETS); pin the same "highest ripe
        # rung" behavior via equivalent unseen_s on the entry.
        for unseen, expected_rung in [(600.0, 1), (1199.0, 1), (1200.0, 2),
                                      (2999.0, 2), (3000.0, 3), (5000.0, 3)]:
            c, now = self._c(0)
            entry = {"count": 0, "last": None, "unseen_s": unseen,
                     "here_s": 0.0, "last_cycle": None}
            hit = watcher.pending_ping(c, entry, now, "away", None)
            self.assertIsNotNone(hit, f"at unseen={unseen}")
            self.assertEqual(hit[0], expected_rung, f"at unseen={unseen}")

    def test_pick_deterministic(self):
        pool = watcher.RUNG_POOLS[0]
        m1 = watcher.pick_message("a1b2c3d4", pool, text="q?")
        m2 = watcher.pick_message("a1b2c3d4", pool, text="q?")
        self.assertEqual(m1, m2)  # same id -> same line, every time
        formatted = {t.format(owner=watcher.owner_name(), text="q?") for t in pool}
        other = watcher.pick_message("ffffffff", pool, text="q?")
        self.assertIn(m1, formatted)
        self.assertIn(other, formatted)

    def test_rung3_always_states_autonomy(self):
        consequences = ("judgment", "I take it from here", "my call now",
                        "deciding without you")
        for template in watcher.RUNG_POOLS[2]:
            msg = template.format(owner="Ada", text="q?")
            self.assertTrue(any(c in msg for c in consequences), msg)

    def test_owner_in_pool_entries(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = core.DATA
            core.DATA = dd
            try:
                (dd / "owner.txt").write_text("Ada", encoding="utf-8")
                # every template in every pool must format cleanly with only
                # {owner}/{text} placeholders (no KeyError from stray fields)
                for pool in watcher.RUNG_POOLS + (watcher.PLAIN_POOL,):
                    for template in pool:
                        template.format(owner="Ada", text="q?")
                # and pick_message actually threads the owner through
                msg = watcher.pick_message("00000000", watcher.RUNG_POOLS[0], text="q?")
                self.assertIn("Ada", msg)
            finally:
                core.DATA = orig

    def test_rung_one_uses_owner_name(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = core.DATA
            core.DATA = dd
            try:
                (dd / "owner.txt").write_text("Ada", encoding="utf-8")
                c, now = self._c(0)
                c["id"] = "00000000"  # int(id, 16) % 5 == 0 -> classic entry
                entry = {"count": 0, "last": None, "unseen_s": 600.0,
                         "here_s": 0.0, "last_cycle": None}
                hit = watcher.pending_ping(c, entry, now, "away", None)
                self.assertTrue(hit[1].startswith("Ada — I'm blocked on:"))
            finally:
                core.DATA = orig

    def test_elsewhere_pools_have_app_and_rung3_consequence(self):
        for pool in watcher.ELSEWHERE_POOLS:
            for entry in pool:
                s = entry.format(owner="O", text="T", app="Figma")
                self.assertIn("T", s)
        for entry in watcher.ELSEWHERE_POOLS[2]:
            s = entry.format(owner="O", text="T", app="Figma")
            self.assertTrue(any(k in s for k in
                ("judgment", "deciding without you", "my call now",
                 "take it from here", "standing down")), s)

    def test_return_pool_formats(self):
        for entry in watcher.RETURN_POOL:
            s = entry.format(owner="O", text="T", away_m=25)
            self.assertIn("T", s)

    def test_pick_message_kwargs_and_fallback(self):
        msg = watcher.pick_message("00000000", watcher.ELSEWHERE_POOLS[0],
                                   text="T", app="Figma")
        self.assertIn("T", msg)
        # missing 'app' → format fails → falls back to pool[0]; pool[0] also
        # needs app → final fallback: the text itself
        bad = watcher.pick_message("00000000", ("{never_defined}: {text}",),
                                   text="T")
        self.assertEqual(bad, "T")

    def test_highest_ripe_rung_collapse(self):
        # overnight: all three rungs ripened while quiet; exactly ONE ping (rung 3)
        c, now = self._c(300)
        entry = {"count": 0, "last": None, "unseen_s": 3000.0,
                 "here_s": 0.0, "last_cycle": None}
        hit = watcher.pending_ping(c, entry, now, "away", None)
        self.assertEqual(hit[0], 3)
        # and after it's recorded, nothing more ever fires
        self.assertIsNone(watcher.pending_ping(
            c, {"count": 3, "last": "x", "unseen_s": 3000.0, "here_s": 0.0,
                "last_cycle": None}, now, "away", None))

    def test_already_pinged_rung_stays_silent(self):
        c, now = self._c(5)  # only rung 1 ripe
        entry = {"count": 1, "last": "x", "unseen_s": 600.0,
                 "here_s": 0.0, "last_cycle": None}
        self.assertIsNone(watcher.pending_ping(c, entry, now, "away", None))

    def test_plain_commitment_caps_at_one(self):
        c, now = self._c(300, kind="plain")
        hit = watcher.pending_ping(c, {"count": 0, "last": None}, now, "away", None)
        expected = watcher.pick_message(c["id"], watcher.PLAIN_POOL, text="q?")
        self.assertEqual(hit, (1, expected))
        self.assertIsNone(watcher.pending_ping(
            c, {"count": 1, "last": "x"}, now, "away", None))

    def test_run_cycle_fires_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.sample_assertions_raw,
                    watcher.sample_screen_locked, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            orig_notify = watcher.desktop_notify
            orig_spawn = watcher._spawn
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher._spawn = lambda cmd: None  # keep the suite AUDIBLY silent
            # Presence unknown -> full legacy degrade (state None): pins the
            # pre-absence-clock v1.5 wall-elapsed-since-due behavior exactly.
            watcher.sample_presence = lambda: {"state": None, "idle_s": None,
                                               "front_app": None}
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_screen_locked = lambda: False  # no live sensor in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                watcher.run_cycle(force=True)
                expected = watcher.pick_message(rec["id"], watcher.RUNG_POOLS[0], text="q?")
                self.assertEqual(fired, [expected])
                saved = core.read_json(watcher.NOTIFIED, {})
                (cid,) = saved.keys()
                self.assertEqual(saved[cid]["count"], 1)
                watcher.run_cycle(force=True)   # same cycle again: silent
                self.assertEqual(len(fired), 1)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.sample_assertions_raw,
                 watcher.sample_screen_locked, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds) = orig
                watcher.desktop_notify = orig_notify
                watcher._spawn = orig_spawn

    def test_run_cycle_skips_corrupted_row(self):
        # A hand-corrupted commitment row (missing "id") and a hand-corrupted
        # notified.json entry (non-int "count") must not crash the cycle; the
        # one valid awaiting-reply item due now still fires and persists.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.sample_assertions_raw,
                    watcher.sample_screen_locked, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            orig_notify = watcher.desktop_notify
            orig_spawn = watcher._spawn
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher._spawn = lambda cmd: None  # keep the suite AUDIBLY silent
            # Presence unknown -> full legacy degrade (state None), same as
            # test_run_cycle_fires_and_persists: deterministic, no live sensors.
            watcher.sample_presence = lambda: {"state": None, "idle_s": None,
                                               "front_app": None}
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_screen_locked = lambda: False  # no live sensor in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                now = core.now_utc()
                malformed = {
                    "created_at": now.isoformat(),
                    "due_at": now.isoformat(),
                    "text": "corrupt: missing id",
                    "source": "t",
                    "status": "open",
                    "kind": "awaiting-reply",
                }  # no "id" -> KeyError on notified.get(c["id"]) if unguarded
                core.write_json(core.COMMITMENTS, [malformed])
                valid = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED,
                                 {"some-other-id": {"count": "not-an-int", "last": None}})
                watcher.run_cycle(force=True)
                expected = watcher.pick_message(valid["id"], watcher.RUNG_POOLS[0], text="q?")
                self.assertEqual(fired, [expected])
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[valid["id"]]["count"], 1)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.sample_assertions_raw,
                 watcher.sample_screen_locked, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds) = orig
                watcher.desktop_notify = orig_notify
                watcher._spawn = orig_spawn


class TestAbsenceClock(unittest.TestCase):
    def setUp(self):
        # The suite must never play real audio on the owner's speakers: any
        # test in this class can reach chime()/speak_final() via run_cycle.
        # Tests that want to OBSERVE _spawn layer their own capture stub on
        # top of this one and restore it; tearDown restores the real seam.
        self._orig_spawn = watcher._spawn
        watcher._spawn = lambda cmd: None
        # Never let a test hit the real screen-lock sensor (subprocess call).
        self._orig_screen_locked = watcher.sample_screen_locked
        watcher.sample_screen_locked = lambda: False

    def tearDown(self):
        watcher._spawn = self._orig_spawn
        watcher.sample_screen_locked = self._orig_screen_locked

    def _c(self, minutes_since_ask, kind="awaiting-reply"):
        now = core.now_utc()
        created = now - timedelta(minutes=minutes_since_ask)
        c = {"id": "a0000001", "created_at": created.isoformat(),
             "due_at": (created + timedelta(minutes=10)).isoformat(),
             "text": "q?", "source": "t", "status": "open"}
        if kind != "plain":
            c["kind"] = kind
        return c, now

    def _entry(self, unseen=0.0, here=0.0, count=0):
        return {"count": count, "last": None, "unseen_s": unseen,
                "here_s": here, "last_cycle": None}

    def test_high_tier_faster_offsets(self):
        c, now = self._c(30)          # 30 wall-min < high 40-min ceiling
        c["weight"] = "high"
        for unseen, expected in ((299, 0), (300, 1), (600, 2), (1200, 3)):
            self.assertEqual(
                watcher.ripe_rung(c, self._entry(unseen=unseen), now, "away"),
                expected, f"high unseen={unseen}")

    def test_low_tier_two_rungs_and_slower(self):
        c, now = self._c(60)
        c["weight"] = "low"
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=1799), now, "away"), 0)
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=1800), now, "away"), 1)
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=5400), now, "away"), 2)
        self.assertEqual(watcher.ripe_rung(c, self._entry(unseen=99999), now, "away"), 2)

    def test_high_tier_wall_ceiling_at_40min(self):
        c, now = self._c(41)
        c["weight"] = "high"
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, "here"), 3)

    def test_normal_tier_unchanged_regression(self):
        c, now = self._c(60)
        for unseen, expected in ((599, 0), (600, 1), (1200, 2), (3000, 3)):
            self.assertEqual(
                watcher.ripe_rung(c, self._entry(unseen=unseen), now, "away"),
                expected, f"normal unseen={unseen}")

    def test_high_tier_message_states_no_false_minutes(self):
        c, now = self._c(30)
        c["weight"] = "high"
        hit = watcher.pending_ping(c, self._entry(unseen=1200), now, "away", None)
        self.assertEqual(hit[0], 3)
        self.assertNotIn("50 min", hit[1])       # normal-pool lie must not appear
        self.assertNotIn("20m", hit[1])
        self.assertIn("standing down", hit[1])    # terminal contract present

    def test_low_terminal_rung_states_contract(self):
        c, now = self._c(60)
        c["weight"] = "low"
        hit = watcher.pending_ping(c, self._entry(unseen=5400), now, "away", None)
        self.assertEqual(hit[0], 2)               # low max rung
        self.assertIn("standing down", hit[1])    # contract on the LAST rung

    def test_terminal_rung_states_specific_default_action(self):
        c, now = self._c(60)
        c["weight"] = "high"
        c["default_action"] = "back up then halt"
        hit = watcher.pending_ping(c, self._entry(unseen=1200), now, "away", None)
        self.assertIn("back up then halt", hit[1])

    def test_normal_tier_copy_unchanged(self):
        c, now = self._c(60)                      # normal, no default_action
        hit = watcher.pending_ping(c, self._entry(unseen=3000), now, "away", None)
        self.assertEqual(hit[0], 3)
        # normal tier still routes through the existing numbered rung-3 pool;
        # assert on the autonomy-consequence set (pick_message picks by id hash)
        self.assertTrue(any(k in hit[1] for k in (
            "proceeding on my judgment", "park it", "my call now", "deciding without you")))

    def test_high_tier_speaks_final_without_speak_txt(self):
        spoken = []
        orig_spawn, orig_data = watcher._spawn, core.DATA
        watcher._spawn = lambda cmd: spoken.append(cmd)
        try:
            with tempfile.TemporaryDirectory() as d:
                core.DATA = Path(d)   # isolate: guarantees no data/speak.txt
                watcher.speak_final("final", audible=True, force=False)
                self.assertEqual(spoken, [])                   # no speak.txt → silent
                watcher.speak_final("final", audible=True, force=True)
                self.assertTrue(any("/usr/bin/say" in c for c in spoken))
                spoken.clear()
                watcher.speak_final("final", audible=False, force=True)
                self.assertEqual(spoken, [])                   # courtesy still wins
        finally:
            watcher._spawn, core.DATA = orig_spawn, orig_data

    def test_pending_ping_uses_stored_rungs(self):
        c, now = self._c(60)
        c["rungs"] = ["my rung one", "my rung two", "my final"]
        hit = watcher.pending_ping(c, self._entry(unseen=600), now, "away", None)
        self.assertEqual(hit[0], 1)
        self.assertIn("my rung one", hit[1])

    def test_stored_final_rung_appends_default_action(self):
        c, now = self._c(60)
        c["rungs"] = ["r1", "r2", "final call"]
        c["default_action"] = "back up then halt"
        hit = watcher.pending_ping(c, self._entry(unseen=3000), now, "away", None)
        self.assertEqual(hit[0], 3)
        self.assertIn("back up then halt", hit[1])

    def test_no_rungs_falls_back_to_pool(self):
        c, now = self._c(60)
        hit = watcher.pending_ping(c, self._entry(unseen=600), now, "away", None)
        self.assertNotIn("my rung one", hit[1])

    def test_run_cycle_refreshes_menubar_on_fire(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                    watcher.sample_net, watcher.sample_recent_fs,
                    watcher.sample_builds, core._menubar_spawn)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            refreshed = []
            core._menubar_spawn = lambda cmd: refreshed.append(cmd)
            watcher.desktop_notify = lambda t, m: True
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 9999.0,
                                               "front_app": None}
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_net = lambda: None
            watcher.sample_recent_fs = lambda: []
            watcher.sample_builds = lambda: {}
            try:
                core.add_commitment("q?", "+0m", kind="awaiting-reply")
                watcher.run_cycle(force=True)
                self.assertTrue(refreshed)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds, core._menubar_spawn) = orig

    def test_accrue_by_state(self):
        c, now = self._c(30)
        for state, unseen, here in (("away", 600.0, 0.0),
                                    ("elsewhere", 300.0, 0.0),
                                    ("here", 0.0, 600.0),
                                    ("present", 0.0, 600.0)):
            e = self._entry()
            e["last_cycle"] = (now - timedelta(seconds=600)).isoformat()
            watcher.accrue(e, state, now, core.parse_iso(c["created_at"]))
            self.assertAlmostEqual(e["unseen_s"], unseen, delta=1.0, msg=state)
            self.assertAlmostEqual(e["here_s"], here, delta=1.0, msg=state)
            self.assertEqual(e["last_cycle"], now.isoformat())

    def test_accrue_ripe_here_credits_one_cycle(self):
        c, now = self._c(30)
        e = self._entry()
        e["last_cycle"] = (now - timedelta(seconds=600)).isoformat()
        watcher.accrue(e, "here", now, core.parse_iso(c["created_at"]),
                       ripe=True)
        self.assertEqual(e["ripe_here_cycles"], 1)

    def test_accrue_ripe_credit_is_here_only(self):
        # "present" (screen-sharing/how-busy ambiguity) never counts toward
        # informed silence; neither do away/elsewhere. here without ripeness
        # doesn't count either.
        c, now = self._c(30)
        for state, ripe in (("present", True), ("away", True),
                            ("elsewhere", True), ("here", False)):
            e = self._entry()
            e["last_cycle"] = (now - timedelta(seconds=600)).isoformat()
            watcher.accrue(e, state, now, core.parse_iso(c["created_at"]),
                           ripe=ripe)
            self.assertEqual(e.get("ripe_here_cycles", 0), 0,
                             f"state={state} ripe={ripe}")

    def test_accrue_sleep_gap_never_credits_ripe_here(self):
        # A 6x-cycle gap means the machine slept: "here" at wake must not
        # retroactively read as present-while-ripe.
        c, now = self._c(60)
        e = self._entry()
        e["last_cycle"] = (now - timedelta(seconds=3600)).isoformat()
        watcher.accrue(e, "here", now, core.parse_iso(c["created_at"]),
                       ripe=True)
        self.assertEqual(e.get("ripe_here_cycles", 0), 0)

    def test_accrue_sleep_gap_counts_away(self):
        c, now = self._c(60)
        e = self._entry()
        e["last_cycle"] = (now - timedelta(seconds=3600)).isoformat()  # 6x cycle
        watcher.accrue(e, "here", now, core.parse_iso(c["created_at"]))
        self.assertAlmostEqual(e["unseen_s"], 3600.0, delta=1.0)  # slept -> away

    def test_ripe_on_unseen_thresholds(self):
        c, now = self._c(60)
        for unseen, expected in ((0, 0), (599, 0), (600, 1), (1199, 1),
                                 (1200, 2), (2999, 2), (3000, 3)):
            self.assertEqual(
                watcher.ripe_rung(c, self._entry(unseen=unseen), now, "away"),
                expected, f"unseen={unseen}")

    def test_wall_ceiling_forces_final_even_here(self):
        c, now = self._c(91)  # 91 wall-minutes since ask
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, "here"), 3)

    def test_legacy_degrade_matches_v15(self):
        c, now = self._c(60)  # due 50 min ago -> legacy rung 3 (2400s past due)
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, None), 3)
        c2, now2 = self._c(15)  # due 5 min ago -> legacy rung 1
        self.assertEqual(watcher.ripe_rung(c2, self._entry(), now2, None), 1)

    def test_state_none_with_accrual_history_ignores_wall_fallback(self):
        # Reviewer repro: state None used to always fall back to v1.5
        # wall-elapsed-since-due, even when the entry already has accrual
        # history (last_cycle set) -- a commitment sitting 50 wall-minutes
        # past due would falsely fire rung 3 on a single sensor blip, even
        # though its actual accrued unseen_s is nowhere near ripe.
        c, now = self._c(60)  # due 50 min ago
        entry_with_history = {"count": 0, "last": None, "unseen_s": 30.0,
                               "here_s": 0.0, "last_cycle": now.isoformat()}
        self.assertEqual(watcher.ripe_rung(c, entry_with_history, now, None), 0)
        # same shape but NO accrual history yet (fresh/pre-absence-clock
        # entry): legacy wall-elapsed path still applies, unchanged.
        entry_fresh = {"count": 0, "last": None, "unseen_s": 30.0,
                       "here_s": 0.0, "last_cycle": None}
        self.assertEqual(watcher.ripe_rung(c, entry_fresh, now, None), 3)

    def test_plain_unchanged(self):
        c, now = self._c(300, kind="plain")
        self.assertEqual(watcher.ripe_rung(c, self._entry(), now, "away"), 1)
        self.assertEqual(
            watcher.ripe_rung(c, self._entry(count=1), now, "away"), 0)

    def test_pending_ping_elsewhere_uses_app_pool(self):
        c, now = self._c(60)
        hit = watcher.pending_ping(c, self._entry(unseen=600), now,
                                   "elsewhere", "Figma")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], 1)
        self.assertIn("Figma", hit[1])

    def test_run_cycle_holds_while_here_and_accrues(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                    watcher.sample_net, watcher.sample_recent_fs,
                    watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "here",
                                               "idle_s": 1.0,
                                               "front_app": "Terminal"}
            # HERE with no ceiling never reaches the batch/watch path, but
            # stub defensively: never fire the real bounded watch in tests.
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])  # held while HERE
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertIn(rec["id"], saved)      # ...but accrual persisted
                self.assertIn("here_s", saved[rec["id"]])
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds) = orig

    def test_run_cycle_credits_ripe_here_cycles(self):
        # HERE with a ripe ask: the held cycle still counts toward informed
        # silence. A brand-new (unripe) ask must NOT be credited by the same
        # cycle — ripeness is judged on the clocks BEFORE this cycle's accrual.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                    watcher.sample_net, watcher.sample_recent_fs,
                    watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.desktop_notify = lambda t, m: True
            watcher.sample_presence = lambda: {"state": "here",
                                               "idle_s": 1.0,
                                               "front_app": "Terminal"}
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_net = lambda: None
            watcher.sample_recent_fs = lambda: []
            watcher.sample_builds = lambda: {}
            try:
                now = core.now_utc()
                ripe_c = core.add_commitment("ripe?", "+0m",
                                             kind="awaiting-reply")
                fresh_c = core.add_commitment("fresh?", "+0m",
                                              kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED, {ripe_c["id"]: {
                    "count": 1, "last": now.isoformat(),
                    "unseen_s": 600.0, "here_s": 0.0,
                    "last_cycle": (now - timedelta(seconds=600)).isoformat()}})
                watcher.run_cycle(force=True)
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[ripe_c["id"]]["ripe_here_cycles"], 1)
                self.assertEqual(
                    saved[fresh_c["id"]].get("ripe_here_cycles", 0), 0)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds) = orig

    def test_ceiling_fires_even_here_when_created_at_missing(self):
        # Reviewer repro: NO created_at, valid due_at 91 min past. ripe_rung's
        # ceiling basis falls back to due_at (rung 3), so run_cycle's HERE-hold
        # must use the same basis — otherwise the ping starves forever.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                    watcher.sample_net, watcher.sample_recent_fs,
                    watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "here",
                                               "idle_s": 1.0,
                                               "front_app": "Terminal"}
            # Ceiling-forced while HERE now runs the one-watch path for real;
            # stub it so the test doesn't block on real sensors/timing.
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                now = core.now_utc()
                c = {"id": "a0000001",
                     "due_at": (now - timedelta(minutes=91)).isoformat(),
                     "text": "q?", "source": "t", "status": "open",
                     "kind": "awaiting-reply"}  # NO created_at key
                core.write_json(core.COMMITMENTS, [c])
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)  # ceiling overrides HERE-hold
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[c["id"]]["count"], 3)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.wait_for_breakpoint, watcher.sample_assertions_raw,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds) = orig

    def test_run_cycle_fires_when_away_and_ripe(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher.sample_assertions_raw, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "away",
                                               "idle_s": 999.0,
                                               "front_app": None}
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 700.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 1)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.sample_assertions_raw, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds) = orig

    def test_record_presence_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, watcher.PRESENCE_FILE)
            core.DATA, watcher.PRESENCE_FILE = dd, dd / "presence.json"
            try:
                now = core.now_utc()
                prev = watcher.record_presence(
                    {"state": "away", "idle_s": 500.0, "front_app": None}, now)
                self.assertEqual(prev, {})            # first record ever
                prev2 = watcher.record_presence(
                    {"state": "away", "idle_s": 800.0, "front_app": None}, now)
                self.assertEqual(prev2.get("state"), "away")
                saved = core.read_json(watcher.PRESENCE_FILE, {})
                self.assertEqual(saved["state"], "away")
                self.assertEqual(saved["since"], now.isoformat())  # preserved
                prev3 = watcher.record_presence(
                    {"state": "here", "idle_s": 1.0, "front_app": "Terminal"},
                    now + timedelta(seconds=600))
                self.assertEqual(prev3.get("state"), "away")
                self.assertEqual(prev3.get("since"), now.isoformat())
                saved = core.read_json(watcher.PRESENCE_FILE, {})
                self.assertEqual(saved["state"], "here")
            finally:
                core.DATA, watcher.PRESENCE_FILE = orig

    def test_return_nudge_fires_once_and_consumes(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.PRESENCE_FILE, watcher.sample_presence,
                    watcher.desktop_notify, watcher.wait_for_breakpoint,
                    watcher.sample_assertions_raw, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.PRESENCE_FILE = dd / "presence.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            # Return-nudge `continue`s past the batch, and the second cycle's
            # hit is already consumed -- batch stays empty either way. Stub
            # defensively so a real bounded watch can never fire in tests.
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                past = (core.now_utc() - timedelta(seconds=1800)).isoformat()
                core.write_json(watcher.PRESENCE_FILE,
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 1300.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.sample_presence = lambda: {"state": "elsewhere",
                                                   "idle_s": 2.0,
                                                   "front_app": "Figma"}
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)          # exactly one nudge
                self.assertTrue(any(w in fired[0] for w in
                                    ("away", "absence", "gone")), fired[0])
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 2)  # consumed rung 2
                watcher.run_cycle(force=True)           # steady elsewhere now
                self.assertEqual(len(fired), 1)          # no double-knock
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.PRESENCE_FILE, watcher.sample_presence,
                 watcher.desktop_notify, watcher.wait_for_breakpoint,
                 watcher.sample_assertions_raw, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds) = orig

    def test_return_transition_keeps_plain_pool_voice(self):
        # Regression: a plain commitment due in the same cycle as an
        # away->back transition must keep its PLAIN_POOL message, never
        # borrow the RETURN_POOL "ripened in your absence" voice.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.PRESENCE_FILE, watcher.sample_presence,
                    watcher.desktop_notify, watcher.wait_for_breakpoint,
                    watcher.sample_assertions_raw, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.PRESENCE_FILE = dd / "presence.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            # This plain-kind item is NOT a return-nudge (kind check fails),
            # so it lands in the batch under state "elsewhere" -> the new
            # one-watch path runs for real. Stub it; otherwise this test
            # blocks for up to DEFER_MAX_S of real wall-clock time.
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                rec = core.add_commitment("q?", "+0m")  # plain kind
                past = (core.now_utc() - timedelta(seconds=1800)).isoformat()
                core.write_json(watcher.PRESENCE_FILE,
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                watcher.sample_presence = lambda: {"state": "elsewhere",
                                                   "idle_s": 2.0,
                                                   "front_app": "Figma"}
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
                self.assertEqual(
                    fired[0],
                    watcher.pick_message(rec["id"], watcher.PLAIN_POOL,
                                         text="q?"))
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.PRESENCE_FILE, watcher.sample_presence,
                 watcher.desktop_notify, watcher.wait_for_breakpoint,
                 watcher.sample_assertions_raw, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds) = orig

    def test_sample_presence_lock_dominates(self):
        # A locked screen overrides the idle/front-app heuristic: even with
        # recent input and a non-CLI app frontmost (normally "elsewhere"),
        # a locked screen reads "away". This is the fix for the lidless-Mac
        # false-present failure.
        p = watcher.presence
        orig = (watcher.sample_screen_locked, p.idle_seconds,
                p.front_app, p.cli_apps)
        try:
            p.idle_seconds = lambda: 2.0
            p.front_app = lambda: "Figma"
            p.cli_apps = lambda data_dir: ()
            watcher.sample_screen_locked = lambda: True
            snap = watcher.sample_presence()
            self.assertEqual(snap["state"], "away")
            self.assertIs(snap["locked"], True)
            watcher.sample_screen_locked = lambda: False
            self.assertEqual(watcher.sample_presence()["state"], "elsewhere")
        finally:
            (watcher.sample_screen_locked, p.idle_seconds,
             p.front_app, p.cli_apps) = orig

    def _run_cycle_isolated(self, dd, presence_since_s):
        """Drive one run_cycle with all live seams stubbed and a prior
        presence of away since `presence_since_s` ago, returning fresh into
        Figma. Returns the welcome_back.json dict ({} if never written)."""
        orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                watcher.PRESENCE_FILE, watcher.sample_presence,
                watcher.desktop_notify, watcher.wait_for_breakpoint,
                watcher.sample_assertions_raw, watcher.sample_net,
                watcher.sample_recent_fs, watcher.sample_builds)
        core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
        watcher.NOTIFIED = dd / "notified.json"
        watcher.PRESENCE_FILE = dd / "presence.json"
        watcher.desktop_notify = lambda t, m: True
        watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
        watcher.sample_assertions_raw = lambda: ""
        watcher.sample_net = lambda: None
        watcher.sample_recent_fs = lambda: []
        watcher.sample_builds = lambda: {}
        try:
            past = (core.now_utc()
                    - timedelta(seconds=presence_since_s)).isoformat()
            core.write_json(watcher.PRESENCE_FILE,
                            {"state": "away", "since": past,
                             "idle_s": 999.0, "front_app": None})
            watcher.sample_presence = lambda: {"state": "elsewhere",
                                               "idle_s": 2.0,
                                               "front_app": "Figma",
                                               "locked": False}
            watcher.run_cycle(force=True)
            return core.read_json(dd / "welcome_back.json", {})
        finally:
            (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
             watcher.PRESENCE_FILE, watcher.sample_presence,
             watcher.desktop_notify, watcher.wait_for_breakpoint,
             watcher.sample_assertions_raw, watcher.sample_net,
             watcher.sample_recent_fs, watcher.sample_builds) = orig

    def test_run_cycle_writes_welcome_back_on_real_return(self):
        with tempfile.TemporaryDirectory() as d:
            wb = self._run_cycle_isolated(Path(d), 1500)  # 25 min away
            self.assertFalse(wb.get("consumed"))
            self.assertGreaterEqual(wb.get("away_s"), 1200)
            self.assertEqual(wb.get("front_app"), "Figma")

    def test_run_cycle_no_welcome_back_below_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            wb = self._run_cycle_isolated(Path(d), 300)  # 5 min: a glance
            self.assertEqual(wb, {})  # never written

    def test_run_cycle_welcome_back_at_threshold_boundary(self):
        # away_s is measured from `since` (set 1200s ago) to a now that is
        # necessarily >= 1200s later, so exactly-at-threshold writes.
        with tempfile.TemporaryDirectory() as d:
            wb = self._run_cycle_isolated(Path(d), watcher.WELCOME_MIN_AWAY_S)
            self.assertFalse(wb.get("consumed"))
            self.assertGreaterEqual(wb.get("away_s"), watcher.WELCOME_MIN_AWAY_S)

    def test_chime_commands_and_state_modifiers(self):
        with tempfile.TemporaryDirectory() as d:
            orig = (core.DATA, watcher._spawn)
            core.DATA = Path(d)
            calls = []
            watcher._spawn = lambda cmd: calls.append(cmd)
            try:
                watcher.chime(1, "away")
                watcher.chime(3, "elsewhere")
                watcher.chime("return", "elsewhere")
                watcher.chime(2, "here")           # silent
                self.assertEqual(len(calls), 3)
                self.assertIn("Tink.aiff", calls[0][-1])
                self.assertAlmostEqual(float(calls[0][2]), 0.35)
                self.assertIn("Hero.aiff", calls[1][-1])
                self.assertAlmostEqual(float(calls[1][2]), 0.36)  # 0.6*0.6
                self.assertIn("Purr.aiff", calls[2][-1])
            finally:
                core.DATA, watcher._spawn = orig

    def test_chime_config_off_and_volume(self):
        with tempfile.TemporaryDirectory() as d:
            orig = (core.DATA, watcher._spawn)
            core.DATA = Path(d)
            calls = []
            watcher._spawn = lambda cmd: calls.append(cmd)
            try:
                (Path(d) / "chime.txt").write_text("off")
                watcher.chime(1, "away")
                self.assertEqual(calls, [])
                (Path(d) / "chime.txt").write_text("0.5")
                watcher.chime(2, "away")   # 0.5 base * 0.5 master = 0.25
                self.assertAlmostEqual(float(calls[0][2]), 0.25)
            finally:
                core.DATA, watcher._spawn = orig

    def test_speak_final_only_when_configured(self):
        with tempfile.TemporaryDirectory() as d:
            orig = (core.DATA, watcher._spawn)
            core.DATA = Path(d)
            calls = []
            watcher._spawn = lambda cmd: calls.append(cmd)
            try:
                watcher.speak_final("msg")          # no speak.txt -> silent
                self.assertEqual(calls, [])
                (Path(d) / "speak.txt").write_text("Samantha")
                watcher.speak_final("msg")
                self.assertEqual(calls[0][:3], ["/usr/bin/say", "-v", "Samantha"])
            finally:
                core.DATA, watcher._spawn = orig

    # --- Fix 5: delivery honesty bundle ---
    def test_chime_rung3_always_plays_even_here(self):
        # A rung-3/final fire is the autonomy-consequence moment; it must
        # never go silent just because presence says HERE.
        with tempfile.TemporaryDirectory() as d:
            orig = (core.DATA, watcher._spawn)
            core.DATA = Path(d)
            calls = []
            watcher._spawn = lambda cmd: calls.append(cmd)
            try:
                watcher.chime(3, "here")
                self.assertEqual(len(calls), 1)
                self.assertIn("afplay", calls[0][0])
                self.assertIn("Hero.aiff", calls[0][-1])
                calls.clear()
                # rungs 1-2 and the return chime keep their HERE silence
                watcher.chime(1, "here")
                watcher.chime(2, "here")
                watcher.chime("return", "here")
                self.assertEqual(calls, [])
            finally:
                core.DATA, watcher._spawn = orig

    def test_pending_ping_caps_long_message(self):
        c, now = self._c(300, kind="plain")
        c["text"] = "x" * 100_000
        hit = watcher.pending_ping(c, {"count": 0, "last": None}, now, "away", None)
        self.assertIsNotNone(hit)
        self.assertLessEqual(len(hit[1]), 301)

    def test_rung3_truncation_preserves_consequence_clause(self):
        # Truncation must eat the TEXT, never the autonomy-consequence tail:
        # every rung-3 template puts the consequence AFTER {text}, so a
        # whole-message cap alone would slice off the one clause that
        # matters most.
        c, now = self._c(60)
        c["text"] = "y" * 100_000
        entry = {"count": 0, "last": None, "unseen_s": 3000.0,
                 "here_s": 0.0, "last_cycle": now.isoformat()}
        hit = watcher.pending_ping(c, entry, now, "away", None)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], 3)
        msg = hit[1]
        self.assertIn("…", msg)  # the text itself was truncated
        self.assertTrue(any(clause in msg for clause in
                            ("judgment", "deciding without you",
                             "my call now", "take it from here")), msg)
        self.assertLessEqual(len(msg), 301)

    def test_notified_sweep_drops_stale_closed_keeps_open(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher.sample_assertions_raw, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 999.0,
                                               "front_app": None}
            watcher.desktop_notify = lambda t, m: True
            watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                closed = core.add_commitment("closed", "+0m")
                core.resolve_commitment(closed["id"], "answered")
                open_ = core.add_commitment("open", "+0m")
                old_last = (core.now_utc() - timedelta(days=10)).isoformat()
                recent_last = (core.now_utc() - timedelta(days=1)).isoformat()
                core.write_json(watcher.NOTIFIED, {
                    closed["id"]: {"count": 1, "last": old_last,
                                   "unseen_s": 0.0, "here_s": 0.0,
                                   "last_cycle": None},
                    open_["id"]: {"count": 1, "last": recent_last,
                                  "unseen_s": 0.0, "here_s": 0.0,
                                  "last_cycle": None},
                })
                watcher.run_cycle(force=True)
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertNotIn(closed["id"], saved)  # closed + stale -> swept
                self.assertIn(open_["id"], saved)      # still open -> kept
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher.sample_assertions_raw, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds) = orig


class TestDeferredDelivery(unittest.TestCase):
    def _env(self, dd, state, front):
        orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                watcher.sample_presence, watcher.desktop_notify,
                watcher._spawn, watcher.wait_for_breakpoint,
                watcher.sample_assertions_raw, watcher.sample_screen_locked,
                watcher.sample_net, watcher.sample_recent_fs,
                watcher.sample_builds)
        core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
        watcher.NOTIFIED = dd / "notified.json"
        watcher.sample_presence = lambda: {"state": state, "idle_s": 2.0,
                                           "front_app": front}
        watcher._spawn = lambda cmd: None
        watcher.sample_assertions_raw = lambda: ""  # no live sensors in tests
        watcher.sample_screen_locked = lambda: False  # no live sensor in tests
        watcher.sample_net = lambda: None  # no live vnstat in tests
        watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
        watcher.sample_builds = lambda: {}  # no live ps sampling in tests
        return orig

    def _restore(self, orig):
        (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
         watcher.sample_presence, watcher.desktop_notify,
         watcher._spawn, watcher.wait_for_breakpoint,
         watcher.sample_assertions_raw, watcher.sample_screen_locked,
         watcher.sample_net, watcher.sample_recent_fs,
         watcher.sample_builds) = orig

    def _ripe_item(self, text="q?"):
        rec = core.add_commitment(text, "+0m", kind="awaiting-reply")
        core.write_json(watcher.NOTIFIED, {rec["id"]: {
            "count": 0, "last": None, "unseen_s": 700.0,
            "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
        return rec

    def test_elsewhere_defers_then_fires_with_telemetry(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, "elsewhere", "Figma")
            fired, watches = [], []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.wait_for_breakpoint = (
                lambda *a, **k: watches.append((a, k)) or ("pause", 20.0))
            try:
                rec = self._ripe_item()
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
                self.assertEqual(len(watches), 1)     # exactly one watch
                saved = core.read_json(watcher.NOTIFIED, {})[rec["id"]]
                self.assertEqual(saved["count"], 1)
                self.assertEqual(saved["defer_reason"], "pause")
                self.assertEqual(saved["deferred_s"], 20.0)
            finally:
                self._restore(orig)

    def test_away_fires_immediately_no_watch(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, "away", None)
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.wait_for_breakpoint = (
                lambda *a, **k: (_ for _ in ()).throw(AssertionError("no watch when away")))
            try:
                rec = self._ripe_item()
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
                saved = core.read_json(watcher.NOTIFIED, {})[rec["id"]]
                self.assertEqual(saved["defer_reason"], "none")
                self.assertEqual(saved["deferred_s"], 0.0)
            finally:
                self._restore(orig)

    def test_answered_during_watch_dies_unfired(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, "elsewhere", "Figma")
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            rec_box = {}
            def watch_and_answer(*a, **k):
                core.close_awaiting()          # human answers mid-watch
                return ("switch", 30.0)
            watcher.wait_for_breakpoint = watch_and_answer
            try:
                rec = self._ripe_item()
                rec_box["id"] = rec["id"]
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])    # died unfired
                saved = core.read_json(watcher.NOTIFIED, {})[rec["id"]]
                self.assertEqual(saved.get("count", 0), 0)  # NOT consumed
            finally:
                self._restore(orig)

    def test_batch_of_two_one_watch(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, "elsewhere", "Figma")
            fired, watches = [], []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.wait_for_breakpoint = (
                lambda *a, **k: watches.append(1) or ("bound", 180.0))
            try:
                self._ripe_item("q1?")
                r2 = core.add_commitment("q2?", "+0m", kind="awaiting-reply")
                n = core.read_json(watcher.NOTIFIED, {})
                n[r2["id"]] = {"count": 0, "last": None, "unseen_s": 700.0,
                               "here_s": 0.0,
                               "last_cycle": core.now_utc().isoformat()}
                core.write_json(watcher.NOTIFIED, n)
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 2)
                self.assertEqual(len(watches), 1)
            finally:
                self._restore(orig)

    def test_state_none_immediate_v102_compat(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, None, None)
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.wait_for_breakpoint = (
                lambda *a, **k: (_ for _ in ()).throw(AssertionError("no watch on degrade")))
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                # state None -> legacy wall path relative to due; make it ripe:
                items = core.load_commitments()
                for it in items:
                    if it["id"] == rec["id"]:
                        it["due_at"] = (core.now_utc()
                                        - timedelta(seconds=700)).isoformat()
                core.write_json(core.COMMITMENTS, items)
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
            finally:
                self._restore(orig)

    def test_return_nudge_never_defers(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, "elsewhere", "Figma")
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.wait_for_breakpoint = (
                lambda *a, **k: (_ for _ in ()).throw(AssertionError("return-nudge must not watch")))
            try:
                self._ripe_item()
                past = (core.now_utc() - timedelta(seconds=1800)).isoformat()
                core.write_json(dd / "presence.json",
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)   # return-nudge, immediate
            finally:
                self._restore(orig)


class TestCourtesy(unittest.TestCase):
    LOCKED_PLIST = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        b'<plist version="1.0"><dict><key>IOConsoleUsers</key><array>'
        b'<dict><key>kCGSSessionUserNameKey</key><string>o</string>'
        b'<key>CGSSessionScreenIsLocked</key><true/></dict>'
        b'</array></dict></plist>')
    UNLOCKED_PLIST = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        b'"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        b'<plist version="1.0"><dict><key>IOConsoleUsers</key><array>'
        b'<dict><key>kCGSSessionUserNameKey</key><string>o</string>'
        b'<key>kCGSSessionOnConsoleKey</key><true/></dict>'
        b'</array></dict></plist>')

    def test_parse_locked(self):
        self.assertTrue(presence.parse_locked(self.LOCKED_PLIST))
        self.assertFalse(presence.parse_locked(self.UNLOCKED_PLIST))
        self.assertIsNone(presence.parse_locked(b"garbage"))
        self.assertIsNone(presence.parse_locked(b""))

    def test_sound_allowed_rules(self):
        now = core.now_utc()
        fresh = {"state": "away", "since": (now - timedelta(seconds=60)).isoformat()}
        stale = {"state": "away", "since": (now - timedelta(seconds=3600)).isoformat()}
        orig = watcher.sample_screen_locked
        try:
            watcher.sample_screen_locked = lambda: False
            self.assertTrue(watcher.sound_allowed("here", fresh, now))
            self.assertTrue(watcher.sound_allowed("away", fresh, now))   # brief away
            self.assertFalse(watcher.sound_allowed("away", stale, now))  # long away
            watcher.sample_screen_locked = lambda: True
            self.assertFalse(watcher.sound_allowed("here", fresh, now))  # locked wins
            watcher.sample_screen_locked = lambda: None
            self.assertTrue(watcher.sound_allowed("elsewhere", fresh, now))
            self.assertFalse(watcher.sound_allowed("away", stale, now))
        finally:
            watcher.sample_screen_locked = orig

    def test_no_quiet_hours_cycle_runs_at_3am(self):
        # force=False must no longer early-return: patch now_local to 03:00
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher._spawn, watcher.wait_for_breakpoint,
                    watcher.sample_assertions_raw, watcher.sample_screen_locked,
                    watcher.sample_net, watcher.sample_recent_fs,
                    watcher.sample_builds, core.now_local)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 999,
                                               "front_app": None}
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher._spawn = lambda cmd: None
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_screen_locked = lambda: False
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            three_am = core.now_utc().astimezone(core.tzinfo()).replace(hour=3)
            core.now_local = lambda tz_name=core.DEFAULT_TZ: three_am
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 700.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=False)      # 3 AM, no force: must fire
                self.assertEqual(len(fired), 1)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher._spawn, watcher.wait_for_breakpoint,
                 watcher.sample_assertions_raw, watcher.sample_screen_locked,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds, core.now_local) = orig

    def test_muted_fire_logged_and_silent(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify,
                    watcher._spawn, watcher.wait_for_breakpoint,
                    watcher.sample_assertions_raw, watcher.sample_screen_locked,
                    watcher.sample_net, watcher.sample_recent_fs,
                    watcher.sample_builds)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            spawned = []
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 9999,
                                               "front_app": None}
            watcher.desktop_notify = lambda t, m: True
            watcher._spawn = lambda cmd: spawned.append(cmd)
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_screen_locked = lambda: True   # locked -> mute
            watcher.sample_net = lambda: None  # no live vnstat in tests
            watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
            watcher.sample_builds = lambda: {}  # no live ps sampling in tests
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 700.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=True)
                self.assertEqual(spawned, [])       # popup yes, sound no
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"muted": true', habits)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify,
                 watcher._spawn, watcher.wait_for_breakpoint,
                 watcher.sample_assertions_raw, watcher.sample_screen_locked,
                 watcher.sample_net, watcher.sample_recent_fs,
                 watcher.sample_builds) = orig


class TestPromptSubmitHook(unittest.TestCase):
    def test_machine_events_detected(self):
        self.assertTrue(prompt_submit.is_machine_event(
            "<task-notification>\n<task-id>abc</task-id>…"))
        self.assertTrue(prompt_submit.is_machine_event(
            "[SYSTEM NOTIFICATION - NOT USER INPUT]\nThis is an automated event"))
        self.assertFalse(prompt_submit.is_machine_event("hey, quick question"))
        self.assertFalse(prompt_submit.is_machine_event(""))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._orig = (core.DATA, core.COMMITMENTS)
        core.DATA, core.COMMITMENTS = d, d / "commitments.json"

    def tearDown(self):
        core.DATA, core.COMMITMENTS = self._orig
        self.tmp.cleanup()

    def test_first_prompt_no_elapsed(self):
        block = prompt_submit.build_context(core)
        self.assertIn("<sundial-tick>", block)
        self.assertIn("Now: ", block)
        self.assertNotIn("Elapsed", block)
        stamped = core.read_json(core.DATA / "last_prompt.json", {})
        self.assertIsNotNone(core.parse_iso(stamped.get("ts")))

    def test_second_prompt_has_elapsed_delta(self):
        past = (core.now_utc() - timedelta(minutes=47)).isoformat()
        core.write_json(core.DATA / "last_prompt.json", {"ts": past})
        block = prompt_submit.build_context(core)
        self.assertIn("Elapsed since your previous prompt: 47m.", block)

    def test_disarms_awaiting_items(self):
        core.add_commitment("q?", "+10m", kind="awaiting-reply")
        prompt_submit.build_context(core)
        open_awaiting = [c for c in core.load_commitments()
                         if c.get("kind") == "awaiting-reply" and c["status"] == "open"]
        self.assertEqual(open_awaiting, [])

    def test_disarm_refreshes_menubar(self):
        refreshed = []
        orig = core._menubar_spawn
        core._menubar_spawn = lambda cmd: refreshed.append(cmd)
        try:
            core.add_commitment("q?", "+0m", kind="awaiting-reply")
            prompt_submit.build_context(core, {"prompt": "hello",
                                               "transcript_path": None})
            self.assertTrue(refreshed)
        finally:
            core._menubar_spawn = orig

    def test_answered_habit_logged_with_latency(self):
        core.add_commitment("q?", "+10m", kind="awaiting-reply")
        prompt_submit.build_context(core)
        habits = (core.DATA / "habits.jsonl").read_text()
        line = json.loads([h for h in habits.splitlines()
                           if '"answered"' in h][0])
        self.assertGreaterEqual(line["latency_s"], 0)

    def test_opportunities_block_surfaces(self):
        sys.path.insert(0, str(WATCHER_DIR))
        import opportunities
        opportunities.add_opportunity("meeting-start", {"app": "zoom.us"},
                                      "Meeting detected. Minutes?", None)
        block = prompt_submit.build_context(core)
        self.assertIn("<opportunities>", block)
        self.assertIn("Minutes?", block)

    def test_no_block_when_no_offers(self):
        block = prompt_submit.build_context(core)
        self.assertNotIn("<opportunities>", block)

    def test_read_own_away_summary_last_and_stripped(self):
        d = Path(self.tmp.name)
        tx = d / "sess.jsonl"
        tx.write_text("\n".join([
            json.dumps({"type": "system", "subtype": "away_summary",
                        "content": "Old recap. (disable recaps in /config)"}),
            json.dumps({"type": "user", "message": {"content": "hi"}}),
            json.dumps({"type": "system", "subtype": "away_summary",
                        "content": "Latest recap. (disable recaps in /config)"}),
        ]) + "\n", encoding="utf-8")
        self.assertEqual(prompt_submit.read_own_away_summary(str(tx)),
                         "Latest recap.")               # last one, tail stripped
        self.assertIsNone(prompt_submit.read_own_away_summary(None))
        empty = d / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        self.assertIsNone(prompt_submit.read_own_away_summary(str(empty)))

    def test_read_own_away_summary_tolerates_malformed(self):
        # Bare non-dict JSON, garbage lines, and a valid record interleaved:
        # must never raise (it runs on the prompt path) and still find the
        # last real away_summary.
        d = Path(self.tmp.name)
        tx = d / "sess.jsonl"
        tx.write_text("\n".join([
            "null",                               # valid JSON, not a dict
            "42",                                 # valid JSON, not a dict
            "{ this is not json",                 # unparseable
            json.dumps(["a", "list"]),            # valid JSON, not a dict
            json.dumps({"type": "system", "subtype": "away_summary",
                        "content": "Survivor recap. (disable recaps in /config)"}),
            "",                                   # blank
        ]) + "\n", encoding="utf-8")
        self.assertEqual(prompt_submit.read_own_away_summary(str(tx)),
                         "Survivor recap.")

    def test_welcome_back_injects_resume_and_consumes_once(self):
        d = Path(self.tmp.name)
        tx = d / "sess.jsonl"
        tx.write_text(json.dumps(
            {"type": "system", "subtype": "away_summary",
             "content": "We were building the bridge. (disable recaps in /config)"}
        ) + "\n", encoding="utf-8")
        core.write_json(core.DATA / "welcome_back.json", {
            "unlocked_at": core.now_utc().isoformat(), "away_s": 1500.0,
            "front_app": "Figma", "consumed": False})
        data = {"transcript_path": str(tx)}
        block = prompt_submit.build_context(core, data)
        self.assertIn("<presence-return>", block)
        self.assertIn("We were building the bridge.", block)
        self.assertIn("25m", block)                     # 1500s humanized
        block2 = prompt_submit.build_context(core, data)
        self.assertNotIn("<presence-return>", block2)   # fire once

    def test_welcome_back_stale_consumes_silently(self):
        old = (core.now_utc() - timedelta(seconds=2400)).isoformat()  # 40 min
        core.write_json(core.DATA / "welcome_back.json", {
            "unlocked_at": old, "away_s": 1500.0, "front_app": "Figma",
            "consumed": False})
        block = prompt_submit.build_context(core, {"transcript_path": None})
        self.assertNotIn("<presence-return>", block)     # too stale to greet
        wb = core.read_json(core.DATA / "welcome_back.json", {})
        self.assertTrue(wb.get("consumed"))              # but consumed anyway

    def test_no_presence_return_without_welcome_back(self):
        block = prompt_submit.build_context(core)        # no data arg: back-compat
        self.assertNotIn("<presence-return>", block)

    def test_budget_flag_fires_once_via_hook_and_persists_state(self):
        # Built directly (not via add_commitment) so the P90 snapshot is
        # exact: 40m, matching TestBudgetFlags' shape from _attach_estimate.
        past = (core.now_utc() - timedelta(minutes=22)).isoformat()   # 55%
        cid = "nudge01"
        core.write_json(core.COMMITMENTS, [{
            "id": cid, "text": "nudge me", "status": "open",
            "created_at": past,
            "est": {"est_s": 40 * 60 * 0.8, "p50_s": 40 * 48,
                    "p90_s": 40 * 60, "n": 8, "confidence": "high"}}])
        block = prompt_submit.build_context(core)
        self.assertIn("⏱", block)           # the clock glyph, once
        self.assertIn("50%", block)
        state = core.read_json(core.DATA / "est_nudges.json", {})
        self.assertEqual(sorted(state[cid]), [0.5])
        # same threshold, next prompt -> silent, state unchanged
        block2 = prompt_submit.build_context(core)
        self.assertNotIn("⏱", block2)
        state2 = core.read_json(core.DATA / "est_nudges.json", {})
        self.assertEqual(state2, state)
        # FIX5: closing the commitment must prune its flag state out of
        # est_nudges.json on the very next hook call -- a closed ask's
        # nudge history must not linger forever.
        items = core.load_commitments()
        for c in items:
            if c.get("id") == cid:
                c["status"] = "done"
        core.write_json(core.COMMITMENTS, items)
        prompt_submit.build_context(core)
        state3 = core.read_json(core.DATA / "est_nudges.json", {})
        self.assertNotIn(cid, state3)

    def _run_main_with_stdin(self, payload):
        import io
        import contextlib
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                prompt_submit.main()
        finally:
            sys.stdin = old_stdin
        return out.getvalue()

    def test_main_human_prompt_refreshes_session_claim(self):
        self._run_main_with_stdin({"prompt": "hey, quick question"})
        claim = core.read_json(core.DATA / "session_claim.json", {})
        self.assertIsNotNone(core.parse_iso(claim.get("ts")))
        self.assertEqual(claim.get("ttl_s"), 3600.0)

    def test_main_machine_event_does_not_refresh_claim(self):
        self._run_main_with_stdin(
            {"prompt": "<task-notification>\n<task-id>abc</task-id>"})
        self.assertFalse((core.DATA / "session_claim.json").exists())


class TestSessionStartHook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._orig = (core.DATA, core.COMMITMENTS, core.BIRTH)
        core.DATA, core.COMMITMENTS = d, d / "commitments.json"
        core.BIRTH = d / "birth.json"

    def tearDown(self):
        core.DATA, core.COMMITMENTS, core.BIRTH = self._orig
        self.tmp.cleanup()

    def test_due_list_caps_at_ten_with_more_line(self):
        past = (core.now_utc() - timedelta(minutes=5)).isoformat()
        for i in range(15):
            core.add_commitment(f"item{i}", past)
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        lines = block.split("\n")
        item_lines = [ln for ln in lines if ln.startswith("  - [")]
        self.assertEqual(len(item_lines), 10)
        self.assertIn("  …and 5 more.", lines)

    def test_due_list_caps_each_item_line_text(self):
        past = (core.now_utc() - timedelta(minutes=5)).isoformat()
        core.add_commitment("z" * 5_000, past)
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        item_lines = [ln for ln in block.split("\n") if ln.startswith("  - [")]
        self.assertEqual(len(item_lines), 1)
        line = item_lines[0]
        self.assertIn("…", line)
        self.assertIn("z" * 200, line)         # first 200 chars kept
        self.assertNotIn("z" * 201, line)      # rest truncated away
        self.assertLess(len(line), 260)        # tag + 200 + ellipsis, bounded

    def test_two_clock_flags_at_risk_when_deadline_tighter_than_p90(self):
        # due in 1 minute, P90 2h (n=0 floor on a 1h est): remaining < P90
        rec = core.add_commitment("tight task", "+1m", est_str="1h")
        self.assertIn("est", rec)
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertIn("at risk", block)
        self.assertIn("Estimation:", block)
        self.assertIn("no closed samples", block)

    def test_two_clock_future_due_never_reds_on_elapsed(self):
        # The soak repro: a 1m-effort task due in 2h. Elapsed since creation
        # blows past P90 (120s) but the deadline is comfortably far -- wall
        # time since promising is NOT time spent working. No flag.
        core.add_commitment("scheduled check", "+2h", est_str="1m")
        items = core.load_commitments()
        items[0]["created_at"] = (
            core.now_utc() - timedelta(hours=1)).isoformat()
        core.write_json(core.COMMITMENTS, items)
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertNotIn("at risk", block)
        self.assertNotIn("running long", block)

    def test_two_clock_no_due_running_long_on_elapsed(self):
        # Without a deadline, elapsed-vs-P90 is the only meaningful clock.
        core.add_commitment("open-ended task", est_str="1m")
        items = core.load_commitments()
        items[0]["created_at"] = (
            core.now_utc() - timedelta(hours=1)).isoformat()
        core.write_json(core.COMMITMENTS, items)
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertIn("running long", block)

    def test_two_clock_health_line_with_samples(self):
        import estimator
        for i in range(6):
            estimator.record_estimate(core.DATA, f"h{i}", 100, actual_s=110)
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertIn("Estimation: 6 closed samples", block)
        self.assertIn("1.1x", block)
        self.assertNotIn("running long", block)

    def test_verdict_block_for_exhausted_ask(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, core.BIRTH, core.LEDGER)
            core.DATA = dd
            core.COMMITMENTS = dd / "commitments.json"
            core.BIRTH = dd / "birth.json"
            core.LEDGER = dd / "session-ledger.json"
            try:
                birth = core.get_or_create_birth()
                rec = core.add_commitment("ship the copy?", "+0m",
                                          kind="awaiting-reply", confidence=0.97)
                core.write_json(dd / "notified.json",
                                {rec["id"]: {"count": 3, "here_s": 0.0,
                                             "unseen_s": 4000.0, "last": None}})
                block = session_start.build_block(core, birth, None)
                self.assertIn("Escalation exhausted", block)
                self.assertIn("PROCEED", block)
            finally:
                (core.DATA, core.COMMITMENTS, core.BIRTH, core.LEDGER) = orig

    def test_standing_duty_line_always_present(self):
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertIn(
            "Session-voice duty: arm a Monitor on data/session_speak.json",
            block)

    def test_speak_queue_line_present_when_unconsumed_entry_exists(self):
        core.write_json(core.DATA / "session_speak.json", {"queue": [
            {"cid": "a", "rung": 1, "message": "m", "text": "t",
             "ts": core.now_utc().isoformat(), "consumed": False},
            {"cid": "b", "rung": 1, "message": "m2", "text": "t2",
             "ts": core.now_utc().isoformat(), "consumed": True},
        ]})
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertIn(
            "Session-voice: 1 message(s) queued — read data/session_speak.json,"
            " speak them time-situated, mark consumed.", block)

    def test_speak_queue_line_absent_when_no_file(self):
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertNotIn("Session-voice:", block)

    def test_speak_queue_line_absent_when_all_consumed(self):
        core.write_json(core.DATA / "session_speak.json", {"queue": [
            {"cid": "a", "rung": 1, "message": "m", "text": "t",
             "ts": core.now_utc().isoformat(), "consumed": True}]})
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        self.assertNotIn("Session-voice:", block)

    def test_speak_queue_malformed_degrades_silently(self):
        (core.DATA / "session_speak.json").write_text(
            "{not valid json", encoding="utf-8")
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)  # must not raise
        self.assertNotIn("Session-voice:", block)
        self.assertIn("Session-voice duty:", block)  # standing duty unaffected


class TestPresence(unittest.TestCase):
    IOREG_SAMPLE = (
        '    | |   "HIDParameters" = {...}\n'
        '    | |   "HIDIdleTime" = 45000000000\n'
    )
    LSAPPINFO_SAMPLE = (
        '"ASN:0x0-0x12f12f-Figma:" info:\n'
        '    "LSDisplayName"="Figma"\n'
        '    "LSBundlePath"="/Applications/Figma.app"\n'
    )

    def test_parse_idle(self):
        self.assertEqual(presence.parse_idle(self.IOREG_SAMPLE), 45.0)
        self.assertIsNone(presence.parse_idle("no idle line here"))
        self.assertIsNone(presence.parse_idle(""))

    def test_parse_front(self):
        self.assertEqual(presence.parse_front(self.LSAPPINFO_SAMPLE), "Figma")
        self.assertIsNone(presence.parse_front("garbage"))
        self.assertIsNone(presence.parse_front(""))

    def test_cli_apps_default_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            apps = presence.cli_apps(Path(d))
            self.assertIn("Terminal", apps)
            self.assertIn("iTerm2", apps)
            (Path(d) / "cli_apps.txt").write_text("MyTerm\n\nGhostty\n")
            apps2 = presence.cli_apps(Path(d))
            self.assertIn("MyTerm", apps2)
            self.assertIn("Terminal", apps2)  # defaults kept

    def test_derive_state_truth_table(self):
        cli = ("Terminal", "iTerm2")
        self.assertEqual(presence.derive_state(300.0, "Figma", cli), "away")
        self.assertEqual(presence.derive_state(10.0, "Figma", cli), "elsewhere")
        self.assertEqual(presence.derive_state(10.0, "Terminal", cli), "here")
        self.assertEqual(presence.derive_state(10.0, None, cli), "present")
        self.assertIsNone(presence.derive_state(None, "Figma", cli))
        self.assertEqual(presence.derive_state(180.0, "Terminal", cli), "away")  # boundary: >= is away

    def test_wrappers_never_raise_on_subprocess_failure(self):
        orig = presence.subprocess.run
        def boom(*a, **k):
            raise OSError("binary missing")
        presence.subprocess.run = boom
        try:
            self.assertIsNone(presence.idle_seconds())
            self.assertIsNone(presence.front_app())
        finally:
            presence.subprocess.run = orig

    def test_wrappers_none_on_nonzero_exit(self):
        class R:
            returncode = 1
            stdout = ""
        orig = presence.subprocess.run
        presence.subprocess.run = lambda *a, **k: R()
        try:
            self.assertIsNone(presence.idle_seconds())
            self.assertIsNone(presence.front_app())
        finally:
            presence.subprocess.run = orig

    PS_SAMPLE = ("  PID ELAPSED COMM\n"
                 "  123 05:12 /usr/local/bin/npm\n"
                 "  456 1-02:03:04 /opt/homebrew/bin/cargo\n"
                 "  789 00:30 /bin/zsh\n")

    def test_parse_ps_builds(self):
        got = presence.parse_ps_builds(self.PS_SAMPLE)
        self.assertEqual(got[123]["cmd"], "npm")
        self.assertEqual(got[123]["etime_s"], 312)
        self.assertEqual(got[456]["etime_s"], 93784)
        self.assertNotIn(789, got)


class TestNetSense(unittest.TestCase):
    @staticmethod
    def _vnstat_json(dead_buckets, live_buckets):
        def bucket(rx, tx):
            return {"date": {"year": 2026, "month": 7, "day": 4},
                    "time": {"hour": 7, "minute": 0}, "rx": rx, "tx": tx}
        return json.dumps({
            "vnstatversion": "2.13", "jsonversion": "2",
            "interfaces": [
                {"name": "utun0",
                 "traffic": {"fiveminute": [bucket(r, t)
                                            for r, t in dead_buckets]}},
                {"name": "en0",
                 "traffic": {"fiveminute": [bucket(r, t)
                                            for r, t in live_buckets]}},
            ]})

    def test_picks_live_over_dead_and_averages_bps(self):
        # one old irrelevant bucket + the 2 that matter for en0
        raw = self._vnstat_json(
            dead_buckets=[(0, 0), (0, 0), (0, 0)],
            live_buckets=[(999_999_999, 999_999_999),
                          (30_000_000, 15_000_000),
                          (30_000_000, 15_000_000)])
        out = presence.net_rates(raw)
        self.assertEqual(out["iface"], "en0")
        self.assertAlmostEqual(out["rx_Bps"], 100_000.0)
        self.assertAlmostEqual(out["tx_Bps"], 50_000.0)

    def test_malformed_or_empty_returns_none(self):
        self.assertIsNone(presence.net_rates("not json"))
        self.assertIsNone(presence.net_rates(""))
        self.assertIsNone(presence.net_rates(json.dumps({})))
        self.assertIsNone(presence.net_rates(json.dumps({"interfaces": []})))
        self.assertIsNone(presence.net_rates(json.dumps(
            {"interfaces": [{"name": "utun0",
                             "traffic": {"fiveminute": []}}]})))

    def test_net_sample_none_when_vnstat_binary_absent(self):
        orig = (presence.VNSTAT_BIN, presence.VNSTAT_BIN_FALLBACK,
                presence.shutil.which)
        presence.VNSTAT_BIN = "/no/such/vnstat"
        presence.VNSTAT_BIN_FALLBACK = "/no/such/vnstat/either"
        presence.shutil.which = lambda name: None
        try:
            self.assertIsNone(presence.net_sample())
        finally:
            (presence.VNSTAT_BIN, presence.VNSTAT_BIN_FALLBACK,
             presence.shutil.which) = orig

    def test_net_sample_uses_fallback_binary_path(self):
        # VNSTAT_BIN missing -> falls back to VNSTAT_BIN_FALLBACK (any real
        # file proves the existence check, not a live vnstat call).
        orig = (presence.VNSTAT_BIN, presence.VNSTAT_BIN_FALLBACK,
                presence._run)
        presence.VNSTAT_BIN = "/no/such/vnstat"
        presence.VNSTAT_BIN_FALLBACK = sys.executable
        seen = []
        presence._run = lambda cmd: seen.append(cmd) or "not json"
        try:
            self.assertIsNone(presence.net_sample())  # "not json" -> None
            self.assertEqual(seen[0][0], sys.executable)
        finally:
            (presence.VNSTAT_BIN, presence.VNSTAT_BIN_FALLBACK,
             presence._run) = orig


class TestBreakpointWatch(unittest.TestCase):
    def _sampler(self, seq):
        it = iter(seq)
        last = seq[-1]
        def sample():
            return next(it, last)
        return sample

    @staticmethod
    def _snap(idle, front, state="elsewhere"):
        return {"state": state, "idle_s": idle, "front_app": front}

    def test_immediate_pause(self):
        s = self._sampler([self._snap(20.0, "Figma")])
        reason, elapsed = watcher.wait_for_breakpoint(s, lambda t: None, "Figma")
        self.assertEqual((reason, elapsed), ("pause", 0.0))

    def test_pause_after_polls(self):
        seq = [self._snap(2.0, "Figma"), self._snap(5.0, "Figma"),
               self._snap(16.0, "Figma")]
        slept = []
        reason, elapsed = watcher.wait_for_breakpoint(
            self._sampler(seq), slept.append, "Figma")
        self.assertEqual(reason, "pause")
        self.assertEqual(elapsed, 2 * watcher.DEFER_POLL_S)
        self.assertEqual(slept, [watcher.DEFER_POLL_S] * 2)

    def test_switch_after_polls(self):
        seq = [self._snap(2.0, "Figma"), self._snap(3.0, "Safari")]
        reason, elapsed = watcher.wait_for_breakpoint(
            self._sampler(seq), lambda t: None, "Figma")
        self.assertEqual(reason, "switch")
        self.assertEqual(elapsed, watcher.DEFER_POLL_S)

    def test_switch_suppressed_when_idle_only(self):
        seq = [self._snap(2.0, "Figma"), self._snap(3.0, "Safari"),
               self._snap(16.0, "Safari")]
        reason, _ = watcher.wait_for_breakpoint(
            self._sampler(seq), lambda t: None, "Figma", idle_only=True)
        self.assertEqual(reason, "pause")  # switch ignored, pause caught later

    def test_bound_expiry(self):
        s = self._sampler([self._snap(2.0, "Figma")])
        reason, elapsed = watcher.wait_for_breakpoint(
            s, lambda t: None, "Figma", max_s=30, poll_s=10)
        self.assertEqual((reason, elapsed), ("bound", 30))

    def test_degrade_on_sampler_failure(self):
        seq = [self._snap(2.0, "Figma"),
               {"state": None, "idle_s": None, "front_app": None}]
        reason, elapsed = watcher.wait_for_breakpoint(
            self._sampler(seq), lambda t: None, "Figma")
        self.assertEqual(reason, "degrade")
        self.assertEqual(elapsed, watcher.DEFER_POLL_S)

    def test_none_front_never_switches(self):
        seq = [self._snap(2.0, None), self._snap(16.0, None)]
        reason, _ = watcher.wait_for_breakpoint(
            self._sampler(seq), lambda t: None, None)
        self.assertEqual(reason, "pause")


class TestOpportunities(unittest.TestCase):
    # Real-shape sample: live macOS per-pid lines carry ALIASES
    # (NoDisplaySleepAssertion, InternalPreventDisplaySleep) — the literal
    # PreventUserIdleDisplaySleep appears only in the system-wide summary.
    PMSET_SAMPLE = (
        "Assertion status system-wide:\n"
        "   PreventUserIdleDisplaySleep    1\n"
        "   pid 616(WindowServer): [0x1] 00:00:33 UserIsActive named: \"x\"\n"
        "   pid 900(zoom.us): [0x2] 00:10:00 NoDisplaySleepAssertion named: \"Zoom meeting\"\n"
        "   pid 901(Brave Browser): [0x3] 00:05:00 PreventUserIdleSystemSleep named: \"y\"\n"
        "   pid 902(Google Chrome Helper (Renderer)): [0x4] 00:01:00 NoDisplaySleepAssertion named: \"Video Wake Lock\"\n"
        "   pid 556(powerd): [0x5] 00:00:11 InternalPreventDisplaySleep named: \"delayDisplayOff\"\n"
    )

    # Real WebRTC-in-Chrome line: the discriminator between a Meet call and
    # plain video playback (which asserts "Video Wake Lock" instead).
    WEBRTC_LINE = (
        '   pid 1234(Google Chrome): [0x5] 00:02:11 PreventUserIdleSystemSleep '
        'named: "WebRTC has active PeerConnections"\n'
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._orig = (core.DATA, core.COMMITMENTS)
        core.DATA, core.COMMITMENTS = d, d / "commitments.json"

    def tearDown(self):
        core.DATA, core.COMMITMENTS = self._orig
        self.tmp.cleanup()

    def test_asserting_display_procs(self):
        procs = presence.asserting_display_procs(self.PMSET_SAMPLE)
        # aliases match; paren-bearing names captured whole; non-display
        # assertions (UserIsActive, PreventUserIdleSystemSleep) excluded
        self.assertEqual(procs, {"zoom.us", "Google Chrome Helper (Renderer)",
                                 "powerd"})
        self.assertEqual(presence.asserting_display_procs(""), set())

    def test_assertion_triples(self):
        triples = presence.assertion_triples(self.PMSET_SAMPLE)
        self.assertIn(("zoom.us", "NoDisplaySleepAssertion", "Zoom meeting"),
                      triples)
        self.assertIn(("Google Chrome Helper (Renderer)",
                       "NoDisplaySleepAssertion", "Video Wake Lock"), triples)
        self.assertIn(("Brave Browser", "PreventUserIdleSystemSleep", "y"),
                      triples)
        self.assertEqual(presence.assertion_triples(""), [])

    def test_assertion_triples_webrtc_line(self):
        # The literal discriminator line: paren-bearing proc name, real
        # assertion type, and the WebRTC-specific name field.
        triples = presence.assertion_triples(self.WEBRTC_LINE)
        self.assertEqual(triples, [("Google Chrome", "PreventUserIdleSystemSleep",
                                    "WebRTC has active PeerConnections")])

    def test_webrtc_procs(self):
        triples = presence.assertion_triples(self.PMSET_SAMPLE + self.WEBRTC_LINE)
        self.assertEqual(opportunities.webrtc_procs(triples), {"Google Chrome"})

    def test_webrtc_procs_case_insensitive_substring(self):
        triples = [("X", "SomeType", "webRTC session active")]
        self.assertEqual(opportunities.webrtc_procs(triples), {"X"})

    def test_webrtc_procs_excludes_video_wake_lock(self):
        triples = [("Google Chrome Helper (Renderer)",
                    "NoDisplaySleepAssertion", "Video Wake Lock")]
        self.assertEqual(opportunities.webrtc_procs(triples), set())

    def test_detect_meeting_start_and_end(self):
        now = core.now_utc()
        events, active = opportunities.detect_meeting(
            {"zoom.us"}, set(), None, now)
        self.assertEqual(events[0]["kind"], "meeting-start")
        self.assertEqual(active["app"], "zoom.us")
        events2, active2 = opportunities.detect_meeting(
            {"zoom.us"}, set(), active, now)
        self.assertEqual(events2, [])                    # steady state
        self.assertEqual(active2, active)
        past = dict(active)
        past["started"] = (
            now - timedelta(seconds=1800)).isoformat()
        events3, active3 = opportunities.detect_meeting(set(), set(), past, now)
        self.assertEqual(events3[0]["kind"], "meeting-end")
        self.assertAlmostEqual(events3[0]["duration_s"], 1800, delta=5)
        self.assertIsNone(active3)

    def test_detect_meeting_ignores_unlisted(self):
        events, active = opportunities.detect_meeting(
            {"Brave Browser"}, set(), None, core.now_utc())
        self.assertEqual((events, active), ([], None))

    def test_detect_meeting_webrtc_only_start_and_end(self):
        # THE case this whole patch exists for: Meet-in-Chrome. Chrome is
        # not in the meeting-apps allowlist and holds no display-sleep
        # assertion of its own here -- only the WebRTC PeerConnections
        # assertion says a call is live.
        now = core.now_utc()
        events, active = opportunities.detect_meeting(
            set(), {"Google Chrome"}, None, now)
        self.assertEqual(events[0]["kind"], "meeting-start")
        self.assertEqual(active["app"], "Google Chrome")
        past = dict(active)
        past["started"] = (
            now - timedelta(seconds=900)).isoformat()
        events2, active2 = opportunities.detect_meeting(set(), set(), past, now)
        self.assertEqual(events2[0]["kind"], "meeting-end")
        self.assertEqual(events2[0]["app"], "Google Chrome")
        self.assertIsNone(active2)

    def test_detect_meeting_video_wake_lock_is_not_a_meeting(self):
        # Chrome playing YouTube asserts "Video Wake Lock", not WebRTC --
        # webrtc_procs correctly excludes it, so no meeting starts even
        # though the process does hold a display-sleep assertion.
        now = core.now_utc()
        triples = [("Google Chrome Helper (Renderer)",
                    "NoDisplaySleepAssertion", "Video Wake Lock")]
        webrtc = opportunities.webrtc_procs(triples)
        display_procs = {"Google Chrome Helper (Renderer)"}
        events, active = opportunities.detect_meeting(
            display_procs, webrtc, None, now)
        self.assertEqual((events, active), ([], None))

    def test_detect_meeting_prefers_webrtc_proc_when_both_live(self):
        now = core.now_utc()
        events, active = opportunities.detect_meeting(
            {"zoom.us"}, {"Google Chrome"}, None, now)
        self.assertEqual(active["app"], "Google Chrome")

    def test_detect_build_finished(self):
        now = core.now_utc()
        state = {"123": {"cmd": "npm", "etime_s": 312},
                 "999": {"cmd": "make", "etime_s": 30}}
        events, new_state = opportunities.detect_build_finished(
            {456: {"cmd": "cargo", "etime_s": 10}}, state, now)
        self.assertEqual(len(events), 1)          # npm ended >=60s; make too short
        self.assertEqual(events[0]["cmd"], "npm")
        self.assertEqual(new_state, {"456": {"cmd": "cargo", "etime_s": 10}})

    def test_build_finished_ignores_long_lived_daemon(self):
        # A "node" that ran for days is a daemon, not a build -- must NOT fire
        # (regression: the detector once reported "node run finished (9196m)").
        now = core.now_utc()
        state = {"111": {"cmd": "node", "etime_s": opportunities.BUILD_MAX_S + 1},
                 "222": {"cmd": "npm", "etime_s": 600}}   # a real 10-min build
        events, _ = opportunities.detect_build_finished({}, state, now)
        cmds = {e["cmd"] for e in events}
        self.assertIn("npm", cmds)         # the real build still fires
        self.assertNotIn("node", cmds)     # the multi-day daemon is capped out

    def test_detect_new_folders(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "existing").mkdir()
            events, known = opportunities.detect_new_folders((root,), {})
            self.assertEqual(events, [])                 # baseline run
            (root / "NewClient").mkdir()
            (root / ".hidden").mkdir()
            events2, known2 = opportunities.detect_new_folders((root,), known)
            self.assertEqual(len(events2), 1)
            self.assertIn("NewClient", events2[0]["folder"])

    def test_detect_new_folders_cap_defers_overflow(self):
        # 5 new folders -> 3 events this cycle; the 2 unreported stay OUT of
        # known and surface next cycle (never silently absorbed).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, known = opportunities.detect_new_folders((root,), {})
            for name in ("A", "B", "C", "D", "E"):
                (root / name).mkdir()
            events, known = opportunities.detect_new_folders((root,), known)
            self.assertEqual(len(events), 3)
            events2, known2 = opportunities.detect_new_folders((root,), known)
            self.assertEqual(len(events2), 2)
            reported = {Path(e["folder"]).name for e in events + events2}
            self.assertEqual(reported, {"A", "B", "C", "D", "E"})
            events3, _ = opportunities.detect_new_folders((root,), known2)
            self.assertEqual(events3, [])            # all caught up

    MD_RUNS = []

    def test_ignore_paths_config_blocks_prefixes(self):
        (core.DATA / "ignore_paths.txt").write_text("/r/ProjectX\n")
        def runner(args):
            return "/r/ProjectX/newfile\n/r/Other/thing\n"
        got = opportunities.mdfind_recent(Path("/r"), 60, runner)
        self.assertEqual(got, ["/r/Other/thing"])

    def test_mdfind_recent_merges_and_caps(self):
        outs = ["/r/a\n/r/b\n", "/r/b\n/r/c\n/r/d\n/r/e\n/r/f\n/r/g\n"]
        def runner(args):
            self.MD_RUNS.append(args)
            return outs.pop(0) if outs else ""
        got = opportunities.mdfind_recent(Path("/r"), 1260, runner)
        self.assertEqual(got, ["/r/a", "/r/b", "/r/c", "/r/d", "/r/e"])  # cap 5, dedup

    def test_mdfind_recent_runner_failure(self):
        def runner(args):
            raise OSError("no mdfind")
        self.assertEqual(opportunities.mdfind_recent(Path("/r"), 60, runner), [])

    def test_detect_recent_fs_events(self):
        def runner(args):
            root = args[args.index("-onlyin") + 1]
            return f"{root}/NewThing\n"
        evts = opportunities.detect_recent_fs((Path("/x"), Path("/y")), runner)
        self.assertEqual(len(evts), 2)
        self.assertEqual(evts[0]["kind"], "curiosity")
        self.assertEqual(evts[0]["via"], "mdfind")

    def test_mdfind_recent_skips_ignored_paths(self):
        outs = ["/r/node_modules/pkg\n/r/.hidden/x\n/r/Real/Sub\n"]

        def runner(args):
            return outs.pop(0) if outs else ""
        got = opportunities.mdfind_recent(Path("/r"), 1260, runner)
        self.assertEqual(got, ["/r/Real/Sub"])

    def test_detect_new_folders_skips_ignored_names(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _, known = opportunities.detect_new_folders((root,), {})
            (root / "node_modules").mkdir()
            (root / "Real").mkdir()
            events, _ = opportunities.detect_new_folders((root,), known)
            names = {Path(e["folder"]).name for e in events}
            self.assertEqual(names, {"Real"})

    def test_auto_watch_appends_desktop_child(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, watcher._desktop_root)
            core.DATA = dd
            watcher._desktop_root = lambda: dd / "Desk"
            try:
                (dd / "Desk").mkdir()
                watcher.maybe_auto_watch(str(dd / "Desk" / "NewProj"))
                roots = (dd / "watch_roots.txt").read_text()
                self.assertIn("NewProj", roots)
                watcher.maybe_auto_watch(str(dd / "Desk" / "NewProj"))  # dup
                self.assertEqual(roots.count("NewProj"),
                                 (dd / "watch_roots.txt").read_text().count("NewProj"))
                watcher.maybe_auto_watch(str(dd / "Elsewhere" / "X"))   # non-desktop
                self.assertNotIn("Elsewhere", (dd / "watch_roots.txt").read_text())
            finally:
                core.DATA, watcher._desktop_root = orig

    def test_add_and_dedup(self):
        rec = opportunities.add_opportunity(
            "meeting-start", {"app": "zoom.us", "started": "X"}, "offer?", None)
        self.assertEqual(rec["status"], "offered")
        dup = opportunities.add_opportunity(
            "meeting-start", {"app": "zoom.us", "started": "X"}, "offer?", None)
        self.assertIsNone(dup)
        self.assertEqual(len(opportunities.load_ledger()), 1)

    def test_open_offers_expiry(self):
        now = core.now_utc()
        opportunities.add_opportunity("meeting-end", {"app": "zoom.us"},
            "minutes?", (now - timedelta(seconds=5)).isoformat())
        opportunities.add_opportunity("curiosity", {"folder": "X"},
            "new folder", None)
        live = opportunities.open_offers(now)
        self.assertEqual([r["kind"] for r in live], ["curiosity"])
        statuses = {r["kind"]: r["status"] for r in opportunities.load_ledger()}
        self.assertEqual(statuses["meeting-end"], "expired")

    def test_daily_cap(self):
        today = "2026-07-04"
        for _ in range(5):
            self.assertTrue(opportunities.offer_allowed(today))
            opportunities.count_offer(today)
        self.assertFalse(opportunities.offer_allowed(today))
        self.assertTrue(opportunities.offer_allowed("2026-07-05"))  # resets

    def test_prep_flag_and_budget(self):
        self.assertFalse(opportunities.prep_enabled())
        (core.DATA / "prep_enabled").write_text("")
        self.assertTrue(opportunities.prep_enabled())
        self.assertEqual(opportunities.prep_budget(), 2)
        (core.DATA / "prep_budget.txt").write_text("5")
        self.assertEqual(opportunities.prep_budget(), 5)
        today = "2026-07-04"
        for _ in range(5):
            self.assertTrue(opportunities.prep_allowed(today))
            opportunities.count_prep(today)
        self.assertFalse(opportunities.prep_allowed(today))

    def test_prep_budget_zero_means_zero(self):
        # fail-closed: a fresh day (no prefs entry yet) must still honor a
        # zero or negative budget -- no free spawn per day-rollover.
        (core.DATA / "prep_budget.txt").write_text("0")
        self.assertFalse(opportunities.prep_allowed("2026-07-04"))
        (core.DATA / "prep_budget.txt").write_text("-3")
        self.assertFalse(opportunities.prep_allowed("2026-07-04"))

    def test_habit_log_and_rotation(self):
        opportunities.log_habit({"kind": "test", "x": 1})
        p = core.DATA / "habits.jsonl"
        line = json.loads(p.read_text().splitlines()[0])
        self.assertEqual(line["kind"], "test")
        self.assertIn("ts", line)
        p.write_text("x" * (opportunities.HABITS_MAX_BYTES + 1))
        opportunities.log_habit({"kind": "after-rotate"})
        self.assertTrue((core.DATA / "habits.1.jsonl").exists())
        self.assertEqual(
            json.loads(p.read_text().splitlines()[0])["kind"], "after-rotate")

    def test_habit_log_never_raises(self):
        orig = core.DATA
        core.DATA = Path("/nonexistent/nope")
        try:
            opportunities.log_habit({"kind": "x"})  # must not raise
        finally:
            core.DATA = orig

    def test_concurrent_add_and_open_offers_no_lost_update(self):
        # Pre-wiring guard: once the daemon calls add_opportunity while a
        # hook calls open_offers, an unlocked load->mutate->write pair loses
        # adds (open_offers' expiry-save clobbers a concurrent add's write).
        # Every add here is born already-expired so open_offers actually
        # WRITES each round (dirty=True) -- that write is the clobberer.
        #
        # Plain thread interleaving is too lockstep to prove the race
        # (same reason the core ledger-lock test injects a slow load), so
        # a barrier INSIDE load_ledger forces both threads to hold stale
        # snapshots at once. A correct _ledger_lock keeps the second thread
        # out of load entirely (the barrier times out, harmlessly); a
        # missing lock lets both proceed and one write eats the other's.
        barrier = threading.Barrier(2)
        orig_load = opportunities.load_ledger

        def rendezvous_load():
            items = orig_load()
            try:
                barrier.wait(timeout=0.05)
            except threading.BrokenBarrierError:
                pass
            return items

        errors = []
        past = (core.now_utc() - timedelta(seconds=5)).isoformat()

        def adder():
            for i in range(50):
                try:
                    opportunities.add_opportunity(
                        "curiosity", {"n": i}, "offer?", past)
                except Exception as e:  # pragma: no cover - failure path
                    errors.append(e)

        def opener():
            now = core.now_utc()
            for _ in range(50):
                try:
                    opportunities.open_offers(now)
                except Exception as e:  # pragma: no cover - failure path
                    errors.append(e)

        opportunities.load_ledger = rendezvous_load
        try:
            t1 = threading.Thread(target=adder)
            t2 = threading.Thread(target=opener)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        finally:
            opportunities.load_ledger = orig_load

        self.assertEqual(errors, [])
        # the raw file must be valid JSON (never truncated/interleaved)
        raw = json.loads(
            (core.DATA / "opportunities.json").read_text(encoding="utf-8"))
        self.assertEqual(len(raw), 50)  # ALL of A's adds survived
        ns = sorted(r["evidence"]["n"] for r in raw)
        self.assertEqual(ns, list(range(50)))

    def test_prune_ledger(self):
        now = core.now_utc()
        old = (now - timedelta(days=20)).isoformat()
        opportunities.save_ledger([
            {"id": "a", "kind": "curiosity", "detected_at": old,
             "status": "fulfilled", "evidence": {}, "offer_msg": "",
             "expires_at": None},
            {"id": "b", "kind": "curiosity", "detected_at": old,
             "status": "offered", "evidence": {}, "offer_msg": "",
             "expires_at": None},
            {"id": "c", "kind": "curiosity",
             "detected_at": now.isoformat(), "status": "expired",
             "evidence": {}, "offer_msg": "", "expires_at": None}])
        self.assertTrue(opportunities.prune_ledger(now))
        ids = [r["id"] for r in opportunities.load_ledger()]
        self.assertEqual(ids, ["b", "c"])  # open kept; young terminal kept

    def test_decline_suppression_cycle(self):
        self.assertFalse(opportunities.kind_suppressed("meeting-start"))
        for n in (1, 2, 3):
            self.assertEqual(opportunities.decline_kind("meeting-start"), n)
        self.assertTrue(opportunities.kind_suppressed("meeting-start"))
        opportunities.allow_kind("meeting-start")
        self.assertFalse(opportunities.kind_suppressed("meeting-start"))

    def test_ignored_skips_dot_and_junk_components(self):
        self.assertTrue(opportunities._ignored("/x/node_modules/y"))
        self.assertTrue(opportunities._ignored("/x/.hidden/z"))
        self.assertFalse(opportunities._ignored("/x/Real/Sub"))

    def test_ignored_checks_components_not_substrings(self):
        # "distX" must NOT be treated as the ignored "dist" component.
        self.assertFalse(opportunities._ignored("/x/distX/y"))
        self.assertTrue(opportunities._ignored("/x/dist/y"))

    def test_mdfind_recent_ignores_relative_to_root_not_ancestors(self):
        # A watch root living under a dotdir (e.g. ~/.config/apps) must not
        # go dark: only components RELATIVE to the queried root count.
        root = Path("/Users/x/.config/apps")

        def runner(args):
            return (f"{root}/NewThing\n{root}/node_modules/y\n")
        got = opportunities.mdfind_recent(root, 1260, runner)
        self.assertIn(f"{root}/NewThing", got)
        self.assertNotIn(f"{root}/node_modules/y", got)


class TestOpportunityCycle(unittest.TestCase):
    @staticmethod
    def _raw_for(procs) -> str:
        """Synthesize a pmset-shaped dump asserting display-sleep for each
        proc name -- exercises the real presence.asserting_display_procs
        parse path instead of a bare set, same as production sees it."""
        lines = ["Assertion status system-wide:\n"]
        for i, p in enumerate(procs):
            lines.append(f'   pid {900 + i}({p}): [0x1] 00:00:00 '
                         f'NoDisplaySleepAssertion named: "meeting"\n')
        return "".join(lines)

    def _env(self, dd, state="elsewhere", front="Figma", procs=frozenset()):
        orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                watcher.sample_presence, watcher.desktop_notify,
                watcher._spawn, watcher.wait_for_breakpoint,
                watcher.sample_assertions_raw, watcher.sample_screen_locked,
                watcher.sample_net, watcher.sample_recent_fs,
                watcher.sample_builds, watcher._spawn_prep_proc)
        core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
        watcher.NOTIFIED = dd / "notified.json"
        watcher.sample_presence = lambda: {"state": state, "idle_s": 2.0,
                                           "front_app": front}
        watcher.desktop_notify = lambda t, m: True
        watcher._spawn = lambda cmd: None
        watcher.wait_for_breakpoint = lambda *a, **k: ("pause", 0.0)
        watcher.sample_assertions_raw = lambda: self._raw_for(procs)
        watcher.sample_screen_locked = lambda: False  # no live sensor in tests
        watcher.sample_net = lambda: None  # no live vnstat in tests
        watcher.sample_recent_fs = lambda: []  # no live mdfind in tests
        watcher.sample_builds = lambda: {}  # no live ps sampling in tests
        watcher._spawn_prep_proc = lambda cmd, cwd: None  # never exec claude
        return orig

    def _restore(self, orig):
        (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
         watcher.sample_presence, watcher.desktop_notify,
         watcher._spawn, watcher.wait_for_breakpoint,
         watcher.sample_assertions_raw, watcher.sample_screen_locked,
         watcher.sample_net, watcher.sample_recent_fs,
         watcher.sample_builds, watcher._spawn_prep_proc) = orig

    def test_meeting_start_offer_held_when_snoozed_logs_habit(self):
        # FIX8: an offer that clears its gates but is suppressed by an
        # active snooze must log a snooze-hold habit, not vanish silently.
        # desktop_notify/chime/speak_final are ALL stubbed to no-op
        # recorders (not just via _env's _spawn interception) so this test
        # can never leak a real popup, sound, or speech onto the owner's
        # machine even if the delivery path is misjudged.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired, chimed, spoken = [], [], []
            orig = self._env(dd, procs={"zoom.us"})
            orig_chime, orig_speak = watcher.chime, watcher.speak_final
            watcher.desktop_notify = lambda t, m: fired.append((t, m)) or True
            watcher.chime = lambda *a, **k: chimed.append(a) or None
            watcher.speak_final = lambda *a, **k: spoken.append(a) or None
            try:
                core.write_json(dd / "snooze.json", {
                    "until": (core.now_utc()
                             + timedelta(minutes=30)).isoformat(),
                    "set_at": core.now_utc().isoformat()})
                watcher.run_cycle(force=True)
                led = core.read_json(dd / "opportunities.json", [])
                self.assertEqual(led[0]["kind"], "meeting-start")  # recorded
                self.assertEqual(fired, [])                        # held
                self.assertEqual(chimed, [])
                self.assertEqual(spoken, [])
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"snooze-hold"', habits)
            finally:
                self._restore(orig)
                watcher.chime, watcher.speak_final = orig_chime, orig_speak

    def test_build_finished_offer_held_when_snoozed_logs_habit(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired, chimed, spoken = [], [], []
            orig = self._env(dd)
            orig_chime, orig_speak = watcher.chime, watcher.speak_final
            watcher.desktop_notify = lambda t, m: fired.append((t, m)) or True
            watcher.chime = lambda *a, **k: chimed.append(a) or None
            watcher.speak_final = lambda *a, **k: spoken.append(a) or None
            core.write_json(dd / "build_state.json",
                            {"111": {"cmd": "make", "etime_s": 300}})
            core.write_json(dd / "snooze.json", {
                "until": (core.now_utc()
                         + timedelta(minutes=30)).isoformat(),
                "set_at": core.now_utc().isoformat()})
            try:
                watcher.run_cycle(force=True)
                led = core.read_json(dd / "opportunities.json", [])
                self.assertTrue(
                    any(r["kind"] == "build-finished" for r in led))
                self.assertEqual(fired, [])
                self.assertEqual(chimed, [])
                self.assertEqual(spoken, [])
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"snooze-hold"', habits)
            finally:
                self._restore(orig)
                watcher.chime, watcher.speak_final = orig_chime, orig_speak

    def test_meeting_start_offers_once(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired = []
            orig = self._env(dd, procs={"zoom.us"})
            watcher.desktop_notify = lambda t, m: fired.append((t, m)) or True
            try:
                watcher.run_cycle(force=True)
                watcher.run_cycle(force=True)   # steady meeting: no repeat
                offers = [f for f in fired if f[0] == "Sundial"]
                self.assertEqual(len(offers), 1)
                self.assertIn("zoom.us", offers[0][1])
                led = core.read_json(dd / "opportunities.json", [])
                self.assertEqual(led[0]["kind"], "meeting-start")
                habits = (dd / "habits.jsonl").read_text().splitlines()
                self.assertTrue(any('"offer"' in h for h in habits))
            finally:
                self._restore(orig)

    def test_silent_prep_flag_off_never_spawns(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, watcher._spawn_prep_proc)
            core.DATA = dd
            spawned = []
            watcher._spawn_prep_proc = lambda cmd, cwd: spawned.append(cmd)
            try:
                rec = {"id": "ab12", "kind": "meeting-start",
                       "evidence": {"app": "zoom.us", "started": "T"}}
                watcher.maybe_silent_prep(rec, "2026-07-04")
                self.assertEqual(spawned, [])          # flag off
                (dd / "prep_enabled").write_text("")
                watcher.maybe_silent_prep(rec, "2026-07-04")
                self.assertEqual(len(spawned), 1)
                self.assertIn("--model", spawned[0])
                self.assertIn("haiku", spawned[0])
                self.assertTrue((dd / "opportunities" / "ab12" /
                                 "prompt.txt").exists())
            finally:
                core.DATA, watcher._spawn_prep_proc = orig

    def test_meeting_start_via_webrtc_assertion_in_chrome(self):
        # THE case this patch exists for: Meet-in-Chrome. Chrome is not in
        # the meeting-apps allowlist and asserts no display-sleep of its
        # own here -- only the raw pmset dump's WebRTC PeerConnections line
        # says a call is live. run_cycle must still fire a meeting-start
        # offer naming Chrome.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired = []
            orig = self._env(dd)
            watcher.sample_assertions_raw = lambda: (
                "Assertion status system-wide:\n"
                '   pid 1234(Google Chrome): [0x5] 00:02:11 '
                'PreventUserIdleSystemSleep named: '
                '"WebRTC has active PeerConnections"\n')
            watcher.desktop_notify = lambda t, m: fired.append((t, m)) or True
            try:
                watcher.run_cycle(force=True)
                offers = [f for f in fired if f[0] == "Sundial"]
                self.assertEqual(len(offers), 1)
                self.assertIn("Google Chrome", offers[0][1])
                led = core.read_json(dd / "opportunities.json", [])
                self.assertEqual(led[0]["kind"], "meeting-start")
                self.assertEqual(led[0]["evidence"]["app"], "Google Chrome")
            finally:
                self._restore(orig)

    def test_meeting_end_offer_and_state_clear(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired = []
            orig = self._env(dd, procs={"zoom.us"})
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            try:
                watcher.run_cycle(force=True)          # start
                watcher.sample_assertions_raw = lambda: ""
                watcher.run_cycle(force=True)          # end
                led = core.read_json(dd / "opportunities.json", [])
                kinds = [r["kind"] for r in led]
                self.assertIn("meeting-end", kinds)
                ms = core.read_json(dd / "meeting_state.json", {})
                self.assertIsNone(ms.get("active"))
            finally:
                self._restore(orig)

    def test_curiosity_never_notifies(self):
        # Rework for the Spotlight-backed seam: run_cycle's curiosity block
        # now calls ONLY watcher.sample_recent_fs, so the legacy poller's
        # baseline-cycle-then-mkdir dance is replaced by stubbing the seam
        # to return the event directly. Popup-absence assertion unchanged.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired = []
            orig = self._env(dd)
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_recent_fs = lambda: [
                {"kind": "curiosity", "folder": str(dd / "NewClient")}]
            try:
                watcher.run_cycle(force=True)
                led = core.read_json(dd / "opportunities.json", [])
                self.assertTrue(any(r["kind"] == "curiosity" for r in led))
                self.assertEqual(fired, [])            # context-only
            finally:
                self._restore(orig)

    def test_curiosity_v2_event_flows_to_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd)
            watcher.sample_recent_fs = lambda: [
                {"kind": "curiosity", "folder": str(dd / "New"), "via": "mdfind"}]
            try:
                watcher.run_cycle(force=True)
                led = core.read_json(dd / "opportunities.json", [])
                self.assertTrue(any(r["kind"] == "curiosity" and
                                    r["evidence"].get("via") == "mdfind"
                                    for r in led))
            finally:
                self._restore(orig)

    def test_daily_cap_blocks_notification_not_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            core.write_json(dd / "opportunity_prefs.json", {"daily": {
                "date": core.now_local().strftime("%Y-%m-%d"), "count": 5}})
            fired = []
            orig = self._env(dd, procs={"zoom.us"})
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            try:
                watcher.run_cycle(force=True)
                led = core.read_json(dd / "opportunities.json", [])
                self.assertEqual(led[0]["kind"], "meeting-start")  # recorded
                self.assertEqual(fired, [])                        # silent
            finally:
                self._restore(orig)

    def test_presence_transition_logged(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            core.write_json(dd / "presence.json",
                            {"state": "away", "since": core.now_utc().isoformat(),
                             "idle_s": 999, "front_app": None})
            orig = self._env(dd, state="here", front="Terminal")
            try:
                watcher.run_cycle(force=True)
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"presence"', habits)
                self.assertIn('"away"', habits)
            finally:
                self._restore(orig)

    def test_stale_meeting_end_recorded_but_muted(self):
        # A 5h "meeting" (machine slept through quiet hours) must never fire
        # a real notification; the ledger still gets the record, with the
        # duration-free stale message, and the habit log carries stale: true.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired = []
            orig = self._env(dd)
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            try:
                started = (core.now_utc()
                           - timedelta(seconds=5 * 3600)).isoformat()
                core.write_json(dd / "meeting_state.json",
                                {"active": {"app": "zoom.us",
                                            "started": started}})
                watcher.run_cycle(force=True)          # end fires (no procs)
                self.assertEqual(fired, [])            # stale news: silent
                led = core.read_json(dd / "opportunities.json", [])
                recs = [r for r in led if r["kind"] == "meeting-end"]
                self.assertEqual(len(recs), 1)         # still recorded
                self.assertIn("zoom.us", recs[0]["offer_msg"])
                self.assertNotIn("300", recs[0]["offer_msg"])  # no duration
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"stale": true', habits)
            finally:
                self._restore(orig)

    def test_meeting_end_expires_same_app_start_offer(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, procs={"zoom.us"})
            try:
                watcher.run_cycle(force=True)          # start
                watcher.sample_assertions_raw = lambda: ""
                watcher.run_cycle(force=True)          # end
                led = core.read_json(dd / "opportunities.json", [])
                statuses = {r["kind"]: r["status"] for r in led}
                self.assertEqual(statuses["meeting-start"], "expired")
                self.assertEqual(statuses["meeting-end"], "offered")
            finally:
                self._restore(orig)

    def test_net_habit_logged_when_sample_present(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd)
            watcher.sample_net = lambda: {"iface": "en0", "rx_Bps": 1234.5,
                                          "tx_Bps": 678.9}
            try:
                watcher.run_cycle(force=True)
                habits = (dd / "habits.jsonl").read_text().splitlines()
                net_lines = [h for h in habits if '"kind": "net"' in h]
                self.assertEqual(len(net_lines), 1)
                self.assertIn('"iface": "en0"', net_lines[0])
                self.assertIn("1234.5", net_lines[0])
                self.assertIn("678.9", net_lines[0])
            finally:
                self._restore(orig)

    def test_net_habit_absent_and_no_crash_when_sample_none(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd)   # default stub: watcher.sample_net -> None
            try:
                watcher.run_cycle(force=True)   # must not raise
                habits_path = dd / "habits.jsonl"
                if habits_path.exists():
                    habits = habits_path.read_text()
                    self.assertNotIn('"kind": "net"', habits)
            finally:
                self._restore(orig)

    def test_meeting_active_logs_meeting_net_habit(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, procs={"zoom.us"})
            watcher.sample_net = lambda: {"iface": "en0", "rx_Bps": 5000.0,
                                          "tx_Bps": 4900.0}
            try:
                watcher.run_cycle(force=True)          # meeting starts + active
                habits = (dd / "habits.jsonl").read_text().splitlines()
                mn_lines = [h for h in habits if '"kind": "meeting-net"' in h]
                self.assertEqual(len(mn_lines), 1)
                self.assertIn('"app": "zoom.us"', mn_lines[0])
                self.assertIn("5000.0", mn_lines[0])
                self.assertIn("4900.0", mn_lines[0])
            finally:
                self._restore(orig)

    def test_meeting_start_evidence_carries_net_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = self._env(dd, procs={"zoom.us"})
            net_snap = {"iface": "en0", "rx_Bps": 5000.0, "tx_Bps": 4900.0}
            watcher.sample_net = lambda: net_snap
            try:
                watcher.run_cycle(force=True)
                led = core.read_json(dd / "opportunities.json", [])
                rec = next(r for r in led if r["kind"] == "meeting-start")
                self.assertEqual(rec["evidence"]["net"], net_snap)
            finally:
                self._restore(orig)

    def test_suppressed_kind_records_but_no_popup(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired = []
            orig = self._env(dd, procs={"zoom.us"})
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            try:
                for _ in range(3):
                    opportunities.decline_kind("meeting-start")
                watcher.run_cycle(force=True)
                led = core.read_json(dd / "opportunities.json", [])
                self.assertTrue(any(r["kind"] == "meeting-start" for r in led))
                self.assertEqual(fired, [])
            finally:
                self._restore(orig)

    def test_build_finished_offer(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            fired = []
            orig = self._env(dd)   # default stub: watcher.sample_builds -> {}
            core.write_json(dd / "build_state.json",
                            {"123": {"cmd": "npm", "etime_s": 300}})
            watcher.desktop_notify = lambda t, m: fired.append((t, m)) or True
            try:
                watcher.run_cycle(force=True)
                offers = [f for f in fired if f[0] == "Sundial"]
                self.assertEqual(len(offers), 1)
                self.assertIn("npm", offers[0][1])
                led = core.read_json(dd / "opportunities.json", [])
                self.assertTrue(any(r["kind"] == "build-finished" for r in led))
            finally:
                self._restore(orig)


class TestOwnerModel(unittest.TestCase):
    def test_distill_stretches_and_latency(self):
        now = core.now_utc()
        t0 = now - timedelta(hours=5)
        evts = [
            {"kind": "presence", "from": "away", "to": "here",
             "ts": t0.isoformat()},
            {"kind": "answered", "latency_s": 120,
             "ts": (t0 + timedelta(minutes=10)).isoformat()},
            {"kind": "presence", "from": "here", "to": "away",
             "ts": (t0 + timedelta(hours=2)).isoformat()},
            {"kind": "presence", "from": "away", "to": "elsewhere",
             "ts": (t0 + timedelta(hours=3)).isoformat()},
            {"kind": "answered", "latency_s": 480,
             "ts": (t0 + timedelta(hours=3, minutes=5)).isoformat()},
            {"kind": "fire", "rung": 1, "state": "away",
             "defer_reason": "pause", "deferred_s": 20.0, "muted": True,
             "ts": (t0 + timedelta(hours=4)).isoformat()},
            {"kind": "estimate", "task": "x", "est_s": 100, "actual_s": 150,
             "ratio": 1.5, "ts": now.isoformat()},
        ]
        m = owner_model.distill(evts, now)
        self.assertEqual(m["active_stretches"]["count"], 1)   # one closed stretch
        self.assertAlmostEqual(m["active_stretches"]["median_m"], 120, delta=1)
        self.assertEqual(m["reply_latency"]["count"], 2)
        self.assertEqual(m["reply_latency"]["median_s"], 300)  # even count -> mean of 120,480
        self.assertEqual(m["fires"]["muted"], 1)
        self.assertEqual(m["estimates"]["count"], 1)
        # owner-driven only: presence away->here (+1), answered (+1),
        # presence here->away is EXCLUDED (to=away), presence away->elsewhere
        # (+1), answered (+1), fire state=away is EXCLUDED, estimate is
        # EXCLUDED (not an owner-activity kind) -> sum == 4
        self.assertEqual(sum(m["hourly_activity"]), 4)
        self.assertGreater(m["data_span_days"], 0.2)

    def test_distill_hourly_ignores_net_telemetry(self):
        now = core.now_utc()
        t0 = now - timedelta(hours=1)
        evts = [
            {"kind": "net", "iface": "en0", "rx_Bps": 100, "tx_Bps": 50,
             "ts": t0.isoformat()},
            {"kind": "meeting-net", "app": "zoom.us", "rx_Bps": 100,
             "tx_Bps": 50, "ts": (t0 + timedelta(minutes=1)).isoformat()},
        ]
        m = owner_model.distill(evts, now)
        self.assertEqual(sum(m["hourly_activity"]), 0)

    def test_refresh_roundtrip_and_age_skip(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = core.DATA
            core.DATA = dd
            try:
                (dd / "habits.jsonl").write_text(json.dumps(
                    {"kind": "fire", "rung": 1, "state": "away",
                     "defer_reason": "none", "deferred_s": 0.0,
                     "muted": False,
                     "ts": core.now_utc().isoformat()}) + "\n")
                m = owner_model.refresh(force=True)
                self.assertEqual(m["fires"]["total"], 1)
                self.assertTrue((dd / "owner_model.json").exists())
                self.assertIsNone(owner_model.refresh())   # young -> skip
            finally:
                core.DATA = orig


class TestEstimatorHelpers(unittest.TestCase):
    def test_parse_duration_forms(self):
        import estimator
        self.assertEqual(estimator.parse_duration("30m"), 1800.0)
        self.assertEqual(estimator.parse_duration("1h"), 3600.0)
        self.assertEqual(estimator.parse_duration("1800s"), 1800.0)
        self.assertEqual(estimator.parse_duration("1800"), 1800.0)
        self.assertEqual(estimator.parse_duration("1h30m"), 5400.0)
        self.assertEqual(estimator.parse_duration("1.5h"), 5400.0)
        self.assertIsNone(estimator.parse_duration(""))
        self.assertIsNone(estimator.parse_duration(None))
        self.assertIsNone(estimator.parse_duration("abc"))
        # strict: garbage must NOT parse to a confidently-wrong number
        self.assertIsNone(estimator.parse_duration("-30m"))   # leading sign
        self.assertIsNone(estimator.parse_duration("30ms"))   # unknown unit
        self.assertIsNone(estimator.parse_duration("1e3s"))   # sci notation
        self.assertIsNone(estimator.parse_duration("1h -30m"))

    def test_percentile_interpolates(self):
        import estimator
        v = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(estimator.percentile(v, 0.0), 1.0)
        self.assertEqual(estimator.percentile(v, 1.0), 4.0)
        self.assertAlmostEqual(estimator.percentile(v, 0.5), 2.5)
        self.assertEqual(estimator.percentile([7.0], 0.9), 7.0)
        with self.assertRaises(ValueError):
            estimator.percentile([], 0.5)


class TestEstimatorCalibrate(unittest.TestCase):
    def test_ratio_high_confidence(self):
        import estimator
        sample = [0.59, 0.60, 0.73, 0.85, 0.93, 1.75, 2.45]  # the 7 real ratios
        r = estimator.calibrate(100.0, sample)
        self.assertEqual(r["n"], 7)
        self.assertEqual(r["confidence"], "high")
        self.assertAlmostEqual(r["p50_s"], 85.0, places=5)      # 100 * 0.85
        # P90 = linear-interp percentile at 0.9 of the 7 ratios:
        # k=5.4 -> 1.75*0.6 + 2.45*0.4 = 2.03 (NOT the raw 1.75 value).
        self.assertAlmostEqual(r["p90_s"], 203.0, places=5)

    def test_ratio_small_n_widens_and_flags(self):
        import estimator
        r = estimator.calibrate(100.0, [0.8, 0.9])              # n=2
        self.assertEqual(r["confidence"], "low")
        self.assertAlmostEqual(r["p90_s"], 200.0, places=5)     # 100 * max(0.89, 2.0)
        self.assertAlmostEqual(r["p50_s"], 85.0, places=0)      # still median-based

    def test_ratio_zero_data_identity(self):
        import estimator
        r = estimator.calibrate(100.0, [])
        self.assertEqual(r, {"p50_s": 100.0, "p90_s": 200.0, "n": 0,
                             "confidence": "none"})

    def test_absolute_mode_and_empty(self):
        import estimator
        r = estimator.calibrate(None, [600.0], absolute=True)   # 1 latency sample
        self.assertEqual(r["confidence"], "low")
        self.assertEqual(r["p50_s"], 600.0)
        self.assertEqual(r["p90_s"], 1200.0)                    # max(600, 600*2)
        empty = estimator.calibrate(None, [], absolute=True)
        self.assertEqual(empty, {"p50_s": None, "p90_s": None, "n": 0,
                                 "confidence": "none"})


class TestEstimatorLedger(unittest.TestCase):
    def _write(self, dd, lines):
        (dd / "habits.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_reads_and_filters(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            self._write(dd, [
                json.dumps({"kind": "estimate", "task": "a", "ratio": 0.8}),
                "not json",                                   # skipped
                json.dumps(42),                               # non-dict, skipped
                json.dumps({"kind": "estimate", "task": "b", "ratio": None}),
                json.dumps({"kind": "answered", "latency_s": 654.0}),
                json.dumps({"kind": "net", "rx_Bps": 1.0}),   # irrelevant
            ])
            ev = estimator._read_habits(dd)
            ratios, used = estimator._estimate_ratios(ev)
            self.assertEqual(ratios, [0.8])                   # None-ratio dropped
            self.assertIsNone(used)
            self.assertEqual(estimator._answered_latencies(ev), [654.0])

    def test_bucket_falls_back_below_threshold(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            self._write(dd, [json.dumps({"kind": "estimate", "task": f"t{i}",
                                         "ratio": 1.0}) for i in range(6)]
                        + [json.dumps({"kind": "estimate", "task": "b",
                                       "ratio": 0.5, "bucket": "build"})])
            ev = estimator._read_habits(dd)
            ratios, used = estimator._estimate_ratios(ev, bucket="build")
            self.assertIsNone(used)                           # only 1 'build' < 5
            self.assertEqual(len(ratios), 7)                  # global fallback

    def test_bucket_selected_when_enough(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            self._write(dd,
                [json.dumps({"kind": "estimate", "task": f"g{i}", "ratio": 1.0})
                 for i in range(3)]
                + [json.dumps({"kind": "estimate", "task": f"b{i}",
                               "ratio": 0.5, "bucket": "build"})
                   for i in range(5)])                        # exactly BUCKET_MIN_N
            ev = estimator._read_habits(dd)
            ratios, used = estimator._estimate_ratios(ev, bucket="build")
            self.assertEqual(used, "build")
            self.assertEqual(ratios, [0.5] * 5)               # tagged subset only
            out = estimator.estimate_execution(100.0, dd, bucket="build")
            self.assertEqual(out["bucket"], "build")

    def test_record_estimate_carries_cid(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            estimator.record_estimate(d, "t", 60, actual_s=90, bucket="build",
                                      cid="abc12345")
            e = json.loads((Path(d) / "habits.jsonl").read_text().strip())
            self.assertEqual(e["cid"], "abc12345")
            self.assertEqual(e["bucket"], "build")

    def test_sanity_line_warns_only_with_data_and_overrun(self):
        import estimator
        calib = {"p50_s": 700.0, "p90_s": 7200.0, "n": 6, "confidence": "high"}
        line = estimator.sanity_line(3600.0, 5400.0, calib)
        self.assertIsNotNone(line)
        self.assertIn("P90", line)
        # no data -> silent
        none_calib = {"p50_s": None, "p90_s": None, "n": 0, "confidence": "none"}
        self.assertIsNone(estimator.sanity_line(3600.0, 5400.0, none_calib))
        # p90 fits inside the deadline -> silent
        ok = {"p50_s": 700.0, "p90_s": 4000.0, "n": 6, "confidence": "high"}
        self.assertIsNone(estimator.sanity_line(3600.0, 5400.0, ok))
        # no deadline -> silent
        self.assertIsNone(estimator.sanity_line(3600.0, None, calib))

    def test_calibration_health_counts_both_clocks(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            estimator.record_estimate(d, "a", 100, actual_s=150)
            estimator.record_estimate(d, "b", 100, actual_s=50)
            estimator.record_estimate(d, "open", 100)          # open: excluded
            with open(Path(d) / "habits.jsonl", "a") as f:
                f.write(json.dumps({"kind": "answered",
                                    "latency_s": 30.0}) + "\n")
            h = estimator.calibration_health(d)
            self.assertEqual(h["n_exec"], 2)
            self.assertAlmostEqual(h["p50_ratio"], 1.0)
            self.assertEqual(h["confidence"], "low")
            self.assertEqual(h["n_review"], 1)
            self.assertEqual(
                estimator.calibration_health(Path(d) / "nope")["n_exec"], 0)


class TestBudgetFlags(unittest.TestCase):
    def _c(self, cid, minutes_ago, p90_min=40.0, status="open", kind=None):
        created = (datetime.now(timezone.utc)
                   - timedelta(minutes=minutes_ago)).isoformat()
        c = {"id": cid, "text": f"task {cid}", "status": status,
             "created_at": created,
             "est": {"est_s": p90_min * 60 * 0.8, "p50_s": p90_min * 48,
                     "p90_s": p90_min * 60, "n": 8, "confidence": "high"}}
        if kind:
            c["kind"] = kind
        return c

    def test_crossing_fires_once(self):
        now = datetime.now(timezone.utc)
        c = self._c("aa", minutes_ago=22)     # 55% of a 40m P90
        lines, fired = estimator.budget_flags([c], {}, now)
        self.assertEqual(len(lines), 1)
        self.assertIn("50%", lines[0])
        # same state again -> silent
        lines2, _ = estimator.budget_flags([c], fired, now)
        self.assertEqual(lines2, [])

    def test_multiple_thresholds_highest_only(self):
        now = datetime.now(timezone.utc)
        c = self._c("bb", minutes_ago=34)     # 85% -> crossed 0.5 and 0.8
        lines, fired = estimator.budget_flags([c], {}, now)
        self.assertEqual(len(lines), 1)       # one line, the highest
        self.assertIn("80%", lines[0])
        self.assertEqual(sorted(fired["bb"]), [0.5, 0.8])

    def test_stale_and_closed_and_asks_skipped(self):
        now = datetime.now(timezone.utc)
        stale = self._c("cc", minutes_ago=40 * 4)          # >3x P90
        closed = self._c("dd", minutes_ago=22, status="done")
        ask = self._c("ee", minutes_ago=22, kind="awaiting-reply")
        no_est = {"id": "ff", "text": "bare", "status": "open",
                  "created_at": now.isoformat()}
        lines, fired = estimator.budget_flags(
            [stale, closed, ask, no_est], {}, now)
        self.assertEqual(lines, [])
        self.assertEqual(fired, {})

    def test_malformed_degrade_silent(self):
        now = datetime.now(timezone.utc)
        junk = [{"id": "gg", "status": "open", "est": "not-a-dict",
                 "created_at": "garbage"}, "not-even-a-dict"]
        lines, fired = estimator.budget_flags(junk, "bad-state", now)
        self.assertEqual(lines, [])


class TestEstimatorAPI(unittest.TestCase):
    def _ledger(self, dd, ratios, latencies=()):
        lines = [json.dumps({"kind": "estimate", "task": f"t{i}",
                             "est_s": 100, "actual_s": 100 * r, "ratio": r})
                 for i, r in enumerate(ratios)]
        lines += [json.dumps({"kind": "answered", "latency_s": x})
                  for x in latencies]
        (dd / "habits.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_execution_matches_real_ratios(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            self._ledger(dd, [0.59, 0.60, 0.73, 0.85, 0.93, 1.75, 2.45])
            r = estimator.estimate_execution(100.0, dd)
            self.assertEqual(r["confidence"], "high")
            self.assertAlmostEqual(r["p50_s"], 85.0, places=5)
            self.assertIsNone(r["bucket"])

    def test_review_thin_and_timeline_sums(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            self._ledger(dd, [0.8, 0.9, 1.0, 1.1, 1.2], latencies=[600.0])
            rv = estimator.estimate_review(dd)
            self.assertEqual(rv["confidence"], "low")          # n=1
            self.assertEqual(rv["p90_s"], 1200.0)
            t = estimator.estimate_timeline(100.0, dd)
            self.assertAlmostEqual(t["end_to_end_p50_s"],
                                   t["execution"]["p50_s"] + 600.0, places=5)

    def test_timeline_omits_review_when_none(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            self._ledger(dd, [0.8, 0.9, 1.0, 1.1, 1.2])        # no answered events
            t = estimator.estimate_timeline(100.0, dd)
            self.assertEqual(t["review"]["confidence"], "none")
            self.assertEqual(t["end_to_end_p50_s"], t["execution"]["p50_s"])

    def test_timeline_zero_latency_added_not_dropped(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            self._ledger(dd, [0.8, 0.9, 1.0, 1.1, 1.2], latencies=[0.0])
            rv = estimator.estimate_review(dd)
            self.assertEqual(rv["p50_s"], 0.0)
            self.assertEqual(rv["confidence"], "low")          # n=1, real data
            t = estimator.estimate_timeline(100.0, dd)
            self.assertEqual(t["end_to_end_p50_s"], t["execution"]["p50_s"])


class TestEstimatorRecord(unittest.TestCase):
    def test_record_complete_and_preregister(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            estimator.record_estimate(dd, "taskA", 200, actual_s=100, bucket="build")
            estimator.record_estimate(dd, "taskB", 300)          # pre-register
            events = estimator._read_habits(dd)
            a = [e for e in events if e.get("task") == "taskA"][0]
            b = [e for e in events if e.get("task") == "taskB"][0]
            self.assertEqual(a["ratio"], 0.5)
            self.assertEqual(a["bucket"], "build")
            self.assertIsNone(b["actual_s"])
            self.assertIsNone(b["ratio"])

    def test_record_creates_missing_dir(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d) / "does" / "not" / "exist"
            estimator.record_estimate(dd, "t", 100, actual_s=50)
            self.assertTrue((dd / "habits.jsonl").exists())
            self.assertEqual(estimator._read_habits(dd)[0]["ratio"], 0.5)

    def test_record_negative_actual_gets_no_ratio(self):
        import estimator
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            estimator.record_estimate(dd, "neg", 100, actual_s=-50)
            self.assertIsNone(estimator._read_habits(dd)[0]["ratio"])


class TestEstimateCLI(unittest.TestCase):
    def test_cli_prints_two_clocks(self):
        import io
        import contextlib
        orig = core.DATA
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            core.DATA = dd
            try:
                (dd / "habits.jsonl").write_text("\n".join(
                    json.dumps({"kind": "estimate", "task": f"t{i}",
                                "est_s": 100, "actual_s": 100 * r, "ratio": r})
                    for i, r in enumerate([0.59, 0.6, 0.73, 0.85, 0.93, 1.75, 2.45])
                ) + "\n", encoding="utf-8")
                sys.path.insert(
                    0, str(Path(__file__).resolve().parent.parent / "cli"))
                import estimate as estimate_cli
                out = io.StringIO()
                old_argv = sys.argv
                sys.argv = ["estimate", "wire the verb", "--raw", "100s"]
                try:
                    with contextlib.redirect_stdout(out):
                        estimate_cli.main()
                finally:
                    sys.argv = old_argv
                text = out.getvalue()
                self.assertIn("P50", text)
                self.assertIn("execution", text.lower())
                self.assertIn("End-to-end", text)
            finally:
                core.DATA = orig

    def test_cli_bad_raw_exits_nonzero(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
        import estimate as estimate_cli
        old_argv = sys.argv
        sys.argv = ["estimate", "x", "--raw", "notaduration"]
        try:
            with self.assertRaises(SystemExit) as cm:
                estimate_cli.main()
            self.assertNotEqual(cm.exception.code, 0)
        finally:
            sys.argv = old_argv


class TestPolicyTiers(unittest.TestCase):
    def test_normal_row_equals_legacy_constants(self):
        self.assertEqual(policy.TIER_TABLE["normal"]["offsets"], (600, 1200, 3000))
        self.assertEqual(policy.TIER_TABLE["normal"]["ceiling"], 5400)
        self.assertEqual(policy.TIER_TABLE["normal"]["rungs"], 3)

    def test_tier_of_defaults_and_reads(self):
        self.assertEqual(policy.tier_of({}), "normal")
        self.assertEqual(policy.tier_of({"weight": "high"}), "high")
        self.assertEqual(policy.tier_of({"weight": "low"}), "low")
        self.assertEqual(policy.tier_of({"weight": "bogus"}), "normal")

    def test_all_tiers_offsets_match_rungs(self):
        for t in ("low", "normal", "high"):
            self.assertIn(t, policy.TIER_TABLE)
            self.assertEqual(len(policy.TIER_TABLE[t]["offsets"]),
                             policy.TIER_TABLE[t]["rungs"])


class TestMenubarSync(unittest.TestCase):
    def test_refresh_fires_swiftbar_url(self):
        seen = []
        orig = core._menubar_spawn
        core._menubar_spawn = lambda cmd: seen.append(cmd)
        try:
            core.refresh_menubar()
            self.assertEqual(len(seen), 1)
            self.assertIn("swiftbar://refreshplugin?name=sundial", " ".join(seen[0]))
        finally:
            core._menubar_spawn = orig

    def test_refresh_never_raises(self):
        orig = core._menubar_spawn
        core._menubar_spawn = lambda cmd: (_ for _ in ()).throw(OSError("no swiftbar"))
        try:
            core.refresh_menubar()   # must swallow
        finally:
            core._menubar_spawn = orig


class TestEstimateCapture(unittest.TestCase):
    def _tmp(self):
        d = tempfile.TemporaryDirectory()
        self._orig = (core.DATA, core.COMMITMENTS)
        core.DATA = Path(d.name)
        core.COMMITMENTS = Path(d.name) / "commitments.json"
        self.addCleanup(lambda: setattr(core, "DATA", self._orig[0]))
        self.addCleanup(lambda: setattr(core, "COMMITMENTS", self._orig[1]))
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _events(self, d):
        p = d / "habits.jsonl"
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

    def test_explicit_est_wins_and_opens_event(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="45m",
                                  bucket="build")
        self.assertEqual(rec["est"]["est_s"], 2700.0)
        self.assertEqual(rec["est"]["bucket"], "build")
        self.assertIn("p90_s", rec["est"])
        ev = self._events(d)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["cid"], rec["id"])
        self.assertIsNone(ev[0]["actual_s"])

    def test_due_derived_when_no_est(self):
        d = self._tmp()
        rec = core.add_commitment("ship y", "+1h")
        self.assertAlmostEqual(rec["est"]["est_s"], 3600.0, delta=5.0)
        self.assertEqual(len(self._events(d)), 1)

    def test_no_est_no_due_no_capture(self):
        d = self._tmp()
        rec = core.add_commitment("someday z")
        self.assertNotIn("est", rec)
        self.assertEqual(self._events(d), [])

    def test_awaiting_reply_never_opens_execution_estimate(self):
        d = self._tmp()
        rec = core.add_commitment("q?", "+10m", kind="awaiting-reply",
                                  est_str="10m")
        self.assertNotIn("est", rec)
        self.assertEqual(self._events(d), [])

    def test_bad_est_string_is_ignored_not_fatal(self):
        self._tmp()
        rec = core.add_commitment("ship w", "+1h", est_str="soonish")
        # falls back to due-derived; the verb layer is where strict parse
        # errors surface to the human
        self.assertAlmostEqual(rec["est"]["est_s"], 3600.0, delta=5.0)

    def test_done_records_actual_and_ratio(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="1h")
        out = core.resolve_commitment(rec["id"], "done")
        self.assertIsInstance(out, dict)
        closes = [e for e in self._events(d) if e.get("actual_s") is not None]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["cid"], rec["id"])
        self.assertEqual(closes[0]["est_s"], 3600.0)
        self.assertIsNotNone(closes[0]["ratio"])

    def test_non_done_close_records_nothing(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="1h")
        core.resolve_commitment(rec["id"], "declined")
        self.assertEqual(
            [e for e in self._events(d) if e.get("actual_s") is not None], [])

    def test_double_done_records_once(self):
        d = self._tmp()
        rec = core.add_commitment("ship x", "+2h", est_str="1h")
        core.resolve_commitment(rec["id"], "done")
        core.resolve_commitment(rec["id"], "done")
        self.assertEqual(
            len([e for e in self._events(d)
                 if e.get("actual_s") is not None]), 1)

    def test_done_without_estimate_records_nothing(self):
        d = self._tmp()
        rec = core.add_commitment("someday z")
        self.assertTrue(core.resolve_commitment(rec["id"], "done"))
        self.assertEqual(self._events(d), [])

    def test_resolve_missing_returns_none(self):
        self._tmp()
        self.assertIsNone(core.resolve_commitment("nope", "done"))

    def test_remember_est_flags_and_sanity(self):
        self._tmp()
        # seed history: chronic 2x overrun so P90 blows any tight deadline
        import estimator
        for i in range(6):
            estimator.record_estimate(core.DATA, f"h{i}", 100, actual_s=200)
        import contextlib
        import importlib
        import io
        sys.path.insert(0, str(Path(core.__file__).resolve().parent.parent
                               / "cli"))
        import remember
        importlib.reload(remember)
        orig_refresh = core.refresh_menubar
        core.refresh_menubar = lambda: None
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["remember", "tight promise", "--due", "+1h",
                    "--est", "50m", "--bucket", "build"]
        try:
            with contextlib.redirect_stdout(buf):
                remember.main()
        finally:
            sys.argv = argv
            core.refresh_menubar = orig_refresh
        out = buf.getvalue()
        self.assertIn("recorded [", out)
        self.assertIn("P90", out)   # 50m * 2.0 ratio = 100m > 60m deadline


class TestAutonomyGate(unittest.TestCase):
    def test_irreversible_never_proceeds(self):
        d = policy.autonomy_decision({"irreversible": True, "confidence": 0.99})
        self.assertEqual(d["action"], "require_explicit_yes")

    def test_high_confidence_reversible_proceeds(self):
        self.assertEqual(policy.autonomy_decision({"confidence": 0.95})["action"], "proceed")
        self.assertEqual(policy.autonomy_decision({"confidence": 0.99})["action"], "proceed")

    def test_below_bar_stands_down(self):
        for conf in (0.0, 0.5, 0.8, 0.9499):
            self.assertEqual(policy.autonomy_decision({"confidence": conf})["action"],
                             "stand_down", f"conf={conf}")

    def test_no_or_garbage_confidence_stands_down(self):
        for c in ({}, {"confidence": None}, {"confidence": "high"}):
            self.assertEqual(policy.autonomy_decision(c)["action"], "stand_down")

    def test_total_never_raises(self):
        for c in (None, {"irreversible": "yes"}, {"confidence": 1.0}):
            self.assertIn(policy.autonomy_decision(c)["action"],
                          ("require_explicit_yes", "proceed", "stand_down"))

    def test_present_silence_proceeds_in_band(self):
        for conf in (0.80, 0.90, 0.9499):
            d = policy.autonomy_decision(
                {"confidence": conf}, {"ripe_here_cycles": 3})
            self.assertEqual(d["action"], "proceed", f"conf={conf}")
            self.assertIn("present-silence", d["reason"])

    def test_present_silence_never_touches_irreversible(self):
        d = policy.autonomy_decision(
            {"irreversible": True, "confidence": 0.99},
            {"ripe_here_cycles": 100})
        self.assertEqual(d["action"], "require_explicit_yes")

    def test_present_silence_needs_enough_cycles(self):
        for cycles in (0, 1, 2):
            d = policy.autonomy_decision(
                {"confidence": 0.90}, {"ripe_here_cycles": cycles})
            self.assertEqual(d["action"], "stand_down", f"cycles={cycles}")

    def test_present_silence_never_below_band(self):
        d = policy.autonomy_decision(
            {"confidence": 0.79}, {"ripe_here_cycles": 100})
        self.assertEqual(d["action"], "stand_down")

    def test_present_silence_garbage_entry_stands_down(self):
        for entry in (None, {}, {"ripe_here_cycles": None},
                      {"ripe_here_cycles": "9"}, {"ripe_here_cycles": True},
                      {"ripe_here_cycles": 3.0}, "not-a-dict"):
            d = policy.autonomy_decision({"confidence": 0.90}, entry)
            self.assertEqual(d["action"], "stand_down", f"entry={entry!r}")

    def test_high_confidence_needs_no_presence(self):
        # ≥ 0.95 proceeds exactly as before, entry or not.
        for entry in (None, {"ripe_here_cycles": 0}):
            self.assertEqual(
                policy.autonomy_decision({"confidence": 0.95}, entry)["action"],
                "proceed")


class TestWallTimeGuard(unittest.TestCase):
    """done on a long-idle commitment must not poison calibration."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _events(self):
        p = self.data / "habits.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_outlier_ratio_recorded_null_with_note(self):
        # actual 21x the estimate -> ratio must be None, note must explain
        estimator.record_estimate(self.data, "t", 1200.0, actual_s=1200.0 * 21,
                                  force_null_ratio=True,
                                  note="wall-time outlier, excluded")
        e = self._events()[-1]
        self.assertIsNone(e["ratio"])
        self.assertIn("wall-time", e["note"])
        self.assertEqual(e["actual_s"], 1200.0 * 21)   # actual preserved

    def test_normal_close_unaffected(self):
        estimator.record_estimate(self.data, "t", 1200.0, actual_s=6000.0)
        e = self._events()[-1]
        self.assertAlmostEqual(e["ratio"], 5.0)
        self.assertNotIn("note", e)

    def test_null_ratio_excluded_from_calibration(self):
        estimator.record_estimate(self.data, "a", 100.0, actual_s=80.0)
        estimator.record_estimate(self.data, "b", 100.0, actual_s=100.0 * 30,
                                  force_null_ratio=True, note="wall-time")
        out = estimator.estimate_execution(1200, self.data)
        self.assertEqual(out["n"], 1)   # only the sane sample counts

    def test_direct_record_estimate_auto_nulls_outlier_without_force(self):
        # FIX4: the guard now lives INSIDE record_estimate itself -- a
        # direct call (no force_null_ratio, no caller note) with a ratio
        # past WALL_OUTLIER_MAX_RATIO must self-null and self-explain.
        estimator.record_estimate(self.data, "t", 100.0, actual_s=100.0 * 21)
        e = self._events()[-1]
        self.assertIsNone(e["ratio"])
        self.assertIn("wall-time", e["note"])
        self.assertEqual(e["actual_s"], 2100.0)   # actual still preserved

    def test_direct_record_estimate_sane_ratio_unaffected(self):
        estimator.record_estimate(self.data, "t", 100.0, actual_s=100.0 * 5)
        e = self._events()[-1]
        self.assertAlmostEqual(e["ratio"], 5.0)
        self.assertNotIn("note", e)

    def test_close_estimate_guards_wall_outlier(self):
        # end-to-end through core: commitment created long ago, closed now
        import core as core_mod
        old_data = core_mod.DATA
        try:
            self._repoint_core(core_mod)
            created = core_mod.now_utc() - timedelta(days=5)
            rec = {"id": "cafe0001", "text": "long idler", "status": "open",
                   "created_at": created.isoformat(),
                   "est": {"est_s": 1200.0, "bucket": "ops"}}
            core_mod.write_json(core_mod.COMMITMENTS, [rec])
            core_mod.resolve_commitment("cafe0001", "done")
            e = self._events()[-1]
            self.assertIsNone(e["ratio"])
            self.assertIn("wall-time", e["note"])
        finally:
            self._restore_core(core_mod, old_data)

    def test_close_estimate_normal_still_records_ratio(self):
        import core as core_mod
        old_data = core_mod.DATA
        try:
            self._repoint_core(core_mod)
            created = core_mod.now_utc() - timedelta(minutes=15)
            rec = {"id": "cafe0002", "text": "quick one", "status": "open",
                   "created_at": created.isoformat(),
                   "est": {"est_s": 1200.0, "bucket": "ops"}}
            core_mod.write_json(core_mod.COMMITMENTS, [rec])
            core_mod.resolve_commitment("cafe0002", "done")
            e = self._events()[-1]
            self.assertIsNotNone(e["ratio"])
            self.assertLess(e["ratio"], 2.0)
        finally:
            self._restore_core(core_mod, old_data)

    # helpers: repoint core's module paths at the temp dir the way
    # TestCore.setUp does (copy that exact mechanism -- DATA, COMMITMENTS,
    # and any module-level derived paths).
    def _repoint_core(self, core_mod):
        core_mod.DATA = self.data
        core_mod.COMMITMENTS = self.data / "commitments.json"
        core_mod.LEDGER = self.data / "session-ledger.json"
        core_mod.BIRTH = self.data / "birth.json"
        core_mod.WEIGHTS = self.data / "memory-weights.json"

    def _restore_core(self, core_mod, old_data):
        core_mod.DATA = old_data
        core_mod.COMMITMENTS = old_data / "commitments.json"
        core_mod.LEDGER = old_data / "session-ledger.json"
        core_mod.BIRTH = old_data / "birth.json"
        core_mod.WEIGHTS = old_data / "memory-weights.json"


class TestSnooze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_snooze_active_true_within_window(self):
        import watcher as w
        now = datetime.now(timezone.utc)
        (self.data / "snooze.json").write_text(json.dumps(
            {"until": (now + timedelta(minutes=30)).isoformat(),
             "set_at": now.isoformat()}))
        self.assertTrue(w.snooze_active(now, data_dir=self.data))

    def test_snooze_active_false_when_expired(self):
        import watcher as w
        now = datetime.now(timezone.utc)
        (self.data / "snooze.json").write_text(json.dumps(
            {"until": (now - timedelta(minutes=1)).isoformat(),
             "set_at": now.isoformat()}))
        self.assertFalse(w.snooze_active(now, data_dir=self.data))

    def test_snooze_active_false_no_file_or_garbage(self):
        import watcher as w
        now = datetime.now(timezone.utc)
        self.assertFalse(w.snooze_active(now, data_dir=self.data))
        (self.data / "snooze.json").write_text("{not json")
        self.assertFalse(w.snooze_active(now, data_dir=self.data))

    def test_breakthrough_filter_keeps_high_tier_ceiling_only(self):
        import watcher as w
        # batch entries are (commitment, entry, rung, message, ceiling).
        # policy.tier_of reads the "weight" field (see lib/policy.py), not
        # "tier". snooze_filter(batch, now) now LIVE re-checks
        # wall_ceiling_passed itself rather than trusting the batch's stale
        # ceiling snapshot (element [4]) -- build commitments a real
        # wall_ceiling_passed can evaluate (created_at vs. each tier's
        # ceiling), not bare tier-only dicts.
        now = datetime.now(timezone.utc)
        past_high = (now - timedelta(
            seconds=policy.TIER_TABLE["high"]["ceiling"] + 10)).isoformat()
        fresh_high = now.isoformat()
        past_normal = (now - timedelta(
            seconds=policy.TIER_TABLE["normal"]["ceiling"] + 10)).isoformat()
        high_ceiling = ({"id": "a", "weight": "high", "created_at": past_high},
                        {}, 3, "m", True)
        high_no_ceiling = ({"id": "b", "weight": "high",
                            "created_at": fresh_high}, {}, 1, "m", False)
        norm_ceiling = ({"id": "c", "weight": "normal",
                         "created_at": past_normal}, {}, 3, "m", True)
        kept = w.snooze_filter(
            [high_ceiling, high_no_ceiling, norm_ceiling], now)
        self.assertEqual([b[0]["id"] for b in kept], ["a"])

    def test_breakthrough_filter_uses_live_recheck_not_stale_flag(self):
        # The batch's own ceiling flag (element [4]) is a snapshot taken
        # earlier in the cycle; snooze_filter must ignore it and re-derive
        # ceiling-passed from `now` itself. A high-tier item whose stale
        # flag says True but whose real created_at is still short of the
        # ceiling must NOT break through.
        import watcher as w
        now = datetime.now(timezone.utc)
        fresh_high_but_stale_flag_true = (
            {"id": "z", "weight": "high", "created_at": now.isoformat()},
            {}, 1, "m", True)   # element [4]=True is a lie for this `now`
        kept = w.snooze_filter([fresh_high_but_stale_flag_true], now)
        self.assertEqual(kept, [])

    def test_return_nudge_held_when_snoozed_logs_habit(self):
        # FIX1: a snoozed, normal-tier return-nudge must be held (no popup,
        # no chime, no entry/count advance) and logged as a snooze-hold, not
        # silently dropped -- AND it must be resolved right there, inside
        # the return-nudge branch's own `continue`, never falling through
        # into the generic pending_ping/batch/wait_for_breakpoint path
        # (the old bug: `if returned and not snoozed` skipped the whole
        # branch when snoozed, so the item fell through and paid for a
        # real bounded wait it should never have entered).
        # desktop_notify/chime/speak_final/_spawn are ALL stubbed to no-op
        # recorders -- this reaches run_cycle's delivery path, so it must
        # never be able to leak a real popup, sound, or speech.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.PRESENCE_FILE, watcher.sample_presence,
                    watcher.desktop_notify, watcher.wait_for_breakpoint,
                    watcher.sample_assertions_raw, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds,
                    watcher.chime, watcher.speak_final, watcher._spawn)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.PRESENCE_FILE = dd / "presence.json"
            fired, chimed, spoken = [], [], []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.chime = lambda *a, **k: chimed.append(a) or None
            watcher.speak_final = lambda *a, **k: spoken.append(a) or None
            watcher._spawn = lambda cmd: None
            bp_calls = []
            watcher.wait_for_breakpoint = (
                lambda *a, **k: bp_calls.append(1) or ("bound", 0.0))
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_net = lambda: None
            watcher.sample_recent_fs = lambda: []
            watcher.sample_builds = lambda: {}
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(dd / "snooze.json", {
                    "until": (core.now_utc()
                             + timedelta(minutes=30)).isoformat(),
                    "set_at": core.now_utc().isoformat()})
                past = (core.now_utc() - timedelta(seconds=1800)).isoformat()
                core.write_json(watcher.PRESENCE_FILE,
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 1300.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.sample_presence = lambda: {"state": "elsewhere",
                                                   "idle_s": 2.0,
                                                   "front_app": "Figma"}
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])              # held, not delivered
                self.assertEqual(chimed, [])
                self.assertEqual(spoken, [])
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 0)  # not advanced
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"snooze-hold"', habits)
                self.assertIn('"held": 1', habits)
                self.assertEqual(bp_calls, [])   # never entered the batch path
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.PRESENCE_FILE, watcher.sample_presence,
                 watcher.desktop_notify, watcher.wait_for_breakpoint,
                 watcher.sample_assertions_raw, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds,
                 watcher.chime, watcher.speak_final, watcher._spawn) = orig

    def test_return_nudge_breakthrough_high_tier_delivers_despite_snooze(self):
        # FIX1 breakthrough case: high tier + wall ceiling passed still
        # delivers the return-nudge even while snoozed. chime/speak_final
        # are stubbed to no-op recorders (never let real audio/speech fire
        # from a test); the recorders let us confirm chime WAS invoked
        # (delivery really happened) without ever touching afplay/say.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.PRESENCE_FILE, watcher.sample_presence,
                    watcher.desktop_notify, watcher.wait_for_breakpoint,
                    watcher.sample_assertions_raw, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds,
                    watcher.chime, watcher.speak_final, watcher._spawn)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.PRESENCE_FILE = dd / "presence.json"
            fired, chimed, spoken = [], [], []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.chime = lambda *a, **k: chimed.append(a) or None
            watcher.speak_final = lambda *a, **k: spoken.append(a) or None
            watcher._spawn = lambda cmd: None
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_net = lambda: None
            watcher.sample_recent_fs = lambda: []
            watcher.sample_builds = lambda: {}
            try:
                core.write_json(dd / "snooze.json", {
                    "until": (core.now_utc()
                             + timedelta(minutes=30)).isoformat(),
                    "set_at": core.now_utc().isoformat()})
                now = core.now_utc()
                created = now - timedelta(
                    seconds=policy.TIER_TABLE["high"]["ceiling"] + 60)
                c = {"id": "hi000002", "created_at": created.isoformat(),
                     "due_at": created.isoformat(), "text": "urgent ask",
                     "source": "t", "status": "open",
                     "kind": "awaiting-reply", "weight": "high"}
                core.write_json(core.COMMITMENTS, [c])
                past = (now - timedelta(seconds=1800)).isoformat()
                core.write_json(watcher.PRESENCE_FILE,
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                core.write_json(watcher.NOTIFIED, {c["id"]: {
                    "count": 0, "last": None, "unseen_s": 0.0,
                    "here_s": 0.0, "last_cycle": now.isoformat()}})
                watcher.sample_presence = lambda: {"state": "elsewhere",
                                                   "idle_s": 2.0,
                                                   "front_app": "Figma"}
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)          # breaks through
                # Delivered via the RETURN_POOL voice, not a generic ladder
                # pool -- the item must stay on the return-nudge path even
                # when it breaks through a snooze, never fall through to
                # pending_ping's tier-neutral/rung copy.
                self.assertTrue(any(w in fired[0] for w in
                                    ("away", "absence", "gone")), fired[0])
                self.assertEqual(len(chimed), 1)   # return chime fired
                self.assertEqual(spoken, [])       # return path never speaks
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[c["id"]]["count"], 3)  # terminal rung
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.PRESENCE_FILE, watcher.sample_presence,
                 watcher.desktop_notify, watcher.wait_for_breakpoint,
                 watcher.sample_assertions_raw, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds,
                 watcher.chime, watcher.speak_final, watcher._spawn) = orig

    def test_batch_path_held_when_snoozed_before_breakpoint_wait(self):
        # FIX2: snooze filtering must run BEFORE wait_for_breakpoint, so a
        # cycle whose whole batch is snooze-held never even calls the
        # bounded-deferral watch. wait_for_breakpoint is stubbed to raise if
        # invoked -- this fails loudly if filtering regresses to running
        # after the wait (the old, wasteful/racy order).
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.sample_assertions_raw,
                    watcher.sample_screen_locked, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds,
                    watcher.wait_for_breakpoint)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired, chimed, spoken = [], [], []
            orig_notify = watcher.desktop_notify
            orig_spawn = watcher._spawn
            orig_chime, orig_speak = watcher.chime, watcher.speak_final
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher._spawn = lambda cmd: None
            watcher.chime = lambda *a, **k: chimed.append(a) or None
            watcher.speak_final = lambda *a, **k: spoken.append(a) or None
            watcher.sample_presence = lambda: {"state": "elsewhere",
                                               "idle_s": 2.0,
                                               "front_app": "Figma"}
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_screen_locked = lambda: False
            watcher.sample_net = lambda: None
            watcher.sample_recent_fs = lambda: []
            watcher.sample_builds = lambda: {}

            def _boom(*a, **k):
                raise AssertionError(
                    "wait_for_breakpoint must not run once snooze empties "
                    "the batch")
            watcher.wait_for_breakpoint = _boom
            try:
                core.write_json(dd / "snooze.json", {
                    "until": (core.now_utc()
                             + timedelta(minutes=30)).isoformat(),
                    "set_at": core.now_utc().isoformat()})
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 600.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])
                self.assertEqual(chimed, [])
                self.assertEqual(spoken, [])
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 0)
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"snooze-hold"', habits)
                self.assertIn('"held": 1', habits)
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.sample_assertions_raw,
                 watcher.sample_screen_locked, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds,
                 watcher.wait_for_breakpoint) = orig
                watcher.desktop_notify = orig_notify
                watcher._spawn = orig_spawn
                watcher.chime, watcher.speak_final = orig_chime, orig_speak

    def test_batch_path_breakthrough_high_tier_ceiling_delivers_despite_snooze(self):
        # FIX2 breakthrough case on the batch path (not the return-nudge
        # path): a high-tier item past its wall ceiling still fires even
        # though the owner has an active snooze window. chime/speak_final
        # are stubbed to no-op recorders so the real terminal-rung
        # (speaks-final) delivery path can be exercised without ever
        # touching afplay/say.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.sample_assertions_raw,
                    watcher.sample_screen_locked, watcher.sample_net,
                    watcher.sample_recent_fs, watcher.sample_builds,
                    watcher.wait_for_breakpoint)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired, chimed, spoken = [], [], []
            orig_notify = watcher.desktop_notify
            orig_spawn = watcher._spawn
            orig_chime, orig_speak = watcher.chime, watcher.speak_final
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher._spawn = lambda cmd: None
            watcher.chime = lambda *a, **k: chimed.append(a) or None
            watcher.speak_final = lambda *a, **k: spoken.append(a) or None
            # Presence unknown -> legacy degrade path, same trick as
            # test_run_cycle_fires_and_persists: deterministic, no live
            # sensors, and NOT a "returned" transition (isolates the batch
            # path from FIX1's return-nudge path).
            watcher.sample_presence = lambda: {"state": None, "idle_s": None,
                                               "front_app": None}
            watcher.sample_assertions_raw = lambda: ""
            watcher.sample_screen_locked = lambda: False
            watcher.sample_net = lambda: None
            watcher.sample_recent_fs = lambda: []
            watcher.sample_builds = lambda: {}
            watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
            try:
                core.write_json(dd / "snooze.json", {
                    "until": (core.now_utc()
                             + timedelta(minutes=30)).isoformat(),
                    "set_at": core.now_utc().isoformat()})
                now = core.now_utc()
                created = now - timedelta(
                    seconds=policy.TIER_TABLE["high"]["ceiling"] + 60)
                c = {"id": "hi000001", "created_at": created.isoformat(),
                     "due_at": created.isoformat(), "text": "urgent",
                     "source": "t", "status": "open",
                     "kind": "awaiting-reply", "weight": "high"}
                core.write_json(core.COMMITMENTS, [c])
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
                self.assertEqual(len(chimed), 1)     # terminal chime fired
                self.assertEqual(len(spoken), 1)     # high-tier speaks final
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[c["id"]]["count"], 3)  # terminal rung
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.sample_assertions_raw,
                 watcher.sample_screen_locked, watcher.sample_net,
                 watcher.sample_recent_fs, watcher.sample_builds,
                 watcher.wait_for_breakpoint) = orig
                watcher.desktop_notify = orig_notify
                watcher._spawn = orig_spawn
                watcher.chime, watcher.speak_final = orig_chime, orig_speak


class TestSnoozeCLI(unittest.TestCase):
    """cli/snooze.py: off-parsing and status delegation to core.snooze_active
    (FIX3 + FIX6). No prior coverage existed for this CLI verb."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self._orig_data = core.DATA
        core.DATA = self.data
        self._orig_refresh = core.refresh_menubar
        core.refresh_menubar = lambda: None
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cli"))
        import snooze as snooze_cli
        self.snooze_cli = snooze_cli
        self._orig_argv = sys.argv

    def tearDown(self):
        core.DATA = self._orig_data
        core.refresh_menubar = self._orig_refresh
        sys.argv = self._orig_argv
        self.tmp.cleanup()

    def _run(self, *argv):
        import io
        import contextlib
        sys.argv = ["snooze", *argv]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.snooze_cli.main()
        return out.getvalue()

    def test_off_case_insensitive_clears(self):
        core.write_json(self.data / "snooze.json", {
            "until": (core.now_utc() + timedelta(minutes=30)).isoformat(),
            "set_at": core.now_utc().isoformat()})
        for variant in ("OFF", "Off", "off"):
            core.write_json(self.data / "snooze.json", {
                "until": (core.now_utc()
                         + timedelta(minutes=30)).isoformat(),
                "set_at": core.now_utc().isoformat()})
            out = self._run(variant)
            self.assertIn("cleared", out)
            self.assertFalse((self.data / "snooze.json").exists())

    def test_status_uses_core_snooze_active(self):
        out = self._run()
        self.assertIn("not snoozed", out)
        core.write_json(self.data / "snooze.json", {
            "until": (core.now_utc() + timedelta(minutes=30)).isoformat(),
            "set_at": core.now_utc().isoformat()})
        out = self._run()
        self.assertIn("snoozed for another", out)

    def test_status_expired_reads_not_snoozed(self):
        core.write_json(self.data / "snooze.json", {
            "until": (core.now_utc() - timedelta(minutes=1)).isoformat(),
            "set_at": core.now_utc().isoformat()})
        out = self._run()
        self.assertIn("not snoozed", out)


class TestSessionClaim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_claim_roundtrip_fresh(self):
        now = datetime.now(timezone.utc)
        core.write_session_claim(data_dir=self.data, ttl_s=3600)
        self.assertTrue(core.session_claim_fresh(now, data_dir=self.data))

    def test_claim_expired_missing_garbage(self):
        now = datetime.now(timezone.utc)
        self.assertFalse(core.session_claim_fresh(now, data_dir=self.data))
        (self.data / "session_claim.json").write_text(json.dumps(
            {"ts": (now - timedelta(seconds=3700)).isoformat(), "ttl_s": 3600}))
        self.assertFalse(core.session_claim_fresh(now, data_dir=self.data))
        (self.data / "session_claim.json").write_text("{broken")
        self.assertFalse(core.session_claim_fresh(now, data_dir=self.data))

    def test_speak_append_prune_cap(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=25)).isoformat()
        core.write_json(self.data / "session_speak.json", {"queue": [
            {"cid": "old1", "rung": 1, "message": "m", "text": "t",
             "ts": old, "consumed": True}]})
        n = core.append_session_speak({"cid": "new1", "rung": 1, "message": "m",
                                       "text": "t", "ts": now.isoformat(),
                                       "consumed": False}, data_dir=self.data)
        self.assertEqual(n, 0)                               # no cap trim yet
        q = core.read_json(self.data / "session_speak.json", {})["queue"]
        self.assertEqual([e["cid"] for e in q], ["new1"])   # old consumed pruned
        evicted = 0
        for i in range(25):
            evicted += core.append_session_speak(
                {"cid": f"c{i}", "rung": 1, "message": "m", "text": "t",
                 "ts": now.isoformat(), "consumed": False}, data_dir=self.data)
        q = core.read_json(self.data / "session_speak.json", {})["queue"]
        self.assertEqual(len(q), 20)                        # cap, drop-oldest
        self.assertEqual(q[-1]["cid"], "c24")
        # 1 (new1) + 25 appends - 20 kept = 6 unconsumed entries lost overall,
        # all of them reported back (none were consumed, so nothing else to
        # evict first).
        self.assertEqual(evicted, 6)

    def test_speak_cap_evicts_consumed_before_unconsumed(self):
        now = datetime.now(timezone.utc)
        queue = [{"cid": f"done{i}", "rung": 1, "message": "m", "text": "t",
                  "ts": now.isoformat(), "consumed": True} for i in range(10)]
        queue += [{"cid": f"open{i}", "rung": 1, "message": "m", "text": "t",
                   "ts": now.isoformat(), "consumed": False} for i in range(14)]
        core.write_json(self.data / "session_speak.json", {"queue": queue})
        n = core.append_session_speak({"cid": "open14", "rung": 1, "message": "m",
                                       "text": "t", "ts": now.isoformat(),
                                       "consumed": False}, data_dir=self.data)
        # 25 entries total (10 consumed-recent + 15 unconsumed), 5 over cap:
        # the 5 oldest consumed entries absorb the whole trim, so no
        # unconsumed fire is lost and the return is 0.
        self.assertEqual(n, 0)
        q = core.read_json(self.data / "session_speak.json", {})["queue"]
        self.assertEqual(len(q), 20)
        unconsumed = [e["cid"] for e in q if not e["consumed"]]
        consumed = [e["cid"] for e in q if e["consumed"]]
        self.assertEqual(set(unconsumed), {f"open{i}" for i in range(15)})
        self.assertEqual(consumed, [f"done{i}" for i in range(5, 10)])  # newest 5 kept

    def test_speak_cap_evicts_oldest_unconsumed_when_none_consumed(self):
        now = datetime.now(timezone.utc)
        queue = [{"cid": f"open{i}", "rung": 1, "message": "m", "text": "t",
                  "ts": now.isoformat(), "consumed": False} for i in range(25)]
        core.write_json(self.data / "session_speak.json", {"queue": queue})
        n = core.append_session_speak({"cid": "open25", "rung": 1, "message": "m",
                                       "text": "t", "ts": now.isoformat(),
                                       "consumed": False}, data_dir=self.data)
        # 26 unconsumed entries, 6 over cap, nothing consumed to absorb it ->
        # 6 unconsumed fires evicted, reported back, newest 20 survive.
        self.assertEqual(n, 6)
        q = core.read_json(self.data / "session_speak.json", {})["queue"]
        self.assertEqual(len(q), 20)
        self.assertEqual([e["cid"] for e in q], [f"open{i}" for i in range(6, 26)])

    def test_speak_append_failsafe_returns_zero(self):
        bad = self.data / "not_a_dir.json"
        bad.write_text("i am a file, not a directory")
        n = core.append_session_speak({"cid": "x", "rung": 1, "message": "m",
                                       "text": "t", "consumed": False}, data_dir=bad)
        self.assertEqual(n, 0)


class TestSessionRouting(unittest.TestCase):
    """Fresh session_claim.json routes ripe fires to session_speak.json
    instead of a popup; rung 3 mirrors both channels; a stale/missing claim
    pops exactly as before T2. Fixture/stub shapes copied from TestSnooze's
    run_cycle tests (incident-#5 rail: desktop_notify/chime/speak_final/
    _spawn always stubbed to recorders, restored in finally)."""

    def _stub(self, dd):
        orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                watcher.PRESENCE_FILE, watcher.sample_presence,
                watcher.desktop_notify, watcher.wait_for_breakpoint,
                watcher.sample_assertions_raw, watcher.sample_net,
                watcher.sample_recent_fs, watcher.sample_builds,
                watcher.sample_screen_locked,
                watcher.chime, watcher.speak_final, watcher._spawn)
        core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
        watcher.NOTIFIED = dd / "notified.json"
        watcher.PRESENCE_FILE = dd / "presence.json"
        fired, chimed, spoken = [], [], []
        watcher.desktop_notify = lambda t, m: fired.append(m) or True
        watcher.chime = lambda *a, **k: chimed.append(a) or None
        watcher.speak_final = lambda *a, **k: spoken.append(a) or None
        watcher._spawn = lambda cmd: None
        watcher.wait_for_breakpoint = lambda *a, **k: ("bound", 0.0)
        watcher.sample_assertions_raw = lambda: ""
        watcher.sample_net = lambda: None
        watcher.sample_recent_fs = lambda: []
        watcher.sample_builds = lambda: {}
        watcher.sample_screen_locked = lambda: False
        return orig, fired, chimed, spoken

    def _unstub(self, orig):
        (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
         watcher.PRESENCE_FILE, watcher.sample_presence,
         watcher.desktop_notify, watcher.wait_for_breakpoint,
         watcher.sample_assertions_raw, watcher.sample_net,
         watcher.sample_recent_fs, watcher.sample_builds,
         watcher.sample_screen_locked,
         watcher.chime, watcher.speak_final, watcher._spawn) = orig

    def test_fresh_claim_rung1_routes_to_queue_not_popup(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 999.0,
                                               "front_app": None}
            try:
                core.write_session_claim(data_dir=dd, ttl_s=3600)
                rec = core.add_commitment("water the plants", "+0m")
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])   # popup recorder EMPTY
                self.assertEqual(chimed, [])
                self.assertEqual(spoken, [])
                q = core.read_json(dd / "session_speak.json", {}).get("queue", [])
                self.assertEqual(len(q), 1)
                self.assertEqual(q[0]["cid"], rec["id"])
                self.assertEqual(q[0]["rung"], 1)
                self.assertIn("message", q[0])
                self.assertFalse(q[0]["consumed"])
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 1)
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"channel": "session"', habits)
            finally:
                self._unstub(orig)

    def test_stale_claim_pops_as_today(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 999.0,
                                               "front_app": None}
            try:
                # no session_claim.json written -> claim is missing/stale
                rec = core.add_commitment("water the plants", "+0m")
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)
                self.assertEqual(len(chimed), 1)
                self.assertFalse((dd / "session_speak.json").exists())
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 1)
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"channel": "desktop"', habits)
            finally:
                self._unstub(orig)

    def test_rung3_mirrors_both_channels(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            # legacy-degrade fixture (state None) forces a deterministic
            # terminal rung 3, same shape as TestSnooze's FIX2 tests.
            watcher.sample_presence = lambda: {"state": None, "idle_s": None,
                                               "front_app": None}
            try:
                core.write_session_claim(data_dir=dd, ttl_s=3600)
                now = core.now_utc()
                created = now - timedelta(
                    seconds=policy.TIER_TABLE["high"]["ceiling"] + 60)
                c = {"id": "hi000009", "created_at": created.isoformat(),
                     "due_at": created.isoformat(), "text": "urgent ask",
                     "source": "t", "status": "open",
                     "kind": "awaiting-reply", "weight": "high"}
                core.write_json(core.COMMITMENTS, [c])
                watcher.run_cycle(force=True)
                q = core.read_json(dd / "session_speak.json", {}).get("queue", [])
                self.assertEqual(len(q), 1)
                self.assertEqual(q[0]["rung"], 3)
                self.assertEqual(len(fired), 1)          # AND popup mirrors
                self.assertEqual(len(chimed), 1)
                self.assertEqual(len(spoken), 1)          # high tier forces speech
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[c["id"]]["count"], 3)
            finally:
                self._unstub(orig)

    def test_snooze_holds_session_channel_too(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            watcher.sample_presence = lambda: {"state": "elsewhere", "idle_s": 2.0,
                                               "front_app": "Figma"}
            try:
                core.write_session_claim(data_dir=dd, ttl_s=3600)
                core.write_json(dd / "snooze.json", {
                    "until": (core.now_utc()
                             + timedelta(minutes=30)).isoformat(),
                    "set_at": core.now_utc().isoformat()})
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 600.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])
                self.assertEqual(chimed, [])
                self.assertEqual(spoken, [])
                self.assertFalse((dd / "session_speak.json").exists())
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 0)
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"snooze-hold"', habits)
                self.assertIn('"held": 1', habits)
            finally:
                self._unstub(orig)

    def test_rung_accounting_parity(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 999.0,
                                               "front_app": None}
            try:
                # Run A: no claim -> desktop path.
                rec_a = core.add_commitment("identical ask", "+0m")
                watcher.run_cycle(force=True)
                entry_a = core.read_json(watcher.NOTIFIED, {})[rec_a["id"]]

                # Reset ledgers, run B: fresh claim -> session path.
                core.write_json(core.COMMITMENTS, [])
                core.write_json(watcher.NOTIFIED, {})
                (dd / "habits.jsonl").unlink(missing_ok=True)
                core.write_session_claim(data_dir=dd, ttl_s=3600)
                rec_b = core.add_commitment("identical ask", "+0m")
                watcher.run_cycle(force=True)
                entry_b = core.read_json(watcher.NOTIFIED, {})[rec_b["id"]]

                self.assertEqual(entry_a["count"], entry_b["count"])
                self.assertEqual(set(entry_a.keys()), set(entry_b.keys()))
            finally:
                self._unstub(orig)

    def test_speak_trim_habit_on_hard_cap_eviction(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 999.0,
                                               "front_app": None}
            try:
                core.write_session_claim(data_dir=dd, ttl_s=3600)
                now = core.now_utc()
                preload = [{"cid": f"pre{i}", "rung": 1, "message": "m",
                            "text": "t", "ts": now.isoformat(),
                            "consumed": False} for i in range(20)]
                core.write_json(dd / "session_speak.json", {"queue": preload})
                core.add_commitment("one more ask", "+0m")
                watcher.run_cycle(force=True)
                q = core.read_json(dd / "session_speak.json", {}).get("queue", [])
                self.assertEqual(len(q), 20)   # cap held
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"speak-trim"', habits)
                self.assertIn('"lost": 1', habits)
            finally:
                self._unstub(orig)

    def test_return_nudge_fresh_claim_routes_to_queue_not_popup(self):
        # Coverage gap flagged in task-2-review.md: the return-nudge
        # delivery site (watcher.py ~726-750) is structurally identical to
        # the well-tested batch site but no test in the suite ever drove
        # returned=True together with a fresh claim. presence.json seeding
        # copied verbatim from TestSnooze's return-nudge tests -- the only
        # place in the suite that produces returned=True.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            watcher.sample_presence = lambda: {"state": "elsewhere",
                                               "idle_s": 2.0,
                                               "front_app": "Figma"}
            try:
                core.write_session_claim(data_dir=dd, ttl_s=3600)
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                past = (core.now_utc() - timedelta(seconds=1800)).isoformat()
                core.write_json(watcher.PRESENCE_FILE,
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 700.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])   # popup recorder EMPTY
                self.assertEqual(chimed, [])
                self.assertEqual(spoken, [])
                q = core.read_json(dd / "session_speak.json", {}).get("queue", [])
                self.assertEqual(len(q), 1)
                self.assertEqual(q[0]["cid"], rec["id"])
                self.assertEqual(q[0]["rung"], 1)
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 1)  # advanced
                self.assertIsNotNone(saved[rec["id"]]["last"])
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"kind": "fire"', habits)
                self.assertIn('"channel": "session"', habits)
            finally:
                self._unstub(orig)

    def test_return_nudge_no_claim_pops_as_today(self):
        # Same returned=True setup as above, but with NO session_claim.json
        # -- proves the return-nudge site still falls back to the popup
        # path exactly as it did before T2 when there's no warm session to
        # route to.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig, fired, chimed, spoken = self._stub(dd)
            watcher.sample_presence = lambda: {"state": "elsewhere",
                                               "idle_s": 2.0,
                                               "front_app": "Figma"}
            try:
                # no session_claim.json written -> claim is missing/stale
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                past = (core.now_utc() - timedelta(seconds=1800)).isoformat()
                core.write_json(watcher.PRESENCE_FILE,
                                {"state": "away", "since": past,
                                 "idle_s": 999.0, "front_app": None})
                core.write_json(watcher.NOTIFIED, {rec["id"]: {
                    "count": 0, "last": None, "unseen_s": 700.0,
                    "here_s": 0.0, "last_cycle": core.now_utc().isoformat()}})
                watcher.run_cycle(force=True)
                self.assertEqual(len(fired), 1)   # popup delivered
                self.assertEqual(len(chimed), 1)
                self.assertEqual(spoken, [])
                self.assertFalse((dd / "session_speak.json").exists())
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertEqual(saved[rec["id"]]["count"], 1)
                habits = (dd / "habits.jsonl").read_text()
                self.assertIn('"channel": "desktop"', habits)
            finally:
                self._unstub(orig)


if __name__ == "__main__":
    unittest.main(verbosity=2)
