#!/usr/bin/env python3
"""Tests for the failed-article retry / last-resort-save behavior in watch_likes.

The retry state machine lives inline in watch_for_saves() (an infinite loop that
is impractical to call directly). To test the behavior without refactoring that
loop, we re-implement the exact same decision logic here as `process_pending`,
parameterized on the real module constants and the real helper functions, and
drive it with controlled clock + mocked I/O. If the loop body and this function
ever diverge, these tests are the canary — keep them in lockstep.
"""

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import watch_likes as wl

# Keep test-triggered watcher_log lines out of the production debug.log.
_LOG_DIR = tempfile.TemporaryDirectory()
_LOG_PATCH = mock.patch.object(wl, "DEBUG_LOG_FILE", Path(_LOG_DIR.name) / "debug.log")


def setUpModule():
    _LOG_PATCH.start()


def tearDownModule():
    _LOG_PATCH.stop()
    _LOG_DIR.cleanup()


def process_pending(
    pending_articles,
    seen_articles,
    attempt_counts,
    last_attempt_at,
    now,
):
    """Faithful copy of the per-loop processing block in watch_for_saves().

    Returns the list of show_notification (title, message) pairs emitted, so tests
    can assert on user-visible failure surfacing.
    """
    notifications = []

    def notify(title, message):
        wl.show_notification(title, message)
        notifications.append((title, message))

    for article_id in sorted(pending_articles):
        last = last_attempt_at.get(article_id)
        if last is not None and (now - last) < wl.RETRY_COOLDOWN_SECONDS:
            continue

        attempt = attempt_counts.get(article_id, 0) + 1
        attempt_counts[article_id] = attempt
        last_attempt_at[article_id] = now

        success = wl.process_new_article(article_id)

        if success is True:
            seen_articles.add(article_id)
            pending_articles.discard(article_id)
            attempt_counts.pop(article_id, None)
            last_attempt_at.pop(article_id, None)
        elif attempt < wl.MAX_PROCESS_ATTEMPTS:
            pass  # stay pending, retry after cooldown
        else:
            saved = wl.save_url_only_last_resort(article_id)
            seen_articles.add(article_id)
            pending_articles.discard(article_id)
            attempt_counts.pop(article_id, None)
            last_attempt_at.pop(article_id, None)
            if saved:
                notify("Apple News → Readwise", "⚠️ Saved link only (full text failed)")
            else:
                notify("Apple News → Readwise", "❌ Failed to save article — check the log")

    return notifications


class RetryLogicTest(unittest.TestCase):
    def setUp(self):
        self.pending = set()
        self.seen = set()
        self.attempts = {}
        self.last_at = {}

    def test_success_marks_seen_and_clears_state(self):
        self.pending.add("Aok")
        with mock.patch.object(wl, "process_new_article", return_value=True), \
             mock.patch.object(wl, "show_notification"):
            process_pending(self.pending, self.seen, self.attempts, self.last_at, now=100.0)
        self.assertIn("Aok", self.seen)
        self.assertNotIn("Aok", self.pending)
        self.assertNotIn("Aok", self.attempts)
        self.assertNotIn("Aok", self.last_at)

    def test_failure_under_cap_stays_pending(self):
        self.pending.add("Afail")
        with mock.patch.object(wl, "process_new_article", return_value=False), \
             mock.patch.object(wl, "show_notification"):
            process_pending(self.pending, self.seen, self.attempts, self.last_at, now=100.0)
        # One attempt made, still pending, not yet seen.
        self.assertEqual(self.attempts["Afail"], 1)
        self.assertIn("Afail", self.pending)
        self.assertNotIn("Afail", self.seen)

    def test_cooldown_blocks_immediate_retry(self):
        self.pending.add("Afail")
        with mock.patch.object(wl, "process_new_article", return_value=False) as p, \
             mock.patch.object(wl, "show_notification"):
            process_pending(self.pending, self.seen, self.attempts, self.last_at, now=100.0)
            # Immediately again, within cooldown -> must NOT call process again.
            process_pending(self.pending, self.seen, self.attempts, self.last_at, now=105.0)
            self.assertEqual(p.call_count, 1)
            self.assertEqual(self.attempts["Afail"], 1)
            # After cooldown elapses -> retried.
            process_pending(
                self.pending, self.seen, self.attempts, self.last_at,
                now=100.0 + wl.RETRY_COOLDOWN_SECONDS + 1,
            )
            self.assertEqual(p.call_count, 2)
            self.assertEqual(self.attempts["Afail"], 2)

    def test_exhausts_retries_then_url_only_save_and_notify(self):
        self.pending.add("Adead")
        t = 100.0
        with mock.patch.object(wl, "process_new_article", return_value=None), \
             mock.patch.object(wl, "save_url_only_last_resort", return_value=True) as saver, \
             mock.patch.object(wl, "show_notification") as notif:
            notifications = []
            for _ in range(wl.MAX_PROCESS_ATTEMPTS):
                notifications += process_pending(
                    self.pending, self.seen, self.attempts, self.last_at, now=t,
                )
                t += wl.RETRY_COOLDOWN_SECONDS + 1
            # After MAX attempts: URL-only save invoked exactly once, marked seen,
            # dropped from pending, and a "link only" notification fired.
            saver.assert_called_once_with("Adead")
            self.assertIn("Adead", self.seen)
            self.assertNotIn("Adead", self.pending)
            self.assertTrue(any("link only" in m for _, m in notifications))
            self.assertTrue(notif.called)

    def test_url_only_save_failure_notifies_hard_failure(self):
        self.pending.add("Agone")
        t = 100.0
        with mock.patch.object(wl, "process_new_article", return_value=False), \
             mock.patch.object(wl, "save_url_only_last_resort", return_value=False), \
             mock.patch.object(wl, "show_notification"):
            notifications = []
            for _ in range(wl.MAX_PROCESS_ATTEMPTS):
                notifications += process_pending(
                    self.pending, self.seen, self.attempts, self.last_at, now=t,
                )
                t += wl.RETRY_COOLDOWN_SECONDS + 1
            # Even when the last-resort save fails, the article is marked seen
            # (so it stops retrying) and a hard-failure notice is surfaced.
            self.assertIn("Agone", self.seen)
            self.assertTrue(any("Failed to save" in m for _, m in notifications))


class NotificationEscapingTest(unittest.TestCase):
    def test_quotes_in_message_are_escaped(self):
        captured = {}

        def fake_run(args, **kwargs):
            # args = ["osascript", "-e", "<script>"]
            captured["script"] = args[2]
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(wl.subprocess, "run", side_effect=fake_run):
            wl.show_notification('Ti"tle', 'He said "hi" \\ bye')
        script = captured["script"]
        # No unescaped double-quote should prematurely close the AppleScript string.
        self.assertIn('\\"', script)
        self.assertIn("\\\\", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
