#!/usr/bin/env python3
"""Tests for news_cache: ANF parsing, recirculation pruning, URL normalization.

Uses synthetic ANF fixtures so the tests are deterministic and portable (the real
Apple News cache varies by machine and changes over time). A separate live smoke
check lives in the __main__ block of news_cache usage, not here.
"""

import json
import unittest
from pathlib import Path
from unittest import mock

import news_cache as nc


def anf(canonical_url, components, title="Test Title", **md):
    """Build a minimal ANF document dict."""
    metadata = {"canonicalURL": canonical_url}
    metadata.update(md)
    return {"title": title, "metadata": metadata, "components": components}


def comp(role, text=None, children=None):
    c = {"role": role}
    if text is not None:
        c["text"] = text
    if children is not None:
        c["components"] = children
    return c


# A realistic doc: title + intro + nested body inside containers, plus a
# recirculation article_link module carrying a DIFFERENT article's title.
LONG_BODY = "This is a real article paragraph with enough length to count as body. " * 8
REALISTIC_DOC = anf(
    "https://www.example.com/news/2026/01/01/real-story/",
    title="Real Story",
    components=[
        comp("title", "Real Story"),
        comp("intro", "A short standfirst that introduces the piece."),
        comp("section", children=[
            comp("container", children=[
                comp("body", LONG_BODY),
                comp("heading2", "A Subheading"),
                comp("body", "Second body paragraph, also reasonably long here."),
            ]),
        ]),
        # Recirculation module — must be pruned entirely.
        comp("article_link", children=[
            comp("article_title", "Some Unrelated Headline About Another Topic"),
            comp("body", "Teaser text for a different article that must not leak."),
        ]),
        comp("byline", "Jane Reporter"),
    ],
)


class NormalizeUrlTest(unittest.TestCase):
    def test_strips_www_query_fragment_slash_and_scheme(self):
        self.assertEqual(
            nc.normalize_url("http://www.Example.com/a/b/?utm=x&dbf=9#frag"),
            "https://example.com/a/b",
        )

    def test_root_path_slash_preserved(self):
        self.assertEqual(nc.normalize_url("https://example.com/"), "https://example.com")

    def test_empty(self):
        self.assertEqual(nc.normalize_url(""), "")

    def test_idempotent(self):
        once = nc.normalize_url("http://www.example.com/x/?a=1")
        self.assertEqual(nc.normalize_url(once), once)


class BodyExtractionTest(unittest.TestCase):
    def test_recurses_into_nested_containers(self):
        roles_texts = list(nc._iter_body_text(REALISTIC_DOC))
        bodies = [t for r, t in roles_texts if r == "body"]
        self.assertTrue(any(LONG_BODY.strip() in t for t in bodies))
        self.assertTrue(any("Second body paragraph" in t for t in bodies))

    def test_prunes_recirculation_subtree(self):
        texts = [t for _, t in nc._iter_body_text(REALISTIC_DOC)]
        joined = " ".join(texts)
        self.assertNotIn("Unrelated Headline", joined)
        self.assertNotIn("different article that must not leak", joined)

    def test_allowlist_excludes_unknown_roles(self):
        doc = anf("https://e.com/x", [
            comp("body", "Keep this body text that is long enough to be real." * 3),
            comp("advertisement", "BUY NOW"),
            comp("link_button", "Subscribe"),
        ])
        texts = [t for _, t in nc._iter_body_text(doc)]
        self.assertNotIn("BUY NOW", texts)
        self.assertNotIn("Subscribe", texts)


class BuildArticleTest(unittest.TestCase):
    def test_full_article_shape(self):
        art = nc._build_article(REALISTIC_DOC, matched_url="https://www.example.com/news/2026/01/01/real-story/")
        self.assertIsNotNone(art)
        self.assertEqual(art["title"], "Real Story")
        self.assertEqual(art["author"], "Jane Reporter")
        self.assertIn("real article paragraph", art["text"])
        self.assertNotIn("Unrelated Headline", art["text"])
        # Title rendered once as <h1>, not duplicated as a heading.
        self.assertEqual(art["html"].count("<h1>Real Story</h1>"), 1)
        self.assertNotIn("<h2>Real Story</h2>", art["html"])
        # Subheading preserved as <h2>.
        self.assertIn("<h2>A Subheading</h2>", art["html"])

    def test_stub_rejected(self):
        # A doc with a URL but no substantial body (e.g. issue TOC) -> None.
        stub = anf("https://e.com/issue", [comp("title", "Issue Cover")])
        self.assertIsNone(nc._build_article(stub, matched_url="https://e.com/issue"))

    def test_html_escaping(self):
        doc = anf("https://e.com/x", [
            comp("body", "Tom & Jerry <script>alert(1)</script> " + "padding " * 80),
        ])
        art = nc._build_article(doc, matched_url="https://e.com/x")
        self.assertIn("&amp;", art["html"])
        self.assertIn("&lt;script&gt;", art["html"])
        self.assertNotIn("<script>", art["html"])


class LookupByUrlTest(unittest.TestCase):
    """Exercise lookup_by_url with a mocked asset store on disk."""

    def setUp(self):
        # Reset the module-level index between tests.
        nc._reset_for_tests()

    def _write_store(self, tmp: Path, docs):
        store = tmp / "shared-assets-assetstore"
        store.mkdir()
        for i, d in enumerate(docs):
            (store / f":P:1:Token{i}:imgfile").write_text(json.dumps(d), encoding="utf-8")
        return store

    def test_hit_and_miss(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = self._write_store(Path(td), [REALISTIC_DOC])
            with mock.patch.object(nc, "ASSET_STORE", store):
                nc._reset_for_tests()
                hit = nc.lookup_by_url("https://example.com/news/2026/01/01/real-story")
                self.assertIsNotNone(hit)
                self.assertEqual(hit["title"], "Real Story")
                miss = nc.lookup_by_url("https://example.com/nope")
                self.assertIsNone(miss)

    def test_rescan_on_miss_finds_newly_cached(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = self._write_store(Path(td), [REALISTIC_DOC])
            with mock.patch.object(nc, "ASSET_STORE", store):
                nc._reset_for_tests()
                # Prime the index (one article).
                self.assertIsNotNone(nc.lookup_by_url("https://example.com/news/2026/01/01/real-story"))
                # A new article gets cached after the index was built.
                new_doc = anf("https://example.com/fresh/", [
                    comp("body", "Freshly cached article body that is long enough. " * 12),
                ], title="Fresh")
                (store / ":P:1:TokenNew:imgfile").write_text(json.dumps(new_doc), encoding="utf-8")
                # Miss in the cached index -> rescan -> found.
                hit = nc.lookup_by_url("https://example.com/fresh")
                self.assertIsNotNone(hit)
                self.assertEqual(hit["title"], "Fresh")

    def test_missing_store_returns_none(self):
        with mock.patch.object(nc, "ASSET_STORE", Path("/nonexistent/path/xyz")):
            nc._reset_for_tests()
            self.assertIsNone(nc.lookup_by_url("https://example.com/anything"))

    def test_corrupt_blob_is_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = self._write_store(Path(td), [REALISTIC_DOC])
            (store / ":P:1:Garbage:imgfile").write_bytes(b'{"truncated really not json')
            (store / ":P:1:Image:imgfile").write_bytes(b"\x89PNG\r\n\x1a\n binary")
            with mock.patch.object(nc, "ASSET_STORE", store):
                nc._reset_for_tests()
                # Corrupt/binary files don't crash the scan; the good one still resolves.
                hit = nc.lookup_by_url("https://example.com/news/2026/01/01/real-story")
                self.assertIsNotNone(hit)


def anf_no_url(title, components):
    """ANF doc with NO canonicalURL — i.e. an Apple News+ exclusive."""
    return {"title": title, "metadata": {}, "components": components}


# Body text comfortably over the 400-char stub-rejection threshold in _build_article.
PLENTY = "Real article body content that is clearly long enough to count. " * 10


class NormalizeTitleTest(unittest.TestCase):
    def test_strips_punctuation_and_case(self):
        self.assertEqual(
            nc.normalize_title("IRON MAIDEN: BURNING AMBITION!"),
            "iron maiden burning ambition",
        )

    def test_collapses_whitespace(self):
        self.assertEqual(nc.normalize_title("  A   B \t C "), "a b c")

    def test_empty(self):
        self.assertEqual(nc.normalize_title(""), "")
        self.assertEqual(nc.normalize_title("!!! ???"), "")


class LookupByTitleTest(unittest.TestCase):
    def setUp(self):
        nc._reset_for_tests()

    def _store(self, tmp: Path, docs):
        store = tmp / "shared-assets-assetstore"
        store.mkdir()
        for i, d in enumerate(docs):
            (store / f":P:1:T{i}:imgfile").write_text(json.dumps(d), encoding="utf-8")
        return store

    def test_matches_news_plus_exclusive_by_title(self):
        import tempfile
        body = comp("body", "An Apple News+ exclusive body with plenty of text here. " * 10)
        doc = anf_no_url("Running Out of Time", [comp("title", "Running Out of Time"), body])
        with tempfile.TemporaryDirectory() as td:
            store = self._store(Path(td), [doc])
            with mock.patch.object(nc, "ASSET_STORE", store):
                nc._reset_for_tests()
                # Window title carries punctuation/case differences and a publisher suffix.
                art = nc.lookup_by_title("running out of time", fallback_url="https://apple.news/AXYZ")
                self.assertIsNotNone(art)
                self.assertEqual(art["title"], "Running Out of Time")
                # No canonicalURL in the doc, so the fallback apple.news URL is used.
                self.assertEqual(art["url"], "https://apple.news/AXYZ")

    def test_refuses_ambiguous_title(self):
        import tempfile
        long_body = "Body text long enough to be real content goes here. " * 10
        a = anf_no_url("Same Title", [comp("body", long_body + "ALPHA")])
        b = anf_no_url("Same Title", [comp("body", long_body + "BETA")])
        with tempfile.TemporaryDirectory() as td:
            store = self._store(Path(td), [a, b])
            with mock.patch.object(nc, "ASSET_STORE", store):
                nc._reset_for_tests()
                # Two different articles share the title -> must NOT guess.
                self.assertIsNone(nc.lookup_by_title("Same Title"))

    def test_title_miss_returns_none(self):
        import tempfile
        doc = anf_no_url("Known", [comp("body", PLENTY)])
        with tempfile.TemporaryDirectory() as td:
            store = self._store(Path(td), [doc])
            with mock.patch.object(nc, "ASSET_STORE", store):
                nc._reset_for_tests()
                self.assertIsNone(nc.lookup_by_title("Totally Different Title"))

    def test_doc_canonical_url_preferred_over_fallback(self):
        import tempfile
        # If the matched doc DOES have a canonicalURL, it wins over the fallback.
        doc = anf(
            "https://www.site.com/real/",
            [comp("body", PLENTY)],
            title="Has URL",
        )
        with tempfile.TemporaryDirectory() as td:
            store = self._store(Path(td), [doc])
            with mock.patch.object(nc, "ASSET_STORE", store):
                nc._reset_for_tests()
                art = nc.lookup_by_title("Has URL", fallback_url="https://apple.news/AXYZ")
                self.assertEqual(art["url"], "https://www.site.com/real/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
