"""
Web tool handlers — search, fetch, news, Wikipedia, arXiv, YouTube transcript,
and article extraction.

Extracted from app.py. Handlers that depend on ModelRegistry / LM Studio
(smart_summarize, structured_extract) remain in app.py.

Registered with the tool registry at import time via TOOL_HANDLERS.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text as _text, get_client, DATABASE_URL, BROWSER_URL  # type: ignore[import]
from tools._search import (  # type: ignore[import]
    normalize_search_query,
    search_terms,
    query_preferred_domains,
    url_has_explicit_content,
    searxng_search,
    extract_ddg_links,
    extract_bing_links,
    extract_google_links,
    BROWSER_HEADERS,
)

# ---------------------------------------------------------------------------
# Lazy singleton for source strategy ranking (mirrors app.py pattern)
# ---------------------------------------------------------------------------

_source_strategy_instance = None  # type: ignore[assignment]


def _get_source_strategy() -> Any:
    """Lazy singleton for search result ranking."""
    global _source_strategy_instance
    if _source_strategy_instance is None:
        from source_strategy import SourceStrategy  # type: ignore[import]
        _source_strategy_instance = SourceStrategy()
    return _source_strategy_instance


# ---------------------------------------------------------------------------
# web_quick_search
# ---------------------------------------------------------------------------

async def _web_quick_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    raw_query = str(args.get("query", "")).strip()
    query, normalize_note = normalize_search_query(raw_query)
    if not query:
        return _text("quick_search: 'query' is required")
    try:
        async with get_client() as c:
            links = await searxng_search(c, query, max_results=8)
            if not links:
                return _text(f"No results found for: {query}")
            lines = [f"[Quick search] Query: {query}"]
            if normalize_note:
                lines.append(normalize_note)
            for idx, (url, title) in enumerate(links[:8], start=1):
                lines.append(f"{idx}. {title or url}")
                lines.append(f"URL: {url}")
            return _text("\n".join(lines))
    except Exception as exc:
        return _text(f"quick_search failed: {exc}")


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

async def _web_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    raw_query = str(args.get("query", "")).strip()
    query, normalize_note = normalize_search_query(raw_query)
    if not query:
        return _text("web_search: 'query' is required")
    engine = str(args.get("engine", "auto")).strip().lower() or "auto"
    max_chars = int(args.get("max_chars", 4000))
    max_chars = max(500, min(max_chars, 16000))
    from urllib.parse import quote_plus as _qp

    q_terms = search_terms(query)
    pref = query_preferred_domains(query)

    def _fmt_links(
        links: "list[tuple[str, str]]",
        label: str = "Search results",
        is_images: bool = False,
    ) -> "str | None":
        """Rank, filter and format [(url, title)] into a result string."""
        cleaned = [
            (u, t) for (u, t) in links
            if u and not url_has_explicit_content(u, t)
        ]
        # Source strategy ranking (preferred sources, dedup, quality)
        _ss = _get_source_strategy()
        _as_dicts = [{"url": u, "title": t} for u, t in cleaned]
        _ranked = _ss.rank_results(_as_dicts, query)
        cleaned = [(r["url"], r["title"]) for r in _ranked]
        if not cleaned:
            return None
        lines = [f"[{label}] Query: {query}"]
        if normalize_note:
            lines.append(normalize_note)
        for idx, (url, title) in enumerate(cleaned[:8], start=1):
            if is_images:
                lines.append(f"{idx}. {title or url}")
                lines.append(f"Image URL: {url}")
            else:
                lines.append(f"{idx}. {title or url}")
                lines.append(f"URL: {url}")
        return "\n".join(lines)[:max_chars]

    async with get_client() as c:
        # ── Tier 0: SearXNG meta-search ──────────────────────────────
        # Maps engine → (primary_engine_filter, category).
        # primary="" means "use all available SearXNG engines".
        # If the primary-filtered search returns nothing (e.g. Google
        # is blocked on this SearXNG instance), we retry with all
        # engines so Bing/Brave still provide results.
        _SEARXNG_ENGINE_MAP: dict[str, tuple[str, str]] = {
            "auto":    ("",            "general"),
            "searxng": ("",            "general"),
            "google":  ("google",      "general"),
            "bing":    ("bing",        "general"),
            "ddg":     ("duckduckgo",  "general"),
            "brave":   ("brave",       "general"),
            "images":  ("",            "images"),
        }
        sx_engines, sx_category = _SEARXNG_ENGINE_MAP.get(
            engine, ("", "general")
        )
        sx_links = await searxng_search(
            c, query,
            engines=sx_engines,
            categories=sx_category,
            max_results=12,
        )
        # If specific engine filter returned nothing, retry with all engines
        if not sx_links and sx_engines:
            sx_links = await searxng_search(
                c, query,
                engines="",
                categories=sx_category,
                max_results=12,
            )
        if sx_links:
            formatted = _fmt_links(
                sx_links,
                label="Search results" if engine != "images" else "Image results",
                is_images=(engine == "images"),
            )
            if formatted:
                return _text(formatted)


        # ── Tier 1b: Engine-specific direct HTML scraping ─────────────
        if engine in ("auto", "ddg"):
            try:
                r = await c.get(
                    f"https://html.duckduckgo.com/html/?q={_qp(query)}",
                    headers=BROWSER_HEADERS,
                    follow_redirects=True,
                )
                challenge_markers = (
                    "bots use duckduckgo too",
                    "select all squares",
                    "error-lite@duckduckgo.com",
                )
                if not any(m in r.text.lower() for m in challenge_markers):
                    ddg_links = extract_ddg_links(r.text, max_results=10)
                    formatted = _fmt_links(ddg_links, label="Search results (DDG)")
                    if formatted:
                        return _text(formatted)
            except Exception:
                pass

        if engine in ("auto", "bing"):
            try:
                rb = await c.get(
                    f"https://www.bing.com/search?q={_qp(query)}&setlang=en-US",
                    headers=BROWSER_HEADERS,
                    follow_redirects=True,
                )
                bing_links = extract_bing_links(rb.text, max_results=10)
                formatted = _fmt_links(bing_links, label="Search results (Bing)")
                if formatted:
                    return _text(formatted)
            except Exception:
                pass

        if engine in ("google",):
            try:
                rg = await c.get(
                    f"https://www.google.com/search?q={_qp(query)}&hl=en&num=10",
                    headers={
                        **BROWSER_HEADERS,
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://www.google.com/",
                    },
                    follow_redirects=True,
                )
                g_links = extract_google_links(rg.text, max_results=10)
                formatted = _fmt_links(g_links, label="Search results (Google)")
                if formatted:
                    return _text(formatted)
            except Exception:
                pass

        # ── Tier 2: Real-browser DOM extraction ───────────────────────
        try:
            # For Google Images, navigate directly to images.google.com
            if engine == "images":
                browser_url_target = f"https://www.google.com/search?q={_qp(query)}&tbm=isch"
                dom_js = r"""
                    JSON.stringify(
                        Array.from(document.querySelectorAll('img[src]'))
                            .map(img => img.src)
                            .filter(s => s && s.startsWith('http') && !s.includes('google.com/logos'))
                            .filter((s, i, a) => a.indexOf(s) === i)
                            .slice(0, 20)
                    )
                """
                await asyncio.wait_for(
                    c.post(f"{BROWSER_URL}/navigate", json={"url": browser_url_target}),
                    timeout=35.0,
                )
            else:
                dom_js = r"""
                    JSON.stringify(
                        Array.from(document.links)
                            .map(a => {
                                try {
                                    const u = new URL(a.href);
                                    if (u.hostname === 'duckduckgo.com' && u.pathname === '/l/')
                                        return [u.searchParams.get('uddg') || null, a.innerText.trim()];
                                    if (!['duckduckgo.com','duck.co','google.com','bing.com'].includes(u.hostname))
                                        return [a.href, a.innerText.trim()];
                                    return null;
                                } catch(e) { return null; }
                            })
                            .filter(x => x && x[0] && x[0].startsWith('http'))
                            .filter((x, i, arr) => arr.findIndex(y => y && y[0] === x[0]) === i)
                            .slice(0, 20)
                    )
                """
                await asyncio.wait_for(
                    c.post(f"{BROWSER_URL}/search", json={"query": query}),
                    timeout=35.0,
                )
            import json as _json
            ev = await c.post(
                f"{BROWSER_URL}/eval",
                json={"code": dom_js},
                timeout=10,
            )
            raw = _json.loads(ev.json().get("result", "[]"))
            if engine == "images":
                browser_links = [(u, u) for u in raw if u]
                formatted = _fmt_links(
                    browser_links, label="Image results (browser)", is_images=True
                )
            else:
                browser_links = [
                    (item[0], item[1]) if isinstance(item, list) else (item, item)
                    for item in raw if item
                ]
                formatted = _fmt_links(browser_links, label="Search results (browser)")
            if formatted:
                return _text(formatted)
        except Exception:
            pass

        # ── Tier 3: DDG lite (last resort text scrape) ────────────────
        try:
            r = await c.get(
                f"https://lite.duckduckgo.com/lite/?q={_qp(query)}",
                headers=BROWSER_HEADERS,
                follow_redirects=True,
            )
            text = re.sub(r"<[^>]+>", " ", r.text)
            text = re.sub(r"\s+", " ", text).strip()[:max_chars]
            header = f"[Search results (lite)] Query: {query}\n"
            if normalize_note:
                header += normalize_note + "\n"
            return _text(header + "\n" + text)
        except Exception as exc:
            return _text(f"web_search failed: {exc}")


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------

async def _web_fetch(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip()
    max_chars = int(args.get("max_chars", 4000))
    max_chars = max(500, min(max_chars, 16000))
    # Check cache first
    async with get_client() as c:
        try:
            cache_r = await c.get(f"{DATABASE_URL}/cache/get", params={"key": url})
            if cache_r.status_code == 200:
                data = cache_r.json()
                if data.get("found"):
                    cached_text = data.get("content", "")
                    # Re-cache items may contain raw HTML from old behavior — strip if needed.
                    # Use `>?` so truncated tags (no closing >) are also removed.
                    if cached_text.lstrip().startswith("<"):
                        cached_text = re.sub(r"<[^>]*>?", " ", cached_text)
                        cached_text = re.sub(r"\s+", " ", cached_text).strip()
                    if len(cached_text) > 50:
                        return _text(f"[cached] {cached_text[:max_chars]}")
                    # Too short (stripped HTML left only a title/nav) — fall through to live fetch
        except Exception:
            pass
        # Fetch via browser (renders JS, returns clean text, handles SSL)
        text = ""
        try:
            nav_r = await asyncio.wait_for(
                c.post(f"{BROWSER_URL}/navigate", json={"url": url}),
                timeout=20.0,
            )
            nav_data = nav_r.json()
            text = nav_data.get("content", "")
            if text:
                title = nav_data.get("title", "")
                final_url = nav_data.get("url", url)
                header = f"Title: {title}\nURL: {final_url}\n\n" if title else ""
                text = (header + text)[:max_chars]
        except Exception:
            pass
        # Fallback: httpx + strip tags
        if not text:
            try:
                r = await c.get(url, headers=BROWSER_HEADERS, follow_redirects=True)
                raw = r.text
                text = re.sub(r"<[^>]+>", " ", raw)
                text = re.sub(r"\s+", " ", text).strip()[:max_chars]
            except Exception as exc:
                return _text(f"web_fetch failed: {exc}")
        try:
            await c.post(f"{DATABASE_URL}/cache/store", json={"url": url, "content": text})
        except Exception:
            pass
        return _text(text)


# ---------------------------------------------------------------------------
# extract_article
# ---------------------------------------------------------------------------

async def _extract_article(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip()
    if not url:
        return _text("extract_article: 'url' is required")
    max_chars = max(500, int(args.get("max_chars", 8000)))
    try:
        async with get_client() as c:
            nav_r = await asyncio.wait_for(
                c.post(f"{BROWSER_URL}/navigate", json={"url": url}),
                timeout=25.0,
            )
            data = nav_r.json()
            title     = data.get("title", "")
            content   = data.get("content", "")
            final_url = data.get("url", url)
            clean_lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
            clean = "\n".join(clean_lines)[:max_chars]
            header = f"Title: {title}\nURL:   {final_url}\n\n" if title else f"URL: {final_url}\n\n"
            return _text(header + clean)
    except Exception as exc:
        return _text(f"extract_article failed: {exc}")


# ---------------------------------------------------------------------------
# news_search
# ---------------------------------------------------------------------------

async def _news_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    import feedparser as _fp_ns
    ns_query   = str(args.get("query", "")).strip().lower()
    ns_sources = [s.lower() for s in (args.get("sources") or [])]
    ns_limit   = max(1, min(50, int(args.get("limit", 10))))
    _NS_FEEDS = {
        "bbc":         "https://feeds.bbci.co.uk/news/rss.xml",
        "guardian":    "https://www.theguardian.com/world/rss",
        "hackernews":  "https://news.ycombinator.com/rss",
        "arstechnica": "https://feeds.arstechnica.com/arstechnica/index",
        "techcrunch":  "https://techcrunch.com/feed/",
    }
    active_feeds = {k: v for k, v in _NS_FEEDS.items()
                    if not ns_sources or k in ns_sources}
    articles_ns: list[dict] = []
    async with get_client() as c:
        for src_ns, feed_url_ns in active_feeds.items():
            try:
                feed_r_ns = await c.get(feed_url_ns, timeout=10, follow_redirects=True)
                if feed_r_ns.status_code != 200:
                    continue
                feed_ns = _fp_ns.parse(feed_r_ns.text)
                for entry_ns in feed_ns.entries:
                    title_ns   = entry_ns.get("title", "")
                    summary_ns = entry_ns.get("summary", "")
                    link_ns    = entry_ns.get("link", "")
                    pub_ns     = entry_ns.get("published", "")
                    text_ns    = (title_ns + " " + summary_ns).lower()
                    if ns_query and ns_query not in text_ns:
                        continue
                    articles_ns.append({
                        "source": src_ns, "title": title_ns,
                        "summary": re.sub(r"<[^>]+>", "", summary_ns)[:200],
                        "url": link_ns, "published": pub_ns,
                    })
            except Exception:
                continue
    if not articles_ns:
        return _text("news_search: no articles found" +
                     (f" matching '{ns_query}'" if ns_query else ""))
    articles_ns = articles_ns[:ns_limit]
    lines_ns = [f"## News{' — ' + ns_query if ns_query else ''} "
                f"({len(articles_ns)} articles)\n"]
    for a_ns in articles_ns:
        lines_ns.append(
            f"**[{a_ns['source'].upper()}] {a_ns['title']}**\n"
            f"{a_ns['summary']}\n"
            f"<{a_ns['url']}> | {a_ns['published']}\n"
        )
    return _text("\n".join(lines_ns))


# ---------------------------------------------------------------------------
# wikipedia
# ---------------------------------------------------------------------------

async def _wikipedia(args: dict[str, Any]) -> list[dict[str, Any]]:
    wp_query  = str(args.get("query", "")).strip()
    wp_lang   = str(args.get("lang", "en")).strip() or "en"
    wp_full   = bool(args.get("full_article", False))
    if not wp_query:
        return _text("wikipedia: 'query' is required")
    _wp_headers = {"User-Agent": "aichat/1.0 (autonomous research bot; open-source)"}
    async with get_client() as c:
        # Step 1: search for the best matching article
        search_r_wp = await c.get(
            f"https://{wp_lang}.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": wp_query,
                    "srlimit": 3, "format": "json", "srprop": "snippet"},
            headers=_wp_headers,
            timeout=10,
        )
        if search_r_wp.status_code != 200:
            return _text(f"wikipedia: search error {search_r_wp.status_code}")
        search_hits_wp = search_r_wp.json().get("query", {}).get("search", [])
        if not search_hits_wp:
            return _text(f"wikipedia: no results for '{wp_query}'")
        best_title_wp = search_hits_wp[0]["title"]
        # Step 2: fetch article
        if wp_full:
            article_r_wp = await c.get(
                f"https://{wp_lang}.wikipedia.org/w/api.php",
                params={"action": "query", "prop": "extracts", "titles": best_title_wp,
                        "exintro": False, "explaintext": True, "format": "json"},
                headers=_wp_headers,
                timeout=15,
            )
            if article_r_wp.status_code != 200:
                return _text(f"wikipedia: article fetch error {article_r_wp.status_code}")
            pages_wp = article_r_wp.json().get("query", {}).get("pages", {})
            text_wp  = next(iter(pages_wp.values()), {}).get("extract", "")[:8000]
        else:
            summary_r_wp = await c.get(
                f"https://{wp_lang}.wikipedia.org/api/rest_v1/page/summary/"
                + best_title_wp.replace(" ", "_"),
                headers=_wp_headers,
                timeout=10,
            )
            if summary_r_wp.status_code != 200:
                return _text(f"wikipedia: summary error {summary_r_wp.status_code}")
            sdata_wp = summary_r_wp.json()
            text_wp  = sdata_wp.get("extract", "")
        other_titles_wp = [h["title"] for h in search_hits_wp[1:]]
        result_wp = f"# {best_title_wp}\n\n{text_wp}"
        if other_titles_wp:
            result_wp += f"\n\n---\n*Also consider: {', '.join(other_titles_wp)}*"
        return _text(result_wp)


# ---------------------------------------------------------------------------
# arxiv_search
# ---------------------------------------------------------------------------

async def _arxiv_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    import defusedxml.ElementTree as _et_ax
    ax_query   = str(args.get("query", "")).strip()
    ax_max     = max(1, min(25, int(args.get("max_results", 8))))
    ax_cat     = str(args.get("category", "")).strip()
    ax_sort    = str(args.get("sort_by", "relevance"))
    if not ax_query:
        return _text("arxiv_search: 'query' is required")
    search_ax = ax_query if not ax_cat else f"{ax_query} AND cat:{ax_cat}"
    async with get_client() as c:
        ax_r = await c.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{search_ax}",
                    "max_results": ax_max, "sortBy": ax_sort},
            timeout=20,
        )
    if ax_r.status_code != 200:
        return _text(f"arxiv_search: arXiv API error {ax_r.status_code}")
    _ns_ax = {"atom": "http://www.w3.org/2005/Atom",
              "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root_ax = _et_ax.fromstring(ax_r.text)
    except Exception as exc_ax:
        return _text(f"arxiv_search: XML parse error — {exc_ax}")
    entries_ax = root_ax.findall("atom:entry", _ns_ax)
    if not entries_ax:
        return _text(f"arxiv_search: no papers found for '{ax_query}'")
    lines_ax = [f"## arXiv: '{ax_query}' — {len(entries_ax)} results\n"]
    for i_ax, entry_ax in enumerate(entries_ax, 1):
        title_ax   = (entry_ax.findtext("atom:title", "", _ns_ax) or "").strip().replace("\n", " ")
        abstract_ax= (entry_ax.findtext("atom:summary", "", _ns_ax) or "").strip()[:400]
        published_ax = (entry_ax.findtext("atom:published", "", _ns_ax) or "")[:10]
        authors_ax = [a.findtext("atom:name", "", _ns_ax)
                      for a in entry_ax.findall("atom:author", _ns_ax)][:4]
        id_elem_ax = entry_ax.findtext("atom:id", "", _ns_ax) or ""
        pdf_ax     = id_elem_ax.replace("abs", "pdf") if "arxiv.org" in id_elem_ax else ""
        lines_ax.append(
            f"### [{i_ax}] {title_ax}\n"
            f"**Authors:** {', '.join(authors_ax)}{' et al.' if len(authors_ax)==4 else ''}\n"
            f"**Date:** {published_ax}\n"
            f"**Abstract:** {abstract_ax}…\n"
            f"**PDF:** {pdf_ax}\n"
        )
    return _text("\n".join(lines_ax))


# ---------------------------------------------------------------------------
# youtube_transcript
# ---------------------------------------------------------------------------

async def _youtube_transcript(args: dict[str, Any]) -> list[dict[str, Any]]:
    import re as _re_yt
    yt_url  = str(args.get("url", "")).strip()
    yt_lang = str(args.get("lang", "en")).strip() or "en"
    yt_ts   = bool(args.get("include_timestamps", True))
    if not yt_url:
        return _text("youtube_transcript: 'url' is required")
    # Extract video ID
    vid_id_yt = None
    patterns_yt = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for pat_yt in patterns_yt:
        m_yt = _re_yt.search(pat_yt, yt_url, _re_yt.IGNORECASE)
        if m_yt:
            vid_id_yt = m_yt.group(1)
            break
    if not vid_id_yt:
        return _text(f"youtube_transcript: cannot extract video ID from '{yt_url}'")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi as _YTA
        from youtube_transcript_api._errors import (
            NoTranscriptFound as _NTF,
            TranscriptsDisabled as _TD,
        )
        _yta_instance = _YTA()
        try:
            transcript_yt = _yta_instance.fetch(
                vid_id_yt, languages=[yt_lang, "en", "en-US"]
            )
        except _NTF:
            # Try any available transcript
            transcript_list_yt = _yta_instance.list(vid_id_yt)
            transcript_yt = next(iter(transcript_list_yt)).fetch()
        lines_yt = [f"# YouTube Transcript: {yt_url}\n"]
        for seg_yt in transcript_yt:
            start_yt = int(getattr(seg_yt, "start", 0))
            text_yt  = str(getattr(seg_yt, "text", "")).strip()
            if yt_ts:
                mm_yt, ss_yt = divmod(start_yt, 60)
                lines_yt.append(f"[{mm_yt:02d}:{ss_yt:02d}] {text_yt}")
            else:
                lines_yt.append(text_yt)
        return _text("\n".join(lines_yt))
    except _TD:
        return _text(f"youtube_transcript: transcripts disabled for video '{vid_id_yt}'")
    except Exception as exc_yt:
        return _text(f"youtube_transcript: {exc_yt}")


# ---------------------------------------------------------------------------
# Register all handlers
# ---------------------------------------------------------------------------

TOOL_HANDLERS["web_quick_search"] = _web_quick_search
TOOL_HANDLERS["web_search"]       = _web_search
TOOL_HANDLERS["web_fetch"]        = _web_fetch
TOOL_HANDLERS["extract_article"]  = _extract_article
TOOL_HANDLERS["news_search"]      = _news_search
TOOL_HANDLERS["wikipedia"]        = _wikipedia
TOOL_HANDLERS["arxiv_search"]     = _arxiv_search
TOOL_HANDLERS["youtube_transcript"] = _youtube_transcript
