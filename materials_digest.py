#!/usr/bin/env python3
"""
Materials News Digest Bot (Academic Edition)
--------------------------------------------
Curates and broadcasts premier, high-impact materials science breakthroughs
to Discord via Webhook in a clean, professional, journal-grade format.

Design Principles:
- Zero emoji clutter; clean typography inspired by Nature / Science digests.
- Journal-level credibility: prioritzes peer-reviewed discoveries published in
  premier journals (Nature, Science, Physical Review Letters, Advanced Materials).
- Experimental validation weighting: rewards synthesized/tested physical discoveries
  over unvalidated theoretical preprint claims.
- Flexible cadence (Quality Gate): sends 1 top paper when a genuine breakthrough
  meets the strict threshold (>= 75 pts), otherwise remains completely silent to
  prevent channel spam.
- Cache-backed state persistence for GitHub Actions without git commit overhead.
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
        "name": "Nature Materials",
        "category": "Materials Science",
        "url": "https://www.nature.com/nmat.rss",
        "type": "rss",
        "curated_boost": 20.0,
    },
    {
        "name": "Nature Energy",
        "category": "Energy Materials",
        "url": "https://www.nature.com/nenergy.rss",
        "type": "rss",
        "curated_boost": 20.0,
    },
    {
        "name": "Physical Review Materials",
        "category": "Materials Physics",
        "url": "https://feeds.aps.org/rss/recent/prmaterials.xml",
        "type": "rss",
        "curated_boost": 20.0,
    },
    {
        "name": "ScienceDaily",
        "category": "Materials Science",
        "url": "https://www.sciencedaily.com/rss/matter_energy/materials_science.xml",
        "type": "rss",
        "curated_boost": 15.0,
    },
    {
        "name": "Phys.org",
        "category": "Materials Science",
        "url": "https://phys.org/rss-feed/physics-news/materials-science/",
        "type": "rss",
        "curated_boost": 15.0,
    },
    {
        "name": "arXiv",
        "category": "cond-mat.mtrl-sci",
        "url": "https://rss.arxiv.org/rss/cond-mat.mtrl-sci",
        "type": "rss",
        "curated_boost": 0.0,
    },
]

# Tier 1: Dedicated Peer-Reviewed Materials Science, Energy & Nanotechnology Journals (+60 pts)
TIER1_MATERIALS_JOURNALS = [
    "Nature Materials", "Nature Energy", "Nature Nanotechnology", "Nature Electronics",
    "Nature Photonics", "Physical Review Materials", "PRMaterials", "Advanced Materials",
    "Advanced Energy Materials", "Advanced Functional Materials", "ACS Nano", "Nano Letters",
    "Acta Materialia", "Matter", "Joule", "Energy & Environmental Science", "APL Materials",
    "Physical Review B", "PRB",
]

# Tier 2: Premier Multidisciplinary Journals (+45 pts, conditional on verified Material Anchor & no penalty)
TIER2_MULTIDISCIPLINARY_JOURNALS = [
    "Nature Communications", "Nature", "Science Advances", "Science",
    "Physical Review Letters", "Physical Review X", "PRL", "PRX",
    "Journal of the American Chemical Society", "JACS", "Optica",
]

PREMIER_JOURNALS = TIER1_MATERIALS_JOURNALS + TIER2_MULTIDISCIPLINARY_JOURNALS
JOURNAL_REGEX = re.compile(
    r"\b(" + "|".join(re.escape(j) for j in PREMIER_JOURNALS) + r")\b",
    re.IGNORECASE,
)

# Core Material Anchor keywords representing physical materials, substances, solid-state properties, and methods
MATERIAL_ANCHORS = [
    # Materials & substances
    "material", "materials", "crystal", "crystals", "crystalline", "lattice",
    "alloy", "alloys", "semiconductor", "semiconductors", "superconductor",
    "superconductors", "superconducting", "superconductivity", "graphene",
    "perovskite", "perovskites", "polymer", "polymers", "oxide", "oxides",
    "nitride", "nitrides", "metal", "metallic", "ceramic", "ceramics",
    "composite", "composites", "monolayer", "heterostructure", "heterostructures",
    "nanotube", "nanotubes", "mxene", "chalcogenide", "chalcogenides",
    "aerogel", "dielectric", "dielectrics", "electrolyte", "electrolytes",
    "catalyst", "catalysts", "electrocatalyst", "electrocatalysts", "membrane",
    "membranes", "solid-state", "thin film", "thin films", "nanomaterial",
    "nanomaterials", "metamaterial", "metamaterials", "metal-organic framework",
    "mof", "covalent organic framework", "cof",
    # Solid-state physics & material properties
    "bandgap", "band gap", "band structure", "carrier mobility", "fermi level",
    "ferroelectric", "piezoelectric", "ferromagnetic", "antiferromagnetic",
    "topological insulator", "topological material", "thermal conductivity",
    "mechanical strength", "dislocation", "grain boundary", "defect", "defects",
    "phase transition", "amorphous", "epitaxy", "epitaxial", "quantum material",
    "quantum materials", "moiré", "twistronics", "interatomic potential",
    # Simulation & characterization of matter
    "density functional theory", "dft", "molecular dynamics", "ab initio",
    "quantum espresso", "lammps", "x-ray diffraction", "xrd",
    "transmission electron microscopy", "tem", "scanning electron microscopy",
    "sem", "operando", "synchrotron", "spectroscopy",
]
ANCHOR_REGEX = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in MATERIAL_ANCHORS) + r")\b",
    re.IGNORECASE,
)

# Negative Categories: severe penalty to disqualify non-materials fields
NEGATIVE_CATEGORIES = {
    "quantum_computing": {
        "penalty": -70.0,
        "keywords": [
            "quantum gate", "quantum gates", "logic gate", "logic gates",
            "qubit", "qubits", "transmon", "quantum processor", "quantum processors",
            "quantum computer", "quantum computers", "quantum computing",
            "quantum algorithm", "quantum algorithms", "quantum circuit", "quantum circuits",
            "microwave pulse", "microwave pulses", "pulse shaping",
            "quantum error correction", "readout fidelity", "coherence time",
            "quantum volume", "quantum teleportation", "quantum key distribution", "qkd",
        ],
        "exempt_terms": ["quantum material", "quantum materials", "topological insulator", "superconducting material", "quantum dot"],
    },
    "clinical_medical": {
        "penalty": -60.0,
        "keywords": [
            "clinical trial", "clinical trials", "in vivo", "patient", "patients",
            "tumor", "tumors", "cancer therapy", "chemotherapy", "clinical study",
            "mice", "mouse model", "animal model", "drug delivery efficacy", "clinical phase",
        ],
        "exempt_terms": [],
    },
    "astrophysics": {
        "penalty": -60.0,
        "keywords": [
            "astronomy", "astrophysics", "telescope", "exoplanet", "exoplanets",
            "galaxy", "galaxies", "black hole", "black holes", "stellar", "cosmic",
            "supernova", "james webb", "interstellar",
        ],
        "exempt_terms": ["meteorite mineral", "high-pressure planetary material"],
    },
    "macro_robotics": {
        "penalty": -50.0,
        "keywords": [
            "legged robot", "humanoid robot", "humanoid robots", "quadruped",
            "drone", "drones", "autonomous vehicle", "autonomous driving",
        ],
        "exempt_terms": ["actuator material", "artificial muscle"],
    },
}

# Experimental validation & synthesis markers
EXPERIMENTAL_TERMS = [
    "synthes", "fabricat", "experiment", "measur", "prototype", "device",
    "single crystal", "thin film", "operando", "in situ", "microscopy", "tem",
    "sem", "afm", "xrd", "spectroscop", "battery test", "cycling", "capacity",
]

# Clean academic topic categories (zero emojis)
TOPIC_CATEGORIES = {
    "breakthrough": {
        "badge": "Breakthrough Discovery",
        "weight": 25.0,
        "keywords": [
            "breakthrough", "record efficiency", "room temperature", "revolutionary",
            "unprecedented", "milestone", "first-ever", "world record", "game-changer",
            "paradigm shift", "holy grail", "record-breaking", "first time", "sets record",
            "bandgap record",
        ],
    },
    "energy_battery": {
        "badge": "Energy Storage & Batteries",
        "weight": 22.0,
        "keywords": [
            "solid-state battery", "lithium-ion", "lithium-metal", "lithium-sulfur",
            "sodium-ion", "zinc-air", "perovskite", "solar cell", "photovoltaic",
            "hydrogen storage", "fuel cell", "electrocatalyst", "anode", "cathode",
            "fast-charging", "energy density", "carbon capture", "supercapacitor",
        ],
    },
    "quantum_supercond": {
        "badge": "Quantum & Superconductors",
        "weight": 22.0,
        "keywords": [
            "superconductor", "superconductivity", "topological insulator",
            "quantum material", "quantum dot", "majorana", "spintronics",
            "twistronics", "moiré", "hall effect",
        ],
    },
    "advanced_2d": {
        "badge": "2D & Advanced Materials",
        "weight": 18.0,
        "keywords": [
            "semiconductor", "wide-bandgap", "ultrawide", "graphene", "mxene",
            "metal-organic framework", "mof", "covalent organic framework",
            "cof", "metamaterial", "2d material", "aerogel", "nanotube", "nanomaterial",
            "self-healing", "biomaterial", "smart material", "transition metal dichalcogenide",
        ],
    },
    "comp_ai": {
        "badge": "Computational & AI Materials",
        "weight": 18.0,
        "keywords": [
            "machine learning", "artificial intelligence", "deep learning", "neural network",
            "diffusion model", "generative ai", "density functional theory", "dft",
            "molecular dynamics", "first-principles", "ab initio", "lammps", "quantum espresso",
            "materials project", "high-throughput", "gnome",
        ],
    },
    "discovery_synthesis": {
        "badge": "Synthesis & Experimental",
        "weight": 12.0,
        "keywords": [
            "synthesize", "synthesis", "fabricate", "fabrication", "discover", "discovery",
            "engineer", "engineering", "novel", "pioneering", "scalable", "uncover",
            "transform", "achieve", "reveal", "thin film",
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
    # Remove redundant "Abstract:" or "Abstract -" prefixes
    clean = re.sub(r"^abstract\s*[:\-–]\s*", "", clean, flags=re.IGNORECASE)
    # Clean LaTeX math and chemical formula markup (frequent in APS / PRMaterials RSS)
    if "$" in clean or "\\mathrm" in clean:
        clean = re.sub(r"\\(?:mathrm|mathbf|mathit|text)\{([^}]+)\}", r"\1", clean)
        clean = re.sub(r"[_^]\{([^}]+)\}", r"\1", clean)
        clean = re.sub(r"[_^]([a-zA-Z0-9])", r"\1", clean)
        clean = re.sub(r"\{([^}]+)\}", r"\1", clean)
        clean = clean.replace("$", "")
        clean = re.sub(r"\\([a-zA-Z]+)", r"\1", clean)
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


def extract_doi_link(url: str, timeout: int = 8) -> Optional[str]:
    """Extracts the original published paper DOI link from article webpage or direct URL."""
    if not url or "arxiv.org" in url:
        return None

    # Fast inline DOI resolution for direct journal URLs
    if "doi.org/" in url or "link.aps.org/doi/" in url:
        doi_m = re.search(r"10\.[0-9]{4,9}/[^\s\"\'<>]+", url)
        if doi_m:
            return f"https://doi.org/{doi_m.group(0).rstrip('.,;()')}"
    if "nature.com/articles/" in url:
        nat_m = re.search(r"articles/([a-zA-Z0-9\.\-\_]+)", url)
        if nat_m:
            return f"https://doi.org/10.1038/{nat_m.group(1)}"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; MaterialsNewsDigestBot/2.0; "
                "+https://github.com/jauhar-nr/materials-news-bot)"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html_text = resp.read().decode("utf-8", errors="ignore")

        # 1. Look for direct DOI url
        doi_match = re.search(r"https?://(?:dx\.)?doi\.org/(10\.[0-9]{4,9}/[^\s\"\'<>]+)", html_text)
        if doi_match:
            doi = doi_match.group(1).rstrip(".,;()")
            return f"https://doi.org/{doi}"

        # 2. Look for journal direct links
        journal_match = re.search(
            r"https?://(?:www\.)?(?:science\.org/doi/|nature\.com/articles/|journals\.aps\.org/prl/abstract/)[^\s\"\'<>]+",
            html_text,
        )
        if journal_match:
            return journal_match.group(0).rstrip(".,;()")

        # 3. Look for raw DOI text
        raw_doi = re.search(r"\b(10\.[0-9]{4,9}/[a-zA-Z0-9\.\-\_]+)\b", html_text)
        if raw_doi:
            return f"https://doi.org/{raw_doi.group(1)}"

        return None
    except Exception as exc:
        logger.debug("Could not extract DOI from %s: %s", url, exc)
        return None


def parse_date(date_str: Optional[str]) -> Optional[datetime.datetime]:
    """Parses standard RFC 822 / RFC 2822 or ISO dates."""
    if not date_str:
        return None
    date_str = date_str.strip()
    try:
        import email.utils
        parsed_tuple = email.utils.parsedate_to_datetime(date_str)
        if parsed_tuple.tzinfo is None:
            parsed_tuple = parsed_tuple.replace(tzinfo=datetime.timezone.utc)
        return parsed_tuple
    except Exception:
        pass

    try:
        dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        pass

    return None


def detect_premier_journal(text: str, source_name: str = "") -> Optional[str]:
    """Detects premier peer-reviewed journal citation in title, body, or source name."""
    if source_name in PREMIER_JOURNALS:
        return source_name

    matches = JOURNAL_REGEX.findall(text)
    if not matches:
        return None
    # Pick the most specific match (longest string)
    matches_sorted = sorted(matches, key=len, reverse=True)
    best_match = matches_sorted[0].strip()
    # Standardize casing
    if best_match.upper() in ("PRL", "PRX", "JACS", "PRB"):
        return best_match.upper()
    return best_match.title()


def calculate_impact_score(
    title: str,
    description: str,
    pub_date: Optional[datetime.datetime],
    source_name: str = "",
    curated_boost: float = 0.0,
) -> Tuple[float, List[str], Optional[str], Dict[str, Any]]:
    """
    Computes a research impact score based on keywords, material anchors,
    negative penalties, journal prestige tiers, experimental validation, and recency.
    Returns (total_score, active_badges, premier_journal, breakdown_dict).
    """
    full_text = f"{title} {description}"
    title_lower = title.lower()
    desc_lower = description.lower()
    full_lower = full_text.lower()
    
    # 1. Material Anchor Gating & Scoring
    title_anchors = set(ANCHOR_REGEX.findall(title))
    desc_anchors = set(ANCHOR_REGEX.findall(description))
    has_material_anchor = (len(title_anchors) >= 1) or (len(desc_anchors) >= 2)
    anchor_boost = min(len(title_anchors) * 12.0, 24.0) + min(len(desc_anchors) * 2.0, 8.0)

    # 2. Negative Keyword Penalty Filter (disqualify non-materials fields)
    negative_penalty = 0.0
    negative_matches: List[str] = []
    for neg_cat, neg_info in NEGATIVE_CATEGORIES.items():
        is_exempt = any(re.search(r"\b" + re.escape(ex) + r"\b", full_lower) for ex in neg_info["exempt_terms"])
        if not is_exempt:
            for kw in neg_info["keywords"]:
                if re.search(r"\b" + re.escape(kw) + r"\b", full_lower):
                    negative_penalty = min(negative_penalty, neg_info["penalty"])
                    negative_matches.append(f"{kw} ({neg_cat})")

    # 3. Topic Category Keyword Scoring
    keyword_score = 0.0
    matched_categories: Set[str] = set()
    matched_keywords: List[str] = []

    for cat_id, cat_info in TOPIC_CATEGORIES.items():
        cat_weight = cat_info["weight"]
        cat_matches = 0
        cat_score = 0.0

        for kw in cat_info["keywords"]:
            kw_pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(kw_pattern, title_lower):
                if cat_matches == 0:
                    cat_score += cat_weight * 2.0
                else:
                    cat_score += 2.0  # minimal incremental bonus for extra keywords in same category
                cat_matches += 1
                matched_keywords.append(f"{kw} (title)")
            elif re.search(kw_pattern, desc_lower):
                if cat_matches == 0:
                    cat_score += cat_weight * 1.0
                else:
                    cat_score += 1.0
                cat_matches += 1
                matched_keywords.append(kw)

        if cat_matches > 0:
            matched_categories.add(cat_id)
            # Cap maximum contribution from any single category to prevent keyword stacking
            cat_cap = cat_weight * 2.2
            keyword_score += min(cat_score, cat_cap)

    # 4. Premier journal prestige bonus (Tier 1 Dedicated Materials vs Tier 2 Multidisciplinary)
    detected_journal = detect_premier_journal(full_text, source_name)
    journal_boost = 0.0
    if detected_journal:
        if detected_journal in TIER1_MATERIALS_JOURNALS or source_name in TIER1_MATERIALS_JOURNALS:
            # Tier 1 Dedicated Materials Journal: +60 pts
            journal_boost = 60.0
        elif detected_journal in TIER2_MULTIDISCIPLINARY_JOURNALS:
            # Tier 2 Multidisciplinary: +45 pts ONLY if Material Anchor verified and no severe negative penalty
            if has_material_anchor and negative_penalty == 0.0:
                journal_boost = 45.0
            else:
                journal_boost = 0.0
        else:
            journal_boost = 25.0

    # 5. Experimental validation check for preprints
    experimental_adj = 0.0
    if source_name == "arXiv":
        has_experimental = any(term in full_lower for term in EXPERIMENTAL_TERMS)
        if has_experimental:
            experimental_adj = 5.0
        else:
            # Discount unvalidated theoretical calculations
            experimental_adj = -25.0

    # 6. Recency boost
    recency_score = 0.0
    now = datetime.datetime.now(datetime.timezone.utc)
    if pub_date:
        age_hours = (now - pub_date).total_seconds() / 3600.0
        if age_hours <= 36:
            recency_score = 15.0
        elif age_hours <= 72:
            recency_score = 8.0
        elif age_hours > 240:  # older than 10 days
            recency_score = -15.0

    raw_total = (
        keyword_score +
        anchor_boost +
        journal_boost +
        curated_boost +
        experimental_adj +
        recency_score +
        negative_penalty
    )

    # 7. Quality & Material Gating Caps
    if not has_material_anchor:
        # Gating Cap 1: Missing core material anchors -> hard-cap at 50.0 pts
        total_score = min(raw_total, 50.0)
    elif negative_penalty <= -50.0:
        # Gating Cap 2: Disqualified field (e.g. quantum computing or clinical trial) -> hard-cap at 45.0 pts
        total_score = min(raw_total, 45.0)
    elif source_name == "arXiv" and not detected_journal:
        # Gating Cap 3: Unvetted arXiv preprints without premier journal capped at 75.0 pts
        total_score = min(raw_total, 75.0)
    else:
        total_score = raw_total

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
        "anchor_boost": round(anchor_boost, 1),
        "journal_boost": round(journal_boost, 1),
        "curated_boost": round(curated_boost, 1),
        "experimental_adj": round(experimental_adj, 1),
        "recency_score": round(recency_score, 1),
        "negative_penalty": round(negative_penalty, 1),
        "has_material_anchor": has_material_anchor,
        "negative_matches": negative_matches[:4],
        "premier_journal": detected_journal,
        "matched_keywords": matched_keywords[:8],
    }

    return round(total_score, 1), badges, detected_journal, breakdown


def fetch_feed(feed_cfg: Dict[str, Any], timeout: int = 15) -> List[Dict[str, Any]]:
    """Fetches and parses a single RSS feed."""
    url = feed_cfg["url"]
    source_name = feed_cfg["name"]
    category = feed_cfg["category"]
    curated_boost = feed_cfg.get("curated_boost", 0.0)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; MaterialsNewsDigestBot/2.0; "
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
    items = root.findall(".//{*}item")
    if not items:
        items = root.findall(".//item")
    if not items:
        items = root.findall(".//{*}entry")

    for item in items:
        title_el = item.find("{*}title")
        raw_title = title_el.text if (title_el is not None and title_el.text) else ""
        title = clean_text(raw_title)
        if not title:
            continue

        link_el = item.find("{*}link")
        raw_link = ""
        if link_el is not None:
            raw_link = link_el.text or link_el.attrib.get("href", "")
        link = clean_url(raw_link)
        if not link:
            continue

        guid_el = item.find("{*}guid")
        if guid_el is None:
            guid_el = item.find("{*}id")
        guid = ""
        if guid_el is not None and guid_el.text:
            guid = guid_el.text.strip()
        else:
            guid = link

        desc_el = item.find("{*}description")
        if desc_el is None:
            desc_el = item.find("{*}summary")
        if desc_el is None:
            desc_el = item.find("{*}content")
        raw_desc = desc_el.text if (desc_el is not None and desc_el.text) else ""
        desc = clean_text(raw_desc)

        pub_el = item.find("{*}pubDate")
        if pub_el is None:
            pub_el = item.find("{*}published")
        if pub_el is None:
            pub_el = item.find("{*}updated")
        pub_str = pub_el.text if (pub_el is not None and pub_el.text) else ""
        pub_date = parse_date(pub_str)

        score, badges, premier_j, breakdown = calculate_impact_score(
            title=title,
            description=desc,
            pub_date=pub_date,
            source_name=source_name,
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
            "premier_journal": premier_j,
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
    """
    Builds a clean, academic Discord Embed (Format 1: Nature / Journal Briefing).
    Zero emojis, understated typography, deep academic blue palette, no footer.
    """
    title = article["title"]
    link = article["link"]
    source = article["source"]
    category = article["category"]
    premier_journal = article.get("premier_journal")
    doi_link = article.get("doi_link")
    badges = article.get("badges", [])
    desc = article.get("description", "")

    # Clean excerpt truncated at ~420 chars on word boundary
    if len(desc) > 420:
        desc = desc[:420].rsplit(" ", 1)[0] + "..."

    # Deep Classic Academic Blue
    embed_color = 0x1E3A8A

    fields = []
    # Display published venue
    if premier_journal:
        fields.append({
            "name": "Published In",
            "value": f"*{premier_journal}*",
            "inline": True,
        })
    else:
        fields.append({
            "name": "Venue",
            "value": f"{source} · {category}",
            "inline": True,
        })

    # Display original paper link / DOI
    if source == "arXiv":
        fields.append({
            "name": "Original Paper",
            "value": f"[arXiv Manuscript]({link}) · [PDF]({link.replace('/abs/', '/pdf/')}.pdf)",
            "inline": True,
        })
    elif doi_link:
        fields.append({
            "name": "Original Paper",
            "value": f"[Direct Journal DOI]({doi_link})",
            "inline": True,
        })

    # Display focus areas
    if badges:
        fields.append({
            "name": "Focus Areas",
            "value": " · ".join(badges),
            "inline": True,
        })

    embed: Dict[str, Any] = {
        "title": title,
        "url": link,
        "description": desc or "*Click paper link above to read full findings.*",
        "color": embed_color,
        "author": {
            "name": f"{source} · {category}",
            "url": link,
        },
        "fields": fields,
    }

    return embed


def send_discord_webhook(
    webhook_url: str,
    article: Dict[str, Any],
    mention_everyone: bool = False,
    content_override: Optional[str] = None,
) -> bool:
    """Dispatches the Discord webhook payload with pristine embed and optional @everyone or custom content."""
    embed = build_discord_embed(article)
    payload: Dict[str, Any] = {
        "embeds": [embed],
    }
    if content_override:
        payload["content"] = content_override
    elif mention_everyone:
        payload["content"] = "@everyone"
        payload["allowed_mentions"] = {"parse": ["everyone"]}

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=req_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "MaterialsNewsDigestBot/2.0",
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


def publish_saved_candidates(
    candidate_file: str,
    history_file: str,
    webhook_url: Optional[str],
    mention_everyone: bool = False,
    dry_run: bool = False,
) -> int:
    """Publishes previously approved candidates from a JSON file and records them in history."""
    if not os.path.exists(candidate_file):
        logger.error("Candidate file %s not found.", candidate_file)
        return 1

    try:
        with open(candidate_file, "r", encoding="utf-8") as f:
            candidates = json.load(f)
    except Exception as exc:
        logger.error("Failed to read candidate file %s: %s", candidate_file, exc)
        return 1

    if not isinstance(candidates, list) or not candidates:
        logger.info("No candidates to publish in %s", candidate_file)
        return 0

    history = load_history(history_file)
    success_count = 0

    for article in candidates:
        if "doi_link" not in article:
            article["doi_link"] = extract_doi_link(article["link"])

        logger.info(
            "Publishing Approved Research: [Score %s] '%s' (%s)",
            article.get("score"),
            article["title"],
            article["link"],
        )

        if dry_run:
            print("\n" + "=" * 60)
            print("DRY RUN: Publishing Approved Discovery")
            print("=" * 60)
            print(json.dumps(build_discord_embed(article), indent=2))
            print("=" * 60 + "\n")
            success_count += 1
        else:
            if not webhook_url:
                logger.error("No Discord Webhook URL provided. Use --webhook-url or set DISCORD_WEBHOOK_URL.")
                return 1

            if send_discord_webhook(webhook_url, article, mention_everyone=mention_everyone):
                history.add(article["id"])
                history.add(article["link"])
                success_count += 1
            else:
                logger.error("Failed to publish approved article '%s'", article["title"])

    if not dry_run and success_count > 0:
        save_history(history_file, history)
        logger.info("Saved %d items to history file %s", len(history), history_file)

    logger.info("Finished publishing: %d/%d successfully broadcast.", success_count, len(candidates))
    return 0


def run_digest(
    history_file: str,
    webhook_url: Optional[str] = None,
    limit: int = 1,
    min_score: float = 115.0,
    days_window: Optional[int] = None,
    query: Optional[str] = None,
    mention_everyone: bool = False,
    staging: bool = False,
    save_candidate: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Main execution flow with flexible quality-gating, staging review, or targeted query."""
    logger.info("Starting Materials News Digest run (limit=%d, min_score=%.1f, staging=%s)", limit, min_score, staging)
    history = load_history(history_file)
    logger.info("Loaded %d previously broadcast items from history", len(history))

    all_articles: List[Dict[str, Any]] = []
    for feed in DEFAULT_FEEDS:
        articles = fetch_feed(feed)
        all_articles.extend(articles)

    logger.info("Aggregated %d total articles across all feeds", len(all_articles))

    # Targeted query mode
    if query and query.strip():
        q_clean = query.strip().lower()
        matched = [
            a for a in all_articles
            if q_clean in a["title"].lower() or q_clean in a["description"].lower()
        ]
        if not matched:
            logger.error("No article matched targeted query: '%s'", query)
            return 1
        matched.sort(key=lambda x: x["score"], reverse=True)
        selected = matched[:limit]
        logger.info("Found %d articles matching targeted query '%s'", len(selected), query)
    else:
        # Optional days window filter
        if days_window:
            cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_window)
            filtered_by_window = []
            for a in all_articles:
                if a["pub_date"]:
                    dt = datetime.datetime.fromisoformat(a["pub_date"])
                    if dt >= cutoff:
                        filtered_by_window.append(a)
                else:
                    filtered_by_window.append(a)
            all_articles = filtered_by_window
            logger.info("Retained %d articles within the last %d days", len(all_articles), days_window)

        # Deduplicate against history
        fresh_candidates = [
            a for a in all_articles
            if a["id"] not in history and a["link"] not in history
        ]
        logger.info("Found %d unseen candidates after history filtering", len(fresh_candidates))

        # Quality Gate: Strict minimum impact threshold
        qualifying = [a for a in fresh_candidates if a["score"] >= min_score]
        logger.info("%d candidates pass quality threshold (>= %.1f pts)", len(qualifying), min_score)

        # Sort descending by impact score
        qualifying.sort(key=lambda x: x["score"], reverse=True)

        if verbose and qualifying:
            logger.info("--- Top Qualifying Discoveries ---")
            for idx, item in enumerate(qualifying[:5], 1):
                j_tag = f" [Venue: {item.get('premier_journal')}]" if item.get("premier_journal") else ""
                logger.info(
                    "[%d] (Score: %s)%s [%s] %s",
                    idx,
                    item["score"],
                    j_tag,
                    item["source"],
                    item["title"],
                )

        selected = qualifying[:limit]

        if not selected:
            logger.info(
                "Quality Gate Active: No research met the high-impact threshold (>= %.1f pts) today. "
                "Remaining silent to prevent channel noise.",
                min_score,
            )
            if save_candidate:
                os.makedirs(os.path.dirname(os.path.abspath(save_candidate)), exist_ok=True)
                with open(save_candidate, "w", encoding="utf-8") as f:
                    json.dump([], f, indent=2)
            return 0

    if save_candidate:
        os.makedirs(os.path.dirname(os.path.abspath(save_candidate)), exist_ok=True)
        with open(save_candidate, "w", encoding="utf-8") as f:
            json.dump(selected, f, indent=2)
        logger.info("Saved %d candidates for review to %s", len(selected), save_candidate)

    success_count = 0
    for article in selected:
        if "doi_link" not in article:
            article["doi_link"] = extract_doi_link(article["link"])
        venue_info = f" ({article['premier_journal']})" if article.get("premier_journal") else ""
        logger.info(
            "Selected Research Discovery: [Score %s] '%s'%s (%s)",
            article["score"],
            article["title"],
            venue_info,
            article["link"],
        )

        if dry_run:
            print("\n" + "=" * 60)
            print("DRY RUN: Academic Discord Embed Preview" + (" (STAGING REVIEW)" if staging else ""))
            print("=" * 60)
            embed = build_discord_embed(article)
            print(json.dumps(embed, indent=2))
            print("=" * 60 + "\n")
            success_count += 1
        else:
            if not webhook_url:
                logger.error("No Discord Webhook URL provided. Use --webhook-url or set DISCORD_WEBHOOK_URL.")
                return 1

            if staging:
                staging_content = (
                    "📋 **[REVIEW REQUIRED] Daily Materials Digest Candidate**\n"
                    f"Impact Score: **{article.get('score')} pts** · Source: **{article.get('source')}**\n"
                    "*Please review the preview below. Approve deployment in GitHub to broadcast to #materials-news.*"
                )
                if send_discord_webhook(webhook_url, article, mention_everyone=False, content_override=staging_content):
                    success_count += 1
                else:
                    logger.error("Failed to deliver staging preview for '%s'", article["title"])
            else:
                if send_discord_webhook(webhook_url, article, mention_everyone=mention_everyone):
                    history.add(article["id"])
                    history.add(article["link"])
                    success_count += 1
                else:
                    logger.error("Failed to deliver article '%s'", article["title"])

    if not dry_run and not staging and success_count > 0:
        save_history(history_file, history)
        logger.info("Saved %d items to history file %s", len(history), history_file)

    logger.info("Finished digest run: %d/%d successfully processed.", success_count, len(selected))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Materials Science News Digest Discord Bot (Academic Edition)")
    parser.add_argument("--history", default="data/history.json", help="Path to history JSON file")
    parser.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL"), help="Discord Webhook URL")
    parser.add_argument("--limit", type=int, default=1, help="Number of top articles to select (default: 1)")
    parser.add_argument("--min-score", type=float, default=115.0, help="Minimum impact threshold (default: 115.0)")
    parser.add_argument("--days", type=int, default=None, help="Filter articles published within the last N days")
    parser.add_argument("--query", type=str, default=None, help="Target a specific article by keyword/title")
    parser.add_argument("--mention-everyone", action="store_true", help="Include @everyone mention in message")
    parser.add_argument("--staging", action="store_true", help="Run in staging review mode without recording history")
    parser.add_argument("--save-candidate", type=str, default=None, help="Filepath to save selected candidate JSON")
    parser.add_argument("--publish-saved", type=str, default=None, help="Filepath to publish approved candidate JSON")
    parser.add_argument("--dry-run", action="store_true", help="Preview output without dispatching webhook")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed scoring breakdown")

    args = parser.parse_args()

    if args.publish_saved:
        sys.exit(
            publish_saved_candidates(
                candidate_file=args.publish_saved,
                history_file=args.history,
                webhook_url=args.webhook_url,
                mention_everyone=args.mention_everyone,
                dry_run=args.dry_run,
            )
        )

    sys.exit(
        run_digest(
            history_file=args.history,
            webhook_url=args.webhook_url,
            limit=args.limit,
            min_score=args.min_score,
            days_window=args.days,
            query=args.query,
            mention_everyone=args.mention_everyone,
            staging=args.staging,
            save_candidate=args.save_candidate,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
