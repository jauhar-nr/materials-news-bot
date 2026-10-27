#!/usr/bin/env python3
"""
Unit tests for Materials News Digest Bot
----------------------------------------
Tests scoring algorithm, text cleaners, RSS parsing, deduplication, and Discord payload builders.
"""

import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from materials_digest import (
    build_discord_embed,
    calculate_impact_score,
    clean_text,
    clean_url,
    fetch_feed,
    load_history,
    parse_date,
    run_digest,
    save_history,
)


class TestTextAndUrlCleaning(unittest.TestCase):
    def test_clean_text_html_stripping(self):
        raw = "<p>This is a <b>major breakthrough</b> in <a href='https://example.com'>batteries</a>.</p>"
        self.assertEqual(clean_text(raw), "This is a major breakthrough in batteries.")

    def test_clean_text_entities(self):
        raw = "Li-ion &amp; solid-state &quot;miracle&quot; &#39;alloy&#39;"
        self.assertEqual(clean_text(raw), 'Li-ion & solid-state "miracle" \'alloy\'')

    def test_clean_text_arxiv_prefix(self):
        raw = "arXiv:2610.12345v1 Announce Type: new \n Abstract: Novel 2D perovskite discovered."
        self.assertEqual(clean_text(raw), "Abstract: Novel 2D perovskite discovered.")

    def test_clean_url_utm_stripping(self):
        url = "https://example.com/article/123?utm_source=rss&utm_medium=feed&ref=sciencedaily"
        cleaned = clean_url(url)
        self.assertNotIn("utm_source", cleaned)
        self.assertNotIn("utm_medium", cleaned)
        self.assertIn("ref=sciencedaily", cleaned)


class TestDateParsing(unittest.TestCase):
    def test_parse_rfc2822_date(self):
        date_str = "Wed, 27 Oct 2026 12:00:00 GMT"
        dt = parse_date(date_str)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 27)

    def test_parse_iso_date(self):
        date_str = "2026-10-27T14:30:00Z"
        dt = parse_date(date_str)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 30)

    def test_parse_invalid_date(self):
        self.assertIsNone(parse_date("not-a-date"))
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))


class TestImpactScoring(unittest.TestCase):
    def test_scoring_title_multiplier(self):
        # Keyword in title gets 2.2x weight
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score_title, badges_title, _ = calculate_impact_score(
            title="Breakthrough in room temperature superconductor",
            description="A novel observation is reported.",
            pub_date=pub_now,
        )

        score_desc, _, _ = calculate_impact_score(
            title="Observation in physics",
            description="Breakthrough in room temperature superconductor reported.",
            pub_date=pub_now,
        )

        self.assertGreater(score_title, score_desc)
        self.assertIn("⚡ Breakthrough", badges_title)
        self.assertIn("⚛️ Quantum & Superconductors", badges_title)

    def test_scoring_recency_boost(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        yesterday = now - datetime.timedelta(hours=10)
        old = now - datetime.timedelta(days=10)

        score_fresh, _, _ = calculate_impact_score(
            title="Solid-state battery research",
            description="Testing new electrolyte",
            pub_date=yesterday,
        )

        score_old, _, _ = calculate_impact_score(
            title="Solid-state battery research",
            description="Testing new electrolyte",
            pub_date=old,
        )

        # Fresh item gets +15 recency boost vs -15 penalty for > 1 week old
        self.assertGreater(score_fresh, score_old)


class TestHistoryTracking(unittest.TestCase):
    def test_load_and_save_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = os.path.join(tmpdir, "history.json")
            
            # Non-existent file starts empty
            seen = load_history(hist_path)
            self.assertEqual(len(seen), 0)

            # Add entries and save
            seen.add("item-1")
            seen.add("item-2")
            seen.add("https://example.com/item-1")
            save_history(hist_path, seen, max_entries=10)

            # Re-read
            reloaded = load_history(hist_path)
            self.assertEqual(len(reloaded), 3)
            self.assertIn("item-1", reloaded)
            self.assertIn("https://example.com/item-1", reloaded)

    def test_save_history_truncation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = os.path.join(tmpdir, "history.json")
            seen = {f"item-{i}" for i in range(100)}
            save_history(hist_path, seen, max_entries=10)

            with open(hist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(len(data), 10)


class TestDiscordEmbed(unittest.TestCase):
    def test_build_discord_embed_structure(self):
        article = {
            "id": "guid-999",
            "title": "Novel 2D MXene with High Energy Density for Fast-Charging Batteries",
            "link": "https://example.com/article/mxene",
            "description": "Researchers developed a new scalable synthesis technique.",
            "source": "ScienceDaily",
            "category": "Materials Science",
            "score": 85.5,
            "badges": ["🔋 Energy & Battery", "🔬 2D & Advanced Materials", "⚡ Breakthrough"],
        }
        embed = build_discord_embed(article)

        self.assertIn("MXene", embed["title"])
        self.assertEqual(embed["url"], "https://example.com/article/mxene")
        self.assertEqual(embed["color"], 0xE67E22)  # Exceptional impact amber color
        self.assertEqual(embed["author"]["name"], "ScienceDaily • Materials Science")
        self.assertEqual(len(embed["fields"]), 2)
        self.assertIn("Exceptional", embed["fields"][0]["value"])
        self.assertIn("Energy & Battery", embed["fields"][1]["value"])


class TestFeedFetching(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_fetch_feed_rss(self, mock_urlopen):
        sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Test Science Feed</title>
                <item>
                    <title>Breakthrough in Perovskite Solar Cells</title>
                    <link>https://example.com/perovskite?utm_source=rss</link>
                    <guid>perov-101</guid>
                    <description>Record efficiency achieved using novel chemical passivation.</description>
                    <pubDate>Wed, 27 Oct 2026 10:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""

        mock_resp = MagicMock()
        mock_resp.read.return_value = sample_xml
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        feed_cfg = {
            "name": "TestFeed",
            "category": "Energy",
            "url": "https://example.com/feed.xml",
            "curated_boost": 5.0,
        }

        articles = fetch_feed(feed_cfg)
        self.assertEqual(len(articles), 1)
        art = articles[0]
        self.assertEqual(art["id"], "perov-101")
        self.assertEqual(art["title"], "Breakthrough in Perovskite Solar Cells")
        self.assertEqual(art["link"], "https://example.com/perovskite")
        self.assertGreater(art["score"], 50.0)
        self.assertIn("⚡ Breakthrough", art["badges"])
        self.assertIn("🔋 Energy & Battery", art["badges"])


class TestRunDigest(unittest.TestCase):
    @patch("materials_digest.fetch_feed")
    def test_run_digest_dry_run(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "id": "art-1",
                "title": "Quantum Spin Liquid Candidate Discovered",
                "link": "https://example.com/spin-liquid",
                "description": "Magnetic measurements reveal topological states.",
                "source": "Phys.org",
                "category": "Physics",
                "score": 90.0,
                "badges": ["⚛️ Quantum & Superconductors"],
                "breakdown": {"matched_keywords": ["quantum", "topological"]},
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            hist_file = os.path.join(tmpdir, "history.json")
            code = run_digest(
                history_file=hist_file,
                limit=1,
                min_score=10.0,
                dry_run=True,
                verbose=False,
            )
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
