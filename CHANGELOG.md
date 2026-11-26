# Changelog

## Unreleased

### PC power control

- Home Assistant can now shut down, restart, sleep, hibernate or lock this PC (hibernate is not available on macOS). Every command is off until you turn it on under Settings > Power Control.
- A button there creates an on/off switch for the PC in Home Assistant. "On" uses Wake-on-LAN, "off" shuts the PC down, and creating the switch turns on remote shutdown.

### Notifications

- The first-run walkthrough now appears in the notification drawer inside the dashboard, like every other message, instead of in a separate floating window. If you close the dashboard part-way through it carries on from the same page, and opening Settings ends it.

### Security

- The auto-updater now requires the release's `SHA256SUMS` file. A release without it is refused, instead of being installed unchecked. Thanks to [@aubierr](https://github.com/aubierr), who made this change first in their fork, `aubierr/prism-desktop`.

## 1.8.2

### Notifications

- Messages now appear inside the dashboard instead of in a separate bar beside it. The dashboard smoothly grows a strip on the side away from the tray, the message slides out into it, and the dashboard shrinks back when it's gone. Nothing else moves: tiles and buttons stay where they are on screen.
- If the screen has no room for the dashboard to grow, the message floats over the bottom of the dashboard instead.
- Each message has an icon and colour for its type (info, success, warning, error). A thin line along the bottom shows how long it stays. Hovering pauses it, and moving away resumes it.
- Messages queue instead of replacing each other, and a message no longer disappears because a question was showing. Back-to-back messages swap in place without the dashboard shrinking in between. Update progress, then the result, update one card in place.
- Questions have clear buttons ("Remove" / "Cancel", "Install" / "Not now") instead of Yes/No, and wait 20 seconds instead of 5 before counting as "no".
- Messages that arrive while the dashboard is closed wait until you next open it. If a shortcut triggers a button that is unavailable while the dashboard is closed, you get a system notification instead.
- A message no longer closes an open control panel, such as a dimmer.
- "Updated to …" links to what's new. The GeoClue message can copy the install command.

### Fixes

- On macOS, the camera picture-in-picture window no longer changes shape when you double-click or drag near its edge. That cropped or stretched the picture.
- On macOS, the close button, camera name and resize handle of the picture-in-picture window now show while another app is in front.
- Removing a camera tile, or pointing it at another camera, now closes its picture-in-picture window. It kept playing before.

### Security

- `prism.log` no longer contains your GPS coordinates when Prism gets a location fix, only how accurate it is.

## 1.8.1

### Fixes

- Updating from inside Prism on Windows works again. The update closed Prism without installing anything or reopening it, because the installer stopped itself along with the app.
- If a silent Windows update ever fails, the installer now leaves `update-installer.log` next to your config.

## 1.8

### Camera picture-in-picture

- New: pop a camera out into a floating window that stays on top and keeps playing while the dashboard is closed. Right-click a camera or 3D printer tile and choose "Picture in picture", or use the new button in the camera view.
- The window never takes focus, so you can keep typing in another app while you move or resize it. It snaps to screen edges, keeps the camera's shape when resized, and remembers its position for each camera.
- On KDE Plasma it hides behind fullscreen windows unless you add a window rule; the README explains how.

### Assist (beta)

- Typed messages now go through your preferred Assist pipeline, the same as voice, so they reach the same conversation agent (for example an LLM agent) and share one conversation.
- Replies from agents that stream now appear as they are written.
- When the agent asks a follow-up question by voice, the mic re-opens after the reply has been spoken.
- "Couldn't understand that" replies are shown as soft warnings, and "didn't catch that" is a status message instead of an error bubble. A request that stalls now times out with a clear message.
- A reply from a closed or stopped request can no longer appear in the next conversation.
- The chat follows the light theme and your accent colour.
- New: a typing indicator while Home Assistant thinks, a mic level halo while listening, and one button that switches between talk, send and stop.
- New: a "new conversation" button, an empty-state hint, and Up/Down history for typed messages.
- Esc now stops listening, a running request or speech, one at a time, before it closes the popup.
- New setting: "Speak replies" turns off spoken replies to voice requests.

### Fixes

- `--toggle` and `--assist` now reach the running app on Windows, where they previously found no instance.
- On large media tiles with album art, the label's frosted background now shows the part of the art that is behind it.
- An empty or relative `XDG_CONFIG_HOME` (or an empty `APPDATA` on Windows) no longer puts the config in the folder Prism was started from.
- Media tiles keep the same generated background for a track after a restart.
- The Linux AppImage now starts on older distributions such as Ubuntu 22.04 and 24.04 and Debian 12. 1.7 needed glibc 2.44 and only ran on very new systems.

### Security

- Only your own user account can send commands such as `--toggle` to a running Prism.
- The flag that triggers the post-update check now lives next to your config instead of the shared temp folder.
- Raised minimum dependency versions so source installs don't pull in releases with known vulnerabilities. Running from source now needs Python 3.11 or newer.
- Updated the workflow actions.
- The icon list download requires TLS 1.2 or newer explicitly, instead of relying on the Python version's defaults.

## Earlier releases

Release notes for 1.7 and earlier are on the [releases page](https://github.com/lasselian/prism-desktop/releases):

- [1.7](https://github.com/lasselian/prism-desktop/releases/tag/1.7) (2026-11-16)
- [1.6](https://github.com/lasselian/prism-desktop/releases/tag/1.6) (2026-09-23)
- [1.5.5](https://github.com/lasselian/prism-desktop/releases/tag/1.5.5) (2026-07-19)
- [1.5.4](https://github.com/lasselian/prism-desktop/releases/tag/1.5.4) (2026-06-09)
- [1.5.3](https://github.com/lasselian/prism-desktop/releases/tag/1.5.3) (2026-05-22)
- [1.5.2](https://github.com/lasselian/prism-desktop/releases/tag/1.5.2) (2026-05-19)
- [1.5.1](https://github.com/lasselian/prism-desktop/releases/tag/1.5.1) (2026-05-12)
- [1.5](https://github.com/lasselian/prism-desktop/releases/tag/1.5) (2026-04-28)
- [1.4.4](https://github.com/lasselian/prism-desktop/releases/tag/1.4.4) (2026-04-07)
- [1.4.3](https://github.com/lasselian/prism-desktop/releases/tag/1.4.3) (2026-04-03)
- [1.4.2](https://github.com/lasselian/prism-desktop/releases/tag/1.4.2) (2026-03-19)
- [1.4.1](https://github.com/lasselian/prism-desktop/releases/tag/1.4.1) (2026-03-13)
- [1.4](https://github.com/lasselian/prism-desktop/releases/tag/1.4) (2026-03-12)
- [1.3.2](https://github.com/lasselian/prism-desktop/releases/tag/1.3.2) (2026-03-03)
- [1.3.1](https://github.com/lasselian/prism-desktop/releases/tag/1.3.1) (2026-03-03)
- [1.3](https://github.com/lasselian/prism-desktop/releases/tag/1.3) (2026-02-28)
- [1.2](https://github.com/lasselian/prism-desktop/releases/tag/1.2) (2026-02-10)
- [1.1](https://github.com/lasselian/prism-desktop/releases/tag/1.1) (2026-02-03)
- [1.0](https://github.com/lasselian/prism-desktop/releases/tag/release) (2026-02-01)
