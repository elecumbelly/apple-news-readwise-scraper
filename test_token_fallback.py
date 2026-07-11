#!/usr/bin/env python3
"""Tests for Readwise token resolution and the cache-hit/send-failed flow.

Background: the watcher originally read the Readwise token from the macOS
Keychain ONCE at import time. When the Keychain entry was missing, the token
was silently empty and every send failed with a 401 — after the article body
had already been extracted perfectly from the News cache. These tests pin the
fixed behavior:

1. Secrets resolve lazily with a fallback chain: Keychain -> environment
   variable -> project .env file, cached after first success.
2. A 401 from Readwise drops the cached token so a fix (e.g. adding the
   Keychain entry) is picked up without restarting the watcher.
3. When the News cache HIT but the Readwise send failed, process_new_article
   must NOT fall through to the screen-scrape/copy fallback (the content was
   fine — the API call failed) and must return False so the retry loop
   re-attempts later.
"""

import os
import tempfile
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


class SecretResolutionTest(unittest.TestCase):
    def setUp(self):
        wl._SECRET_CACHE.clear()

    def tearDown(self):
        wl._SECRET_CACHE.clear()

    def test_keychain_wins_when_present(self):
        with mock.patch.object(wl, "_get_keychain_value", return_value="kc-token"), \
             mock.patch.dict(os.environ, {"READWISE_TOKEN": "env-token"}):
            self.assertEqual(wl.get_readwise_token(), "kc-token")

    def test_falls_back_to_env_var(self):
        with mock.patch.object(wl, "_get_keychain_value", return_value=""), \
             mock.patch.dict(os.environ, {"READWISE_TOKEN": "env-token"}):
            self.assertEqual(wl.get_readwise_token(), "env-token")

    def test_falls_back_to_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("IMGBB_API_KEY=img-key\nREADWISE_TOKEN=file-token\n")
            with mock.patch.object(wl, "_get_keychain_value", return_value=""), \
                 mock.patch.dict(os.environ, {}, clear=False), \
                 mock.patch.object(wl, "ENV_FILE", env_file):
                os.environ.pop("READWISE_TOKEN", None)
                self.assertEqual(wl.get_readwise_token(), "file-token")

    def test_empty_everywhere_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"  # does not exist
            with mock.patch.object(wl, "_get_keychain_value", return_value=""), \
                 mock.patch.object(wl, "ENV_FILE", env_file):
                os.environ.pop("READWISE_TOKEN", None)
                self.assertEqual(wl.get_readwise_token(), "")

    def test_result_is_cached_after_first_resolve(self):
        with mock.patch.object(wl, "_get_keychain_value", return_value="kc-token") as kc:
            self.assertEqual(wl.get_readwise_token(), "kc-token")
            self.assertEqual(wl.get_readwise_token(), "kc-token")
            self.assertEqual(kc.call_count, 1)

    def test_miss_is_not_cached(self):
        """An empty resolve must retry next time (Keychain may unlock later)."""
        with mock.patch.object(wl, "_get_keychain_value", side_effect=["", "kc-token"]) as kc, \
             mock.patch.object(wl, "ENV_FILE", Path("/nonexistent/.env")):
            os.environ.pop("READWISE_TOKEN", None)
            self.assertEqual(wl.get_readwise_token(), "")
            self.assertEqual(wl.get_readwise_token(), "kc-token")
            self.assertEqual(kc.call_count, 2)


class SendTokenHandlingTest(unittest.TestCase):
    def setUp(self):
        wl._SECRET_CACHE.clear()

    def tearDown(self):
        wl._SECRET_CACHE.clear()

    def test_send_without_token_fails_fast_without_api_call(self):
        with mock.patch.object(wl, "get_readwise_token", return_value=""), \
             mock.patch("requests.post") as post:
            self.assertFalse(wl.send_to_readwise_reader({"url": "https://x.test/a"}))
            post.assert_not_called()

    def test_401_drops_cached_token(self):
        wl._SECRET_CACHE["readwise-token"] = "stale-token"
        response = mock.Mock(status_code=401, text="denied")
        with mock.patch("requests.post", return_value=response):
            self.assertFalse(wl.send_to_readwise_reader({"url": "https://x.test/a"}))
        self.assertNotIn("readwise-token", wl._SECRET_CACHE)


class CacheHitSendFailFlowTest(unittest.TestCase):
    """process_new_article must not screen-scrape when only the SEND failed."""

    CACHED = {
        "title": "T", "html": "<article/>", "text": "x" * 500,
        "summary": "s", "author": None, "url": "https://pub.test/a",
    }

    def test_publisher_url_cache_hit_send_fail_returns_false_no_fetch(self):
        with mock.patch.object(wl, "resolve_apple_news_url", return_value="https://pub.test/a"), \
             mock.patch.object(wl, "lookup_cached_article", return_value=dict(self.CACHED)), \
             mock.patch.object(wl, "send_to_readwise_reader", return_value=False), \
             mock.patch.object(wl, "fetch_and_clean_article") as fetch, \
             mock.patch.object(wl, "process_article_from_screen") as scrape, \
             mock.patch.object(wl, "show_notification"):
            self.assertIs(wl.process_new_article("Aabc"), False)
            fetch.assert_not_called()
            scrape.assert_not_called()

    def test_news_plus_cache_hit_send_fail_returns_false_no_scrape(self):
        apple_url = "https://apple.news/Aabc"
        with mock.patch.object(wl, "resolve_apple_news_url", return_value=apple_url), \
             mock.patch.object(wl, "lookup_cached_article_by_title", return_value=dict(self.CACHED)), \
             mock.patch.object(wl, "send_to_readwise_reader", return_value=False), \
             mock.patch.object(wl, "process_article_from_screen") as scrape, \
             mock.patch.object(wl, "show_notification"):
            self.assertIs(wl.process_new_article("Aabc"), False)
            scrape.assert_not_called()

    def test_news_plus_cache_miss_still_falls_back_to_scrape(self):
        apple_url = "https://apple.news/Aabc"
        with mock.patch.object(wl, "resolve_apple_news_url", return_value=apple_url), \
             mock.patch.object(wl, "lookup_cached_article_by_title", return_value=None), \
             mock.patch.object(wl, "process_article_from_screen", return_value=True) as scrape, \
             mock.patch.object(wl, "show_notification"):
            self.assertIs(wl.process_new_article("Aabc"), True)
            scrape.assert_called_once()

    def test_cache_hit_send_success_returns_true(self):
        with mock.patch.object(wl, "resolve_apple_news_url", return_value="https://pub.test/a"), \
             mock.patch.object(wl, "lookup_cached_article", return_value=dict(self.CACHED)), \
             mock.patch.object(wl, "send_to_readwise_reader", return_value=True), \
             mock.patch.object(wl, "show_notification"):
            self.assertIs(wl.process_new_article("Aabc"), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
