import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import requests
from django.core.cache import cache


logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3"
NEWS_URL = "https://api.mediastack.com/v1/news"

REQUEST_TIMEOUT = (3.05, 10)
MARKET_TTL = 120
CHART_TTL = 300
NEWS_TTL = 900
STALE_TTL = 3600
MOVER_COUNT = 6


def _number(value):
    """Return a finite number, or None for missing/invalid values."""
    if value is None or isinstance(value, bool):
        return None

    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _percentage(value):
    """Round displayed percentages and remove negative zero."""
    number = _number(value)
    if number is None:
        return None

    rounded = round(number, 2)
    return 0.0 if rounded == 0 else rounded


def _trend(change):
    if change is None:
        return "unknown"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _coin_id(value):
    return str(value or "").strip().lower()


def _unavailable(reason):
    return {
        "state": "unavailable",
        "reason": reason,
        "updated_at": None,
    }


def _retry_delay(response):
    """Support both seconds and HTTP-date Retry-After headers."""
    value = response.headers.get("Retry-After", "")

    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(1, math.ceil(retry_at.timestamp() - time.time()))
        except (TypeError, ValueError, OverflowError):
            return 120


def _request_json(provider, url, params, ttl, validator):
    """
    Cache validated responses and retain old data for temporary failures.

    Status state is fresh, stale, or unavailable.
    Cooldowns are shared across endpoints for the same provider.
    """
    headers = {}
    if provider == "coingecko":
        api_key = os.getenv("COINGECKO_API_KEY", "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key

    # Keep credentials and request parameters out of readable cache keys.
    identity = json.dumps(
        [url, params, headers], sort_keys=True
    ).encode()
    digest = hashlib.sha256(identity).hexdigest()
    key = f"crypto:v1:{provider}:{digest}"
    cooldown_key = f"crypto:v1:{provider}:cooldown"
    failure_key = f"{key}:failure"

    cached = cache.get(key)

    def result(payload, reason=None):
        return payload, {
            "state": "stale" if reason else "fresh",
            "reason": reason,
            "updated_at": cached["saved_at"] if cached else None,
        }

    def fallback(reason):
        if cached is not None:
            return result(cached["payload"], reason)
        return None, _unavailable(reason)

    if cached is not None and time.time() - cached["saved_at"] < ttl:
        return result(cached["payload"])

    reason = cache.get(cooldown_key) or cache.get(failure_key)
    if reason:
        return fallback(reason)

    reason = "unavailable"
    delay = 30

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 429:
            delay = _retry_delay(response)
            reason = "rate_limited"
            cache.set(cooldown_key, reason, timeout=delay)
            logger.warning(
                "%s rate limited; pausing requests for %s seconds.",
                provider,
                delay,
            )
            return fallback(reason)

        # Providers may return error objects even with HTTP 200.
        try:
            payload = response.json()
        except ValueError:
            payload = None

        error = payload.get("error") if isinstance(payload, dict) else None

        if response.status_code == 404:
            reason = "not_found"
            logger.warning("%s returned HTTP 404.", provider)
        elif error:
            error_details = _mapping(error)
            code = str(error_details.get("code", "provider_error"))

            # Never log raw responses or URLs containing credentials.
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
                code = "provider_error"

            reason = f"provider_error:{code}"
            delay = 60
            logger.warning(
                "%s API error: %s (HTTP %s).",
                provider,
                code,
                response.status_code,
            )
        elif response.status_code != 200:
            reason = f"http_{response.status_code}"
            logger.warning(
                "%s returned HTTP %s.", provider, response.status_code
            )
        elif not validator(payload):
            reason = "invalid_response"
            logger.warning(
                "%s returned an unexpected data format.", provider
            )
        else:
            cached = {
                "payload": payload,
                "saved_at": time.time(),
            }
            cache.set(key, cached, timeout=STALE_TTL)
            cache.delete(failure_key)
            return result(payload)

    except requests.RequestException as exc:
        reason = "connection_error"
        logger.warning(
            "%s request failed (%s).", provider, type(exc).__name__
        )

    cache.set(failure_key, reason, timeout=delay)
    return fallback(reason)


def _fetch_market_data():
    """Shared cached market response for the dashboard and search."""
    return _request_json(
        "coingecko",
        f"{COINGECKO_URL}/coins/markets",
        {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 100,
            "page": 1,
            "price_change_percentage": "24h",
        },
        MARKET_TTL,
        lambda value: (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and bool(item["id"])
                for item in value
            )
        ),
    )


def resolve_coin_id(query):
    """
    Resolve an exact ID, name, or symbol within the fetched top 100.

    Unknown queries pass through as IDs for the detail endpoint to check.
    Ambiguous names/symbols are not silently resolved to one coin.
    """
    query = _coin_id(query)
    if not query:
        return None, _unavailable("missing_query")

    coins, status = _fetch_market_data()
    if coins is None:
        return None, status

    # Exact IDs take priority, preserving existing dashboard links.
    for coin in coins:
        if _coin_id(coin["id"]) == query:
            return coin["id"], status

    # Prefer full names over symbols.
    for field in ("name", "symbol"):
        matches = {
            coin["id"]
            for coin in coins
            if _coin_id(coin.get(field)) == query
        }

        if len(matches) == 1:
            return next(iter(matches)), status

        if len(matches) > 1:
            return None, _unavailable(f"ambiguous_{field}")

    # Allows exact IDs outside the top 100 without another search API.
    return query, status


def format_coin_data(coins):
    formatted = []

    for coin in coins:
        if not isinstance(coin, dict) or not coin.get("id"):
            continue

        change = _percentage(coin.get("price_change_percentage_24h"))

        formatted.append({
            "id": coin["id"],
            "name": coin.get("name") or coin["id"],
            "symbol": str(coin.get("symbol") or "").upper(),
            "image": coin.get("image") or "",
            "price": _number(coin.get("current_price")),
            "percentage_change_24h": change,
            "trend": _trend(change),
        })

    return formatted


def fetch_top_coins():
    data, status = _fetch_market_data()
    coins = format_coin_data(data or [])

    ranked = [
        coin for coin in coins
        if coin["percentage_change_24h"] is not None
    ]

    # Changes rounded to zero are neutral and excluded from movers.
    winners = sorted(
        (
            coin for coin in ranked
            if coin["percentage_change_24h"] > 0
        ),
        key=lambda coin: coin["percentage_change_24h"],
        reverse=True,
    )[:MOVER_COUNT]

    losers = sorted(
        (
            coin for coin in ranked
            if coin["percentage_change_24h"] < 0
        ),
        key=lambda coin: coin["percentage_change_24h"],
    )[:MOVER_COUNT]

    return {
        "winners": winners,
        "losers": losers,
        "popular": coins[:5],
        "status": status,
    }


def format_volume(value):
    volume = _number(value)
    if volume is None:
        return "N/A"
    if volume >= 1_000_000_000:
        return f"{volume / 1_000_000_000:.1f}B"
    if volume >= 1_000_000:
        return f"{volume / 1_000_000:.1f}M"
    return f"{volume:,.0f}"


def fetch_coin_details(coin_id, *, with_status=False):
    coin_id = _coin_id(coin_id)
    if not coin_id:
        status = _unavailable("missing_query")
        return ({}, status) if with_status else {}

    data, status = _request_json(
        "coingecko",
        f"{COINGECKO_URL}/coins/{quote(coin_id, safe='')}",
        {
            "localization": "false",
            "tickers": "false",
            "community_data": "false",
            "developer_data": "false",
        },
        MARKET_TTL,
        lambda value: (
            isinstance(value, dict)
            and bool(value.get("id"))
            and isinstance(value.get("market_data"), dict)
        ),
    )

    details = {}
    if data is not None:
        market = _mapping(data.get("market_data"))
        change = _percentage(
            market.get("price_change_percentage_30d")
        )
        news, news_status = fetch_coin_news(
            data.get("name") or coin_id,
            with_status=True,
        )

        details = {
            "id": data["id"],
            "name": data.get("name") or coin_id,
            "symbol": str(data.get("symbol") or "").upper(),
            "image": _mapping(data.get("image")).get("large") or "",
            "price": _number(
                _mapping(market.get("current_price")).get("usd")
            ),
            "volume": format_volume(
                _mapping(market.get("total_volume")).get("usd")
            ),
            "percentage_change_30d": change,
            "trend": _trend(change),
            "news": news,
            "news_status": news_status,
        }

    return (details, status) if with_status else details


def fetch_coin_news(coin_name, *, with_status=False):
    api_key = os.getenv("NEWS_API_KEY", "").strip()

    if not api_key:
        status = _unavailable("missing_api_key")
        logger.warning("News unavailable: NEWS_API_KEY is not configured.")
        return ([], status) if with_status else []

    payload, status = _request_json(
        "mediastack",
        NEWS_URL,
        {
            "access_key": api_key,
            "keywords": str(coin_name).strip(),
            "languages": "en",
            "sort": "published_desc",
            "limit": 10,
        },
        NEWS_TTL,
        lambda value: (
            isinstance(value, dict)
            and isinstance(value.get("data"), list)
        ),
    )

    articles = []
    for article in (payload or {}).get("data", []):
        if not isinstance(article, dict):
            continue

        title = article.get("title")
        link = article.get("url")

        if not isinstance(title, str) or not isinstance(link, str):
            continue

        try:
            parsed = urlparse(link)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
        except ValueError:
            continue

        articles.append({
            "title": title,
            "link": link,
            "source": article.get("source") or "",
            "time_since": time_since_posting(article.get("published_at")),
        })

    return (articles, status) if with_status else articles


def time_since_posting(published_date):
    try:
        published = datetime.fromisoformat(
            str(published_date).replace("Z", "+00:00")
        )
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        seconds = max(
            0,
            int((datetime.now(timezone.utc) - published).total_seconds()),
        )
    except (TypeError, ValueError, OverflowError):
        return "Unknown date"

    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        count = seconds // size
        if count:
            return f"{count} {unit}{'s' if count != 1 else ''} ago"

    return "Just now"


def fetch_coin_chart_data(coin_id, *, with_status=False):
    coin_id = _coin_id(coin_id)
    if not coin_id:
        status = _unavailable("missing_query")
        return ([], [], status) if with_status else ([], [])

    payload, status = _request_json(
        "coingecko",
        f"{COINGECKO_URL}/coins/{quote(coin_id, safe='')}/market_chart",
        {"vs_currency": "usd", "days": 30},
        CHART_TTL,
        lambda value: (
            isinstance(value, dict)
            and isinstance(value.get("prices"), list)
            and bool(value["prices"])
            and all(
                isinstance(point, (list, tuple))
                and len(point) >= 2
                and _number(point[0]) is not None
                and _number(point[1]) is not None
                and 0 <= float(point[0]) < 253402300799000
                for point in value["prices"]
            )
        ),
    )

    dates, prices = [], []
    for timestamp, price, *_ in (payload or {}).get("prices", []):
        dates.append(
            datetime.fromtimestamp(
                float(timestamp) / 1000,
                tz=timezone.utc,
            ).strftime("%Y-%m-%d")
        )
        prices.append(float(price))

    if with_status:
        return dates, prices, status
    return dates, prices