"""
Server-side GIF search (Giphy primary; optional Tenor fallback) and CDN allowlisting.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

_GIF_HOST_PATTERNS = (
    re.compile(r"^media\d*\.tenor\.com$", re.I),
    re.compile(r"^c\.tenor\.com$", re.I),
    # Giphy serves from media0.giphy.com, media1.giphy.com, … not only media.giphy.com
    re.compile(r"^media\d*\.giphy\.com$", re.I),
    re.compile(r"^i\.giphy\.com$", re.I),
    re.compile(r"^giphy\.com$", re.I),
)


def is_allowed_gif_cdn_url(url: str) -> bool:
    """Only allow HTTPS URLs on known Tenor/Giphy media hosts."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not u.startswith("https://"):
        return False
    if len(u) > 2048:
        return False
    try:
        host = (urlparse(u).hostname or "").lower()
    except Exception:
        return False
    return any(p.match(host) for p in _GIF_HOST_PATTERNS)


def _http_json(url: str, timeout: int = 12) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": "SparkChat/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        logger.warning("GIF provider request failed: %s", e)
        return None


def search_tenor(query: str, limit: int = 24) -> list[dict]:
    key = getattr(settings, "TENOR_API_KEY", "") or ""
    if not key:
        return []
    client = getattr(settings, "TENOR_CLIENT_KEY", "spark_chat") or "spark_chat"
    base = {
        "key": key,
        "client_key": client,
        "limit": str(min(limit, 50)),
        "media_filter": "gif",
        "contentfilter": "low",
    }
    q = (query or "").strip()
    if q:
        base["q"] = q
        url = f"https://tenor.googleapis.com/v2/search?{urllib.parse.urlencode(base)}"
    else:
        url = f"https://tenor.googleapis.com/v2/featured?{urllib.parse.urlencode(base)}"
    data = _http_json(url)
    if not data or "results" not in data:
        return []
    out: list[dict] = []
    for item in data["results"][:limit]:
        mf = item.get("media_formats") or {}
        gif = mf.get("gif") or mf.get("mediumgif") or mf.get("tinygif") or {}
        prev = mf.get("tinygif") or mf.get("nanogif") or gif
        send_url = (gif.get("url") or "").strip()
        preview = (prev.get("url") or send_url).strip()
        if send_url and is_allowed_gif_cdn_url(send_url):
            out.append(
                {
                    "id": str(item.get("id", "")),
                    "preview": preview,
                    "url": send_url,
                    "title": (item.get("content_description") or item.get("title") or "")[:200],
                }
            )
    return out


def search_giphy(query: str, limit: int = 24) -> list[dict]:
    key = getattr(settings, "GIPHY_API_KEY", "") or ""
    if not key:
        return []
    q = (query or "").strip() or "trending"
    path = "search" if q != "trending" else "trending"
    if path == "trending":
        params = urllib.parse.urlencode({"api_key": key, "limit": str(min(limit, 50))})
        url = f"https://api.giphy.com/v1/gifs/trending?{params}"
    else:
        params = urllib.parse.urlencode(
            {"api_key": key, "q": q, "limit": str(min(limit, 50)), "rating": "pg-13"}
        )
        url = f"https://api.giphy.com/v1/gifs/search?{params}"
    data = _http_json(url)
    if not data or "data" not in data:
        return []
    out: list[dict] = []
    for item in data["data"][:limit]:
        images = item.get("images") or {}
        downsized = (
            images.get("downsized_medium")
            or images.get("downsized_large")
            or images.get("downsized")
            or images.get("original")
            or {}
        )
        prev = (
            images.get("fixed_height_small")
            or images.get("preview_gif")
            or images.get("fixed_width_small")
            or downsized
            or {}
        )
        send_url = (downsized.get("url") or "").strip()
        preview = (prev.get("url") or send_url).strip()
        if send_url and is_allowed_gif_cdn_url(send_url):
            out.append(
                {
                    "id": str(item.get("id", "")),
                    "preview": preview,
                    "url": send_url,
                    "title": (item.get("title") or "")[:200],
                }
            )
    return out


def search_gifs(query: str, limit: int = 24) -> tuple[list[dict], str]:
    """
    Returns (results, provider_name). Giphy is used first (Tenor is optional fallback).
    """
    if getattr(settings, "GIPHY_API_KEY", ""):
        r = search_giphy(query, limit)
        if r:
            return r, "giphy"
    if getattr(settings, "TENOR_API_KEY", ""):
        r = search_tenor(query, limit)
        if r:
            return r, "tenor"
    return [], "none"


def gif_picker_configured() -> bool:
    return bool(getattr(settings, "TENOR_API_KEY", "") or getattr(settings, "GIPHY_API_KEY", ""))
