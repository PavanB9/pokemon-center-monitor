# Pokemon Center Queue & Product Monitor

A lightweight Windows desktop app that monitors **pokemoncenter.com** and alerts you when:

- A **virtual queue** (Queue-it waiting room) is detected
- **New products** appear on the New Arrivals page

## How to Run

**Option 1 -- Double-click:**
Just double-click `run.bat` -- the app launches with **no terminal window**.

**Option 2 -- Command line:**
```
venv\Scripts\pythonw.exe monitor.py
```

## Features

### Configurable Check Interval
Choose how often to check (1--10 minutes) from the Interval dropdown. The setting is saved and remembered between sessions. The dropdown is locked while monitoring is active -- stop monitoring to change it.

### Alert Channels
The app notifies you through multiple channels:

- **Windows Notification** -- Pop-up banner in the bottom-right of your screen (always on)
- **Sound** -- Plays a custom alert sound (`alert.wav`). Toggle on/off in Settings. You can replace the WAV file with any sound you like.
- **Discord Webhook** -- Sends a rich embed message to a Discord channel. Paste your webhook URL in Settings, click Save, and use Test to verify. The Test button reports real success/failure from the Discord API.

### System Tray Mode
Toggle **"Minimize to tray on close"** in Settings. When enabled:
- Clicking **X** hides the window to the system tray instead of quitting
- A Pokeball icon appears in the taskbar notification area
- **Right-click** the tray icon for a status menu showing:
  - Monitor status (running/stopped and check count)
  - Last check time
  - Latest result summary
  - Next check estimate
- **Double-click** or choose **"Show Window"** to restore the app
- Choose **"Quit"** to fully exit

### Settings Storage
Settings (interval, toggles, Discord webhook URL) are stored in:
```
settings.json
```
right next to `monitor.py` in the project folder. This keeps all app files together locally.

Because the settings file can contain user-local secrets like Discord webhook URLs, it is ignored by Git via `.gitignore` and should not be committed. See `settings.example.json` for the default structure.

## What It Monitors

| Target | URL | What It Detects |
|--------|-----|-----------------|
| Homepage | pokemoncenter.com | Queue activation, redirects |
| New Arrivals | pokemoncenter.com/category/new-arrivals | Queue + new product listings |

## How It Works

1. Fetches each page at the configured interval with a standard browser-like HTTP GET request
2. Scans the response for Queue-it signatures (queue-it.net, waiting room text, etc.)
3. Tracks product listings on the New Arrivals page -- alerts on new additions
4. Fires alerts through all enabled channels (notification, sound, Discord)
5. Overlapping checks are prevented -- clicking "Check Now" while a check is already running will skip cleanly

## Safety & Privacy

- **Read-only** -- only performs GET requests (same as visiting in your browser)
- **No login / no cookies** -- never touches your account
- **No data sent anywhere** -- everything stays on your PC (except Discord webhook alerts if you enable them, which only sends the alert text to Discord's servers)
- **Webhook URL validation** -- Only URLs matching `https://discord.com/api/webhooks/...` or `https://discordapp.com/api/webhooks/...` are accepted. This is a basic format check to prevent typos; the URL is user-supplied and stored locally.
- **Git safety** -- `settings.json` stays local and is ignored by Git so user secrets are not meant to be published
- **Local venv** -- all dependencies are inside `venv/`, nothing installed globally
- **Open source** -- single Python file, easy to audit

## Project Structure

```
pokemon-center-monitor/
  monitor.py              main app (GUI + monitoring + tray)
  run.bat                 double-click launcher (no terminal)
  alert.wav               notification sound file
  settings.json           local runtime settings (gitignored, auto-created/updated)
  settings.example.json   example settings (safe to commit)
  .gitignore              keeps venv, pycache, and secrets out of Git
  README.md               this file
  venv/                   local Python virtual environment
```
