# Materials News Digest Bot (Academic Edition)

An intelligent, zero-cost Discord research bot that curates and broadcasts premier, high-impact materials science discoveries.

Designed with clean, understated academic typography (Nature / Science briefing style) and powered by a quality-gated scoring engine prioritizing peer-reviewed discoveries published in world-leading journals (*Nature*, *Science*, *Physical Review Letters*, *Advanced Materials*).

---

## Key Features

- **Peer-Reviewed Journal Prestige**: Automatically detects and prioritizes papers published in premier international journals (*Nature*, *Science*, *PRL*, *PRX*, *Advanced Materials*, *ACS Nano*, *JACS*).
- **Experimental Validation Weighting**: Rewards physical fabrication, single-crystal synthesis, and battery prototype testing over unvalidated theoretical preprint claims.
- **Flexible Quality-Gating (Anti-Spam)**: The bot checks feeds daily, but only broadcasts when a discovery meets the high-impact threshold (`>= 75.0` pts). On low-news or ordinary incremental days, it remains completely silent to protect your channel from spam.
- **Zero Running Costs**: Runs entirely on personal GitHub Actions (unlimited free runner minutes for public repositories) with zero cloud hosting bills.
- **Cache-Backed State**: Uses `actions/cache` for deduplication history, eliminating git push permissions and commit clutter.
- **Zero External Dependencies**: Pure Python 3 standard library (`urllib`, `xml.etree.ElementTree`, `json`).

---

## Setup Guide

### 1. Create a Discord Webhook
1. In your Discord server, go to your designated channel settings -> **Integrations** -> **Webhooks**.
2. Click **New Webhook**, name it (e.g. `Materials Science Digest`), and optionally upload an avatar.
3. Click **Copy Webhook URL**.

### 2. Configure GitHub Secret
1. Go to this repository on GitHub: **Settings** -> **Secrets and variables** -> **Actions**.
2. Click **New repository secret**.
3. Name: `DISCORD_WEBHOOK_URL`
4. Value: Paste your Discord Webhook URL.
5. Click **Add secret**.

### 3. Schedule & Flexible Trigger
- **Automatic**: Runs daily at `01:00 UTC` (**08:00 WIB** / Jakarta time). Broadcasts only if a genuine breakthrough meets the quality gate.
- **Manual Trigger**: Go to **Actions** -> **Daily Materials Science Digest** -> **Run workflow**.

---

## Local Usage & Testing

```bash
# Preview today's top pick without sending to Discord (Dry Run)
python3 materials_digest.py --dry-run --verbose

# Run unit tests
python3 -m unittest test_digest.py -v

# Send to Discord using local webhook URL
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
python3 materials_digest.py --limit 1
```

---

## License
MIT
