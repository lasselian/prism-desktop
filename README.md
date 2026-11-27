# Prism Desktop
 
**A Home Assistant desktop app for Windows, Linux & macOS — control your smart home from your PC.**
 
[![Code quality](https://github.com/lasselian/prism-desktop/actions/workflows/quality.yml/badge.svg)](https://github.com/lasselian/prism-desktop/actions/workflows/quality.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](https://github.com/lasselian/prism-desktop/releases)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-WebSocket%20API-41BDF5?logo=home-assistant)](https://www.home-assistant.io/)
[![GitHub Releases](https://img.shields.io/github/v/release/lasselian/prism-desktop)](https://github.com/lasselian/prism-desktop/releases)
 
<img width="623" height="600" alt="Prism Desktop – Home Assistant dashboard on Windows" src="https://github.com/user-attachments/assets/bb88339a-f68c-42ac-9d09-d6b443c204d8" />
---
 
## What is Prism Desktop?
 
Prism Desktop is a **lightweight Home Assistant client for Windows, Linux and macOS**. It lives in your system tray and gives you instant access to your smart home — lights, thermostats, cameras, sensors, and more — without opening a browser.
 
Built on Home Assistant's WebSocket API, it keeps your dashboard in **real-time sync** with your home. Control entities, trigger automations, and receive PC notifications, all from a sleek, customizable dashboard.
 
> **Perfect for:** Home Assistant users who want a native desktop experience on Windows, Linux or macOS instead of a browser tab.
 
---
 
## Table of Contents
 
- [Features](#features)
- [Supported Entities](#supported-entity-types)
- [Installation](#installation)
  - [Windows](#windows-installer)
  - [Linux](#linux-appimage)
  - [macOS](#macos-dmg--zip)
  - [Nix (flakes)](#nix-flakes)
  - [Running from Source](#running-from-source)
- [Configuration](#configuration)
- [Building](#building)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Contributing](#contributing)
- [License](#license)
---
 
## Features
 
- **System Tray Integration** — The app lives in your tray until you need it; never gets in your way.
- **PC Notifications** — Receive Home Assistant alerts directly as desktop notifications.
- **Morphing Controls** — Click and hold a widget to expand it into granular controls like dimmers or thermostats.
- **Drag & Drop Dashboard** — Rearrange tiles freely by dragging them around the grid.
- **Resizable Dashboard** — Adjust the dashboard size to fit your screen and workflow.
- **Real-time Sync** — Powered by Home Assistant's WebSocket API for instant state updates.
- **Customizable Appearance** — Choose border effects (Rainbow, Aurora, and more) and customize button colors.
- **Keyboard Shortcuts** — Bind global shortcuts to toggle the app or trigger individual entities.
- **Assist (beta)** — Talk or type to Home Assistant's Assist pipeline from the dashboard. Off by default; turn it on under *Settings → Assist (Beta)*.
- **Camera Picture-in-Picture** — Pop a camera out into a small floating window that stays on top while the dashboard is closed. See [Camera Picture-in-Picture](#camera-picture-in-picture).
---
 
## Supported Entity Types
 
| Category | Entities |
|----------|----------|
| Lighting & Power | Light, Switch |
| Climate | Climate (thermostat), Fan |
| Media | Media Controller |
| Covers | Curtain / Cover |
| Outdoor | Lawn Mower, Vacuum |
| Monitoring | Sensor, Sun, Weather, Camera |
| Automation | Automation, Scene, Script |
 
### 3D Printer Tile
Dedicated tile for 3D printer monitoring:
- Live camera feed
- Nozzle temperature & target
- Bed temperature & target
- Print state
---
 
## Adjustable Grid
 
![gif-grid](https://github.com/user-attachments/assets/70d9b5f6-bef0-4f86-a6e3-59790e3f5460)
 
## Widget Overlays
 
![gif-overlay](https://github.com/user-attachments/assets/244bb7a7-be80-499e-a343-ec8773bb1307)
 
## Camera Picture-in-Picture
 
Right-click a camera tile (or a 3D printer tile with a camera) and choose *Picture in picture*, or press the picture-in-picture button next to the close button in the camera view. The feed opens in its own small window that stays on top of other apps and keeps running when the dashboard closes.
 
- **Move** it by dragging anywhere; it snaps to screen edges.
- **Resize** it from any corner; the camera's shape is kept.
- **Close** it with the × that appears when you hover over it.
 
Clicking or dragging the window never takes focus away from the app you're typing in. Each camera remembers where you last put its window. The feed is Home Assistant's live MJPEG stream (cameras without one update about twice a second).
 
**KDE Plasma:** KWin puts a focused fullscreen window above every "keep above" window, so the camera window hides behind fullscreen video. To keep it on top, add a window rule: *System Settings → Window Management → Window Rules → Add New*. Use *Add Property* to add *Window role*, set to *Exact Match* and `prism-camera-pip`, then *Add Property* again to add *Layer*, set to *Force* and *Popup*. Click *Apply*.
 
### Open it from a Home Assistant automation
 
An automation can pop up a camera on your desktop, for example when the doorbell rings or motion is detected. Send a command through the Prism device's notify service (`notify.mobile_app_<device>`):
 
```yaml
action: notify.mobile_app_<device>
data:
  message: command_camera_pip
  title: Front door        # optional window name
  data:
    entity_id: camera.front_door
    duration: 30           # optional: close after this many seconds
```
 
Without `duration` (or with `0`) the window stays open until you close it; the longest duration is 24 hours. Triggering again restarts a running countdown (or cancels it, if the new command has no `duration`), but never adds one to a window that is already staying open. The camera doesn't need a tile on the dashboard. Without a `title`, the window is named after the camera's tile or its Home Assistant name.
 
To close it early:
 
```yaml
action: notify.mobile_app_<device>
data:
  message: command_camera_pip_close
  data:
    entity_id: camera.front_door   # optional: leave out to close all camera windows
```
 
Prism must be running and connected to Home Assistant to receive the command.
 
---
 
## Keyboard Shortcuts
 
- **App Toggle**: Set a global shortcut in Settings → *App toggle* to show/hide the window from anywhere.
- **Entity Shortcuts**: Assign custom shortcuts to any button tile via the Add/Edit menu.
---
 
## Installation
 
### Windows Installer
 
Download the latest `PrismDesktopSetup.exe` from the [Releases page](https://github.com/lasselian/prism-desktop/releases).
 
The installer will set up the app and optionally configure it to start with Windows.
 
Prefer not to install anything? Download the standalone `.exe` instead — it runs portably and stores its config in the same directory.
 
---
 
### Linux (AppImage)
 
Download the latest `.AppImage` from the [Releases page](https://github.com/lasselian/prism-desktop/releases).
 
```bash
chmod +x PrismDesktop-x86_64.AppImage
./PrismDesktop-x86_64.AppImage
```
 
> **Ubuntu 22.04+** requires `libfuse2`:
> ```bash
> sudo apt install libfuse2
> ```
 
**GNOME:**
- Install **AppIndicator and KStatusNotifierItem Support** via Extension Manager for system tray support.
- Wayland users: bind a custom shortcut in system settings to toggle the app:
  ```bash
  /path/to/PrismDesktop-x86_64.AppImage --toggle
  ```
  > Per-entity shortcuts are not supported on GNOME Wayland.
**KDE:**
- System tray works out of the box.
- App-toggle shortcut is supported via `org.freedesktop.portal.GlobalShortcuts`.
- Per-entity shortcuts are not supported on KDE.
### macOS (.dmg / .zip)

Download the latest `.dmg` (or `.zip`) for your Mac from the [Releases page](https://github.com/lasselian/prism-desktop/releases):

- `PrismDesktop-macOS-arm64` — Apple Silicon (M1/M2/M3/M4)
- `PrismDesktop-macOS-x86_64` — Intel Macs

1. **Install**: Open the `.dmg` and drag `PrismDesktop.app` into your `/Applications` folder.
2. **First-Run Gatekeeper Approval (Unsigned App)**:
   Because this open-source build is distributed without a paid Apple Developer certificate ($99/year), macOS Gatekeeper may show a security notice on first launch:
   - **Method 1 (Recommended Terminal command)**:
     ```bash
     xattr -cr /Applications/PrismDesktop.app
     ```
   - **Method 2 (Finder)**:
     Right-click (or Control-click) `PrismDesktop.app` in `/Applications`, select **Open**, and click **Open** in the dialog.
3. **Global Shortcuts**:
   To allow Prism Desktop to listen for global keyboard shortcuts from anywhere on your Mac:
   - Open **System Settings → Privacy & Security → Accessibility**.
   - Click **+** and add **PrismDesktop** (or toggle the switch to ON).
4. **Menu Bar App**:
   Prism Desktop lives in your **top-right macOS Menu Bar** (near the clock and Wi-Fi icons). Click the Prism cube icon to toggle your dashboard.
5. **Token storage**:
   Because these builds are ad-hoc signed (no paid Apple Developer certificate), the macOS Keychain is not used — the app's code signature changes with every update, which would otherwise trigger repeated Keychain password prompts. Your Home Assistant token is instead stored in an encrypted file keyed to this Mac's hardware UUID. This protects the token at rest, but is weaker than the Keychain: other software running under your user account could potentially recover it.

---

### Nix (flakes)
 
Run directly without installing:
 
```bash
nix run github:lasselian/prism-desktop
```
 
Add to your profile:
 
```bash
nix profile add github:lasselian/prism-desktop#default
```
 
---
 
### Running from Source
 
Requires Python 3.11 or newer.
 
```bash
git clone https://github.com/lasselian/prism-desktop.git
cd prism-desktop
pip install -r requirements.txt
python main.py
```
 
---
 
## Configuration
 
On first launch, you'll be prompted for:
1. Your **Home Assistant URL** (e.g. `http://homeassistant.local:8123`)
2. A **Long-Lived Access Token** — generate one in your Home Assistant profile under *Security → Long-Lived Access Tokens*

---
 
## Building
 
### Windows
 
```bash
python build_exe.py
```
 
This runs PyInstaller and outputs a single-file executable to `dist/`. To build the installer, open `setup.iss` with [Inno Setup](https://jrsoftware.org/isdl.php) and compile.
 
### Linux (AppImage)
 
1. Download `appimagetool-x86_64.AppImage` from the [appimagetool releases](https://github.com/AppImage/appimagetool/releases) and place it in the project root.
2. Run the build script:
```bash
python3 build_linux.py
```
 
This builds the binary, creates an AppDir, and packages it into an AppImage.

### macOS (.app, .dmg, .zip)

1. Install dependencies:
```bash
pip install -r requirements.txt pyinstaller
```
2. Run the macOS build script:
```bash
python3 build_macos.py
```

This compiles `PrismDesktop.app` (supporting both Intel and Apple Silicon M-series Macs), applies an ad-hoc cryptographic signature, and generates `.dmg` and `.zip` releases in `dist/`.

> **Gatekeeper / First Launch Note**: On modern macOS, remove the quarantine attribute after copying to `/Applications`:
> ```bash
> xattr -cr /Applications/PrismDesktop.app
> ```
> or right-click (Control-click) `PrismDesktop.app` and choose **Open**.
>
> **Global Shortcuts**: Grant Accessibility permissions in **System Settings → Privacy & Security → Accessibility**.
 
---
 
## Troubleshooting
 
**`WS Error: 400 – Duplicate 'Server' header found`**
 
Your reverse proxy is adding a duplicate `Server` header. Fix for common proxies:
 
- **Caddy**: Add `header_up -Server` to your `reverse_proxy` block.
- **Other proxies**: Check for a similar "remove upstream header" option.
---
 
## How it works

Prism keeps one WebSocket connection to Home Assistant for live state updates
and uses the REST API for service calls, camera images and media. Qt and
asyncio share one event loop (via qasync), so the UI stays responsive while it
waits on the network. It can also register itself as a mobile app device, which
gives Home Assistant a notify service and sensors for this computer.

- [Architecture](docs/ARCHITECTURE.md): the code layers, the event loop, how state and clicks flow
- [Platform integrations](docs/platforms.md): what differs on Windows, Linux and macOS
- [Assist](docs/assist.md): how voice and text requests reach your Assist pipeline

---

## Contributing

Pull requests, translations and bug reports are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately as
described in [SECURITY.md](SECURITY.md).

---

## License
 
MIT License — see [LICENSE](LICENSE) for details.
 
---
 
*Keywords: Home Assistant desktop app, Home Assistant Windows client, Home Assistant Linux app, smart home dashboard PC, Home Assistant system tray, HA desktop client*
