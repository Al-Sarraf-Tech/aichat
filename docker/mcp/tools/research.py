"""
Research tool handlers — deep_research and realtime.

Extracted from app.py. researchbox_search and researchbox_push were
already extracted to tools/custom_tools.py.

deep_research: multi-hop SearXNG search → page fetch → synthesize.
realtime: live weather / time / stocks / crypto / forex.

Registered with the tool registry at import time via TOOL_HANDLERS.
"""
from __future__ import annotations

import re
from typing import Any

from tools import TOOL_HANDLERS  # type: ignore[import]
from tools._helpers import text as _text, get_client  # type: ignore[import]
from tools._search import SEARXNG_URL  # type: ignore[import]


# ---------------------------------------------------------------------------
# deep_research — multi-hop research: search → fetch → synthesize
# ---------------------------------------------------------------------------

async def _deep_research(args: dict[str, Any]) -> list[dict[str, Any]]:
    question_dr = str(args.get("question", "")).strip()
    depth_dr    = max(1, min(3, int(args.get("depth", 2))))
    max_src_dr  = max(1, min(8, int(args.get("max_sources", 4))))
    if not question_dr:
        return _text("deep_research: 'question' is required")
    if not SEARXNG_URL:
        return _text("deep_research: SEARXNG_URL not configured")

    all_content_dr: list[dict] = []
    search_queries_dr = [question_dr]

    async with get_client(timeout=60) as c:
        for round_dr in range(depth_dr):
            for sq_dr in search_queries_dr[:2]:
                try:
                    sr_dr = await c.get(
                        f"{SEARXNG_URL}/search",
                        params={"q": sq_dr, "format": "json", "categories": "general"},
                        timeout=20,
                    )
                    if sr_dr.status_code != 200:
                        continue
                    results_dr = sr_dr.json().get("results", [])[:max_src_dr]
                except Exception:
                    continue
                for res_dr in results_dr:
                    url_dr   = res_dr.get("url", "")
                    title_dr = res_dr.get("title", "")
                    snip_dr  = res_dr.get("content", "")
                    if not url_dr:
                        continue
                    try:
                        pr_dr = await c.get(url_dr, timeout=12, follow_redirects=True)
                        raw_dr = pr_dr.text
                        try:
                            import trafilatura as _traf_dr
                            text_dr = _traf_dr.extract(raw_dr) or ""
                        except Exception:
                            text_dr = re.sub(r"<[^>]+>", " ", raw_dr)
                            text_dr = re.sub(r"\s+", " ", text_dr).strip()
                        text_dr = text_dr[:4000]
                    except Exception:
                        text_dr = snip_dr
                    if text_dr:
                        all_content_dr.append({
                            "title": title_dr, "url": url_dr,
                            "content": text_dr, "round": round_dr + 1,
                        })
            # Follow-up query: append top keywords from fetched content
            if round_dr < depth_dr - 1 and all_content_dr:
                combined_dr = " ".join(d["content"] for d in all_content_dr[-max_src_dr:])
                words_dr = [w for w in combined_dr.split() if len(w) > 5][:10]
                if words_dr:
                    search_queries_dr = [question_dr + " " + " ".join(words_dr[:5])]

    if not all_content_dr:
        return _text("deep_research: no content retrieved — try a different question")

    lines_dr = [f"# Research Report: {question_dr}\n",
                f"**Sources consulted:** {len(all_content_dr)} | "
                f"**Rounds:** {depth_dr}\n",
                "---\n", "## Source Summaries\n"]
    citations_dr: list[str] = []
    for i_dr, item_dr in enumerate(all_content_dr, 1):
        excerpt_dr = item_dr["content"][:600].rstrip()
        lines_dr.append(f"### [{i_dr}] {item_dr['title']}\n{excerpt_dr}…\n")
        citations_dr.append(f"[{i_dr}] {item_dr['title']} — {item_dr['url']}")
    lines_dr.append("---\n## Citations\n")
    lines_dr.extend(citations_dr)
    return _text("\n".join(lines_dr))


# ---------------------------------------------------------------------------
# realtime — live weather / time / stocks / crypto / forex
# ---------------------------------------------------------------------------

async def _realtime(args: dict[str, Any]) -> list[dict[str, Any]]:
    rt_type  = str(args.get("type", "time")).lower()
    rt_query = str(args.get("query", "UTC")).strip()

    if rt_type == "time":
        from datetime import datetime as _dt_rt
        import zoneinfo as _zi_rt
        try:
            tz_rt = _zi_rt.ZoneInfo(rt_query)
            now_rt = _dt_rt.now(tz_rt)
            return _text(
                f"Current time in {rt_query}:\n"
                f"  {now_rt.strftime('%A, %B %d, %Y  %H:%M:%S %Z')}\n"
                f"  UTC offset: {now_rt.strftime('%z')}"
            )
        except Exception as exc_rt:
            return _text(f"realtime/time: invalid timezone '{rt_query}' — {exc_rt}")

    if rt_type == "weather":
        try:
            async with get_client(timeout=30) as c:
                geo_r = await c.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": rt_query, "count": 1, "language": "en", "format": "json"},
                    timeout=10,
                )
                if geo_r.status_code != 200 or not geo_r.json().get("results"):
                    return _text(f"realtime/weather: city '{rt_query}' not found")
                geo_rt = geo_r.json()["results"][0]
                lat_rt, lon_rt = geo_rt["latitude"], geo_rt["longitude"]
                wm_r = await c.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": lat_rt, "longitude": lon_rt,
                        "current": "temperature_2m,relative_humidity_2m,precipitation,"
                                   "weather_code,wind_speed_10m,apparent_temperature",
                        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                        "timezone": "auto", "forecast_days": 7,
                    },
                    timeout=10,
                )
                if wm_r.status_code != 200:
                    return _text(f"realtime/weather: Open-Meteo error {wm_r.status_code}")
                wm_rt = wm_r.json()
            cur_rt = wm_rt.get("current", {})
            daily_rt = wm_rt.get("daily", {})
            _wmo = {0:"Clear",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",
                    45:"Fog",48:"Icy fog",51:"Light drizzle",53:"Drizzle",55:"Heavy drizzle",
                    61:"Light rain",63:"Rain",65:"Heavy rain",71:"Light snow",73:"Snow",
                    75:"Heavy snow",80:"Showers",81:"Heavy showers",95:"Thunderstorm",
                    96:"Hailstorm",99:"Heavy hailstorm"}
            wcode_rt = int(cur_rt.get("weather_code", 0))
            cond_rt = _wmo.get(wcode_rt, f"Code {wcode_rt}")
            lines_rt = [
                f"**Weather in {geo_rt['name']}, {geo_rt.get('country','')}**",
                f"Condition: {cond_rt}",
                f"Temperature: {cur_rt.get('temperature_2m','?')}°C "
                f"(feels like {cur_rt.get('apparent_temperature','?')}°C)",
                f"Humidity: {cur_rt.get('relative_humidity_2m','?')}%",
                f"Wind: {cur_rt.get('wind_speed_10m','?')} km/h",
                f"Precipitation: {cur_rt.get('precipitation','?')} mm\n",
                "**7-Day Forecast:**",
            ]
            dates_rt = daily_rt.get("time", [])
            tmax_rt  = daily_rt.get("temperature_2m_max", [])
            tmin_rt  = daily_rt.get("temperature_2m_min", [])
            prec_rt  = daily_rt.get("precipitation_sum", [])
            wcodes_rt= daily_rt.get("weather_code", [])
            for di_rt in range(min(7, len(dates_rt))):
                dc_rt = _wmo.get(int(wcodes_rt[di_rt]) if di_rt < len(wcodes_rt) else 0, "")
                lines_rt.append(
                    f"  {dates_rt[di_rt]}: {tmin_rt[di_rt] if di_rt<len(tmin_rt) else '?'}–"
                    f"{tmax_rt[di_rt] if di_rt<len(tmax_rt) else '?'}°C  "
                    f"precip {prec_rt[di_rt] if di_rt<len(prec_rt) else '?'}mm  {dc_rt}"
                )
            return _text("\n".join(lines_rt))
        except Exception as exc_weather:
            return _text(f"realtime/weather: {exc_weather}")

    if rt_type == "stock":
        try:
            import yfinance as _yf_rt
            ticker_rt = _yf_rt.Ticker(rt_query.upper())
            info_rt   = ticker_rt.fast_info
            hist_rt   = ticker_rt.history(period="2d")
            price_rt  = float(info_rt.last_price) if hasattr(info_rt, "last_price") else None
            prev_rt   = float(hist_rt["Close"].iloc[-2]) if len(hist_rt) >= 2 else None
            chg_rt    = ((price_rt - prev_rt) / prev_rt * 100) if price_rt and prev_rt else None
            lines_rt2 = [
                f"**{rt_query.upper()} — {getattr(info_rt,'exchange','?')}**",
                f"Price: ${price_rt:.4f}" if price_rt else "Price: N/A",
                f"Change: {chg_rt:+.2f}%" if chg_rt is not None else "Change: N/A",
                f"52w High: ${float(info_rt.year_high):.2f}" if hasattr(info_rt,"year_high") else "",
                f"52w Low: ${float(info_rt.year_low):.2f}" if hasattr(info_rt,"year_low") else "",
                f"Market Cap: ${float(info_rt.market_cap)/1e9:.1f}B" if hasattr(info_rt,"market_cap") and info_rt.market_cap else "",
            ]
            return _text("\n".join(l for l in lines_rt2 if l))
        except Exception as exc_rt2:
            return _text(f"realtime/stock: {exc_rt2}")

    if rt_type == "crypto":
        try:
            async with get_client(timeout=15) as c:
                cg_r = await c.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": rt_query.lower(), "vs_currencies": "usd,eur,btc",
                            "include_24hr_change": "true", "include_market_cap": "true"},
                    timeout=10,
                )
                if cg_r.status_code != 200:
                    return _text(f"realtime/crypto: CoinGecko error {cg_r.status_code}")
                cg_rt = cg_r.json().get(rt_query.lower())
            if not cg_rt:
                return _text(f"realtime/crypto: coin '{rt_query}' not found on CoinGecko")
            lines_cg = [
                f"**{rt_query.title()}**",
                f"USD: ${cg_rt.get('usd','?'):,}",
                f"EUR: €{cg_rt.get('eur','?'):,}",
                f"24h Change: {cg_rt.get('usd_24h_change','?'):.2f}%" if isinstance(cg_rt.get('usd_24h_change'), float) else "",
                f"Market Cap: ${cg_rt.get('usd_market_cap','?'):,.0f}" if isinstance(cg_rt.get('usd_market_cap'), float) else "",
            ]
            return _text("\n".join(l for l in lines_cg if l))
        except Exception as exc_crypto:
            return _text(f"realtime/crypto: {exc_crypto}")

    if rt_type == "forex":
        try:
            parts_fx = rt_query.upper().replace("-", "/").split("/")
            if len(parts_fx) != 2:
                return _text("realtime/forex: use format 'USD/EUR' or 'GBP/JPY'")
            base_fx, target_fx = parts_fx
            async with get_client(timeout=15) as c:
                fx_r = await c.get(
                    f"https://open.er-api.com/v6/latest/{base_fx}", timeout=10
                )
                if fx_r.status_code != 200:
                    return _text(f"realtime/forex: exchange rate API error {fx_r.status_code}")
                fx_rt = fx_r.json()
            rate_fx = fx_rt.get("rates", {}).get(target_fx)
            if rate_fx is None:
                return _text(f"realtime/forex: currency '{target_fx}' not found")
            return _text(
                f"**{base_fx} → {target_fx}**\n"
                f"1 {base_fx} = {rate_fx:.6f} {target_fx}\n"
                f"Updated: {fx_rt.get('time_last_update_utc','?')}"
            )
        except Exception as exc_forex:
            return _text(f"realtime/forex: {exc_forex}")

    return _text(f"realtime: unknown type '{rt_type}'")


# ---------------------------------------------------------------------------
# Register handlers
# ---------------------------------------------------------------------------

TOOL_HANDLERS["deep_research"] = _deep_research
TOOL_HANDLERS["realtime"]      = _realtime
