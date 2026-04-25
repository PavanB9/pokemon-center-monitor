"""
Pokemon Center Queue & New Product Monitor
============================================
Checks pokemoncenter.com every 3 minutes for:
  - Virtual queue activation (Queue-it waiting room)
  - New product drops on the "just arrived" / "new" pages

Sends Windows toast notifications when something is detected.
All traffic is standard HTTPS GET — same as opening the page in your browser.
"""

import ctypes
import winsound
import requests
import threading
import time
import json
import re
import sys
import os

# ─── MUST be called before tkinter is imported ────────────────────────────────
# This tells Windows to treat this process as its own app (not pythonw.exe),
# so the taskbar shows our custom icon instead of the Python logo.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "pokemoncenter.monitor.1.1"
    )
except Exception:
    pass

import tkinter as tk
from tkinter import scrolledtext, ttk
from datetime import datetime
from winotify import Notification, audio

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_INTERVAL_MINUTES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

URLS_TO_CHECK = {
    "Homepage": "https://www.pokemoncenter.com/",
    "New Arrivals": "https://www.pokemoncenter.com/category/new-arrivals",
}

# Queue-it fingerprints we look for in the response
QUEUE_SIGNATURES = [
    "queue-it.net",
    "queue.pokemoncenter.com",
    "waiting room",
    "waitingroom",
    "you are now in line",
    "virtual queue",
    "Queue-it",
    "queueittoken",
]

# Settings file path (next to monitor.py).
# Keep this file out of Git via .gitignore because it may contain user-local secrets.
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# ─── Settings persistence ────────────────────────────────────────────────────

def load_settings() -> dict:
    """Load settings from disk, returning defaults if the file is missing."""
    defaults = {
        "minimize_to_tray": False,
        "sound_enabled": True,
        "interval_minutes": DEFAULT_INTERVAL_MINUTES,
        "discord_webhook_url": "",
    }
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            defaults.update(data)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


def save_settings(settings: dict):
    """Persist settings to disk."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass

# ─── Webhook URL validation ──────────────────────────────────────────────────

# Only these URL prefixes are accepted. This is a basic format check, not a
# security boundary — the webhook URL is user-supplied and stored locally.
_VALID_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)

def _is_valid_discord_webhook(url: str) -> bool:
    """Check that a URL matches the expected Discord webhook format."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    return any(url.startswith(prefix) for prefix in _VALID_WEBHOOK_PREFIXES)

# ─── Notification helpers ────────────────────────────────────────────────────

def send_toast(title: str, message: str):
    """Fire a native Windows 10/11 toast notification."""
    try:
        toast = Notification(
            app_id="Pokemon Center Monitor",
            title=title,
            msg=message,
            duration="long",
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception as e:
        print(f"[toast error] {e}")

# ─── Alert sound ─────────────────────────────────────────────────────────────

_ALERT_WAV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert.wav")
_sound_lock = threading.Lock()


def play_alert_sound():
    """Play the alert WAV file (non-blocking, ignores rapid re-clicks)."""
    def _play():
        if not _sound_lock.acquire(blocking=False):
            return  # Already playing — skip
        try:
            winsound.PlaySound(_ALERT_WAV_PATH, winsound.SND_FILENAME)
        except Exception:
            pass
        finally:
            _sound_lock.release()
    threading.Thread(target=_play, daemon=True).start()


def _send_discord_webhook_sync(webhook_url: str, title: str, message: str):
    """Send a Discord webhook message synchronously.

    Returns (success: bool, detail: str) so the caller can report the outcome.
    Only sends to URLs matching the expected Discord webhook format.
    """
    if not _is_valid_discord_webhook(webhook_url):
        return False, "Invalid webhook URL"

    try:
        payload = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": 0xE94560,
                "footer": {"text": "Pokemon Center Monitor"},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }]
        }
        resp = requests.post(
            webhook_url, json=payload, timeout=10,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (200, 204):
            return True, f"HTTP {resp.status_code}"
        return False, f"HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, "Request timed out"
    except requests.exceptions.ConnectionError:
        return False, "Connection failed"
    except Exception as e:
        return False, str(e)


def send_discord_webhook_async(webhook_url: str, title: str, message: str):
    """Fire-and-forget wrapper for background alert delivery."""
    def _send():
        ok, detail = _send_discord_webhook_sync(webhook_url, title, message)
        if not ok:
            print(f"[discord] {detail}")
    threading.Thread(target=_send, daemon=True).start()

# ─── Tray icon image ─────────────────────────────────────────────────────────

def _create_tray_icon(size=64):
    """Create a small pokeball-like icon for the system tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    center = size // 2
    radius = center - 2

    # Outer circle
    draw.ellipse([2, 2, size - 2, size - 2], fill="#e94560", outline="#1a1a2e", width=2)

    # Bottom half (white)
    draw.pieslice([2, 2, size - 2, size - 2], start=0, end=180, fill="#eaeaea", outline="#1a1a2e", width=2)

    # Center band
    band_h = size // 8
    draw.rectangle([2, center - band_h, size - 2, center + band_h], fill="#1a1a2e")

    # Center circle
    inner_r = size // 6
    draw.ellipse(
        [center - inner_r, center - inner_r, center + inner_r, center + inner_r],
        fill="#eaeaea", outline="#1a1a2e", width=2,
    )
    small_r = inner_r // 2
    draw.ellipse(
        [center - small_r, center - small_r, center + small_r, center + small_r],
        fill="#e94560", outline="#1a1a2e", width=1,
    )

    return img

# ─── Core checking logic ─────────────────────────────────────────────────────

class PokemonCenterMonitor:
    def __init__(self, log_callback=None, status_callback=None, interval_callback=None,
                 alert_callback=None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.running = False
        self._thread = None
        self._check_lock = threading.Lock()  # prevents overlapping checks
        self.log = log_callback or print
        self.set_status = status_callback or (lambda *a: None)
        self._get_interval = interval_callback or (lambda: DEFAULT_INTERVAL_MINUTES * 60)
        self._fire_alert = alert_callback or (lambda t, m: None)
        self.check_count = 0
        self.last_check_time = None
        self.last_status_summary = "No checks yet"
        self.last_seen_products = {}   # url -> set of product identifiers

    # ── single check ──────────────────────────────────────────────────────

    def _check_url(self, label: str, url: str):
        """Fetch one URL and inspect the response."""
        try:
            resp = self.session.get(url, timeout=20, allow_redirects=True)
            status = resp.status_code
            body = resp.text.lower()
            final_url = resp.url.lower()

            self.log(f"  [{label}] HTTP {status}  ({len(body):,} chars)")

            # 1) Queue detection  ─────────────────────────────────────────
            queue_hits = [sig for sig in QUEUE_SIGNATURES if sig.lower() in body or sig.lower() in final_url]
            if queue_hits:
                msg = f"🚨 QUEUE DETECTED on {label}!\nMatched: {', '.join(queue_hits)}"
                self.log(f"  ⚠️  {msg}")
                self.last_status_summary = f"QUEUE on {label}!"
                send_toast("🚨 Queue Active!", f"Pokemon Center queue detected on {label}!")
                self._fire_alert("🚨 Queue Active!", f"Pokemon Center queue detected on {label}!")
                return

            # 2) Redirect to a queue domain?  ─────────────────────────────
            if "queue-it" in final_url or "queue" in final_url:
                msg = f"🚨 Redirected to queue page: {resp.url}"
                self.log(f"  ⚠️  {msg}")
                self.last_status_summary = f"Queue redirect on {label}!"
                send_toast("🚨 Queue Redirect!", msg)
                self._fire_alert("🚨 Queue Redirect!", msg)
                return

            # 3) New-product detection (for product-listing pages) ────────
            if "new-arrivals" in url or "just-arrived" in url:
                product_links = set(re.findall(
                    r'href="(/product/[^"]+)"', resp.text
                ))
                prev = self.last_seen_products.get(url, set())
                if prev:
                    new_ones = product_links - prev
                    if new_ones:
                        count = len(new_ones)
                        msg = f"🆕 {count} new product(s) on {label}!"
                        self.log(f"  🆕 {msg}")
                        self.last_status_summary = msg
                        for p in list(new_ones)[:5]:
                            self.log(f"      → pokemoncenter.com{p}")
                        send_toast("🆕 New Products!", msg)
                        self._fire_alert("🆕 New Products!", msg)
                    else:
                        self.log(f"  ✅ No new products on {label}")
                else:
                    self.log(f"  📋 Baseline captured: {len(product_links)} products on {label}")
                self.last_seen_products[url] = product_links
            else:
                self.log(f"  ✅ No queue on {label}")

        except requests.exceptions.Timeout:
            self.log(f"  ⏱️  [{label}] Timeout — site may be under heavy load")
            self.last_status_summary = f"Timeout on {label}"
            send_toast("⏱️ Timeout", f"Pokemon Center {label} timed out — possible high traffic!")
            self._fire_alert("⏱️ Timeout", f"{label} timed out — possible high traffic!")
        except requests.exceptions.ConnectionError as e:
            self.log(f"  🔴 [{label}] Connection error: {e}")
            self.last_status_summary = f"Connection error on {label}"
        except Exception as e:
            self.log(f"  ❌ [{label}] Error: {e}")
            self.last_status_summary = f"Error on {label}"

    def run_check(self):
        """Run one round of checks across all URLs.

        Uses _check_lock to prevent overlapping checks from the scheduled loop
        and manual 'Check Now' calls.
        """
        if not self._check_lock.acquire(blocking=False):
            self.log("  ⏭️  Check already in progress — skipped")
            return
        try:
            self.check_count += 1
            now = datetime.now().strftime("%I:%M:%S %p")
            self.last_check_time = now
            self.log(f"\n{'─'*50}")
            self.log(f"Check #{self.check_count}  •  {now}")
            self.log(f"{'─'*50}")
            self.set_status(f"Checking...  (#{self.check_count})")
            self.last_status_summary = "All clear"

            for label, url in URLS_TO_CHECK.items():
                self._check_url(label, url)

            interval_sec = self._get_interval()
            interval_min = interval_sec // 60
            self.set_status(f"Idle  •  Last check: {now}  •  Next in {interval_min} min")
            self.log(f"  ⏳ Next check in {interval_min} minute{'s' if interval_min != 1 else ''}...")
        finally:
            self._check_lock.release()

    # ── loop control ──────────────────────────────────────────────────────

    def _loop(self):
        while self.running:
            self.run_check()
            # Sleep in 1-second increments so we can stop quickly
            interval_sec = self._get_interval()
            for _ in range(interval_sec):
                if not self.running:
                    break
                time.sleep(1)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        interval_min = self._get_interval() // 60
        self.log(f"▶️  Monitor started — checking every {interval_min} minute{'s' if interval_min != 1 else ''}")
        self.set_status("Starting first check...")

    def stop(self):
        self.running = False
        self.log("⏹️  Monitor stopped")
        self.set_status("Stopped")


# ─── GUI ──────────────────────────────────────────────────────────────────────

class MonitorApp:
    BG           = "#1a1a2e"
    BG_SECONDARY = "#16213e"
    ACCENT       = "#e94560"
    ACCENT_HOVER = "#ff6b81"
    GREEN        = "#0f9b58"
    GREEN_HOVER  = "#13bf6d"
    TEXT         = "#eaeaea"
    TEXT_DIM     = "#8892b0"
    LOG_BG       = "#0f0f1a"
    TOGGLE_ON    = "#0f9b58"
    TOGGLE_OFF   = "#3a3a5c"
    FONT         = ("Segoe UI", 10)
    FONT_BOLD    = ("Segoe UI", 10, "bold")
    FONT_TITLE   = ("Segoe UI", 16, "bold")
    FONT_MONO    = ("Cascadia Code", 9)
    FONT_SMALL   = ("Segoe UI", 9)

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Pokemon Center Monitor")
        self.root.geometry("720x710")
        self.root.configure(bg=self.BG)
        self.root.resizable(True, True)
        self.root.minsize(500, 450)

        # Set Pokeball window icon (title bar + taskbar)
        try:
            self._icon_img = _create_tray_icon(256)
            self._tk_icon = ImageTk.PhotoImage(self._icon_img)
            self.root.iconphoto(True, self._tk_icon)
        except Exception:
            pass

        # Load settings
        self.settings = load_settings()
        self.minimize_to_tray_var = tk.BooleanVar(value=self.settings.get("minimize_to_tray", False))
        self.interval_minutes = tk.IntVar(value=self.settings.get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
        self.sound_enabled_var = tk.BooleanVar(value=self.settings.get("sound_enabled", True))
        self.discord_webhook_url = self.settings.get("discord_webhook_url", "")

        self.monitor = PokemonCenterMonitor(
            log_callback=self._append_log,
            status_callback=self._set_status,
            interval_callback=self._get_interval_seconds,
            alert_callback=self._on_alert,
        )

        # System tray
        self._tray_icon = None
        self._tray_thread = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        # Title bar
        header = tk.Frame(self.root, bg=self.BG)
        header.pack(fill="x", padx=20, pady=(18, 4))

        tk.Label(
            header, text="⚡ Pokemon Center Monitor",
            font=self.FONT_TITLE, fg=self.ACCENT, bg=self.BG,
        ).pack(side="left")

        self.status_label = tk.Label(
            header, text="Ready", font=self.FONT, fg=self.TEXT_DIM, bg=self.BG,
        )
        self.status_label.pack(side="right")

        # Subtitle
        tk.Label(
            self.root,
            text="Monitors for virtual queue activation & new product drops",
            font=self.FONT, fg=self.TEXT_DIM, bg=self.BG,
        ).pack(anchor="w", padx=22, pady=(0, 10))

        # ── Info cards ────────────────────────────────────────────────────
        cards_frame = tk.Frame(self.root, bg=self.BG)
        cards_frame.pack(fill="x", padx=20, pady=(0, 8))

        self._make_interval_card(cards_frame).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._make_card(cards_frame, "Targets", f"{len(URLS_TO_CHECK)} URLs").pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._alerts_card_value = tk.StringVar()
        alerts_card = tk.Frame(cards_frame, bg=self.BG_SECONDARY, padx=12, pady=8)
        tk.Label(alerts_card, text="Alerts", font=("Segoe UI", 8), fg=self.TEXT_DIM, bg=self.BG_SECONDARY).pack(anchor="w")
        tk.Label(alerts_card, textvariable=self._alerts_card_value, font=self.FONT_BOLD, fg=self.TEXT, bg=self.BG_SECONDARY, wraplength=150, justify="left").pack(anchor="w")
        alerts_card.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._update_alerts_card()

        # ── Buttons ──────────────────────────────────────────────────────
        btn_frame = tk.Frame(self.root, bg=self.BG)
        btn_frame.pack(fill="x", padx=20, pady=(0, 8))

        self.start_btn = tk.Button(
            btn_frame, text="▶  Start Monitoring", font=self.FONT_BOLD,
            fg="white", bg=self.GREEN, activebackground=self.GREEN_HOVER,
            activeforeground="white", bd=0, padx=18, pady=8,
            cursor="hand2", command=self._start,
        )
        self.start_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = tk.Button(
            btn_frame, text="⏹  Stop", font=self.FONT_BOLD,
            fg="white", bg=self.ACCENT, activebackground=self.ACCENT_HOVER,
            activeforeground="white", bd=0, padx=18, pady=8,
            cursor="hand2", command=self._stop, state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(0, 6))

        self.check_now_btn = tk.Button(
            btn_frame, text="🔄  Check Now", font=self.FONT_BOLD,
            fg=self.TEXT, bg=self.BG_SECONDARY, activebackground="#1e2d4d",
            activeforeground="white", bd=0, padx=18, pady=8,
            cursor="hand2", command=self._check_now,
        )
        self.check_now_btn.pack(side="left")

        # ── Settings panel ────────────────────────────────────────────────
        settings_outer = tk.Frame(self.root, bg=self.BG_SECONDARY, padx=14, pady=10)
        settings_outer.pack(fill="x", padx=20, pady=(0, 8))

        tk.Label(
            settings_outer, text="⚙  Settings",
            font=self.FONT_BOLD, fg=self.TEXT_DIM, bg=self.BG_SECONDARY,
        ).pack(anchor="w")

        # Row 1: Toggles
        toggles_row = tk.Frame(settings_outer, bg=self.BG_SECONDARY)
        toggles_row.pack(fill="x", pady=(6, 0))

        # Sound alert toggle
        sound_frame = tk.Frame(toggles_row, bg=self.BG_SECONDARY)
        sound_frame.pack(side="left", padx=(0, 20))

        tk.Label(
            sound_frame, text="Sound alert",
            font=self.FONT_SMALL, fg=self.TEXT_DIM, bg=self.BG_SECONDARY,
        ).pack(side="left", padx=(0, 8))

        self.sound_toggle_canvas = tk.Canvas(
            sound_frame, width=44, height=24,
            bg=self.BG_SECONDARY, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        self.sound_toggle_canvas.pack(side="left")
        self.sound_toggle_canvas.bind("<Button-1>", self._toggle_sound_setting)
        self._draw_toggle_on(self.sound_toggle_canvas, self.sound_enabled_var.get())

        tk.Button(
            sound_frame, text="Test", font=self.FONT_SMALL,
            fg=self.TEXT, bg="#2a2a4a", activebackground="#3a3a6a",
            activeforeground="white", bd=0, padx=8, pady=1,
            cursor="hand2", command=play_alert_sound,
        ).pack(side="left", padx=(6, 0))

        # Minimize-to-tray toggle
        tray_toggle_frame = tk.Frame(toggles_row, bg=self.BG_SECONDARY)
        tray_toggle_frame.pack(side="left")

        tk.Label(
            tray_toggle_frame, text="Minimize to tray on close",
            font=self.FONT_SMALL, fg=self.TEXT_DIM, bg=self.BG_SECONDARY,
        ).pack(side="left", padx=(0, 8))

        self.toggle_canvas = tk.Canvas(
            tray_toggle_frame, width=44, height=24,
            bg=self.BG_SECONDARY, bd=0, highlightthickness=0,
            cursor="hand2",
        )
        self.toggle_canvas.pack(side="left")
        self.toggle_canvas.bind("<Button-1>", self._toggle_tray_setting)
        self._draw_toggle_on(self.toggle_canvas, self.minimize_to_tray_var.get())

        # Row 2: Discord webhook
        discord_row = tk.Frame(settings_outer, bg=self.BG_SECONDARY)
        discord_row.pack(fill="x", pady=(8, 0))

        tk.Label(
            discord_row, text="Discord Webhook",
            font=self.FONT_SMALL, fg=self.TEXT_DIM, bg=self.BG_SECONDARY,
        ).pack(side="left", padx=(0, 8))

        self.webhook_entry = tk.Entry(
            discord_row, font=self.FONT_SMALL, width=38,
            bg="#111122", fg=self.TEXT, insertbackground=self.TEXT,
            bd=0, highlightthickness=1, highlightcolor="#3a3a6a",
            highlightbackground="#2a2a4a", show="•",
        )
        self.webhook_entry.pack(side="left", padx=(0, 6), ipady=3)
        if self.discord_webhook_url:
            self.webhook_entry.insert(0, self.discord_webhook_url)

        self.webhook_save_btn = tk.Button(
            discord_row, text="Save", font=self.FONT_SMALL,
            fg=self.TEXT, bg="#2a2a4a", activebackground="#3a3a6a",
            activeforeground="white", bd=0, padx=10, pady=2,
            cursor="hand2", command=self._save_webhook,
        )
        self.webhook_save_btn.pack(side="left", padx=(0, 4))

        self.webhook_test_btn = tk.Button(
            discord_row, text="Test", font=self.FONT_SMALL,
            fg=self.TEXT, bg="#2a2a4a", activebackground="#3a3a6a",
            activeforeground="white", bd=0, padx=10, pady=2,
            cursor="hand2", command=self._test_webhook,
        )
        self.webhook_test_btn.pack(side="left")

        self.webhook_status = tk.Label(
            discord_row, text="", font=self.FONT_SMALL,
            fg=self.TEXT_DIM, bg=self.BG_SECONDARY,
        )
        self.webhook_status.pack(side="left", padx=(8, 0))

        # ── Log area ─────────────────────────────────────────────────────
        log_frame = tk.Frame(self.root, bg=self.LOG_BG, bd=0)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(0, 14))

        tk.Label(
            log_frame, text="  Activity Log", font=self.FONT_BOLD,
            fg=self.TEXT_DIM, bg=self.LOG_BG, anchor="w",
        ).pack(fill="x", pady=(6, 0))

        self.log_area = scrolledtext.ScrolledText(
            log_frame, wrap="word", font=self.FONT_MONO,
            bg=self.LOG_BG, fg=self.TEXT, insertbackground=self.TEXT,
            selectbackground=self.ACCENT, bd=0, padx=10, pady=6,
            state="disabled",
        )
        self.log_area.pack(fill="both", expand=True)

        # Welcome message
        self._append_log("Pokemon Center Monitor v1.1")
        self._append_log("━" * 44)
        self._append_log("Watching:")
        for label, url in URLS_TO_CHECK.items():
            self._append_log(f"  • {label}: {url}")
        self._append_log("")
        self._append_log('Press "Start Monitoring" to begin.')

    def _make_card(self, parent, title, value):
        frame = tk.Frame(parent, bg=self.BG_SECONDARY, padx=12, pady=8)
        tk.Label(frame, text=title, font=("Segoe UI", 8), fg=self.TEXT_DIM, bg=self.BG_SECONDARY).pack(anchor="w")
        tk.Label(frame, text=value, font=self.FONT_BOLD, fg=self.TEXT, bg=self.BG_SECONDARY).pack(anchor="w")
        return frame

    def _make_interval_card(self, parent):
        """Build the Interval card with a dropdown instead of static text."""
        frame = tk.Frame(parent, bg=self.BG_SECONDARY, padx=12, pady=8)
        tk.Label(frame, text="Interval", font=("Segoe UI", 8), fg=self.TEXT_DIM, bg=self.BG_SECONDARY).pack(anchor="w")

        # Style the combobox to match the dark theme (no harsh borders)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Interval.TCombobox",
            fieldbackground=self.BG_SECONDARY,
            background=self.BG_SECONDARY,
            foreground=self.TEXT,
            arrowcolor=self.TEXT_DIM,
            borderwidth=1,
            relief="flat",
            lightcolor=self.BG_SECONDARY,
            darkcolor=self.BG_SECONDARY,
            bordercolor="#2a2a4a",
            insertcolor=self.TEXT,
        )
        style.map("Interval.TCombobox",
            fieldbackground=[("readonly", self.BG_SECONDARY), ("disabled", "#111122")],
            foreground=[("readonly", self.TEXT), ("disabled", self.TEXT_DIM)],
            selectbackground=[("readonly", self.BG_SECONDARY)],
            selectforeground=[("readonly", self.TEXT)],
            bordercolor=[("focus", "#3a3a6a"), ("!focus", "#2a2a4a")],
            lightcolor=[("focus", "#3a3a6a"), ("!focus", self.BG_SECONDARY)],
            darkcolor=[("focus", "#3a3a6a"), ("!focus", self.BG_SECONDARY)],
            arrowcolor=[("disabled", "#3a3a5c")],
        )
        # Style the dropdown list itself
        self.root.option_add("*TCombobox*Listbox.background", self.BG_SECONDARY)
        self.root.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#2a2a5a")
        self.root.option_add("*TCombobox*Listbox.selectForeground", self.TEXT)
        self.root.option_add("*TCombobox*Listbox.borderWidth", "0")

        options = [f"Every {i} min" for i in range(1, 11)]
        self.interval_combo = ttk.Combobox(
            frame, values=options, state="readonly",
            style="Interval.TCombobox",
            font=self.FONT_BOLD, width=12,
            textvariable=tk.StringVar(),
        )
        # Set current selection from saved setting
        self.interval_combo.current(self.interval_minutes.get() - 1)
        self.interval_combo.pack(anchor="w", pady=(2, 0))
        self.interval_combo.bind("<<ComboboxSelected>>", self._on_interval_change)
        return frame

    def _on_interval_change(self, event=None):
        """Handle interval dropdown change."""
        idx = self.interval_combo.current()  # 0-based index
        minutes = idx + 1
        self.interval_minutes.set(minutes)
        self.settings["interval_minutes"] = minutes
        save_settings(self.settings)
        self._append_log(f"⚙️  Check interval changed to {minutes} minute{'s' if minutes != 1 else ''}")

    def _get_interval_seconds(self):
        """Return the current interval in seconds."""
        return self.interval_minutes.get() * 60

    def _update_alerts_card(self):
        """Update the Alerts card to reflect which channels are active."""
        channels = ["Notification"]  # Always on
        if self.sound_enabled_var.get():
            channels.append("Sound")
        if self.discord_webhook_url and _is_valid_discord_webhook(self.discord_webhook_url):
            channels.append("Discord")
        self._alerts_card_value.set(", ".join(channels))

    # ── Toggle widget ─────────────────────────────────────────────────────

    def _draw_toggle_on(self, canvas, on):
        """Draw a modern on/off toggle switch on a given canvas."""
        canvas.delete("all")
        bg_color = self.TOGGLE_ON if on else self.TOGGLE_OFF
        knob_x = 28 if on else 12

        # Track (rounded rectangle via overlapping shapes)
        canvas.create_oval(2, 2, 22, 22, fill=bg_color, outline=bg_color)
        canvas.create_oval(22, 2, 42, 22, fill=bg_color, outline=bg_color)
        canvas.create_rectangle(12, 2, 32, 22, fill=bg_color, outline=bg_color)

        # Knob
        canvas.create_oval(knob_x - 9, 3, knob_x + 9, 21, fill="white", outline="#ccc")

    def _toggle_tray_setting(self, event=None):
        """Toggle the minimize-to-tray setting."""
        new_val = not self.minimize_to_tray_var.get()
        self.minimize_to_tray_var.set(new_val)
        self._draw_toggle_on(self.toggle_canvas, new_val)
        self.settings["minimize_to_tray"] = new_val
        save_settings(self.settings)
        state_text = "ON" if new_val else "OFF"
        self._append_log(f"⚙️  Minimize to tray: {state_text}")

    def _toggle_sound_setting(self, event=None):
        """Toggle the sound alert setting."""
        new_val = not self.sound_enabled_var.get()
        self.sound_enabled_var.set(new_val)
        self._draw_toggle_on(self.sound_toggle_canvas, new_val)
        self.settings["sound_enabled"] = new_val
        save_settings(self.settings)
        state_text = "ON" if new_val else "OFF"
        self._append_log(f"🔊  Sound alerts: {state_text}")
        self._update_alerts_card()
        if new_val:
            play_alert_sound()  # Quick preview

    # ── Discord webhook handlers ──────────────────────────────────────────

    def _save_webhook(self):
        """Validate and save the Discord webhook URL."""
        url = self.webhook_entry.get().strip()
        if not url:
            # Clear the webhook
            self.discord_webhook_url = ""
            self.settings["discord_webhook_url"] = ""
            save_settings(self.settings)
            self.webhook_status.config(text="Cleared", fg=self.TEXT_DIM)
            self._append_log("⚙️  Discord webhook cleared")
            self._update_alerts_card()
            return

        if not _is_valid_discord_webhook(url):
            self.webhook_status.config(text="Invalid URL", fg=self.ACCENT)
            self._append_log("⚠️  Invalid webhook — must be a discord.com webhook URL")
            return

        self.discord_webhook_url = url
        self.settings["discord_webhook_url"] = url
        save_settings(self.settings)
        self.webhook_status.config(text="Saved ✓", fg=self.GREEN)
        self._append_log("⚙️  Discord webhook saved")
        self._update_alerts_card()

    def _test_webhook(self):
        """Send a test message and report real success/failure."""
        url = self.discord_webhook_url
        if not url:
            self.webhook_status.config(text="No webhook saved", fg=self.ACCENT)
            return
        if not _is_valid_discord_webhook(url):
            self.webhook_status.config(text="Invalid URL", fg=self.ACCENT)
            return

        self.webhook_status.config(text="Sending...", fg=self.TEXT_DIM)
        self.webhook_test_btn.config(state="disabled")
        self._append_log("📤  Sending test message to Discord...")

        def _do_test():
            ok, detail = _send_discord_webhook_sync(
                url, "🧪 Test Alert", "This is a test from Pokemon Center Monitor!"
            )
            # Schedule UI update back on the main thread
            def _update():
                self.webhook_test_btn.config(state="normal")
                if ok:
                    self.webhook_status.config(text="Sent ✓", fg=self.GREEN)
                    self._append_log(f"✅  Discord test succeeded ({detail})")
                else:
                    self.webhook_status.config(text=f"Failed: {detail}", fg=self.ACCENT)
                    self._append_log(f"❌  Discord test failed: {detail}")
            self.root.after(0, _update)

        threading.Thread(target=_do_test, daemon=True).start()

    # ── Alert routing ─────────────────────────────────────────────────────

    def _on_alert(self, title: str, message: str):
        """Route alerts to enabled channels (sound, Discord)."""
        if self.sound_enabled_var.get():
            play_alert_sound()
        if self.discord_webhook_url and _is_valid_discord_webhook(self.discord_webhook_url):
            send_discord_webhook_async(self.discord_webhook_url, title, message)

    # ── Actions ───────────────────────────────────────────────────────────

    def _start(self):
        self.monitor.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.interval_combo.config(state="disabled")

    def _stop(self):
        self.monitor.stop()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.interval_combo.config(state="readonly")

    def _check_now(self):
        threading.Thread(target=self.monitor.run_check, daemon=True).start()

    # ── Threadsafe UI helpers ─────────────────────────────────────────────

    def _append_log(self, text: str):
        def _do():
            self.log_area.config(state="normal")
            self.log_area.insert("end", text + "\n")
            self.log_area.see("end")
            self.log_area.config(state="disabled")
        self.root.after(0, _do)

    def _set_status(self, text: str):
        def _do():
            self.status_label.config(text=text)
        self.root.after(0, _do)

    # ── System tray ───────────────────────────────────────────────────────

    def _create_tray_menu(self):
        """Build the right-click menu for the tray icon."""
        monitor = self.monitor

        def get_status_text(item):
            if monitor.running:
                return f"Status: Running (#{monitor.check_count} checks)"
            return "Status: Stopped"

        def get_last_check_text(item):
            if monitor.last_check_time:
                return f"Last check: {monitor.last_check_time}"
            return "Last check: —"

        def get_result_text(item):
            return f"Result: {monitor.last_status_summary}"

        def get_next_check_text(item):
            if monitor.running:
                mins = self._get_interval_seconds() // 60
                return f"Next check: ~{mins} min"
            return "Next check: —"

        return pystray.Menu(
            pystray.MenuItem(get_status_text, None, enabled=False),
            pystray.MenuItem(get_last_check_text, None, enabled=False),
            pystray.MenuItem(get_result_text, None, enabled=False),
            pystray.MenuItem(get_next_check_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Window", self._restore_from_tray, default=True),
            pystray.MenuItem("Quit", self._quit_from_tray),
        )

    def _minimize_to_tray(self):
        """Hide the window and show a system tray icon."""
        self.root.withdraw()  # Hide the window

        icon_image = _create_tray_icon(64)
        self._tray_icon = pystray.Icon(
            "pokemon_monitor",
            icon_image,
            "Pokemon Center Monitor",
            menu=self._create_tray_menu(),
        )

        # Run tray icon in a background thread
        self._tray_thread = threading.Thread(target=self._tray_icon.run, daemon=True)
        self._tray_thread.start()

        # Show a toast so the user knows where the app went
        send_toast("Minimized to Tray", "Pokemon Center Monitor is still running in the system tray.")

    def _restore_from_tray(self, icon=None, item=None):
        """Remove the tray icon and show the window again."""
        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None

        # Schedule the UI restore on the main thread
        self.root.after(0, self._do_restore)

    def _do_restore(self):
        """Actually restore the window (must run on main thread)."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit_from_tray(self, icon=None, item=None):
        """Fully quit the app from the tray menu."""
        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self.monitor.stop()
        self.root.after(0, self.root.destroy)

    # ── Close handler ─────────────────────────────────────────────────────

    def _on_close(self):
        """Handle the window close (X) button."""
        if self.minimize_to_tray_var.get():
            self._minimize_to_tray()
        else:
            self.monitor.stop()
            if self._tray_icon:
                self._tray_icon.stop()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = MonitorApp()
    app.run()
