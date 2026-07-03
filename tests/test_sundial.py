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
from datetime import timedelta, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))

import core  # noqa: E402
import decay  # noqa: E402
import tzutil  # noqa: E402

WATCHER_DIR = Path(__file__).resolve().parent.parent / "watcher"
sys.path.insert(0, str(WATCHER_DIR))

import watcher  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import prompt_submit  # noqa: E402
import session_start  # noqa: E402

import presence  # noqa: E402  (watcher dir already on sys.path)


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
                          "unseen_s": 0.0, "here_s": 0.0, "last_cycle": None})
        d = {"count": 2, "last": "x", "unseen_s": 5.0, "here_s": 1.0, "last_cycle": "t"}
        self.assertEqual(watcher.migrate_entry(d), d)
        self.assertEqual(watcher.migrate_entry(None),
                         {"count": 0, "last": None, "unseen_s": 0.0,
                          "here_s": 0.0, "last_cycle": None})

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
                    watcher.sample_presence)
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
                core.DATA, core.COMMITMENTS, watcher.NOTIFIED, watcher.sample_presence = orig
                watcher.desktop_notify = orig_notify
                watcher._spawn = orig_spawn

    def test_run_cycle_skips_corrupted_row(self):
        # A hand-corrupted commitment row (missing "id") and a hand-corrupted
        # notified.json entry (non-int "count") must not crash the cycle; the
        # one valid awaiting-reply item due now still fires and persists.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence)
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
                core.DATA, core.COMMITMENTS, watcher.NOTIFIED, watcher.sample_presence = orig
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

    def tearDown(self):
        watcher._spawn = self._orig_spawn

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
                    watcher.sample_presence, watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "here",
                                               "idle_s": 1.0,
                                               "front_app": "Terminal"}
            try:
                rec = core.add_commitment("q?", "+0m", kind="awaiting-reply")
                watcher.run_cycle(force=True)
                self.assertEqual(fired, [])  # held while HERE
                saved = core.read_json(watcher.NOTIFIED, {})
                self.assertIn(rec["id"], saved)      # ...but accrual persisted
                self.assertIn("here_s", saved[rec["id"]])
            finally:
                (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                 watcher.sample_presence, watcher.desktop_notify) = orig

    def test_ceiling_fires_even_here_when_created_at_missing(self):
        # Reviewer repro: NO created_at, valid due_at 91 min past. ripe_rung's
        # ceiling basis falls back to due_at (rung 3), so run_cycle's HERE-hold
        # must use the same basis — otherwise the ping starves forever.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "here",
                                               "idle_s": 1.0,
                                               "front_app": "Terminal"}
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
                 watcher.sample_presence, watcher.desktop_notify) = orig

    def test_run_cycle_fires_when_away_and_ripe(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.sample_presence, watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
            watcher.sample_presence = lambda: {"state": "away",
                                               "idle_s": 999.0,
                                               "front_app": None}
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
                 watcher.sample_presence, watcher.desktop_notify) = orig

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
                    watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.PRESENCE_FILE = dd / "presence.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
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
                 watcher.desktop_notify) = orig

    def test_return_transition_keeps_plain_pool_voice(self):
        # Regression: a plain commitment due in the same cycle as an
        # away->back transition must keep its PLAIN_POOL message, never
        # borrow the RETURN_POOL "ripened in your absence" voice.
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            orig = (core.DATA, core.COMMITMENTS, watcher.NOTIFIED,
                    watcher.PRESENCE_FILE, watcher.sample_presence,
                    watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.PRESENCE_FILE = dd / "presence.json"
            fired = []
            watcher.desktop_notify = lambda t, m: fired.append(m) or True
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
                 watcher.desktop_notify) = orig

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
                    watcher.sample_presence, watcher.desktop_notify)
            core.DATA, core.COMMITMENTS = dd, dd / "commitments.json"
            watcher.NOTIFIED = dd / "notified.json"
            watcher.sample_presence = lambda: {"state": "away", "idle_s": 999.0,
                                               "front_app": None}
            watcher.desktop_notify = lambda t, m: True
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
                 watcher.sample_presence, watcher.desktop_notify) = orig


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
        item_lines = [l for l in lines if l.startswith("  - [")]
        self.assertEqual(len(item_lines), 10)
        self.assertIn("  …and 5 more.", lines)

    def test_due_list_caps_each_item_line_text(self):
        past = (core.now_utc() - timedelta(minutes=5)).isoformat()
        core.add_commitment("z" * 5_000, past)
        birth = core.get_or_create_birth()
        block = session_start.build_block(core, birth, None)
        item_lines = [l for l in block.split("\n") if l.startswith("  - [")]
        self.assertEqual(len(item_lines), 1)
        line = item_lines[0]
        self.assertIn("…", line)
        self.assertIn("z" * 200, line)         # first 200 chars kept
        self.assertNotIn("z" * 201, line)      # rest truncated away
        self.assertLess(len(line), 260)        # tag + 200 + ellipsis, bounded


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
