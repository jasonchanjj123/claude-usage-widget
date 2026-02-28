#!/usr/bin/env python3
"""
Claude Usage Widget — macOS Menu Bar

Shows Anthropic API rate-limit status (requests/tokens remaining, plan tier)
in the menu bar.  Refreshes automatically every 60 s.

Data source: rate-limit response headers from GET /v1/models.
These headers reflect your account's per-minute limits, which indicate
your plan tier (Free / Build / Scale / Enterprise).

For monthly billing usage, visit: https://console.anthropic.com/settings/usage

Requirements:
    pip install rumps requests

Run:
    python3 claude_usage_widget.py

Set your API key either via the 'Set API Key…' menu item or the
ANTHROPIC_API_KEY environment variable.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
import rumps

# ── Constants ──────────────────────────────────────────────────────────────────
ENDPOINT    = "https://api.anthropic.com/v1/models"
API_VER     = "2023-06-01"
CONFIG_FILE = Path.home() / ".config" / "claude-widget" / "config.json"
REFRESH_SEC = 60        # auto-refresh interval
BAR_WIDTH   = 10        # progress-bar character width


# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_num(n: int) -> str:
    """Format large integers as 1.5M / 150K."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def fmt_countdown(iso: str) -> str:
    """Convert an ISO-8601 UTC timestamp to a human '2m 05s' countdown."""
    if not iso:
        return "—"
    try:
        dt   = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        m, s = divmod(int(secs), 60)
        return f"{m}m {s:02d}s" if m else f"{s}s"
    except Exception:
        return "—"


def progress_bar(remaining: int, limit: int, width: int = BAR_WIDTH) -> str:
    """▓▓▓▓░░░░░░  — filled portion = remaining capacity."""
    if limit <= 0:
        return "─" * width
    filled = round((remaining / limit) * width)
    return "▓" * filled + "░" * (width - filled)


def color_dot(remaining: int, limit: int) -> str:
    """Return a coloured circle based on how much capacity remains."""
    if limit <= 0:
        return "⚪"
    r = remaining / limit
    return "🟢" if r > 0.5 else ("🟡" if r > 0.2 else "🔴")


def infer_tier(rpm: int) -> str:
    """Guess plan tier from the requests-per-minute limit."""
    if rpm == 0:    return "—"
    if rpm <= 5:    return "Free"
    if rpm <= 50:   return "Build"
    if rpm <= 2000: return "Scale"
    return "Enterprise"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── App ────────────────────────────────────────────────────────────────────────

class ClaudeWidget(rumps.App):
    """Lightweight macOS menu bar widget for Anthropic API status."""

    def __init__(self):
        super().__init__("Claude", quit_button=None)

        self._cfg      = load_config()
        self._fetching = False
        self._lock     = threading.Lock()

        # ── Persistent menu items (titles are updated in-place) ────────────
        self.mi_status = rumps.MenuItem("Loading…")
        self.mi_plan   = rumps.MenuItem("📋  Plan: —")
        self.mi_req    = rumps.MenuItem("📊  Requests: —")
        self.mi_tok    = rumps.MenuItem("🔤  Tokens: —")
        self.mi_reset  = rumps.MenuItem("⏱  Resets in: —")
        self.mi_time   = rumps.MenuItem("🕐  Updated: —")

        self.menu = [
            self.mi_status,
            self.mi_plan,
            None,
            self.mi_req,
            self.mi_tok,
            self.mi_reset,
            None,
            self.mi_time,
            None,
            rumps.MenuItem("Refresh Now",  callback=self.on_refresh),
            rumps.MenuItem("Set API Key…", callback=self.on_set_key),
            None,
            rumps.MenuItem("Quit",         callback=rumps.quit_application),
        ]

        # Auto-refresh timer
        self._timer = rumps.Timer(self._tick, REFRESH_SEC)
        self._timer.start()

        # First fetch on startup
        self._spawn_fetch()

    # ── Callbacks ───────────────────────────────────────────────────────────

    def on_refresh(self, _):
        self._spawn_fetch()

    def on_set_key(self, _):
        current = self._cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        win = rumps.Window(
            message="Paste your Anthropic API key (starts with sk-ant-):",
            title="Set API Key",
            default_text=current,
            dimensions=(420, 24),
            ok="Save",
            cancel="Cancel",
        )
        resp = win.run()
        if resp.clicked and resp.text.strip():
            self._cfg["api_key"] = resp.text.strip()
            save_config(self._cfg)
            self._spawn_fetch()

    # ── Internal ────────────────────────────────────────────────────────────

    def _tick(self, _timer):
        self._spawn_fetch()

    def _api_key(self) -> str:
        return self._cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    def _spawn_fetch(self):
        """Start a background fetch if none is already running."""
        with self._lock:
            if self._fetching:
                return
            self._fetching = True
        self.title = "Claude ⏳"
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            key = self._api_key()
            if not key:
                self._ui_no_key()
                return

            resp = requests.get(
                ENDPOINT,
                headers={"x-api-key": key, "anthropic-version": API_VER},
                timeout=10,
            )

            if resp.status_code == 401:
                self._ui_error("Invalid API key")
                return
            if resp.status_code != 200:
                self._ui_error(f"HTTP {resp.status_code}")
                return

            h = resp.headers

            def hi(name: str) -> int:
                try:
                    return int(h.get(name) or 0)
                except ValueError:
                    return 0

            self._ui_ok(
                req_lim=hi("anthropic-ratelimit-requests-limit"),
                req_rem=hi("anthropic-ratelimit-requests-remaining"),
                req_rst=h.get("anthropic-ratelimit-requests-reset", ""),
                tok_lim=hi("anthropic-ratelimit-tokens-limit"),
                tok_rem=hi("anthropic-ratelimit-tokens-remaining"),
                tok_rst=h.get("anthropic-ratelimit-tokens-reset", ""),
            )

        except requests.exceptions.ConnectionError:
            self._ui_error("No internet connection")
        except Exception as exc:
            self._ui_error(str(exc)[:60])
        finally:
            with self._lock:
                self._fetching = False

    # ── UI setters (called from background thread — safe for rumps) ─────────

    def _ui_no_key(self):
        self.title            = "Claude 🔑"
        self.mi_status.title  = "⚠️  No API key — use 'Set API Key…' below"
        self.mi_plan.title    = "📋  Plan: (set key to detect)"
        self.mi_req.title     = "📊  Requests: —"
        self.mi_tok.title     = "🔤  Tokens: —"
        self.mi_reset.title   = "⏱  Resets in: —"
        self.mi_time.title    = "🕐  Updated: —"

    def _ui_error(self, msg: str):
        self.title            = "Claude ⚠️"
        self.mi_status.title  = f"❌  {msg}"
        self.mi_time.title    = f"🕐  Tried: {datetime.now().strftime('%H:%M:%S')}"

    def _ui_ok(
        self,
        req_lim: int, req_rem: int, req_rst: str,
        tok_lim: int, tok_rem: int, tok_rst: str,
    ):
        req_dot = color_dot(req_rem, req_lim)
        tok_dot = color_dot(tok_rem, tok_lim)
        worst   = ("🔴" if "🔴" in (req_dot, tok_dot)
                   else "🟡" if "🟡" in (req_dot, tok_dot)
                   else "🟢")

        req_pct = int((req_lim - req_rem) / req_lim * 100) if req_lim else 0
        tok_pct = int((tok_lim - tok_rem) / tok_lim * 100) if tok_lim else 0

        self.title           = f"Claude {worst}"
        self.mi_status.title = f"{worst}  Connected"
        self.mi_plan.title   = f"📋  Tier: {infer_tier(req_lim)}"
        self.mi_req.title    = (
            f"📊  Requests  {progress_bar(req_rem, req_lim)}"
            f"  {fmt_num(req_rem)}/{fmt_num(req_lim)} left  ({req_pct}% used)"
        )
        self.mi_tok.title    = (
            f"🔤  Tokens    {progress_bar(tok_rem, tok_lim)}"
            f"  {fmt_num(tok_rem)}/{fmt_num(tok_lim)} left  ({tok_pct}% used)"
        )
        self.mi_reset.title  = f"⏱  Resets in: {fmt_countdown(req_rst)}"
        self.mi_time.title   = f"🕐  Updated: {datetime.now().strftime('%H:%M:%S')}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ClaudeWidget().run()
