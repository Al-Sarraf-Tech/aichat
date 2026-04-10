"""
Browser tool handlers — screenshot, page extraction, scraping, image download,
and headless Chromium automation.

Extracted from app.py. All handlers use only helpers from tools._helpers,
tools._imaging, and tools._search.

Registered with the tool registry at import time via TOOL_HANDLERS.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote as _url_unquote

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import (  # type: ignore[import]
    text as _text,
    get_client,
    BROWSER_URL,
    BROWSER_WORKSPACE,
    BROWSER_AUTO_URL,
    DATABASE_URL,
    VIDEO_URL,
)
from tools._imaging import (  # type: ignore[import]
    renderer,
    image_blocks as _image_blocks,
    pil_to_blocks as _pil_to_blocks,
    HAS_PIL as _HAS_PIL,
    PilImage as _PilImage,
    ImageOps as _ImageOps,
)
from tools._search import (  # type: ignore[import]
    BROWSER_HEADERS as _BROWSER_HEADERS,
    normalize_search_query as _normalize_search_query,
    extract_ddg_links as _extract_ddg_links,
    extract_bing_links as _extract_bing_links,
    search_terms as _search_terms,
    query_preferred_domains as _query_preferred_domains,
    score_url_relevance as _score_url_relevance,
    url_has_explicit_content as _url_has_explicit_content,
)


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------

async def _screenshot(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip()
    if not url:
        return _text("screenshot: 'url' is required")
    find_text  = str(args.get("find_text",  "")).strip() or None
    find_image = str(args.get("find_image", "")).strip() or None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    container_path = f"/workspace/screenshot_{ts}.png"
    shot_req: dict = {"url": url, "path": container_path}
    if find_text:
        shot_req["find_text"] = find_text
    elif find_image:
        shot_req["find_image"] = find_image
    async with get_client() as c:
        try:
            r = await c.post(f"{BROWSER_URL}/screenshot",
                             json=shot_req, timeout=20)
            data = r.json()
        except Exception as exc:
            return _text(f"Screenshot failed (browser unreachable): {exc}")
        error = data.get("error", "")
        image_urls = data.get("image_urls", [])
        container_path = data.get("path", container_path)
        filename = os.path.basename(container_path)
        host_path = f"/docker/human_browser/workspace/{filename}"
        local_path = os.path.join(BROWSER_WORKSPACE, filename)
        page_title = data.get("title", "") or url
        clipped = data.get("clipped", False)
        image_meta = data.get("image_meta", {})
        if clipped and find_image:
            src_hint = image_meta.get("src", find_image)
            nat_w = image_meta.get("natural_w", 0)
            nat_h = image_meta.get("natural_h", 0)
            dim_note = f" ({nat_w}×{nat_h} natural)" if nat_w and nat_h else ""
            clip_note = f"\nImage: '{src_hint}'{dim_note}"
        elif clipped and find_text:
            clip_note = f"\nZoomed to: '{find_text}'"
        else:
            clip_note = ""
        summary = (
            f"Screenshot of: {page_title}\n"
            f"URL: {url}{clip_note}\n"
            f"File: {host_path}"
        )
        # Happy path — screenshot file was written
        if os.path.isfile(local_path):
            try:
                await c.post(f"{DATABASE_URL}/images/store", json={
                    "url": url,
                    "host_path": host_path,
                    "alt_text": f"Screenshot of {page_title}",
                })
            except Exception:
                pass
            return _image_blocks(container_path, summary)
        # Screenshot file missing — browser was blocked or crashed.
        # Try fetching a real image from the page DOM (browser v2+ returns image_urls).
        img_hdrs = {
            "User-Agent": _BROWSER_HEADERS["User-Agent"],
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        }
        for img_url in image_urls[:3]:
            try:
                ir = await c.get(img_url, headers=img_hdrs,
                                 follow_redirects=True, timeout=15)
                if ir.status_code == 200:
                    ct = ir.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    fallback_summary = (
                        f"Screenshot of: {page_title}\n"
                        f"URL: {url}\n"
                        f"(screenshot blocked — showing page image)"
                    )
                    return renderer.encode_url_bytes(ir.content, ct, fallback_summary)
            except Exception:
                continue
        return _text(f"Screenshot failed: {error or 'unknown error'}. URL: {url}")


# ---------------------------------------------------------------------------
# fetch_image
# ---------------------------------------------------------------------------

async def _fetch_image(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip()
    if not url:
        return _text("fetch_image: 'url' is required")
    img_fetch_headers = {
        "User-Agent": _BROWSER_HEADERS["User-Agent"],
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": _BROWSER_HEADERS["Accept-Language"],
        "Accept-Encoding": _BROWSER_HEADERS["Accept-Encoding"],
        "DNT": "1",
    }
    last_exc: Exception | None = None
    content_type = "image/jpeg"
    img_data = b""
    async with get_client() as c:
        for attempt in range(2):
            try:
                r = await c.get(url, headers=img_fetch_headers, follow_redirects=True)
                if r.status_code == 429 and attempt == 0:
                    retry_after = min(int(r.headers.get("retry-after", "15")), 30)
                    await asyncio.sleep(retry_after)
                    continue
                r.raise_for_status()
                content_type = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                img_data = r.content
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(3)
                    continue
        else:
            return _text(f"fetch_image failed: {last_exc}")
        # Derive host_path for DB metadata (workspace is writable via host bind-mount)
        raw_name = url.split("?")[0].split("/")[-1] or "image"
        if "." not in raw_name:
            ext = {"image/jpeg": ".jpg", "image/png": ".png",
                   "image/gif": ".gif", "image/webp": ".webp"}.get(content_type, ".jpg")
            raw_name += ext
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"img_{ts}_{raw_name}"
        host_path = f"/docker/human_browser/workspace/{filename}"
        # Save metadata to DB
        try:
            await c.post(f"{DATABASE_URL}/images/store", json={
                "url": url,
                "host_path": host_path,
                "alt_text": f"Image from {url}",
            })
        except Exception:
            pass
        # Return inline base64 image — always compress via ImageRenderer so
        # large PNGs/WebPs never exceed LM Studio's MCP payload cap.
        summary = (
            f"Image from: {url}\n"
            f"Type: {content_type}  Size: {len(img_data):,} bytes\n"
            f"File: {host_path}"
        )
        return renderer.encode_url_bytes(img_data, content_type, summary)


# ---------------------------------------------------------------------------
# screenshot_search
# ---------------------------------------------------------------------------

async def _screenshot_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    raw_query = str(args.get("query", "")).strip()
    query, normalize_note = _normalize_search_query(raw_query)
    if not query:
        return _text("screenshot_search: 'query' is required")
    max_results = max(1, min(int(args.get("max_results", 3)), 5))
    from urllib.parse import quote_plus as _qp

    async with get_client() as c:
        # Search DuckDuckGo HTML for result URLs (realistic headers)
        try:
            r = await c.get(
                f"https://html.duckduckgo.com/html/?q={_qp(query)}",
                headers=_BROWSER_HEADERS,
                follow_redirects=True,
            )
            html = r.text
        except Exception as exc:
            return _text(f"Search failed: {exc}")

        # Tier 1: parse DDG result links.
        _DDG_HOSTS = ('duckduckgo.com', 'ddg.gg', 'duck.co')
        seen_u: set[str] = set()
        urls: list[str] = []
        for url, _title in _extract_ddg_links(html, max_results=20):
            if any(d in url for d in _DDG_HOSTS):
                continue
            if _url_has_explicit_content(url):
                continue
            if url in seen_u:
                continue
            seen_u.add(url)
            urls.append(url)

        # Tier 2: direct href links (fallback if DDG changed format or rate-limited)
        if not urls:
            href_raw = re.findall(r'href=["\']?(https?://[^"\'>\s]+)', html)
            urls = list(dict.fromkeys(
                u for u in href_raw
                if not any(d in u for d in _DDG_HOSTS)
                and not _url_has_explicit_content(u)
            ))

        # Tier 2b: Bing fallback when DDG yields challenge/empty results.
        if not urls:
            try:
                rb = await c.get(
                    f"https://www.bing.com/search?q={_qp(query)}&setlang=en-US",
                    headers=_BROWSER_HEADERS,
                    follow_redirects=True,
                )
                urls = [
                    u for u, _t in _extract_bing_links(rb.text, max_results=20)
                    if not _url_has_explicit_content(u)
                ]
            except Exception:
                pass

        # Tier 3: browser search + DOM eval (Chromium w/ anti-detection, most reliable)
        if not urls:
            try:
                await asyncio.wait_for(
                    c.post(f"{BROWSER_URL}/search", json={"query": query}),
                    timeout=35.0,
                )
                ev = await c.post(f"{BROWSER_URL}/eval", json={"code": r"""
                    JSON.stringify(
                        Array.from(document.links)
                            .map(a => {
                                try {
                                    const u = new URL(a.href);
                                    if (u.hostname === 'duckduckgo.com' && u.pathname === '/l/')
                                        return u.searchParams.get('uddg') || null;
                                    if (u.hostname !== 'duckduckgo.com' && u.hostname !== 'duck.co')
                                        return a.href;
                                    return null;
                                } catch(e) { return null; }
                            })
                            .filter(u => u && u.startsWith('http'))
                            .filter((u, i, arr) => arr.indexOf(u) === i)
                            .slice(0, 5)
                    )
                """}, timeout=10)
                extracted = json.loads(ev.json().get("result", "[]"))
                urls = [u for u in extracted if u and not _url_has_explicit_content(u)]
            except Exception:
                pass

        if not urls:
            return _text(f"No URLs found in search results for: {query}")

        # Rank URLs for relevance and safer domains.
        _SKIP_T1 = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com",
                    "twitch.tv", "pinterest.com", "instagram.com", "facebook.com")
        q_terms = _search_terms(query)
        preferred_domains = _query_preferred_domains(query)
        urls = sorted(
            list(dict.fromkeys(u for u in urls if u.startswith("http"))),
            key=lambda u: (
                _score_url_relevance(u, q_terms, preferred_domains)
                - (30 if any(s in u.lower() for s in _SKIP_T1) else 0)
            ),
            reverse=True,
        )[:max_results]

        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": (
                f"Visual search: '{query}' — screenshotting {len(urls)} result(s)...\n"
                + (normalize_note + "\n" if normalize_note else "")
            )}
        ]
        img_hdrs = {
            "User-Agent": _BROWSER_HEADERS["User-Agent"],
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        }
        # 24-second budget for all screenshots — fits within LM Studio's timeout.
        _deadline = asyncio.get_running_loop().time() + 24.0
        for i, url in enumerate(urls):
            remaining = _deadline - asyncio.get_running_loop().time()
            if remaining < 3:
                blocks.append({"type": "text", "text": f"(time budget reached — stopped at {i} of {len(urls)} results)"})
                break
            ts = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{i}"
            cp = f"/workspace/screenshot_{ts}.png"
            try:
                sr = await c.post(f"{BROWSER_URL}/screenshot",
                                  json={"url": url, "path": cp},
                                  timeout=min(15.0, remaining - 2.0))
                data = sr.json()
            except Exception as exc:
                blocks.append({"type": "text", "text": f"Failed to screenshot {url}: {exc}"})
                continue
            err = data.get("error", "")
            s_image_urls = data.get("image_urls", [])
            container_path = data.get("path", cp)
            filename = os.path.basename(container_path)
            host_path = f"/docker/human_browser/workspace/{filename}"
            local_path = os.path.join(BROWSER_WORKSPACE, filename)
            page_title = data.get("title", "") or url
            summary = f"{page_title}\n{url}\nFile: {host_path}"
            if os.path.isfile(local_path):
                try:
                    await c.post(f"{DATABASE_URL}/images/store", json={
                        "url": url,
                        "host_path": host_path,
                        "alt_text": f"Search: '{query}' — {page_title}",
                    })
                except Exception:
                    pass
                blocks.extend(_image_blocks(container_path, summary))
            else:
                # Screenshot failed — try image_urls fallback
                fetched = False
                for img_url in s_image_urls[:3]:
                    try:
                        ir = await c.get(img_url, headers=img_hdrs,
                                         follow_redirects=True, timeout=15)
                        if ir.status_code == 200:
                            ct = ir.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                            b64 = base64.standard_b64encode(ir.content).decode("ascii")
                            fb_summary = f"{page_title}\n{url}\n(screenshot blocked — showing page image)"
                            blocks.extend([
                                {"type": "text", "text": fb_summary},
                                {"type": "image", "data": b64, "mimeType": ct},
                            ])
                            fetched = True
                            break
                    except Exception:
                        continue
                if not fetched:
                    blocks.append({"type": "text", "text": f"Failed: {url} — {err or 'screenshot unavailable'}"})
        return blocks


# ---------------------------------------------------------------------------
# db_list_images
# ---------------------------------------------------------------------------

async def _db_list_images(args: dict[str, Any]) -> list[dict[str, Any]]:
    limit = int(args.get("limit", 20))
    async with get_client() as c:
        r = await c.get(f"{DATABASE_URL}/images/list", params={"limit": limit})
        if r.status_code >= 400:
            return _text(f"db_list_images: upstream returned {r.status_code}")
        data = r.json()
        images = data.get("images", [])
        if not images:
            return _text("No screenshots stored yet.")
        lines = [f"Stored screenshots ({len(images)}):"]
        for img in images:
            hp = img.get("host_path") or img.get("url", "")
            alt = img.get("alt_text", "")
            ts = img.get("stored_at", "")[:19].replace("T", " ")
            lines.append(f"  {hp}" + (f"  [{alt}]" if alt else "") + (f"  {ts}" if ts else ""))
        # Inline the most recent image — derive container path from host_path basename
        hp0 = images[0].get("host_path", "") or ""
        most_recent = f"/workspace/{os.path.basename(hp0)}" if hp0 else ""
        return _image_blocks(most_recent, "\n".join(lines))


# ---------------------------------------------------------------------------
# browser  (multi-action handler)
# ---------------------------------------------------------------------------

async def _browser(args: dict[str, Any]) -> list[dict[str, Any]]:
    action = str(args.get("action", "")).strip()
    if not action:
        return _text("browser: 'action' is required")
    async with get_client() as c:
        if action == "navigate":
            url = str(args.get("url", "")).strip()
            if not url:
                return _text("browser navigate: 'url' is required")
            try:
                nav_r = await asyncio.wait_for(
                    c.post(f"{BROWSER_URL}/navigate", json={"url": url}),
                    timeout=20.0,
                )
                data = nav_r.json()
                content = data.get("content", "")
                title = data.get("title", "")
                final_url = data.get("url", url)
                header = f"Title: {title}\nURL: {final_url}\n\n" if title else ""
                return _text((header + content)[:8000])
            except Exception as exc:
                return _text(f"browser navigate failed: {exc}")
        if action == "read":
            try:
                read_r = await c.get(f"{BROWSER_URL}/read", timeout=10.0)
                data = read_r.json()
                content = data.get("content", "")
                title = data.get("title", "")
                header = f"Title: {title}\n\n" if title else ""
                return _text((header + content)[:8000])
            except Exception as exc:
                return _text(f"browser read failed: {exc}")
        if action in {"click", "left_click", "right_click"}:
            selector = str(args.get("selector", "")).strip()
            if not selector:
                return _text("browser click: 'selector' is required")
            try:
                button = str(args.get("button", "left")).strip().lower() or "left"
                if action == "left_click":
                    button = "left"
                if action == "right_click":
                    button = "right"
                try:
                    click_count = int(args.get("click_count", 1))
                except (TypeError, ValueError):
                    click_count = 1
                click_r = await c.post(
                    f"{BROWSER_URL}/click",
                    json={
                        "selector": selector,
                        "button": button,
                        "click_count": click_count,
                    },
                    timeout=10.0,
                )
                data = click_r.json()
                return _text(data.get("content", "Clicked."))
            except Exception as exc:
                return _text(f"browser click failed: {exc}")
        if action == "scroll":
            direction = str(args.get("direction", "down")).strip().lower() or "down"
            try:
                amount = int(args.get("amount", 800))
            except (TypeError, ValueError):
                amount = 800
            behavior = str(args.get("behavior", "instant")).strip().lower() or "instant"
            try:
                scroll_r = await c.post(
                    f"{BROWSER_URL}/scroll",
                    json={"direction": direction, "amount": amount, "behavior": behavior},
                    timeout=10.0,
                )
                data = scroll_r.json()
                if data.get("error"):
                    return _text(f"browser scroll failed: {data.get('error')}")
                return _text(
                    "Scrolled page.\n"
                    f"Direction: {data.get('direction', direction)}  "
                    f"Amount: {data.get('amount', amount)}  "
                    f"Behavior: {data.get('behavior', behavior)}\n"
                    f"Position: x={data.get('scroll_x', 0)} y={data.get('scroll_y', 0)}"
                )
            except Exception as exc:
                return _text(f"browser scroll failed: {exc}")
        if action == "fill":
            selector = str(args.get("selector", "")).strip()
            value = str(args.get("value", ""))
            if not selector:
                return _text("browser fill: 'selector' is required")
            try:
                fill_r = await c.post(
                    f"{BROWSER_URL}/fill",
                    json={"selector": selector, "value": value},
                    timeout=10.0,
                )
                data = fill_r.json()
                return _text(data.get("content", "Filled."))
            except Exception as exc:
                return _text(f"browser fill failed: {exc}")
        if action == "eval":
            code = str(args.get("code", "")).strip()
            if not code:
                return _text("browser eval: 'code' is required")
            try:
                eval_r = await c.post(
                    f"{BROWSER_URL}/eval", json={"code": code}, timeout=10.0
                )
                data = eval_r.json()
                return _text(str(data.get("result", "")))
            except Exception as exc:
                return _text(f"browser eval failed: {exc}")
        if action == "screenshot_element":
            selector = str(args.get("selector", "")).strip()
            if not selector:
                return _text("browser screenshot_element: 'selector' is required")
            pad = int(args.get("pad", 20))
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            container_path = f"/workspace/element_{ts}.png"
            try:
                el_r = await c.post(
                    f"{BROWSER_URL}/screenshot_element",
                    json={"selector": selector, "path": container_path, "pad": pad},
                    timeout=20.0,
                )
                data = el_r.json()
            except Exception as exc:
                return _text(f"browser screenshot_element failed: {exc}")
            err = data.get("error", "")
            if err:
                return _text(f"browser screenshot_element: {err}")
            saved_path = data.get("path", container_path)
            filename = os.path.basename(saved_path)
            local_path = os.path.join(BROWSER_WORKSPACE, filename)
            host_path = f"/docker/human_browser/workspace/{filename}"
            bbox = data.get("bbox", {})
            bbox_note = (
                f"  bbox: x={bbox.get('x',0):.0f}, y={bbox.get('y',0):.0f}, "
                f"w={bbox.get('width',0):.0f}, h={bbox.get('height',0):.0f}"
                if bbox else ""
            )
            summary = (
                f"Element screenshot: {selector}\n"
                f"File: {host_path}{bbox_note}"
            )
            if os.path.isfile(local_path):
                return _image_blocks(saved_path, summary)
            return _text(f"screenshot_element: file not found at {local_path}")
        if action == "list_images_detail":
            try:
                imgs_r = await c.get(f"{BROWSER_URL}/images", timeout=10.0)
                imgs_data = imgs_r.json()
            except Exception as exc:
                return _text(f"browser list_images_detail failed: {exc}")
            images = imgs_data.get("images", imgs_data) if isinstance(imgs_data, dict) else imgs_data
            if not images:
                return _text("No images found on the current page.")
            lines = ["Images on current page:"]
            for img in images:
                idx  = img.get("index", "?")
                src  = img.get("src", "")
                alt  = img.get("alt", "")
                rw   = img.get("rendered_w", 0)
                rh   = img.get("rendered_h", 0)
                nw   = img.get("natural_w", 0)
                nh   = img.get("natural_h", 0)
                vis  = "✓" if img.get("visible") else "✗"
                vp   = "in-viewport" if img.get("in_viewport") else "off-screen"
                dim  = f"{rw}×{rh}px rendered" + (f" ({nw}×{nh} natural)" if nw else "")
                lines.append(
                    f"  [{idx}] {vis} {vp}  {dim}\n"
                    f"       src: {src[:120]}\n"
                    f"       alt: {alt[:80]}"
                )
            return _text("\n".join(lines))
        if action == "save_images":
            raw_urls = args.get("urls", [])
            if isinstance(raw_urls, str):
                raw_urls = [u.strip() for u in raw_urls.split(",") if u.strip()]
            if not raw_urls:
                return _text("browser save_images: 'urls' is required")
            prefix = str(args.get("prefix", "image")).strip() or "image"
            try:
                max_imgs = int(args.get("max", 20))
            except (TypeError, ValueError):
                max_imgs = 20
            try:
                sav_r = await c.post(
                    f"{BROWSER_URL}/save_images",
                    json={"urls": raw_urls, "prefix": prefix, "max": max_imgs},
                    timeout=120.0,
                )
                data = sav_r.json()
            except Exception as exc:
                return _text(f"browser save_images failed: {exc}")
            if data.get("error"):
                return _text(f"browser save_images failed: {data.get('error')}")
            saved = data.get("saved", [])
            errors = data.get("errors", [])
            if not saved and errors:
                errs = "; ".join(e.get("error", "?") for e in errors[:3])
                return _text(f"browser save_images: all downloads failed — {errs}")
            blocks: list = []
            lines = [f"Downloaded {len(saved)} image(s)" +
                     (f"  ({len(errors)} failed)" if errors else "")]
            for item in saved:
                p = item.get("path", "")
                fname = os.path.basename(p)
                hp = f"/docker/human_browser/workspace/{fname}" if fname else ""
                size_kb = item.get("size", 0) // 1024
                lines.append(f"  [{item.get('index','?')}] {fname}  {size_kb} KB")
                if hp and os.path.isfile(os.path.join(BROWSER_WORKSPACE, fname)) and len(blocks) < 10:
                    blocks.extend(_image_blocks(p, fname))
            blocks.insert(0, _text("\n".join(lines))[0])
            return blocks
        if action == "download_page_images":
            url = str(args.get("url", "")).strip()
            if url:
                try:
                    nav_r = await c.post(f"{BROWSER_URL}/navigate", json={"url": url}, timeout=20.0)
                    nav_data = nav_r.json()
                    if nav_data.get("error"):
                        return _text(f"browser download_page_images: navigate failed — {nav_data['error']}")
                except Exception as exc:
                    return _text(f"browser download_page_images: navigate failed — {exc}")
            prefix = str(args.get("prefix", "image")).strip() or "image"
            try:
                max_imgs = int(args.get("max", 20))
            except (TypeError, ValueError):
                max_imgs = 20
            filter_q = str(args.get("filter", "")).strip() or None
            payload: dict[str, Any] = {"prefix": prefix, "max": max_imgs}
            if filter_q:
                payload["filter"] = filter_q
            try:
                dl_r = await c.post(
                    f"{BROWSER_URL}/download_page_images",
                    json=payload,
                    timeout=120.0,
                )
                data = dl_r.json()
            except Exception as exc:
                return _text(f"browser download_page_images failed: {exc}")
            if data.get("error"):
                return _text(f"browser download_page_images failed: {data.get('error')}")
            saved = data.get("saved", [])
            errors = data.get("errors", [])
            applied_filter = data.get("filter")
            filter_note = f" (filter: '{applied_filter}')" if applied_filter else ""
            if not saved:
                msg = f"No images downloaded{filter_note}"
                if errors:
                    errs = "; ".join(e.get("error", "?") for e in errors[:3])
                    msg += f" — {errs}"
                return _text(msg)
            blocks = []
            lines = [f"Downloaded {len(saved)} image(s){filter_note}" +
                     (f"  ({len(errors)} failed)" if errors else "")]
            for item in saved:
                p = item.get("path", "")
                fname = os.path.basename(p)
                size_kb = item.get("size", 0) // 1024
                lines.append(f"  [{item.get('index','?')}] {fname}  {size_kb} KB")
                if fname and os.path.isfile(os.path.join(BROWSER_WORKSPACE, fname)) and len(blocks) < 10:
                    blocks.extend(_image_blocks(p, fname))
            blocks.insert(0, _text("\n".join(lines))[0])
            return blocks
        return _text(f"browser: unknown action '{action}'")


# ---------------------------------------------------------------------------
# page_extract
# ---------------------------------------------------------------------------

async def _page_extract(args: dict[str, Any]) -> list[dict[str, Any]]:
    include = list(args.get("include") or ["links", "headings", "tables", "images", "meta", "text"])
    max_links = max(1, int(args.get("max_links", 50)))
    max_text  = max(100, int(args.get("max_text", 3000)))
    js = (
        "(function(){"
        "var out={};"
        "var inc=" + json.dumps(include) + ";"
        "function has(k){return inc.indexOf(k)!==-1;}"
        "if(has('meta')){"
        " var m={};document.querySelectorAll('meta[name],meta[property]').forEach(function(el){"
        "  var k=el.getAttribute('name')||el.getAttribute('property');"
        "  if(k)m[k]=el.getAttribute('content')||'';"
        " });out.meta=m;out.title=document.title;}"
        "if(has('headings')){"
        " out.headings=Array.from(document.querySelectorAll('h1,h2,h3,h4')).slice(0,50).map(function(h){"
        "  return{tag:h.tagName,text:h.innerText.trim()};});}"
        "if(has('links')){"
        " out.links=Array.from(document.querySelectorAll('a[href]')).slice(0," + str(max_links) + ").map(function(a){"
        "  return{text:a.innerText.trim().slice(0,120),href:a.href};});}"
        "if(has('images')){"
        " out.images=Array.from(document.querySelectorAll('img[src]')).slice(0,30).map(function(i){"
        "  return{src:i.src,alt:i.alt||''};});}"
        "if(has('tables')){"
        " out.tables=Array.from(document.querySelectorAll('table')).slice(0,5).map(function(tbl){"
        "  return Array.from(tbl.querySelectorAll('tr')).slice(0,20).map(function(tr){"
        "   return Array.from(tr.querySelectorAll('th,td')).map(function(td){return td.innerText.trim();});});});}"
        "if(has('text')){"
        " out.text=(document.body?document.body.innerText:'').slice(0," + str(max_text) + ");}"
        "return JSON.stringify(out);})()"
    )
    async with get_client() as c:
        try:
            eval_r = await c.post(f"{BROWSER_URL}/eval", json={"code": js}, timeout=15.0)
            raw = eval_r.json().get("result", "{}")
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            lines: list[str] = []
            if data.get("title"):
                lines.append(f"Title: {data.get('title')}")
            for k, v in list((data.get("meta") or {}).items())[:10]:
                lines.append(f"  meta[{k}]: {str(v)[:120]}")
            if "headings" in data:
                lines.append(f"\nHeadings ({len(data['headings'])}):")
                for hd in data["headings"]:
                    lines.append(f"  {hd.get('tag','?')}: {str(hd.get('text',''))[:120]}")
            if "links" in data:
                lines.append(f"\nLinks ({len(data['links'])}):")
                for lk in data["links"]:
                    lines.append(f"  [{str(lk.get('text',''))[:60]}] → {str(lk.get('href',''))[:120]}")
            if "images" in data:
                lines.append(f"\nImages ({len(data['images'])}):")
                for im in data["images"][:10]:
                    lines.append(f"  {str(im.get('src',''))[:100]}  alt='{str(im.get('alt',''))[:60]}'")
            if "tables" in data:
                lines.append(f"\nTables ({len(data['tables'])}):")
                for ti, tbl in enumerate(data["tables"]):
                    lines.append(f"  Table {ti+1} ({len(tbl)} rows):")
                    for row in tbl[:5]:
                        lines.append("    | " + " | ".join(str(cell)[:30] for cell in row))
            if "text" in data:
                lines.append(f"\nText excerpt:\n{data['text'][:1500]}")
            return _text("\n".join(lines) or "No data extracted.")
        except Exception as exc:
            return _text(f"page_extract failed: {exc}")


# ---------------------------------------------------------------------------
# page_scrape
# ---------------------------------------------------------------------------

async def _page_scrape(args: dict[str, Any]) -> list[dict[str, Any]]:
    url         = str(args.get("url", "")).strip()
    max_scrolls = max(1, min(int(args.get("max_scrolls", 10)), 30))
    wait_ms     = max(100, min(int(args.get("wait_ms", 500)), 3000))
    max_chars   = max(500, min(int(args.get("max_chars", 16000)), 64000))
    include_links = bool(args.get("include_links", False))
    payload: dict = {
        "max_scrolls": max_scrolls,
        "wait_ms":     wait_ms,
        "max_chars":   max_chars,
        "include_links": include_links,
    }
    if url:
        payload["url"] = url
    async with get_client() as c:
        try:
            timeout_s = max_scrolls * (wait_ms / 1000.0) + 30.0
            scrape_r = await asyncio.wait_for(
                c.post(f"{BROWSER_URL}/scrape", json=payload),
                timeout=timeout_s,
            )
            data = scrape_r.json()
        except Exception as exc:
            return _text(f"page_scrape: browser unreachable — {exc}")
        if data.get("error"):
            return _text(f"page_scrape error: {data['error']}")
        title   = data.get("title", "")
        content = data.get("content", "")
        final_url = data.get("url", url)
        steps   = data.get("scroll_steps", 0)
        grew    = data.get("content_grew_on_scroll", False)
        height  = data.get("final_page_height", 0)
        chars   = data.get("char_count", len(content))
        header = (
            f"Title: {title}\nURL: {final_url}\n"
            f"Scrolled: {steps} steps | Page height: {height}px"
            + (" | lazy content grew" if grew else "")
            + f" | {chars} chars extracted\n\n"
        )
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        body = "\n".join(lines)
        result_text = header + body
        if include_links and data.get("links"):
            link_lines = [f"[{lk.get('text','')[:60]}] → {lk.get('href','')[:120]}"
                          for lk in data["links"][:100]]
            result_text += f"\n\nLinks ({len(data['links'])}):\n" + "\n".join(link_lines)
        return _text(result_text)


# ---------------------------------------------------------------------------
# page_images
# ---------------------------------------------------------------------------

async def _page_images(args: dict[str, Any]) -> list[dict[str, Any]]:
    url         = str(args.get("url", "")).strip()
    scroll      = bool(args.get("scroll", True))
    max_scrolls = max(1, min(int(args.get("max_scrolls", 3)), 20))
    if not url:
        return _text("page_images: 'url' is required")
    payload = {"url": url, "scroll": scroll, "max_scrolls": max_scrolls}
    timeout_s = max_scrolls * 2.0 + 30.0
    async with get_client() as c:
        try:
            pi_r = await asyncio.wait_for(
                c.post(f"{BROWSER_URL}/page_images", json=payload),
                timeout=timeout_s,
            )
            data = pi_r.json()
        except Exception as exc:
            return _text(f"page_images: browser unreachable — {exc}")
        if data.get("error"):
            return _text(f"page_images error: {data['error']}")
        imgs   = data.get("images", [])
        count  = data.get("count", len(imgs))
        title  = data.get("title", "")
        final_url = data.get("url", url)
        lines = [f"Found {count} images on: {final_url}  ({title})"]
        for img in imgs:
            line = f"[{img.get('type','?')}] {img.get('url','')}"
            if img.get("alt"):
                line += f"  alt={img.get('alt','')!r}"
            if img.get("natural_w"):
                line += f"  {img.get('natural_w',0)}×{img.get('natural_h',0)}"
            elif img.get("srcset_width"):
                line += f"  {img.get('srcset_width','')}w"
            lines.append(line)
        return _text("\n".join(lines))


# ---------------------------------------------------------------------------
# bulk_screenshot
# ---------------------------------------------------------------------------

async def _bulk_screenshot(args: dict[str, Any]) -> list[dict[str, Any]]:
    urls = [str(u).strip() for u in args.get("urls", []) if str(u).strip()]
    if not urls:
        return _text("bulk_screenshot: 'urls' list is required")
    urls = urls[:6]

    async with get_client() as c:
        async def _single_shot(shot_url: str, idx: int) -> list[dict[str, Any]]:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            container_path = f"/workspace/bulk_{idx}_{ts}.png"
            try:
                shot_r = await c.post(
                    f"{BROWSER_URL}/screenshot",
                    json={"url": shot_url, "path": container_path},
                    timeout=25.0,
                )
                shot_data = shot_r.json()
                cp    = shot_data.get("path", container_path)
                title = shot_data.get("title", shot_url)
                fname = os.path.basename(cp)
                lp    = os.path.join(BROWSER_WORKSPACE, fname)
                summary = f"[{idx+1}/{len(urls)}] {title}\n{shot_url}\nFile: {fname}"
                if os.path.isfile(lp):
                    return _image_blocks(cp, summary)
                return _text(f"[{idx+1}] {shot_url} — screenshot missing: {shot_data.get('error', 'unknown')}")
            except Exception as exc:
                return _text(f"[{idx+1}] {shot_url} — failed: {exc}")

        tasks = [_single_shot(u, i) for i, u in enumerate(urls)]
        results = await asyncio.gather(*tasks)
        combined: list[dict[str, Any]] = []
        for blocks in results:
            combined.extend(blocks)
        return combined


# ---------------------------------------------------------------------------
# scroll_screenshot
# ---------------------------------------------------------------------------

async def _scroll_screenshot(args: dict[str, Any]) -> list[dict[str, Any]]:
    if not _HAS_PIL:
        return _text("scroll_screenshot: Pillow is not installed in this container.")
    url = str(args.get("url", "")).strip() or None
    max_scrolls = max(1, min(int(args.get("max_scrolls", 5)), 10))
    overlap     = max(0, int(args.get("scroll_overlap", 100)))
    async with get_client() as c:
        if url:
            try:
                await asyncio.wait_for(
                    c.post(f"{BROWSER_URL}/navigate", json={"url": url}),
                    timeout=20.0,
                )
            except Exception as exc:
                return _text(f"scroll_screenshot: navigate failed: {exc}")
        try:
            h_r  = await c.post(f"{BROWSER_URL}/eval",
                                json={"code": "document.documentElement.scrollHeight"},
                                timeout=5.0)
            page_h = int(h_r.json().get("result", 0) or 0)
            vp_r = await c.post(f"{BROWSER_URL}/eval",
                                json={"code": "window.innerHeight"},
                                timeout=5.0)
            vp_h = int(vp_r.json().get("result", 800) or 800)
        except Exception:
            page_h, vp_h = 0, 800
        step = max(vp_h - overlap, 100)
        frames: list["_PilImage.Image"] = []
        ts_base = datetime.now().strftime("%Y%m%d_%H%M%S")
        for i in range(max_scrolls):
            scroll_y = i * step
            if page_h > 0 and scroll_y >= page_h:
                break
            try:
                await c.post(f"{BROWSER_URL}/eval",
                             json={"code": f"window.scrollTo(0, {scroll_y})"},
                             timeout=5.0)
                await asyncio.sleep(0.3)
                container_path = f"/workspace/scroll_{i}_{ts_base}.png"
                shot_r = await c.post(f"{BROWSER_URL}/screenshot",
                                      json={"path": container_path},
                                      timeout=15.0)
                cp    = shot_r.json().get("path", container_path)
                fname = os.path.basename(cp)
                lp    = os.path.join(BROWSER_WORKSPACE, fname)
                if os.path.isfile(lp):
                    with _PilImage.open(lp) as fr:
                        frames.append(_ImageOps.exif_transpose(fr).convert("RGB").copy())
            except Exception:
                break
        if not frames:
            return _text("scroll_screenshot: no frames captured.")
        total_h = sum(f.height for f in frames)
        max_w   = max(f.width  for f in frames)
        canvas  = _PilImage.new("RGB", (max_w, total_h), (255, 255, 255))
        y = 0
        for fr in frames:
            canvas.paste(fr, (0, y))
            y += fr.height
        summary = (
            f"Full-page scroll screenshot: {len(frames)} frames stitched\n"
            f"Canvas: {canvas.width}×{canvas.height}"
            + (f"  URL: {url}" if url else "")
        )
        return _pil_to_blocks(canvas, summary, quality=85, save_prefix="fullpage")


# ---------------------------------------------------------------------------
# browser_save_images
# ---------------------------------------------------------------------------

async def _browser_save_images(args: dict[str, Any]) -> list[dict[str, Any]]:
    raw_urls = args.get("urls", [])
    if isinstance(raw_urls, str):
        raw_urls = [u.strip() for u in raw_urls.split(",") if u.strip()]
    if not raw_urls:
        return _text("browser_save_images: 'urls' is required (list or comma-separated string)")
    prefix = str(args.get("prefix", "image")).strip() or "image"
    max_imgs = int(args.get("max", 20))
    async with get_client() as c:
        try:
            save_r = await c.post(
                f"{BROWSER_URL}/save_images",
                json={"urls": raw_urls, "prefix": prefix, "max": max_imgs},
                timeout=120.0,
            )
            save_data = save_r.json()
        except Exception as exc:
            return _text(f"browser_save_images: browser unreachable — {exc}")
        saved = save_data.get("saved", [])
        errors = save_data.get("errors", [])
        if not saved and errors:
            errs = "; ".join(e.get("error", "?") for e in errors[:3])
            return _text(f"browser_save_images: all downloads failed — {errs}")
        blocks: list = []
        lines = [f"Downloaded {len(saved)} image(s)" +
                 (f"  ({len(errors)} failed)" if errors else "")]
        for item in saved:
            fname = os.path.basename(item.get("path", ""))
            host_path = f"/docker/human_browser/workspace/{fname}"
            local_path = os.path.join(BROWSER_WORKSPACE, fname)
            size_kb = item.get("size", 0) // 1024
            lines.append(f"  [{item.get('index','?')}] {fname}  {size_kb} KB  {item.get('url','')[:80]}")
            if os.path.isfile(local_path) and len(blocks) < 6:
                blocks.extend(_image_blocks(item["path"], f"[{item.get('index','?')}] {fname}"))
        blocks.insert(0, {"type": "text", "text": "\n".join(lines)})
        return blocks


# ---------------------------------------------------------------------------
# browser_download_page_images
# ---------------------------------------------------------------------------

async def _browser_download_page_images(args: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(args.get("url", "")).strip() or None
    async with get_client() as c:
        if url:
            try:
                await c.post(f"{BROWSER_URL}/navigate", json={"url": url}, timeout=20.0)
            except Exception as exc:
                return _text(f"browser_download_page_images: navigate failed — {exc}")
        filter_q = str(args.get("filter", "")).strip() or None
        prefix = str(args.get("prefix", "image")).strip() or "image"
        max_imgs = int(args.get("max", 20))
        payload: dict = {"max": max_imgs, "prefix": prefix}
        if filter_q:
            payload["filter"] = filter_q
        try:
            dl_r = await c.post(
                f"{BROWSER_URL}/download_page_images",
                json=payload,
                timeout=120.0,
            )
            dl_data = dl_r.json()
        except Exception as exc:
            return _text(f"browser_download_page_images: browser unreachable — {exc}")
        saved = dl_data.get("saved", [])
        errors = dl_data.get("errors", [])
        applied_filter = dl_data.get("filter")
        filter_note = f" (filter: '{applied_filter}')" if applied_filter else ""
        if not saved and errors:
            errs = "; ".join(e.get("error", "?") for e in errors[:3])
            return _text(f"browser_download_page_images: all downloads failed{filter_note} — {errs}")
        if not saved:
            return _text(f"browser_download_page_images: no images found on page{filter_note}")
        blocks = []
        lines = [
            f"Downloaded {len(saved)} image(s){filter_note}" +
            (f"  ({len(errors)} failed)" if errors else "")
        ]
        for item in saved:
            fname = os.path.basename(item.get("path", ""))
            local_path = os.path.join(BROWSER_WORKSPACE, fname)
            size_kb = item.get("size", 0) // 1024
            lines.append(f"  [{item.get('index','?')}] {fname}  {size_kb} KB  {item.get('url','')[:80]}")
            if os.path.isfile(local_path) and len(blocks) < 9:
                blocks.extend(_image_blocks(item["path"], f"[{item.get('index','?')}] {fname}"))
        blocks.insert(0, {"type": "text", "text": "\n".join(lines)})
        return blocks


# ---------------------------------------------------------------------------
# video_thumbnail
# ---------------------------------------------------------------------------

async def _video_thumbnail(args: dict[str, Any]) -> list[dict[str, Any]]:
    url_vt  = str(args.get("url", "")).strip()
    ts_vt   = float(args.get("timestamp_sec", 0.0))
    if not url_vt:
        return _text("video_thumbnail: 'url' is required")
    async with get_client(timeout=90) as c:
        try:
            r_vt = await c.post(
                f"{VIDEO_URL}/thumbnail",
                json={"url": url_vt, "timestamp_sec": ts_vt},
                timeout=90,
            )
            r_vt.raise_for_status()
            d_vt = r_vt.json()
        except Exception as exc:
            return _text(f"video_thumbnail: failed — {exc}")
        b64_vt = d_vt.get("b64", "")
        if not b64_vt:
            return _text("video_thumbnail: no image returned")
        raw_vt = base64.b64decode(b64_vt)
        summary_vt = (f"Video thumbnail at {ts_vt}s — "
                      f"{d_vt.get('width')}×{d_vt.get('height')} from {url_vt}")
        return renderer.encode_url_bytes(raw_vt, "image/png", summary_vt)


# ---------------------------------------------------------------------------
# browser_navigate
# ---------------------------------------------------------------------------

async def _browser_navigate(args: dict[str, Any]) -> list[dict[str, Any]]:
    bn_url = str(args.get("url", "")).strip()
    if not bn_url:
        return _text("browser_navigate: 'url' is required")
    async with get_client() as c:
        try:
            bn_payload: dict[str, Any] = {"url": bn_url}
            if args.get("wait_until"):
                bn_payload["wait_until"] = args["wait_until"]
            br = await c.post(f"{BROWSER_AUTO_URL}/navigate", json=bn_payload, timeout=45)
            br.raise_for_status()
            bd = br.json()
            blocks_bn: list[dict] = [{"type": "text", "text": f"Navigated to: {bd.get('url')}\nTitle: {bd.get('title')}"}]
            if bd.get("screenshot_b64"):
                blocks_bn.append({"type": "image", "data": bd["screenshot_b64"], "mimeType": "image/png"})
            return blocks_bn
        except Exception as e_bn:
            return _text(f"browser_navigate: {e_bn}")


# ---------------------------------------------------------------------------
# browser_click
# ---------------------------------------------------------------------------

async def _browser_click(args: dict[str, Any]) -> list[dict[str, Any]]:
    async with get_client() as c:
        try:
            bc_payload: dict[str, Any] = {}
            for k_bc in ("selector", "x", "y", "button", "click_count"):
                if args.get(k_bc) is not None:
                    bc_payload[k_bc] = args[k_bc]
            br = await c.post(f"{BROWSER_AUTO_URL}/click", json=bc_payload, timeout=20)
            br.raise_for_status()
            bd = br.json()
            blocks_bc: list[dict] = [{"type": "text", "text": f"Clicked. URL: {bd.get('url')}"}]
            if bd.get("screenshot_b64"):
                blocks_bc.append({"type": "image", "data": bd["screenshot_b64"], "mimeType": "image/png"})
            return blocks_bc
        except Exception as e_bc:
            return _text(f"browser_click: {e_bc}")


# ---------------------------------------------------------------------------
# browser_type
# ---------------------------------------------------------------------------

async def _browser_type(args: dict[str, Any]) -> list[dict[str, Any]]:
    bt_text = str(args.get("text", ""))
    async with get_client() as c:
        try:
            bt_payload: dict[str, Any] = {"text": bt_text}
            if args.get("selector"):
                bt_payload["selector"] = args["selector"]
            if args.get("clear_first"):
                bt_payload["clear_first"] = True
            br = await c.post(f"{BROWSER_AUTO_URL}/type", json=bt_payload, timeout=20)
            br.raise_for_status()
            bd = br.json()
            blocks_bt: list[dict] = [{"type": "text", "text": f"Typed {bd.get('text_length', 0)} chars"}]
            if bd.get("screenshot_b64"):
                blocks_bt.append({"type": "image", "data": bd["screenshot_b64"], "mimeType": "image/png"})
            return blocks_bt
        except Exception as e_bt:
            return _text(f"browser_type: {e_bt}")


# ---------------------------------------------------------------------------
# browser_screenshot_page
# ---------------------------------------------------------------------------

async def _browser_screenshot_page(args: dict[str, Any]) -> list[dict[str, Any]]:
    async with get_client() as c:
        try:
            bs_payload: dict[str, Any] = {}
            if args.get("full_page"):
                bs_payload["full_page"] = True
            if args.get("selector"):
                bs_payload["selector"] = args["selector"]
            br = await c.post(f"{BROWSER_AUTO_URL}/screenshot", json=bs_payload, timeout=15)
            br.raise_for_status()
            bd = br.json()
            blocks_bs: list[dict] = [{"type": "text", "text": f"Screenshot of {bd.get('url')}\nTitle: {bd.get('title')}"}]
            if bd.get("screenshot_b64"):
                blocks_bs.append({"type": "image", "data": bd["screenshot_b64"], "mimeType": "image/png"})
            return blocks_bs
        except Exception as e_bs:
            return _text(f"browser_screenshot_page: {e_bs}")


# ---------------------------------------------------------------------------
# browser_extract
# ---------------------------------------------------------------------------

async def _browser_extract(args: dict[str, Any]) -> list[dict[str, Any]]:
    be_what = str(args.get("what", "text")).strip()
    async with get_client() as c:
        try:
            br = await c.post(f"{BROWSER_AUTO_URL}/extract", json={"what": be_what}, timeout=15)
            br.raise_for_status()
            bd = br.json()
            if be_what == "text":
                return _text(f"Page text from {bd.get('url')}:\n\n{bd.get('text', '')[:10000]}")
            elif be_what == "links":
                links_str = "\n".join(f"- [{l.get('text', '')}]({l.get('href', '')})" for l in bd.get("links", [])[:50])
                return _text(f"Links ({bd.get('count', 0)}) from {bd.get('url')}:\n{links_str}")
            else:
                return _text(json.dumps(bd, indent=2)[:10000])
        except Exception as e_be:
            return _text(f"browser_extract: {e_be}")


# ---------------------------------------------------------------------------
# browser_keyboard
# ---------------------------------------------------------------------------

async def _browser_keyboard(args: dict[str, Any]) -> list[dict[str, Any]]:
    bk_key = str(args.get("key", "")).strip()
    if not bk_key:
        return _text("browser_keyboard: 'key' is required")
    async with get_client() as c:
        try:
            br = await c.post(f"{BROWSER_AUTO_URL}/keyboard", json={"key": bk_key}, timeout=10)
            br.raise_for_status()
            bd = br.json()
            blocks_bk: list[dict] = [{"type": "text", "text": f"Pressed {bk_key}"}]
            if bd.get("screenshot_b64"):
                blocks_bk.append({"type": "image", "data": bd["screenshot_b64"], "mimeType": "image/png"})
            return blocks_bk
        except Exception as e_bk:
            return _text(f"browser_keyboard: {e_bk}")


# ---------------------------------------------------------------------------
# browser_fill_form
# ---------------------------------------------------------------------------

async def _browser_fill_form(args: dict[str, Any]) -> list[dict[str, Any]]:
    bf_fields = args.get("fields", [])
    if not bf_fields:
        return _text("browser_fill_form: 'fields' array is required")
    async with get_client() as c:
        try:
            br = await c.post(f"{BROWSER_AUTO_URL}/fill_form", json={"fields": bf_fields}, timeout=20)
            br.raise_for_status()
            bd = br.json()
            blocks_bf: list[dict] = [{"type": "text", "text": f"Filled {bd.get('filled', 0)}/{bd.get('total', 0)} fields"}]
            if bd.get("screenshot_b64"):
                blocks_bf.append({"type": "image", "data": bd["screenshot_b64"], "mimeType": "image/png"})
            return blocks_bf
        except Exception as e_bf:
            return _text(f"browser_fill_form: {e_bf}")


# ---------------------------------------------------------------------------
# browser_scroll
# ---------------------------------------------------------------------------

async def _browser_scroll(args: dict[str, Any]) -> list[dict[str, Any]]:
    async with get_client() as c:
        try:
            bsc_payload: dict[str, Any] = {}
            for k_bsc in ("direction", "amount", "selector"):
                if args.get(k_bsc) is not None:
                    bsc_payload[k_bsc] = args[k_bsc]
            br = await c.post(f"{BROWSER_AUTO_URL}/scroll", json=bsc_payload, timeout=10)
            br.raise_for_status()
            bd = br.json()
            blocks_bsc: list[dict] = [{"type": "text", "text": "Scrolled"}]
            if bd.get("screenshot_b64"):
                blocks_bsc.append({"type": "image", "data": bd["screenshot_b64"], "mimeType": "image/png"})
            return blocks_bsc
        except Exception as e_bsc:
            return _text(f"browser_scroll: {e_bsc}")


# ---------------------------------------------------------------------------
# browser_evaluate
# ---------------------------------------------------------------------------

async def _browser_evaluate(args: dict[str, Any]) -> list[dict[str, Any]]:
    bev_expr = str(args.get("expression", "")).strip()
    if not bev_expr:
        return _text("browser_evaluate: 'expression' is required")
    async with get_client() as c:
        try:
            br = await c.post(f"{BROWSER_AUTO_URL}/evaluate", json={"expression": bev_expr}, timeout=15)
            br.raise_for_status()
            bd = br.json()
            return _text(f"JS result: {json.dumps(bd.get('result'), indent=2, default=str)[:5000]}")
        except Exception as e_bev:
            return _text(f"browser_evaluate: {e_bev}")


# ---------------------------------------------------------------------------
# Register all handlers
# ---------------------------------------------------------------------------

TOOL_HANDLERS["screenshot"]                    = _screenshot
TOOL_HANDLERS["fetch_image"]                   = _fetch_image
TOOL_HANDLERS["screenshot_search"]             = _screenshot_search
TOOL_HANDLERS["db_list_images"]                = _db_list_images
TOOL_HANDLERS["browser"]                       = _browser
TOOL_HANDLERS["page_extract"]                  = _page_extract
TOOL_HANDLERS["page_scrape"]                   = _page_scrape
TOOL_HANDLERS["page_images"]                   = _page_images
TOOL_HANDLERS["bulk_screenshot"]               = _bulk_screenshot
TOOL_HANDLERS["scroll_screenshot"]             = _scroll_screenshot
TOOL_HANDLERS["browser_save_images"]           = _browser_save_images
TOOL_HANDLERS["browser_download_page_images"]  = _browser_download_page_images
TOOL_HANDLERS["video_thumbnail"]               = _video_thumbnail
TOOL_HANDLERS["browser_navigate"]              = _browser_navigate
TOOL_HANDLERS["browser_click"]                 = _browser_click
TOOL_HANDLERS["browser_type"]                  = _browser_type
TOOL_HANDLERS["browser_screenshot_page"]       = _browser_screenshot_page
TOOL_HANDLERS["browser_extract"]               = _browser_extract
TOOL_HANDLERS["browser_keyboard"]              = _browser_keyboard
TOOL_HANDLERS["browser_fill_form"]             = _browser_fill_form
TOOL_HANDLERS["browser_scroll"]                = _browser_scroll
TOOL_HANDLERS["browser_evaluate"]              = _browser_evaluate
