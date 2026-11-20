# Changelog

## Unreleased

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

### Security

- Raised minimum dependency versions so source installs don't pull in releases with known vulnerabilities. Running from source now needs Python 3.11 or newer.
- Updated the workflow actions.
