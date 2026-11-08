#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面多语言：文案存放于 i18n/<lang>.json，缺词时回退到英文。"""

import json
import locale
import os
import sys

LANGUAGES = [
    ("zh-Hans", "简体中文"),
    ("zh-Hant", "繁體中文"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("es", "Español"),
    ("ru", "Русский"),
]
DEFAULT_LANGUAGE = "en"
FALLBACK_LANGUAGE = "en"

_lang = DEFAULT_LANGUAGE
_strings = {}
_fallback = {}


def resource_dir():
    """i18n 目录（打包后位于 PyInstaller 解压目录）。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "i18n")


def _read(lang):
    path = os.path.join(resource_dir(), "%s.json" % lang)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def normalize(lang):
    """把各种语言标签（zh-CN / zh-TW / en-US …）归一化到支持的 7 种。"""
    tag = str(lang or "").replace("_", "-").strip()
    low = tag.lower()
    if not low:
        return DEFAULT_LANGUAGE
    if low.startswith("zh"):
        if any(k in low for k in ("hant", "tw", "hk", "mo")):
            return "zh-Hant"
        return "zh-Hans"
    base = low.split("-")[0]
    for code, _name in LANGUAGES:
        if base == code:
            return code
    return DEFAULT_LANGUAGE


def detect_system_language():
    """Windows 用 GetUserDefaultUILanguage，其他平台退回 locale。"""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            primary = langid & 0x3FF
            sub = (langid >> 10) & 0x3F
            if primary == 0x04:        # 中文
                # 0x04(zh-TW) / 0x06(zh-HK) / 0x05(zh-MO) 为繁体，其余简体
                return "zh-Hant" if sub in (0x04, 0x06, 0x05) else "zh-Hans"
            if primary == 0x11:
                return "ja"
            if primary == 0x12:
                return "ko"
            if primary == 0x0A:
                return "es"
            if primary == 0x19:
                return "ru"
            if primary == 0x09:
                return "en"
        except Exception:
            pass
    try:
        tag = locale.getdefaultlocale()[0]
    except Exception:
        tag = None
    return normalize(tag)


def set_language(lang):
    """加载指定语言；始终预载英文用于缺词回退。"""
    global _lang, _strings, _fallback
    _lang = normalize(lang)
    _fallback = _read(FALLBACK_LANGUAGE) if _lang != FALLBACK_LANGUAGE else {}
    _strings = _read(_lang)
    return _lang


def current_language():
    return _lang


def t(key, **kwargs):
    """取文案；缺词先查英文，再退回 key 本身。"""
    value = _strings.get(key)
    if value is None:
        value = _fallback.get(key)
    if value is None:
        value = key
    if kwargs:
        try:
            return str(value).format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return str(value)
    return str(value)


def keys(lang):
    return set(_read(lang).keys())
