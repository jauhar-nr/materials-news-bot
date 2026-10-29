#!/usr/bin/env python3
"""
Unit tests for Materials News Digest Bot (Academic Edition)
-----------------------------------------------------------
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
    detect_premier_journal,
    fetch_feed,
    load_history,
    parse_date,
    run_digest,
    save_history,
    send_discord_webhook,
)


class TestTextAndUrlCleaning(unittest.TestCase):
    def test_clean_text_html_stripping(self):
        raw = "<p>This is a <b>major breakthrough</b> in <a href='https://example.com'>batteries</a>.</p>"
        self.assertEqual(clean_text(raw), "This is a major breakthrough in batteries.")

    def test_clean_text_entities(self):
        raw = "Li-ion &amp; solid-state &quot;miracle&quot; &#39;alloy&#39;"
        self.assertEqual(clean_text(raw), 'Li-ion & solid-state "miracle" \'alloy\'')

    def test_clean_text_abstract_prefix(self):
        raw = "Abstract: First-principles calculations explore the novel monolayer."
        self.assertEqual(clean_text(raw), "First-principles calculations explore the novel monolayer.")

    def test_clean_text_arxiv_prefix(self):
        raw = "arXiv:2610.12345v1 Announce Type: new \n Abstract: Novel 2D perovskite discovered."
        self.assertEqual(clean_text(raw), "Novel 2D perovskite discovered.")

    def test_clean_text_latex_formulas(self):
        raw = "monolayer ${\\mathrm{CuPdBr}}_{3}$ and machine-learned $\\mathrm{DFT}+U$ in ${\\mathrm{Bi}}_{0.5}{\\mathrm{Sb}}_{1.5}{\\mathrm{Te}}_{3}$"
        self.assertEqual(clean_text(raw), "monolayer CuPdBr3 and machine-learned DFT+U in Bi0.5Sb1.5Te3")

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


class TestJournalDetection(unittest.TestCase):
    def test_detect_premier_journal(self):
        text = "This breakthrough was published in Nature Materials yesterday."
        self.assertEqual(detect_premier_journal(text), "Nature Materials")

    def test_detect_prl(self):
        text = "Researchers report novel spin states in Physical Review Letters."
        self.assertEqual(detect_premier_journal(text), "Physical Review Letters")


class TestImpactScoring(unittest.TestCase):
    def test_scoring_title_multiplier(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score_title, badges_title, _, _ = calculate_impact_score(
            title="Breakthrough in room temperature superconductor",
            description="A novel observation is reported.",
            pub_date=pub_now,
        )

        score_desc, _, _, _ = calculate_impact_score(
            title="Observation in physics",
            description="Breakthrough in room temperature superconductor reported.",
            pub_date=pub_now,
        )

        self.assertGreater(score_title, score_desc)
        self.assertIn("Breakthrough Discovery", badges_title)
        self.assertIn("Quantum & Superconductors", badges_title)

    def test_scoring_journal_boost(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score_nature, _, journal_name, _ = calculate_impact_score(
            title="Observation in high-Tc superconductor",
            description="The study was published in Science and demonstrates novel phases in cuprate crystals.",
            pub_date=pub_now,
        )
        score_plain, _, _, _ = calculate_impact_score(
            title="Observation in high-Tc superconductor",
            description="The study demonstrates novel phases in cuprate crystals.",
            pub_date=pub_now,
        )
        self.assertEqual(journal_name, "Science")
        self.assertGreater(score_nature, score_plain)

    def test_scoring_recency_boost(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        yesterday = now - datetime.timedelta(hours=10)
        old = now - datetime.timedelta(days=15)

        score_fresh, _, _, _ = calculate_impact_score(
            title="Solid-state battery research",
            description="Testing new electrolyte",
            pub_date=yesterday,
        )

        score_old, _, _, _ = calculate_impact_score(
            title="Solid-state battery research",
            description="Testing new electrolyte",
            pub_date=old,
        )

        self.assertGreater(score_fresh, score_old)

    def test_scoring_quantum_computing_disqualification(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score, _, _, breakdown = calculate_impact_score(
            title="Quantum gates set speed record at 6.8 nanoseconds with over 99.98% accuracy",
            description="Researchers from Jülich perform quantum operation on a qubit. The paper was published in Nature Communications.",
            pub_date=pub_now,
        )
        # Must be disqualified (< 50.0 pts) and never cross the 115.0 quality gate
        self.assertLessEqual(score, 45.0)
        self.assertLess(breakdown["negative_penalty"], 0.0)

    def test_scoring_material_anchor_required(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score, _, _, breakdown = calculate_impact_score(
            title="Breakthrough milestone discovery sets speed record",
            description="Researchers achieve unprecedented results in physical sciences. Published in Science.",
            pub_date=pub_now,
        )
        self.assertFalse(breakdown["has_material_anchor"])
        self.assertLessEqual(score, 50.0)

    def test_scoring_pure_materials_breakthrough(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score, _, journal, breakdown = calculate_impact_score(
            title="Sapphire-grown semiconductor film sets bandgap record",
            description="Adding silicon to thin films of aluminum gallium oxide achieves record bandgap. Published in Nature.",
            pub_date=pub_now,
        )
        self.assertTrue(breakdown["has_material_anchor"])
        self.assertEqual(journal, "Nature")
        self.assertGreaterEqual(score, 140.0)

    def test_scoring_tier1_materials_journal(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score, _, journal, breakdown = calculate_impact_score(
            title="Single-crystal growth and physical properties of the antiferromagnetic double perovskite Ba2CoMoO6",
            description="Magnetic and thermodynamic measurements on high-purity single crystals reveal phase transitions.",
            source_name="Physical Review Materials",
            pub_date=pub_now,
        )
        self.assertTrue(breakdown["has_material_anchor"])
        self.assertEqual(breakdown["journal_boost"], 60.0)
        self.assertGreaterEqual(score, 100.0)

    def test_scoring_arxiv_unvetted_hard_cap(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        # Even with many computational and 2D keywords, unvetted arXiv must be capped at 75.0
        score, _, journal, _ = calculate_impact_score(
            title="First-Principles Investigation of Irida-Graphane: A Wide-Band-Gap Semiconductor Derived from Irida-Graphene",
            description="We perform density functional theory DFT calculations, ab initio molecular dynamics, and electronic bandgap simulations to explore device applications.",
            source_name="arXiv",
            pub_date=pub_now,
        )
        self.assertIsNone(journal)
        self.assertLessEqual(score, 75.0)

    def test_scoring_arxiv_with_premier_journal_not_hard_capped(self):
        pub_now = datetime.datetime.now(datetime.timezone.utc)
        score, _, journal, _ = calculate_impact_score(
            title="Observation of fractional quantum hall effect in moiré graphene",
            description="Published in Nature. We demonstrate quantum phase transitions using high-field transport.",
            source_name="arXiv",
            pub_date=pub_now,
        )
        self.assertEqual(journal, "Nature")
        self.assertGreater(score, 115.0)


class TestHistoryTracking(unittest.TestCase):
    def test_load_and_save_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hist_path = os.path.join(tmpdir, "history.json")
            
            seen = load_history(hist_path)
            self.assertEqual(len(seen), 0)

            seen.add("item-1")
            seen.add("item-2")
            seen.add("https://example.com/item-1")
            save_history(hist_path, seen, max_entries=10)

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
            "score": 115.5,
            "premier_journal": "Science",
            "doi_link": "https://doi.org/10.1126/science.12345",
            "badges": ["Energy Storage & Batteries", "2D & Advanced Materials"],
        }
        embed = build_discord_embed(article)

        self.assertIn("MXene", embed["title"])
        self.assertNotIn("🔥", embed["title"])
        self.assertEqual(embed["url"], "https://example.com/article/mxene")
        self.assertEqual(embed["color"], 0x1E3A8A)  # Classic Academic Blue
        self.assertEqual(embed["author"]["name"], "ScienceDaily · Materials Science")
        self.assertEqual(len(embed["fields"]), 3)
        self.assertEqual(embed["fields"][0]["name"], "Published In")
        self.assertIn("Science", embed["fields"][0]["value"])
        self.assertEqual(embed["fields"][1]["name"], "Original Paper")
        self.assertIn("https://doi.org/10.1126/science.12345", embed["fields"][1]["value"])
        self.assertEqual(embed["fields"][2]["name"], "Focus Areas")
        self.assertIn("Energy Storage & Batteries", embed["fields"][2]["value"])
        self.assertNotIn("footer", embed)
        self.assertNotIn("timestamp", embed)

    @patch("urllib.request.urlopen")
    def test_send_discord_webhook_with_mention(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        article = {
            "id": "test-1",
            "title": "Quantum Breakthrough",
            "link": "https://example.com/test",
            "description": "Novel state discovered.",
            "source": "ScienceDaily",
            "category": "Physics",
            "score": 120.0,
            "badges": ["Quantum & Superconductors"],
        }
        res = send_discord_webhook("https://discord.com/api/webhooks/123/xyz", article, mention_everyone=True)
        self.assertTrue(res)

        req_called = mock_urlopen.call_args[0][0]
        payload = json.loads(req_called.data.decode("utf-8"))
        self.assertEqual(payload.get("content"), "@everyone")
        self.assertEqual(payload.get("allowed_mentions"), {"parse": ["everyone"]})
        self.assertEqual(len(payload.get("embeds", [])), 1)

    @patch("urllib.request.urlopen")
    def test_send_discord_webhook_without_mention(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        article = {
            "id": "test-2",
            "title": "Quantum Breakthrough",
            "link": "https://example.com/test",
            "description": "Novel state discovered.",
            "source": "ScienceDaily",
            "category": "Physics",
            "score": 120.0,
            "badges": ["Quantum & Superconductors"],
        }
        res = send_discord_webhook("https://discord.com/api/webhooks/123/xyz", article, mention_everyone=False)
        self.assertTrue(res)

        req_called = mock_urlopen.call_args[0][0]
        payload = json.loads(req_called.data.decode("utf-8"))
        self.assertNotIn("content", payload)
        self.assertNotIn("allowed_mentions", payload)


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
                    <description>Record efficiency achieved in Nature Energy using novel chemical passivation.</description>
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
            "curated_boost": 15.0,
        }

        articles = fetch_feed(feed_cfg)
        self.assertEqual(len(articles), 1)
        art = articles[0]
        self.assertEqual(art["id"], "perov-101")
        self.assertEqual(art["title"], "Breakthrough in Perovskite Solar Cells")
        self.assertEqual(art["link"], "https://example.com/perovskite")
        self.assertGreater(art["score"], 80.0)
        self.assertEqual(art["premier_journal"], "Nature Energy")
        self.assertIn("Breakthrough Discovery", art["badges"])
        self.assertIn("Energy Storage & Batteries", art["badges"])


class TestRunDigest(unittest.TestCase):
    @patch("materials_digest.fetch_feed")
    def test_run_digest_dry_run(self, mock_fetch):
        mock_fetch.return_value = [
            {
                "id": "art-1",
                "title": "Quantum Spin Liquid Candidate Discovered",
                "link": "https://example.com/spin-liquid",
                "description": "Magnetic measurements in Nature reveal topological states.",
                "source": "Phys.org",
                "category": "Physics",
                "score": 110.0,
                "premier_journal": "Nature",
                "badges": ["Quantum & Superconductors"],
                "breakdown": {"matched_keywords": ["quantum", "topological"]},
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            hist_file = os.path.join(tmpdir, "history.json")
            code = run_digest(
                history_file=hist_file,
                limit=1,
                min_score=75.0,
                dry_run=True,
                verbose=False,
            )
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
