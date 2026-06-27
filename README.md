# AdsBot

Google search automation bot with a dark-themed management panel. Searches Google, clicks target sites or ads, browses them with human-like behavior, and rotates IP via Android airplane mode (ADB).

> For authorized testing on your own sites/campaigns only.

## Features

- **Search & click** – type a query, click target domains (or first N results, or only ads)
- **Anti-detection** – `undetected-chromedriver`, stealth JS fingerprint patches (webdriver, plugins, languages, WebGL, permissions), disabled automation switches
- **Human-like behavior** – Bezier-curve mouse movement, variable typing speed with typos, irregular wait patterns, occasional wrong-click-and-return
- **IP rotation** – toggle Android airplane mode over ADB to get a fresh IP between runs
- **GUI panel** – manage queries/targets, settings, live IP + device status, log console
- **CAPTCHA aware** – pauses for manual solve (visible mode) when Google blocks

## Requirements

- Python 3.10+
- Google Chrome installed
- Android device with USB debugging (optional, for IP rotation)

```bash
pip install selenium undetected-chromedriver
```

## Usage

GUI:

```bash
python panel.py
```

CLI:

```bash
python google_bot.py "search term"
```

## Build (Windows exe)

```bash
pyinstaller GoogleBot.spec
```

## Files

| File | Purpose |
|------|---------|
| `google_bot.py` | Core bot: Selenium driver, stealth, human behavior, ADB |
| `panel.py` | tkinter management GUI |
| `aramalar.json` | Saved queries + target sites (gitignored) |
| `GoogleBot.spec` | PyInstaller build config |

## Disclaimer

Educational / authorized-use only. Automating clicks on ads or search results you do not own may violate Google's Terms of Service and local laws. Use at your own risk.
