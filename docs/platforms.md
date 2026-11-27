# Platform integrations

Prism is one Python code base on Windows, Linux and macOS. Where the
operating systems differ, the code picks a backend at runtime. This page lists
those places, so a change for one platform doesn't break another.

| Feature | Windows | Linux | macOS |
|---|---|---|---|
| Notifications | Toasts via `win11toast`, buttons as `http:prism-action-N` protocol links; app id registered under `HKCU\Software\Classes\AppUserModelId` (installer and runtime) | `org.freedesktop.Notifications` over D-Bus with action buttons (`services/dbus_notifier.py`); tray balloon fallback | Tray notifications, else `osascript` |
| Global shortcut | `pynput` | X11: `pynput`. Wayland: the XDG `GlobalShortcuts` portal on KDE Plasma (`services/wayland_global_shortcut.py`); other Wayland desktops can bind `--toggle` instead | `pynput`, with a fix for keyboard-layout queries off the main thread (see below); needs Accessibility permission |
| Toggle from outside (`--toggle`, `--assist`) | Named pipe `\\.\pipe\prism-desktop-<hash>` | Unix socket in the temp folder | Unix socket in `$TMPDIR` |
| Token storage | Windows Credential Locker | Secret Service (GNOME Keyring, KWallet) | Encrypted file only: the Keychain needs a paid Developer ID certificate; this protects the token at rest, not from other processes of the same user |
| Encrypted-file key | DPAPI (the Windows account); `MachineGuid` if DPAPI fails | `/etc/machine-id` | `IOPlatformUUID` |
| Location sensor | WinRT `Windows.Devices.Geolocation` | GeoClue2 over the system D-Bus | — |
| Idle time | `GetLastInputInfo` | Mutter `IdleMonitor` (GNOME, also on Wayland), else `xprintidle` | `ioreg` `HIDIdleTime` |
| Screen locked | Input desktop check | `loginctl` `LockedHint` | `ioreg` `CGSSessionScreenIsLocked` |
| Mic / camera in use | Capability consent store in the registry | `pactl` capture streams / `/dev/video*` held open | — |
| Power control | `shutdown /s /r /h`, `SetSuspendState` (sleep), `LockWorkStation` | `systemctl` / `loginctl` (`poweroff`, `reboot`, `suspend`, `hibernate`, `lock-session`) | `osascript` System Events (shut down, restart, lock), `pmset sleepnow` (sleep); hibernate unsupported |
| Glass UI (background blur) | Prism blurs a screen capture of the desktop behind the window itself (`capture_glass_background`), with its own window excluded from the grab (`SetWindowDisplayAffinity`) | Compositor blur only. GNOME 51+: `ext-background-effect-v1` on native Wayland, with the dashboard on a see-through maximized stage window (`ui/utils/wayland_blur.py`, `ui/utils/wayland_stage.py`); toggling needs a restart because Qt picks its platform at startup. KDE Plasma (X11 and Wayland): `_KDE_NET_WM_BLUR_BEHIND_REGION` on the xcb/XWayland window (`ui/utils/kwin_blur.py`), offered when KWin's blur effect is on. Hidden on other desktops | Screen capture blurred by Prism, as on Windows, without the capture exclusion |
| Camera picture-in-picture | Tool window | xcb: `WM_TAKE_FOCUS` removed and `SKIP_TASKBAR` set via libX11 (`ui/utils/floating_window.py`) | Non-activating panel on all Spaces |
| Updates | Downloads and runs the Inno Setup installer silently; it relaunches the app | Replaces the AppImage in place (written beside it, then renamed) | Swaps the `.app` bundle, rolling back if the second move fails |
| Packaging | `build_exe.py` (PyInstaller) + `setup.iss` | `build_linux.py` (AppImage), `flake.nix` | `build_macos.py` (`.app`, `.dmg`, `.zip`; universal2 with a host-arch fallback) |

An update is only installed if the release has a `SHA256SUMS` asset and the download is listed there with a matching hash.

## Platform workarounds worth knowing

- **macOS keyboard layout (`services/input_manager.py`):** macOS 14+ requires
  the Carbon/TIS keyboard-layout calls on the main thread, and `pynput` makes
  them from its listener thread, which crashes the app. Prism reads the layout
  once on the main thread and hands `pynput` the cached value.
- **Python 3.15 (`core/py315_compat.py`):** patches `qasync`'s reader/writer
  hooks to accept the new `context` argument, and restores
  `typing.no_type_check_decorator` for `dbus-next`. Each patch checks first and
  switches itself off once the dependency is fixed.
- **Frozen builds and TLS (`core/utils.configure_ssl`):** Linux uses the
  system CA bundle; Windows and macOS use `certifi`, because the Windows store
  can be incomplete on a given machine.
- **Raspberry Pi (`main.py`, `_create_task_safe`):** distro-packaged `qasync`
  asserts when a task is created from inside a Qt timer callback, so tasks are
  started on the next loop turn.

## Testing on a platform

Behaviour that depends on the OS is only partly covered by automated tests;
CI imports every module on Windows, macOS and Linux (skipping the
D-Bus modules off Linux), but nothing beyond that.
When you change a row in the table above, please say in the pull request which
systems you tried it on.
