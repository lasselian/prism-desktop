import json
import logging
from core.utils import get_resource_path

logger = logging.getLogger(__name__)

_SUPPORTED_LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "it": "Italiano",
    "de": "Deutsch",
    "nl": "Nederlands",
    "no": "Norsk",
    "sv": "Svenska",
    "fi": "Suomi",
    "da": "Dansk",
    "pt": "Português",
    "pl": "Polski",
    "cs": "Čeština",
    "hu": "Magyar",
    "ro": "Română",
    "sr": "Srpski",
    "ru": "Русский",
    "uk": "Українська",
    "tr": "Türkçe",
    "zh": "中文（简体）",
    "zh-TW": "中文（繁體）",
    "ko": "한국어",
    "ja": "日本語",
}


class _LocalizationManager:
    def __init__(self):
        self._language = "en"
        self._strings: dict = {}
        self._fallback: dict = {}

    def init(self, language: str = "en") -> None:
        self._language = language if language in _SUPPORTED_LANGUAGES else "en"
        self._fallback = self._load_file("en")
        self._strings = self._load_file(self._language) if self._language != "en" else self._fallback

    def _load_file(self, lang: str) -> dict:
        path = get_resource_path(f"translations/{lang}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"[i18n] Translation file not found: {path}")
            return {}
        except json.JSONDecodeError as exc:
            logger.error(f"[i18n] Malformed translation file {path}: {exc}")
            return {}

    def translate(self, key: str, **kwargs) -> str:
        raw = self._strings.get(key) or self._fallback.get(key) or key
        if kwargs:
            try:
                return raw.format(**kwargs)
            except (KeyError, ValueError):
                return raw
        return raw

    @property
    def current_language(self) -> str:
        return self._language

    @staticmethod
    def supported_languages() -> dict:
        return dict(_SUPPORTED_LANGUAGES)


_manager = _LocalizationManager()


def init_localization(language: str = "en") -> None:
    _manager.init(language)


def t(key: str, **kwargs) -> str:
    return _manager.translate(key, **kwargs)


def current_language() -> str:
    return _manager.current_language


def supported_languages() -> dict:
    return _manager.supported_languages()
