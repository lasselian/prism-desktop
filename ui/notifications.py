"""
Centralised notification helpers.
Every in-app message goes through here so copy, icon, severity, duration and
queueing key are defined in one place. See ui/widgets/notification_pill.py
for what a Notice can carry.
"""

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication

from core.localization_manager import t
from ui.widgets.notification_pill import Guide, Notice

_RELEASES_URL = "https://github.com/lasselian/prism-desktop/releases"
# Update progress, restart and failure share a key so each replaces the last.
_UPDATE_KEY = "update"


def _open_url(url: str):
    QDesktopServices.openUrl(QUrl(url))


# ── Entity ───────────────────────────────────────────────────────────────────


def notify_entity_unavailable(dashboard, label: str):
    """Shown when the user taps a button (or its shortcut) whose entity is unavailable/unknown."""
    dashboard.show_toast(
        t("notifications.entity_unavailable", label=label),
        kind="warning",
        key=f"unavailable:{label}",
        urgent=True,
        system_when_hidden=True,
    )


# ── Glass UI ─────────────────────────────────────────────────────────────────


def notify_glass_ui_warning(dashboard):
    """Shown once when Glass UI is first enabled."""
    msg = t("notifications.glass_ui_warning")
    QTimer.singleShot(600, lambda: dashboard.show_toast(msg, duration_ms=8000, kind="warning"))


# ── Button / grid ─────────────────────────────────────────────────────────────


def notify_page_full(dashboard, page_num: int):
    """Shown when a button cannot be moved because the target page is full."""
    dashboard.show_toast(t("notifications.page_full", page_num=page_num), kind="warning", key="page_full", urgent=True)


def notify_move_as_1x1(dashboard, orig_w: int, orig_h: int, page_num: int, on_confirm):
    """Ask whether a button that doesn't fit should be moved as 1×1 instead."""
    dashboard.show_confirm(
        t("notifications.move_as_1x1", orig_w=orig_w, orig_h=orig_h, page_num=page_num),
        on_confirm=on_confirm,
        confirm_text=t("notification_banner.move"),
        cancel_text=t("notification_banner.cancel"),
        icon="arrow-collapse",
        key="move_as_1x1",
        urgent=True,
    )


# ── Shortcuts ─────────────────────────────────────────────────────────────────


def notify_delete_shortcut(dashboard, name: str, on_confirm):
    """Ask before clearing a button's custom shortcut."""
    msg = t("notifications.delete_shortcut", name=name) if name else t("notifications.delete_shortcut_unnamed")
    dashboard.show_confirm(
        msg,
        on_confirm=on_confirm,
        confirm_text=t("notification_banner.remove"),
        cancel_text=t("notification_banner.cancel"),
        destructive=True,
        kind="warning",
        icon="trash-can-outline",
        key="delete_shortcut",
        urgent=True,
    )


# ── Updates ───────────────────────────────────────────────────────────────────


def notify_update_available(dashboard, new_version: str, on_confirm):
    """Offer to install a new version."""
    dashboard.show_confirm(
        t("notifications.update_available", version=new_version),
        on_confirm=on_confirm,
        confirm_text=t("notification_banner.install"),
        cancel_text=t("notification_banner.not_now"),
        icon="update",
        key=_UPDATE_KEY,
    )


def notify_update_restart(dashboard):
    """Shown immediately before the app restarts after a successful update."""
    dashboard.show_toast(t("notifications.update_restart"), kind="success", icon="restart", key=_UPDATE_KEY)


def notify_update_success(dashboard, version: str):
    """Shown on the first startup after a successful auto-update."""
    dashboard.show_toast(
        t("notifications.update_success", version=version),
        duration_ms=8000,
        kind="success",
        action_text=t("notification_banner.whats_new"),
        on_action=lambda: _open_url(f"{_RELEASES_URL}/tag/{version}"),
    )


def notify_update_sanity_failed(dashboard, version: str):
    """Shown when the post-restart check still finds an available update."""
    dashboard.show_toast(t("notifications.update_sanity_failed", version=version), duration_ms=8000, kind="warning")


def notify_update_error(dashboard, detail: str = ""):
    """Shown when the auto-update process fails."""
    msg = t("notifications.update_error")
    if detail:
        msg += f": {detail}"
    dashboard.show_toast(msg, duration_ms=8000, kind="error", key=_UPDATE_KEY, system_when_hidden=True)


def notify_download_error(dashboard, detail: str = ""):
    """Shown when the update download fails; offers the releases page instead."""
    msg = t("notifications.update_error")
    if detail:
        msg += f": {detail}"
    dashboard.show_toast(
        msg,
        duration_ms=12000,
        kind="error",
        key=_UPDATE_KEY,
        system_when_hidden=True,
        action_text=t("notifications.download_manually"),
        on_action=lambda: _open_url(f"{_RELEASES_URL}/latest"),
    )


# ── Connection / settings ─────────────────────────────────────────────────────


def notify_connection_test_result(dashboard, success: bool, message: str):
    """Shown after a connection test completes. Error message comes from HA — not translated."""
    dashboard.show_toast(
        t("notifications.connected") if success else message,
        duration_ms=None if success else 8000,
        kind="success" if success else "error",
        icon="lan-connect" if success else "lan-disconnect",
        key="connection_test",
        urgent=True,
    )


def notify_geoclue2_missing(dashboard, install_cmd: str):
    """Shown on Linux when GeoClue2 is not installed; offers to copy the install command."""

    def _copy():
        QGuiApplication.clipboard().setText(install_cmd)
        dashboard.show_toast(t("notification_banner.copied"), kind="success", icon="content-copy", urgent=True)

    dashboard.show_toast(
        t("notifications.geoclue2_missing", install_cmd=install_cmd),
        duration_ms=12000,
        kind="warning",
        icon="map-marker-off-outline",
        action_text=t("notification_banner.copy_command") if install_cmd else None,
        on_action=_copy if install_cmd else None,
    )


def notify_language_restart(dashboard):
    """Shown when the user saves a language change that requires a restart."""
    dashboard.show_toast(t("notifications.language_restart"), duration_ms=8000, icon="translate")


# ── First run ────────────────────────────────────────────────────────────────

_WELCOME_KEY = "welcome"
_WELCOME_STEPS = 5


def notify_welcome(dashboard, on_done):
    """The first-run walkthrough: one card in the drawer that turns its pages.

    `on_done` runs once however it ends: Done, Skip, or dismiss_welcome().
    """
    guide = Guide(
        pages=[(t(f"welcome.step_{i}.title"), t(f"welcome.step_{i}.body")) for i in range(1, _WELCOME_STEPS + 1)],
        skip_text=t("welcome.btn_skip"),
        next_text=t("welcome.btn_next"),
        done_text=t("welcome.btn_done"),
    )
    dashboard.notify(
        Notice(
            t("welcome.step_1.title"),
            icon="hand-wave-outline",
            duration_ms=0,
            key=_WELCOME_KEY,
            guide=guide,
            on_confirm=on_done,
            on_reject=on_done,
        )
    )


def dismiss_welcome(dashboard):
    """End the walkthrough early (the user went to Settings), shown or still waiting."""
    dashboard.dismiss_notice(_WELCOME_KEY)


# ── PC Power Control ──────────────────────────────────────────────────────────


def notify_power_switch_result(dashboard, result):
    """Shown after creating or updating the Home Assistant PC power switch."""
    if not result.ok:
        dashboard.show_toast(
            t("settings.power.switch_failed", error=result.error),
            duration_ms=8000,
            kind="error",
            icon="power",
            key="power_switch",
            urgent=True,
        )
        return

    if not result.wol_available:
        dashboard.show_toast(
            t("settings.power.switch_wol_hint", name=result.switch_name),
            duration_ms=10000,
            kind="warning",
            icon="power",
            key="power_switch",
            urgent=True,
        )
        return

    key = "settings.power.switch_updated" if result.updated else "settings.power.switch_created"
    dashboard.show_toast(
        t(key, name=result.switch_name),
        duration_ms=6000,
        kind="success",
        icon="power",
        key="power_switch",
        urgent=True,
    )
