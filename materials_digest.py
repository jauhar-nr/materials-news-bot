#!/usr/bin/env python3
"""
Materials News Digest Bot
-------------------------
Curates and broadcasts the #1 highest-impact materials science discovery daily
to Discord via Webhook.

Features:
- Multi-source RSS aggregation (ScienceDaily, Phys.org, arXiv).
- Weighted Impact Scoring Engine: analyzes breakthroughs, energy/batteries,
  superconductors, novel 2D materials, and DFT/MD computational advances.
- Intelligent deduplication with local JSON history.
- Clean, attractive Discord Embed with topic badges and impact scores.
- Zero external dependencies (Python 3 standard library only).
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("materials_digest")

DEFAULT_FEEDS = [
    {
        "name": "ScienceDaily",
        "category": "Materials Science",
        "url": "https://www.sciencedaily.com/rss/matter_energy/materials_science.xml",
        "type": "rss",
        "curated_boost": 8.0,
    },
    {
        "name": "Phys.org",
        "category": "Materials Science",
        "url": "https://phys.org/rss-feed/physics-news/materials-science/",
        "type": "rss",
        "curated_boost": 8.0,
    },
    {
        "name": "arXiv",
        "category": "cond-mat.mtrl-sci",
        "url": "https://rss.arxiv.org/rss/cond-mat.mtrl-sci",
        "type": "rss",
        "curated_boost": 0.0,
    },
]

# Scoring lexicon with categories, weights, and display badges
TOPIC_CATEGORIES = {
    "breakthrough": {
        "badge": "⚡ Breakthrough",
        "weight": 25.0,
        "keywords": [
            "breakthrough", "record efficiency", "room temperature", "revolutionary",
            "unprecedented", "milestone", "first-ever", "world record", "game-changer",
            "paradigm shift", "holy grail", "record-breaking", "first time",
        ],
    },
    "energy_battery": {
        "badge": "🔋 Energy & Battery",
        "weight": 20.0,
        "keywords": [
            "solid-state battery", "lithium-ion", "lithium-metal", "lithium-sulfur",
            "sodium-ion", "zinc-air", "perovskite", "solar cell", "photovoltaic",
            "hydrogen storage", "fuel cell", "electrocatalyst", "anode", "cathode",
            "fast-charging", "energy density", "carbon capture", "supercapacitor",
        ],
    },
    "quantum_supercond": {
        "badge": "⚛️ Quantum & Superconductors",
        "weight": 20.0,
        "keywords": [
            "superconductor", "superconductivity", "topological insulator",
            "quantum material", "quantum dot", "majorana", "spintronics",
            "twistronics", "moiré", "quantum computing", "hall effect",
        ],
    },
    "advanced_2d": {
        "badge": "🔬 2D & Advanced Materials",
        "weight": 18.0,
        "keywords": [
            "graphene", "mxene", "metal-organic framework", "mof", "covalent organic framework",
            "cof", "metamaterial", "2d material", "aerogel", "nanotube", "nanomaterial",
            "self-healing", "biomaterial", "smart material", "transition metal dichalcogenide",
        ],
    },
    "comp_ai": {
        "badge": "💻 Computational & AI",
        "weight": 18.0,
        "keywords": [
            "machine learning", "artificial intelligence", "deep learning", "neural network",
            "diffusion model", "generative ai", "density functional theory", "dft",
            "molecular dynamics", "first-principles", "ab initio", "lammps", "quantum espresso",
            "materials project", "high-throughput", "gnome",
        ],
    },
    "discovery_synthesis": {
        "badge": "🧪 Synthesis & Discovery",
        "weight": 10.0,
        "keywords": [
            "synthesize", "synthesis", "fabricate", "fabrication", "discover", "discovery",
            "engineer", "engineering", "novel", "pioneering", "scalable", "uncover",
            "transform", "achieve", "reveal",
        ],
    },
}


def clean_text(text: Optional[str]) -> str:
    """Strips HTML tags, decodes HTML entities, and normalizes whitespace."""
    if not text:
        return ""
    # Strip HTML tags
    clean = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities (&amp;, &#39;, etc.)
    clean = html.unescape(clean)
    # Remove leading arXiv metadata prefixes if present (e.g. arXiv:2610.12345v1 Announce Type: new)
    clean = re.sub(r"^arXiv:[0-9\.]+v?[0-9]*\s*Announce Type:\s*\w+\s*", "", clean, flags=re.IGNORECASE)
    # Normalize whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    # Normalize spaces before punctuation
    clean = re.sub(r"\s+([,\.!\?;:])", r"\1", clean)
    return clean


def clean_url(url: Optional[str]) -> str:
    """Strips query parameters used for tracking (utm_*, etc.)."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url.strip())
    # Rebuild URL without tracking query parameters
    query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
    filtered_params = {k: v for k, v in query_params.items() if not k.startswith("utm_")}
    clean_query = urllib.parse.urlencode(filtered_params, doseq=True)
    return urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        clean_query,
        parsed.fragment,
    ))


def parse_date(date_str: Optional[str]) -> Optional[datetime.datetime]:
    """Parses standard RFC 822 / RFC 2822 or ISO dates."""
    if not date_str:
        return None
    date_str = date_str.strip()
    # Try email/RFC 2822 parsing (standard in RSS)
    try:
        import email.utils
        parsed_tuple = email.utils.parsedate_to_datetime(date_str)
        if parsed_tuple.tzinfo is None:
            parsed_tuple = parsed_tuple.replace(tzinfo=datetime.timezone.utc)
        return parsed_tuple
    except Exception:
        pass

    # Try ISO formats
    try:
        dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        pass

    return None


def calculate_impact_score(
    title: str,
    description: str,
    pub_date: Optional[datetime.datetime],
    curated_boost: float = 0.0,
) -> Tuple[float, List[str], Dict[str, Any]]:
    """
    Computes an impact score based on keywords, domain relevance, recency,
    and title emphasis. Returns (total_score, active_badges, breakdown_dict).
    """
    title_lower = title.lower()
    desc_lower = description.lower()
    
    keyword_score = 0.0
    matched_categories: Set[str] = set()
    matched_keywords: List[str] = []

    for cat_id, cat_info in TOPIC_CATEGORIES.items():
        cat_matched = False
        cat_weight = cat_info["weight"]
        for kw in cat_info["keywords"]:
            # Check title first (title gets 2.2x multiplier)
            if kw in title_lower:
                keyword_score += cat_weight * 2.2
                cat_matched = True
                matched_keywords.append(f"{kw} (title)")
            elif kw in desc_lower:
                keyword_score += cat_weight * 1.0
                cat_matched = True
                matched_keywords.append(kw)

        if cat_matched:
            matched_categories.add(cat_id)

    # Recency boost
    recency_score = 0.0
    now = datetime.datetime.now(datetime.timezone.utc)
    if pub_date:
        age_hours = (now - pub_date).total_seconds() / 3600.0
        if age_hours <= 24:
            recency_score = 15.0
        elif age_hours <= 48:
            recency_score = 8.0
        elif age_hours <= 72:
            recency_score = 3.0
        elif age_hours > 168:  # older than 1 week
            recency_score = -15.0

    total_score = keyword_score + recency_score + curated_boost

    # Collect up to 3 highest priority badges
    priority_order = [
        "breakthrough",
        "energy_battery",
        "quantum_supercond",
        "advanced_2d",
        "comp_ai",
        "discovery_synthesis",
    ]
    badges = [
        TOPIC_CATEGORIES[c]["badge"]
        for c in priority_order
        if c in matched_categories
    ][:3]

    breakdown = {
        "keyword_score": round(keyword_score, 1),
        "recency_score": round(recency_score, 1),
        "curated_boost": round(curated_boost, 1),
        "matched_keywords": matched_keywords[:8],
    }

    return round(total_score, 1), badges, breakdown


def fetch_feed(feed_cfg: Dict[str, Any], timeout: int = 15) -> List[Dict[str, Any]]:
    """Fetches and parses a single RSS feed."""
    url = feed_cfg["url"]
    source_name = feed_cfg["name"]
    category = feed_cfg["category"]
    curated_boost = feed_cfg.get("curated_boost", 0.0)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; MaterialsNewsDigestBot/1.0; "
            "+https://github.com/jauhar-nr/materials-news-bot)"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read()
    except Exception as exc:
        logger.warning("Failed to fetch feed %s (%s): %s", source_name, url, exc)
        return []

    try:
        root = ET.fromstring(data)
    except Exception as exc:
        logger.warning("Failed to parse XML for %s: %s", source_name, exc)
        return []

    articles = []
    # Find all items (RSS 2.0 or RDF)
    items = root.findall(".//item")
    if not items:
        # Check for Atom entry tags
        items = root.findall(".//{*}entry")

    for item in items:
        # Title
        title_el = item.find("{*}title")
        raw_title = title_el.text if (title_el is not None and title_el.text) else ""
        title = clean_text(raw_title)
        if not title:
            continue

        # Link
        link_el = item.find("{*}link")
        raw_link = ""
        if link_el is not None:
            raw_link = link_el.text or link_el.attrib.get("href", "")
        link = clean_url(raw_link)
        if not link:
            continue

        # ID / GUID
        guid_el = item.find("{*}guid")
        if guid_el is None:
            guid_el = item.find("{*}id")
        guid = ""
        if guid_el is not None and guid_el.text:
            guid = guid_el.text.strip()
        else:
            guid = link

        # Description / Summary
        desc_el = item.find("{*}description")
        if desc_el is None:
            desc_el = item.find("{*}summary")
        if desc_el is None:
            desc_el = item.find("{*}content")
        raw_desc = desc_el.text if (desc_el is not None and desc_el.text) else ""
        desc = clean_text(raw_desc)

        # Publication date
        pub_el = item.find("{*}pubDate")
        if pub_el is None:
            pub_el = item.find("{*}published")
        if pub_el is None:
            pub_el = item.find("{*}updated")
        pub_str = pub_el.text if (pub_el is not None and pub_el.text) else ""
        pub_date = parse_date(pub_str)

        # Compute impact score
        score, badges, breakdown = calculate_impact_score(
            title=title,
            description=desc,
            pub_date=pub_date,
            curated_boost=curated_boost,
        )

        articles.append({
            "id": guid,
            "title": title,
            "link": link,
            "description": desc,
            "source": source_name,
            "category": category,
            "pub_date": pub_date.isoformat() if pub_date else None,
            "score": score,
            "badges": badges,
            "breakdown": breakdown,
        })

    logger.info("Fetched %d articles from %s", len(articles), source_name)
    return articles


def load_history(history_file: str) -> Set[str]:
    """Loads previously published item IDs and URLs."""
    if not os.path.exists(history_file):
        return set()
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            return set()
    except Exception as exc:
        logger.warning("Could not read history file %s: %s", history_file, exc)
        return set()


def save_history(history_file: str, seen: Set[str], max_entries: int = 500) -> None:
    """Saves updated history to file, maintaining maximum size."""
    os.makedirs(os.path.dirname(os.path.abspath(history_file)), exist_ok=True)
    trimmed_list = sorted(list(seen))[-max_entries:]
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(trimmed_list, f, indent=2)


def build_discord_embed(article: Dict[str, Any]) -> Dict[str, Any]:
    """Builds a rich Discord Embed object for the daily top pick."""
    title = article["title"]
    link = article["link"]
    source = article["source"]
    category = article["category"]
    score = article["score"]
    badges = article.get("badges", [])
    desc = article.get("description", "")

    # Clean description snippet (truncate at ~380 chars on word boundary)
    if len(desc) > 380:
        desc = desc[:380].rsplit(" ", 1)[0] + "..."

    # Impact Tier representation
    if score >= 60.0:
        score_display = f"🔥 Exceptional ({score:g} pts)"
        embed_color = 0xE67E22  # Rich warm amber / orange
    elif score >= 35.0:
        score_display = f"⭐ High Impact ({score:g} pts)"
        embed_color = 0x3498DB  # Vibrant scientific blue
    else:
        score_display = f"✨ Notable ({score:g} pts)"
        embed_color = 0x2ECC71  # Emerald green

    badges_text = " • ".join(badges) if badges else "🧪 Materials Science"

    embed: Dict[str, Any] = {
        "title": f"🔥 {title}",
        "url": link,
        "description": desc or "*No excerpt provided. Click title to read full paper/article.*",
        "color": embed_color,
        "author": {
            "name": f"{source} • {category}",
            "url": link,
        },
        "fields": [
            {
                "name": "⚡ Impact Rating",
                "value": score_display,
                "inline": True,
            },
            {
                "name": "🏷️ Key Focus",
                "value": badges_text,
                "inline": True,
            },
        ],
        "footer": {
            "text": "Phynite Materials Digest • Curated Daily Top Discovery",
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    return embed


def send_discord_webhook(webhook_url: str, article: Dict[str, Any]) -> bool:
    """Dispatches the Discord webhook payload."""
    embed = build_discord_embed(article)
    payload = {
        "content": "**📢 Daily Materials Science Top Discovery**\nHere is today's highest-impact materials breakthrough:",
        "embeds": [embed],
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "MaterialsNewsDigestBot/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 204):
                logger.info("Successfully dispatched Discord webhook for: %s", article["title"])
                return True
            else:
                logger.error("Discord returned unexpected status %d", resp.status)
                return False
    except Exception as exc:
        logger.error("Failed to send Discord webhook: %s", exc)
        return False


def run_digest(
    history_file: str,
    webhook_url: Optional[str] = None,
    limit: int = 1,
    min_score: float = 10.0,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Main execution flow."""
    logger.info("Starting Materials News Digest run (limit=%d, min_score=%.1f)", limit, min_score)
    history = load_history(history_file)
    logger.info("Loaded %d previously broadcast items from history", len(history))

    all_articles: List[Dict[str, Any]] = []
    for feed in DEFAULT_FEEDS:
        articles = fetch_feed(feed)
        all_articles.extend(articles)

    logger.info("Aggregated %d total articles across all feeds", len(all_articles))

    # Deduplicate against history (check both ID and clean URL)
    fresh_candidates = [
        a for a in all_articles
        if a["id"] not in history and a["link"] not in history
    ]
    logger.info("Found %d unseen candidates after history filtering", len(fresh_candidates))

    # Filter by minimum score
    qualifying = [a for a in fresh_candidates if a["score"] >= min_score]
    logger.info("%d candidates meet minimum score threshold (>= %.1f)", len(qualifying), min_score)

    # Sort descending by impact score
    qualifying.sort(key=lambda x: x["score"], reverse=True)

    if verbose:
        logger.info("--- Top 5 Candidates Breakdown ---")
        for idx, item in enumerate(qualifying[:5], 1):
            logger.info(
                "[%d] (Score: %s) [%s] %s (Keywords: %s)",
                idx,
                item["score"],
                item["source"],
                item["title"],
                item["breakdown"].get("matched_keywords"),
            )

    selected = qualifying[:limit]

    if not selected:
        logger.info("No qualifying new articles to publish today.")
        return 0

    success_count = 0
    for article in selected:
        logger.info(
            "Selected Daily Top Pick: [Score %s] '%s' (%s)",
            article["score"],
            article["title"],
            article["link"],
        )

        if dry_run:
            print("\n" + "=" * 60)
            print("DRY RUN: Discord Webhook Payload Preview")
            print("=" * 60)
            embed = build_discord_embed(article)
            print(json.dumps(embed, indent=2))
            print("=" * 60 + "\n")
            success_count += 1
            # Mark seen in dry run too if needed or keep dry run read-only
        else:
            if not webhook_url:
                logger.error("No Discord Webhook URL provided. Use --webhook-url or set DISCORD_WEBHOOK_URL.")
                return 1

            if send_discord_webhook(webhook_url, article):
                history.add(article["id"])
                history.add(article["link"])
                success_count += 1
            else:
                logger.error("Failed to deliver article '%s'", article["title"])

    if not dry_run and success_count > 0:
        save_history(history_file, history)
        logger.info("Saved %d items to history file %s", len(history), history_file)

    logger.info("Finished digest run: %d/%d successfully processed.", success_count, len(selected))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Materials Science News Digest Discord Bot")
    parser.add_argument("--history", default="data/history.json", help="Path to history JSON file")
    parser.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL"), help="Discord Webhook URL")
    parser.add_argument("--limit", type=int, default=1, help="Number of top articles to select (default: 1)")
    parser.add_argument("--min-score", type=float, default=10.0, help="Minimum impact score threshold (default: 10.0)")
    parser.add_argument("--dry-run", action="store_true", help="Preview output without dispatching webhook")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed scoring breakdown")

    args = parser.parse_args()

    sys.exit(
        run_digest(
            history_file=args.history,
            webhook_url=args.webhook_url,
            limit=args.limit,
            min_score=args.min_score,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
