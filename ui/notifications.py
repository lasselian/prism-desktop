"""
Centralised notification helpers.
All show_toast / show_confirm calls go through here so message copy,
icons, and durations are defined in one place.
"""

from PyQt6.QtCore import QTimer
from ui.icons import Icons, get_mdi_font
from core.localization_manager import t


def _icon_html(icon) -> str:
    family = get_mdi_font().family()
    return f'<span style="font-family: \'{family}\'; font-size: 16px;">{icon}</span>'


def _with_icon(icon, message: str) -> str:
    return f"{_icon_html(icon)}&nbsp;&nbsp;{message}"


# ── Entity ───────────────────────────────────────────────────────────────────

def notify_entity_unavailable(dashboard, label: str):
    """Shown when the user taps a button whose entity is unavailable/unknown."""
    dashboard.show_toast(_with_icon(Icons.ALERT_CIRCLE_OUTLINE, t("notifications.entity_unavailable", label=label)))


# ── Glass UI ─────────────────────────────────────────────────────────────────

def notify_glass_ui_warning(dashboard):
    """Shown once when Glass UI is first enabled."""
    msg = _with_icon(Icons.ALERT_CIRCLE_OUTLINE, t("notifications.glass_ui_warning"))
    QTimer.singleShot(600, lambda: dashboard.show_toast(msg, duration_ms=8000))


# ── Button / grid ─────────────────────────────────────────────────────────────

def notify_page_full(dashboard, page_num: int):
    """Shown when a button cannot be moved because the target page is full."""
    dashboard.show_toast(t("notifications.page_full", page_num=page_num))


def notify_move_as_1x1(dashboard, orig_w: int, orig_h: int, page_num: int, on_confirm):
    """Confirm dialog asking if a button should be moved as 1×1 instead."""
    dashboard.show_confirm(
        t("notifications.move_as_1x1", orig_w=orig_w, orig_h=orig_h, page_num=page_num),
        on_confirm=on_confirm,
    )


# ── Updates ───────────────────────────────────────────────────────────────────

def notify_update_available(dashboard, new_version: str, on_confirm):
    """Confirm dialog offering to auto-install a new version."""
    dashboard.show_confirm(
        t("notifications.update_available", version=new_version),
        on_confirm=on_confirm,
    )


def notify_update_restart(dashboard):
    """Toast shown immediately before the app restarts after a successful update."""
    dashboard.show_toast(t("notifications.update_restart"))


def notify_update_success(dashboard, version: str):
    """Toast shown on the first startup after a successful auto-update."""
    dashboard.show_toast(t("notifications.update_success", version=version))


def notify_update_sanity_failed(dashboard, version: str):
    """Toast shown when the post-restart check still finds an available update."""
    dashboard.show_toast(t("notifications.update_sanity_failed", version=version))


_GH_RELEASES_URL = "https://github.com/lasselian/prism-desktop/releases/latest"


def notify_update_error(dashboard, detail: str = ""):
    """Toast shown when the auto-update process fails."""
    msg = t("notifications.update_error")
    if detail:
        msg += f": {detail}"
    dashboard.show_toast(msg, duration_ms=8000)


def notify_download_error(dashboard, detail: str = ""):
    """Toast shown when the update download fails; includes a link to the releases page."""
    msg = t("notifications.update_error")
    if detail:
        msg += f": {detail}"
    msg += f'&nbsp;&nbsp;<a href="{_GH_RELEASES_URL}" style="color: #88aaff;">{t("notifications.download_manually")}</a>'
    dashboard.show_toast(msg, duration_ms=12000)


# ── Connection / settings ─────────────────────────────────────────────────────

def notify_connection_test_result(dashboard, success: bool, message: str):
    """Shown after a connection test completes. Error message comes from HA — not translated."""
    icon = Icons.LAN_CONNECT if success else Icons.LAN_DISCONNECT
    text = t("notifications.connected") if success else message
    dashboard.show_toast(_with_icon(icon, text))


def notify_geoclue2_missing(dashboard, install_cmd: str):
    """Shown on Linux when GeoClue2 is not installed."""
    dashboard.show_toast(t("notifications.geoclue2_missing", install_cmd=install_cmd))


def notify_language_restart(dashboard):
    """Shown when the user saves a language change that requires a restart."""
    dashboard.show_toast(t("notifications.language_restart"))
