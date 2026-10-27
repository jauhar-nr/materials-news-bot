# 🔬 Materials News Digest Bot

An intelligent, zero-cost Discord bot that curates and broadcasts the **#1 highest-impact materials science discovery daily**.

Powered by a weighted impact scoring engine that scans RSS feeds from **ScienceDaily**, **Phys.org**, and **arXiv**, selecting breakthroughs across energy storage, superconductors, 2D materials, and computational modeling (DFT/MD).

---

## ✨ Features

- **Daily Curated Top Pick**: Sends strictly 1 highest-impact article every day to avoid channel clutter.
- **Weighted Impact Scoring**:
  - ⚡ **Breakthroughs & Records**: Identifies room-temperature discoveries, world-record efficiencies, and paradigm shifts.
  - 🔋 **Energy & Battery**: Evaluates solid-state electrolytes, lithium/sodium battery innovations, and photovoltaics.
  - ⚛️ **Quantum & Superconductors**: Prioritizes topological insulators, Majorana modes, and novel superconductors.
  - 🔬 **2D & Advanced Materials**: Tracks MXenes, graphene, MOFs/COFs, and aerogels.
  - 💻 **Computational & AI**: Boosts DFT, molecular dynamics, and machine learning materials discovery (GNoME, Materials Project).
  - ⏱️ **Recency & Title Weighting**: Recent publications (<24h) and title claims are heavily weighted.
- **Rich Discord Embed**: Professional formatting with author source badges, topic tags, impact score ratings, and direct paper links.
- **Zero Running Costs**: Runs entirely on personal GitHub Actions (unlimited free runner minutes for public repositories) with zero cloud hosting bills.
- **Cache-Backed State**: Uses `actions/cache` for deduplication history, eliminating git push permissions and commit clutter.
- **Zero External Dependencies**: Pure Python 3 standard library (`urllib`, `xml.etree.ElementTree`, `json`).

---

## 🚀 Setup Guide

### 1. Create a Discord Webhook
1. In your Discord server, go to your designated channel settings -> **Integrations** -> **Webhooks**.
2. Click **New Webhook**, name it (e.g. `Materials Science Bot`), and optionally upload an avatar.
3. Click **Copy Webhook URL**.

### 2. Configure GitHub Secret
1. Go to this repository on GitHub: **Settings** -> **Secrets and variables** -> **Actions**.
2. Click **New repository secret**.
3. Name: `DISCORD_WEBHOOK_URL`
4. Value: Paste your Discord Webhook URL.
5. Click **Add secret**.

### 3. Schedule & Manual Trigger
- **Automatic**: Runs daily at `01:00 UTC` (which corresponds to **08:00 WIB** / Jakarta time).
- **Manual Trigger**: Go to **Actions** -> **Daily Materials Science Digest** -> **Run workflow** (supports dry-run mode and custom limits).

---

## 💻 Local Usage & Testing

You can run the bot locally with zero setup:

```bash
# Preview today's #1 top pick without sending to Discord (Dry Run)
python3 materials_digest.py --dry-run --verbose

# Run unit tests
python3 -m unittest test_digest.py -v

# Send to Discord using local webhook URL
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python3 materials_digest.py --limit 1
```

### CLI Arguments
| Flag | Default | Description |
|---|---|---|
| `--dry-run` | `False` | Simulates the run and prints the Discord embed payload to stdout. |
| `--limit N` | `1` | Number of top articles to select. |
| `--min-score` | `10.0` | Minimum score threshold required for an article to qualify. |
| `--history` | `data/history.json` | Path to deduplication history file. |
| `--verbose` | `False` | Prints scoring breakdown of top candidates. |
| `--webhook-url` | `$DISCORD_WEBHOOK_URL` | Discord webhook endpoint. |

---

## 🧪 Testing

The repository includes a comprehensive unit test suite:

```bash
python3 -m unittest test_digest.py
```

Checks:
- HTML stripping and Unicode/entity decoding.
- URL tracking parameter stripping (`utm_*`).
- Date parsing (RFC 822/2822 and ISO-8601).
- Keyword scoring logic and title multiplier verification.
- Deduplication history truncation and persistence.
- Discord embed payload generation and color tiering.

---

## 📄 License
MIT
