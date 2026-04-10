"""
Shared search infrastructure — perceptual hashing, URL utilities,
SearXNG multi-instance failover, and HTML link extractors.

Extracted from app.py so that web.py and other tool modules can
import these helpers without pulling in all of app.py.
"""
from __future__ import annotations

import base64
import html as _html
import os
import random as _random
import re
import time as _time
from typing import Any
from urllib.parse import unquote as _url_unquote

# PIL imports re-used from _imaging to avoid duplicate try/except blocks
from tools._imaging import HAS_PIL as _HAS_PIL, PilImage as _PilImage, ImageStat as _ImageStat  # type: ignore[import]

# SEARXNG_URL is read from the environment at import time (matches app.py behaviour).
SEARXNG_URL: str = os.environ.get("SEARXNG_URL", "http://aichat-searxng:8080")

# ---------------------------------------------------------------------------
# Perceptual hash helpers — pure PIL, no extra packages
# ---------------------------------------------------------------------------

def dhash(img: "_PilImage.Image") -> str:
    """64-bit difference hash of a PIL Image → 16-char hex string."""
    if not _HAS_PIL:
        return ""
    try:
        gray = img.convert("L").resize((9, 8), _PilImage.LANCZOS)
        # Pillow deprecates Image.getdata(); tobytes() is stable and faster here.
        px = gray.tobytes()
        bits = sum(
            1 << i for i in range(64)
            if px[i % 8 + (i // 8) * 9] > px[i % 8 + (i // 8) * 9 + 1]
        )
        return f"{bits:016x}"
    except Exception:
        return ""


def hamming(h1: str, h2: str) -> int:
    """Bit-level Hamming distance between two 16-hex-char dHashes."""
    if not h1 or not h2 or len(h1) != 16 or len(h2) != 16:
        return 64
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def domain_from_url(url: str) -> str:
    """Lowercased hostname without leading www., or empty on parse failure."""
    from urllib.parse import urlparse as _urlparse

    try:
        host = (_urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return host.removeprefix("www.")


def url_has_explicit_content(url: str, text: str = "") -> bool:
    """Content filter — disabled. All URLs pass through."""
    return False


def normalize_search_query(query: str) -> tuple[str, str]:
    """Normalize common query typos; return (normalized_query, note_or_empty)."""
    original = re.sub(r"\s+", " ", (query or "")).strip()
    if not original:
        return "", ""
    fixed = original
    fixes: tuple[tuple[str, str], ...] = (
        (r"\bkluki\b", "Klukai"),
        (r"\bgirls?\s*frontline\s*2\b", "Girls Frontline 2"),
        (r"\bgirls?\s+frontline2\b", "Girls Frontline 2"),
    )
    for pat, rep in fixes:
        fixed = re.sub(pat, rep, fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"\s+", " ", fixed).strip()
    if fixed.lower() == original.lower():
        return original, ""
    return fixed, f"Query normalized: '{original}' -> '{fixed}'"


def search_terms(query: str) -> list[str]:
    """Tokenize query into lowercased word terms useful for URL relevance scoring."""
    return [w.lower() for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower())]


def query_preferred_domains(query: str) -> tuple[str, ...]:
    """Domain preferences for known entities to improve relevance ordering."""
    q = (query or "").lower()
    if any(k in q for k in ("girls frontline 2", "gfl2", "klukai")):
        return ("iopwiki.com", "gf2exilium.com", "fandom.com", "prydwen.gg")
    return tuple()


def score_url_relevance(url: str, query_terms: list[str], preferred_domains: tuple[str, ...] = ()) -> int:
    """Simple URL relevance score used for ranking candidate pages/images."""
    url_l = (url or "").lower()
    host = domain_from_url(url_l)
    score = 0
    for w in query_terms:
        if w in url_l:
            score += 3
        if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", url_l):
            score += 2
    for dom in preferred_domains:
        if host == dom or host.endswith(f".{dom}"):
            score += 8
    return score


def unwrap_ddg_redirect(url: str) -> str:
    """Return target URL when the input is a DuckDuckGo redirect URL."""
    from urllib.parse import parse_qs as _parse_qs, urlparse as _urlparse

    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    if not u:
        return ""
    try:
        parsed = _urlparse(u)
        host = (parsed.hostname or "").lower()
        if "duckduckgo.com" in host and parsed.path == "/l/":
            cand = _url_unquote((_parse_qs(parsed.query).get("uddg") or [""])[0])
            if cand.startswith("http"):
                return cand
        m = re.search(r"uddg=(https?%3A[^&\s\"'>]+)", u)
        if m:
            cand2 = _url_unquote(m.group(1))
            if cand2.startswith("http"):
                return cand2
    except Exception:
        return u
    return u


def extract_ddg_links(html: str, max_results: int = 12) -> list[tuple[str, str]]:
    """Parse DDG HTML search results into [(url, title)] with deduped URLs."""
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Primary: result anchors with class="result__a"
    for m in re.finditer(
        r"<a[^>]+class=[\"'][^\"']*result__a[^\"']*[\"'][^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = _html.unescape(m.group(1))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = _html.unescape(re.sub(r"\s+", " ", title).strip())
        url = unwrap_ddg_redirect(href)
        if not url.startswith("http") or url in seen:
            continue
        links.append((url, title or url))
        seen.add(url)
        if len(links) >= max_results:
            return links

    # Fallback: uddg redirect parameters
    for enc in re.findall(r"uddg=(https?%3A[^&\"'>\s]+)", html):
        url = _url_unquote(enc)
        if not url.startswith("http") or url in seen:
            continue
        links.append((url, url))
        seen.add(url)
        if len(links) >= max_results:
            break
    return links


def extract_bing_links(html: str, max_results: int = 12) -> list[tuple[str, str]]:
    """Parse Bing HTML web search results into [(url, title)]."""
    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _unwrap_bing_redirect(url: str) -> str:
        from urllib.parse import parse_qs as _parse_qs, urlparse as _urlparse

        u = (url or "").strip()
        try:
            p = _urlparse(u)
            host = (p.hostname or "").lower()
            if "bing.com" in host and p.path.startswith("/ck/a"):
                raw_u = (_parse_qs(p.query).get("u") or [""])[0]
                if raw_u.startswith("a1"):
                    b64 = raw_u[2:]
                    b64 += "=" * ((4 - (len(b64) % 4)) % 4)
                    dec = base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8", errors="ignore")
                    if dec.startswith("http"):
                        return dec
        except Exception:
            return u
        return u

    for m in re.finditer(
        r"<li[^>]+class=[\"'][^\"']*b_algo[^\"']*[\"'][^>]*>.*?<h2[^>]*>.*?<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        url = _unwrap_bing_redirect(_html.unescape(m.group(1)))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = _html.unescape(re.sub(r"\s+", " ", title).strip())
        if not url.startswith("http") or url in seen:
            continue
        links.append((url, title or url))
        seen.add(url)
        if len(links) >= max_results:
            break
    return links


def extract_google_links(html: str, max_results: int = 12) -> list[tuple[str, str]]:
    """Parse Google HTML search results into [(url, title)]."""
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    from urllib.parse import parse_qs as _pqs, urlparse as _up_g

    # Google wraps result links as /url?q=<target>&... — unwrap them
    def _unwrap_google(href: str) -> str:
        if href.startswith("/url?") or href.startswith("https://www.google.com/url?"):
            qs = _pqs(_up_g(href).query)
            cand = (qs.get("q") or [""])[0]
            if cand.startswith("http"):
                return cand
        return href

    # Pattern 1: anchor before or wrapping h3 (standard blue links)
    for m in re.finditer(
        r'<a[^>]+href="([^"]*)"[^>]*>(?:[^<]|<(?!h3))*?<h3[^>]*>(.*?)</h3>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        url = _unwrap_google(_html.unescape(m.group(1)))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = _html.unescape(re.sub(r"\s+", " ", title).strip())
        if not url.startswith("http") or url in seen:
            continue
        host = domain_from_url(url)
        if "google.com" in host or "googleapis.com" in host:
            continue
        links.append((url, title or url))
        seen.add(url)
        if len(links) >= max_results:
            return links

    # Pattern 2: cite elements (often contain clean destination URLs)
    for m in re.finditer(r'<cite[^>]*>([^<]+)</cite>', html, flags=re.IGNORECASE):
        raw = _html.unescape(m.group(1)).strip().split(" ")[0]
        if not raw.startswith("http"):
            raw = "https://" + raw
        url = raw.split("›")[0].strip().rstrip("/")
        if not url.startswith("http") or url in seen:
            continue
        host = domain_from_url(url)
        if "google.com" in host:
            continue
        links.append((url, url))
        seen.add(url)
        if len(links) >= max_results:
            break

    return links


# ---------------------------------------------------------------------------
# Public SearXNG instances — used as PRIMARY search backends.
# Local self-hosted instance (SEARXNG_URL) is the LAST fallback.
# Instances are tried round-robin starting from a random offset so traffic
# is distributed across the pool and no single instance gets hammered.
# Source: https://searx.space/
# ---------------------------------------------------------------------------

_PUBLIC_SEARXNG_INSTANCES: list[str] = [
    "https://priv.au",
    "https://search.freestater.org",
    "https://etsi.me",
    "https://copp.gg",
    "https://search.femboy.ad",
    "https://search.unredacted.org",
    "https://searx.party",
    "https://search.rhscz.eu",
    "https://search.internetsucks.net",
    "https://ooglester.com",
    "https://search.abohiccups.com",
]

# Track which instances are healthy; unhealthy ones are skipped for a cooldown.
_searxng_fail_until: dict[str, float] = {}
_SEARXNG_COOLDOWN = 300.0  # 5 min cooldown for hard-failed (non-429) instances
_SEARXNG_429_COOLDOWN = 60.0  # 1 min cooldown for rate-limited instances


def searxng_endpoints() -> list[str]:
    """Return SearXNG endpoints in priority order: public first, local last.

    Failed instances are skipped if still within their cooldown window.
    Public instances are shuffled each call to distribute load.
    """
    now = _time.monotonic()
    healthy_public = [
        u for u in _PUBLIC_SEARXNG_INSTANCES
        if _searxng_fail_until.get(u, 0.0) <= now
    ]
    _random.shuffle(healthy_public)
    endpoints = list(healthy_public)
    # Local self-hosted instance as last fallback
    if SEARXNG_URL:
        endpoints.append(SEARXNG_URL)
    return endpoints


def extract_searxng_html_results(html_text: str, max_results: int = 12) -> list[dict]:
    """Parse SearXNG HTML search results into structured dicts.

    SearXNG default template uses:
      <h3><a href="URL">TITLE</a></h3>  or
      <a href="URL" class="...">TITLE</a> inside result blocks.
    Also extracts image results from <img> tags with data-src or src.
    """
    results: list[dict] = []
    seen: set[str] = set()

    # Pattern 1: h3/h4 anchors (standard web results)
    for m in re.finditer(
        r'<h[34][^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        url = _html.unescape(m.group(1))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = _html.unescape(re.sub(r"\s+", " ", title).strip())
        if url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        results.append({"url": url, "title": title})
        if len(results) >= max_results:
            return results

    # Pattern 2: result__a class (some SearXNG themes)
    for m in re.finditer(
        r'<a[^>]+class="[^"]*result[^"]*"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        url = _html.unescape(m.group(1))
        title = re.sub(r"<[^>]+>", " ", m.group(2))
        title = _html.unescape(re.sub(r"\s+", " ", title).strip())
        if url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        results.append({"url": url, "title": title})
        if len(results) >= max_results:
            return results

    # Pattern 3: any external link with substantial text (broadest fallback)
    for m in re.finditer(
        r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]{8,})</a>',
        html_text,
        flags=re.IGNORECASE,
    ):
        url = _html.unescape(m.group(1))
        title = _html.unescape(m.group(2).strip())
        if url in seen or not url.startswith("http"):
            continue
        # Skip self-links and navigation
        for skip in ("searx", "searxng", "github.com/searxng", "/preferences", "/about"):
            if skip in url.lower():
                break
        else:
            seen.add(url)
            results.append({"url": url, "title": title})
            if len(results) >= max_results:
                return results

    return results


async def searxng_query_one(
    client: Any,
    base_url: str,
    params: dict[str, str],
) -> dict | None:
    """Try a single SearXNG instance; return parsed JSON or None on failure.

    Strategy:
      1. Try JSON API (format=json) — works on local instances and some public ones.
      2. On 429 (rate limit), try HTML and parse results from the page.
      3. On other errors, mark instance as failed with cooldown.
    """
    # --- Attempt 1: JSON API ---
    try:
        r = await client.get(
            f"{base_url}/search",
            params=params,
            headers={**BROWSER_HEADERS, "Accept": "application/json"},
            timeout=12.0,
        )
        if r.status_code == 200:
            try:
                data = r.json()
                if data.get("results"):
                    return data
            except Exception:
                pass  # non-JSON response, try HTML below
        elif r.status_code == 429:
            # Rate-limited on JSON — try HTML (many instances allow HTML but block JSON API)
            pass
        else:
            _searxng_fail_until[base_url] = _time.monotonic() + _SEARXNG_COOLDOWN
            return None
    except Exception:
        _searxng_fail_until[base_url] = _time.monotonic() + _SEARXNG_COOLDOWN
        return None

    # --- Attempt 2: HTML parsing fallback (same query, no format=json) ---
    try:
        html_params = {k: v for k, v in params.items() if k != "format"}
        r = await client.get(
            f"{base_url}/search",
            params=html_params,
            headers=BROWSER_HEADERS,
            timeout=12.0,
        )
        if r.status_code == 200 and len(r.text) > 500:
            parsed = extract_searxng_html_results(r.text)
            if parsed:
                # Convert to the same format as JSON API response
                return {"results": parsed}
        elif r.status_code == 429:
            _searxng_fail_until[base_url] = _time.monotonic() + _SEARXNG_429_COOLDOWN
            return None
        else:
            _searxng_fail_until[base_url] = _time.monotonic() + _SEARXNG_COOLDOWN
            return None
    except Exception:
        _searxng_fail_until[base_url] = _time.monotonic() + _SEARXNG_429_COOLDOWN
        return None

    return None


async def searxng_search(
    client: Any,
    query: str,
    *,
    engines: str = "",
    categories: str = "general",
    max_results: int = 10,
) -> list[tuple[str, str]]:
    """Query SearXNG JSON API with multi-instance failover; return [(url, title)].

    Tries public instances first (shuffled for load distribution), then falls
    back to the local self-hosted instance.  Failed instances are cooled down
    for 120 s so subsequent calls skip them immediately.
    """
    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "safesearch": "0",
        "language": "en",
    }
    if engines:
        params["engines"] = engines
    if categories:
        params["categories"] = categories

    for base_url in searxng_endpoints():
        data = await searxng_query_one(client, base_url, params)
        if data is None:
            continue
        results = data.get("results") or []
        links: list[tuple[str, str]] = []
        seen: set[str] = set()
        for res in results:
            url = str(res.get("url") or "").strip()
            title = str(res.get("title") or url).strip()
            if not url.startswith("http") or url in seen:
                continue
            links.append((url, title))
            seen.add(url)
            if len(links) >= max_results:
                break
        if links:
            return links
    return []


async def searxng_image_search(
    client: Any,
    query: str,
    *,
    max_results: int = 30,
) -> list[dict]:
    """Query SearXNG image search with multi-instance failover.

    Returns list of image candidate dicts with keys: url, page, alt, natural_w, type.
    Tries public instances first, local last.
    """
    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "safesearch": "0",
        "language": "en",
        "categories": "images",
    }

    for base_url in searxng_endpoints():
        data = await searxng_query_one(client, base_url, params)
        if data is None:
            continue
        results = data.get("results") or []
        candidates: list[dict] = []
        seen: set[str] = set()
        for res in results:
            img_src = str(res.get("img_src") or "").strip()
            if not img_src.startswith("http") or img_src in seen:
                continue
            candidates.append({
                "url":     img_src,
                "page":    str(res.get("url") or ""),
                "alt":     str(res.get("title") or ""),
                "natural_w": 0,
                "type":    "searxng",
            })
            seen.add(img_src)
            if len(candidates) >= max_results:
                break
        if candidates:
            return candidates
    return []


def is_low_information_image(img: "_PilImage.Image") -> bool:
    """Detect near-solid placeholder images (e.g., pure black/white blocks)."""
    if not _HAS_PIL:
        return False
    try:
        sample = img.convert("RGB").copy()
        sample.thumbnail((96, 96), _PilImage.BILINEAR)
        stats = _ImageStat.Stat(sample)
        if not stats.mean or not stats.stddev:
            return False
        mean_luma = sum(float(v) for v in stats.mean) / 3.0
        max_std = max(float(v) for v in stats.stddev)
        return max_std < 6.0 and (mean_luma < 20.0 or mean_luma > 245.0)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Realistic browser headers — used for all outbound httpx requests to reduce
# bot-detection and rate-limit exposure.
# ---------------------------------------------------------------------------

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
