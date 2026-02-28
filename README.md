# Claude Usage Widget

A lightweight macOS menu bar widget that shows your **Anthropic API rate-limit status** at a glance — plan tier, requests remaining, tokens remaining, and when limits reset.

```
Claude 🟢                     ← lives in your menu bar
────────────────────────────
🟢  Connected
📋  Tier: Scale
────────────────────────────
📊 Requests  ▓▓▓▓▓▓▓▓▓░  95/100 left  (5% used)
🔤 Tokens    ▓▓▓▓▓▓▓░░░  70K/100K left (30% used)
⏱  Resets in: 0m 32s
────────────────────────────
🕐  Updated: 14:30:25
────────────────────────────
Refresh Now
Set API Key…
────────────────────────────
Quit
```

---

## Requirements

| Requirement | Notes |
|---|---|
| macOS 10.15+ | Catalina or newer |
| Python 3.8+ | Pre-installed on modern macOS |
| Anthropic API key | Get one at [console.anthropic.com](https://console.anthropic.com) |

---

## Installation

### Option A — One command (recommended)

```bash
./install.sh
```

This installs dependencies and launches the widget immediately.

---

### Option B — Manual

**1. Install dependencies**

```bash
pip3 install rumps requests
```

**2. Run the widget**

```bash
python3 claude_usage_widget.py
```

> The widget appears in your macOS menu bar as **Claude 🟢** (or 🟡 / 🔴 based on usage).

---

## Setting Your API Key

You have two options:

**Option 1 — Via the menu (recommended)**

1. Click **Claude** in the menu bar
2. Choose **Set API Key…**
3. Paste your key (starts with `sk-ant-`) and click **Save**

The key is stored at `~/.config/claude-widget/config.json`.

**Option 2 — Environment variable**

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
python3 claude_usage_widget.py
```

---

## What It Shows

| Indicator | Meaning |
|---|---|
| `Claude 🟢` | >50% capacity remaining — all good |
| `Claude 🟡` | 20–50% remaining — moderate usage |
| `Claude 🔴` | <20% remaining — near limit |
| `Claude 🔑` | No API key configured |
| `Claude ⚠️` | Connection error or invalid key |

### Menu details

| Item | Description |
|---|---|
| **Tier** | Your plan tier inferred from rate limits (Free / Build / Scale / Enterprise) |
| **Requests bar** | Per-minute request rate: `▓` = remaining, `░` = used |
| **Tokens bar** | Per-minute token rate: `▓` = remaining, `░` = used |
| **Resets in** | Countdown to when the current 1-minute window resets |
| **Updated** | Timestamp of the last successful refresh |

> The widget auto-refreshes every **60 seconds** in the background.
> Click **Refresh Now** to update immediately.

---

## Plan Tier Reference

The tier is inferred from your account's requests-per-minute (RPM) limit:

| Tier | RPM limit |
|---|---|
| Free | ≤ 5 |
| Build | ≤ 50 |
| Scale | ≤ 2,000 |
| Enterprise | > 2,000 |

---

## Auto-Launch at Login

To have the widget start automatically when you log in:

1. Open **System Settings → General → Login Items**
2. Click **+** under "Open at Login"
3. Navigate to `claude_usage_widget.py` and select it

> Alternatively, wrap it in a shell script and add that to Login Items.

---

## Project Structure

```
ClaudeOut/
├── claude_usage_widget.py   # Main app (~180 lines of Python)
├── requirements.txt          # Python dependencies: rumps, requests
├── install.sh                # One-command installer
└── README.md                 # This file
```

---

## How It Works

On each refresh, the widget makes a lightweight `GET /v1/models` request to the Anthropic API. This call:

- Uses **no tokens** (no inference is run)
- Returns **rate-limit headers** in the HTTP response
- Costs nothing beyond your standard API access

The headers used:

```
anthropic-ratelimit-requests-limit
anthropic-ratelimit-requests-remaining
anthropic-ratelimit-requests-reset
anthropic-ratelimit-tokens-limit
anthropic-ratelimit-tokens-remaining
anthropic-ratelimit-tokens-reset
```

---

## Limitations

- Shows **API rate limits** (per-minute window), not monthly billing usage.
  For monthly usage and cost breakdowns, visit [console.anthropic.com/settings/usage](https://console.anthropic.com/settings/usage).
- Does **not** track Claude.ai web subscription usage (Pro / Max plans).
- The API key is stored in plain text at `~/.config/claude-widget/config.json`.
  Keep this file private (`chmod 600 ~/.config/claude-widget/config.json`).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Claude ⚠️ Invalid API key` | Re-enter your key via **Set API Key…** |
| `Claude ⚠️ No internet connection` | Check your network, then click **Refresh Now** |
| Widget not in menu bar | Make sure the script is still running in the terminal |
| `ModuleNotFoundError: No module named 'rumps'` | Run `pip3 install rumps requests` |
| Menu bar shows `Claude ⏳` forever | Restart the script; a fetch may have hung |

---

## Dependencies

| Package | Purpose | License |
|---|---|---|
| [rumps](https://github.com/jaredks/rumps) | macOS menu bar framework | BSD |
| [requests](https://github.com/psf/requests) | HTTP client | Apache 2.0 |

---

## Contributing

This project was built with [Claude Code](https://claude.ai/claude-code) — Anthropic's official AI coding assistant.

> Built with Claude Code
