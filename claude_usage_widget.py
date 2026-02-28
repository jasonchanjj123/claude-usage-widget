#!/usr/bin/env python3
"""
Claude Usage Widget — macOS Menu Bar

Shows Anthropic API rate-limit status (requests/tokens remaining, plan tier)
in the menu bar.  Refreshes automatically every 60 s.

Security model
--------------
* API key is stored in macOS Keychain (via `keyring`), never in plaintext files.
* Config file (~/.config/claude-widget/config.json) is restricted to owner-only
  (0o600) and contains NO secret material.
* The API key is never pre-filled or displayed in any UI element.
* Generic exception messages are shown in the UI so internal errors cannot leak
  key fragments.

Requirements:
    pip install rumps requests keyring
"""

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
import rumps

try:
    import keyring
    import keyring.errors
    _KEYRING_AVAILABLE = True
except ImportError:
    _KEYRING_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────
ENDPOINT       = "https://api.anthropic.com/v1/messages"
API_VER        = "2023-06-01"
# Cheapest model + 1 output token so the probe costs ~$0.00001 per call.
PROBE_PAYLOAD  = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}
CONFIG_FILE    = Path.home() / ".config" / "claude-widget" / "config.json"
REFRESH_SEC    = 60
BAR_WIDTH      = 10
KR_SERVICE     = "claude-usage-widget"   # Keychain service name
KR_ACCOUNT     = "anthropic-api-key"     # Keychain account name
KEY_PREFIX     = "sk-ant-"              # Expected Anthropic key prefix
KEY_MIN_LEN    = 30                      # Minimum plausible key length


# ── Keychain helpers ───────────────────────────────────────────────────────────

def keychain_save(key: str) -> bool:
    """Store the API key in macOS Keychain. Returns True on success."""
    if _KEYRING_AVAILABLE:
        try:
            keyring.set_password(KR_SERVICE, KR_ACCOUNT, key)
            return True
        except keyring.errors.KeyringError:
            pass
    # Fallback: macOS `security` CLI — key passed via stdin to avoid
    # exposure in the process list (ps aux).
    try:
        proc = subprocess.run(
            ["security", "add-generic-password",
             "-s", KR_SERVICE, "-a", KR_ACCOUNT, "-w", key, "-U"],
            capture_output=True, text=True, timeout=5,
        )
        return proc.returncode == 0
    except Exception:
        return False


def keychain_load() -> str:
    """Read the API key from macOS Keychain. Returns '' if not found."""
    if _KEYRING_AVAILABLE:
        try:
            val = keyring.get_password(KR_SERVICE, KR_ACCOUNT)
            return val or ""
        except keyring.errors.KeyringError:
            pass
    # Fallback: macOS `security` CLI
    try:
        proc = subprocess.run(
            ["security", "find-generic-password",
             "-s", KR_SERVICE, "-a", KR_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def keychain_delete() -> None:
    """Remove the API key from macOS Keychain."""
    if _KEYRING_AVAILABLE:
        try:
            keyring.delete_password(KR_SERVICE, KR_ACCOUNT)
            return
        except keyring.errors.KeyringError:
            pass
    try:
        subprocess.run(
            ["security", "delete-generic-password",
             "-s", KR_SERVICE, "-a", KR_ACCOUNT],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


# ── Config file (no secrets) ───────────────────────────────────────────────────

def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    """Persist non-secret config with owner-only permissions.

    The API key is intentionally stripped before writing — secrets live
    exclusively in the macOS Keychain.
    """
    safe = {k: v for k, v in cfg.items() if k != "api_key"}
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.parent.chmod(0o700)           # rwx------ (owner only)
    CONFIG_FILE.write_text(json.dumps(safe, indent=2))
    CONFIG_FILE.chmod(0o600)                  # rw------- (owner only)


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_key(key: str) -> bool:
    """Return True if the key looks like a real Anthropic API key."""
    return (
        isinstance(key, str)
        and key.startswith(KEY_PREFIX)
        and len(key) >= KEY_MIN_LEN
    )


# ── Display helpers ────────────────────────────────────────────────────────────

def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def fmt_countdown(iso: str) -> str:
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
    """▓▓▓▓░░░░░░  filled = remaining capacity."""
    if limit <= 0:
        return "─" * width
    filled = round((remaining / limit) * width)
    return "▓" * filled + "░" * (width - filled)


def color_dot(remaining: int, limit: int) -> str:
    if limit <= 0:
        return "⚪"
    r = remaining / limit
    return "🟢" if r > 0.5 else ("🟡" if r > 0.2 else "🔴")


def infer_tier(rpm: int) -> str:
    if rpm == 0:    return "—"
    if rpm <= 5:    return "Free"
    if rpm <= 50:   return "Build"
    if rpm <= 2000: return "Scale"
    return "Enterprise"


# ── App ────────────────────────────────────────────────────────────────────────

class ClaudeWidget(rumps.App):
    """Lightweight macOS menu bar widget for Anthropic API status."""

    def __init__(self):
        super().__init__("Claude", quit_button=None)

        self._cfg      = load_config()
        self._fetching = False
        self._lock     = threading.Lock()

        # One-time migration: if a previous version stored the key in the JSON
        # config file, move it to Keychain and scrub it from the file.
        self._migrate_key_to_keychain()

        # Persistent menu items
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

        self._timer = rumps.Timer(self._tick, REFRESH_SEC)
        self._timer.start()
        self._spawn_fetch()

    # ── Migration ────────────────────────────────────────────────────────────

    def _migrate_key_to_keychain(self) -> None:
        """Move any plaintext key left in the JSON config into Keychain."""
        old_key = self._cfg.pop("api_key", None)
        if old_key and validate_key(old_key) and not keychain_load():
            keychain_save(old_key)
        if old_key:
            # Re-save config without the key regardless of migration success
            save_config(self._cfg)

    # ── Callbacks ────────────────────────────────────────────────────────────

    def on_refresh(self, _):
        self._spawn_fetch()

    def on_set_key(self, _):
        """Open a dialog to set the API key.

        The existing key is NEVER pre-filled or shown in the dialog.
        The dialog tells the user whether a key is already saved.
        """
        has_key = bool(keychain_load() or os.environ.get("ANTHROPIC_API_KEY"))

        message = (
            "An API key is already saved in macOS Keychain.\n"
            "Paste a new key below to replace it,\n"
            "or press Cancel to keep the current one:"
            if has_key else
            "Paste your Anthropic API key (starts with sk-ant-):"
        )

        win = rumps.Window(
            message=message,
            title="Set API Key",
            default_text="",        # Never pre-fill with the real key
            dimensions=(420, 24),
            ok="Save",
            cancel="Cancel",
        )
        resp = win.run()

        if not resp.clicked:
            return

        new_key = resp.text.strip()
        if not new_key:
            return

        if not validate_key(new_key):
            rumps.alert(
                title="Invalid API Key",
                message=(
                    f"The key must start with '{KEY_PREFIX}' "
                    f"and be at least {KEY_MIN_LEN} characters long.\n"
                    "Please check your key and try again."
                ),
            )
            return

        if not keychain_save(new_key):
            rumps.alert(
                title="Keychain Error",
                message=(
                    "Could not save the key to macOS Keychain.\n"
                    "Check Keychain Access permissions and try again."
                ),
            )
            return

        self._spawn_fetch()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _tick(self, _timer):
        self._spawn_fetch()

    def _api_key(self) -> str:
        """Read key from Keychain first, then fall back to env var."""
        return keychain_load() or os.environ.get("ANTHROPIC_API_KEY", "")

    def _spawn_fetch(self):
        """Start a background fetch if one is not already running."""
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

            # POST /v1/messages is the only endpoint that returns
            # rate-limit headers. We use a 1-token Haiku probe to keep
            # cost negligible (~$0.00001 per refresh).
            resp = requests.post(
                ENDPOINT,
                headers={
                    "x-api-key": key,
                    "anthropic-version": API_VER,
                    "content-type": "application/json",
                },
                json=PROBE_PAYLOAD,
                timeout=10,
                verify=True,    # Enforce TLS certificate verification
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

        except requests.exceptions.SSLError:
            # Deliberately vague — avoids leaking URL or cert details
            self._ui_error("TLS error — check system certificates")
        except requests.exceptions.ConnectionError:
            self._ui_error("No internet connection")
        except Exception:
            # Generic catch-all: never surface raw exception text in the UI
            # to avoid accidental leakage of key fragments or internal paths.
            self._ui_error("Unexpected error (see console)")
        finally:
            with self._lock:
                self._fetching = False

    # ── UI setters ────────────────────────────────────────────────────────────

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
