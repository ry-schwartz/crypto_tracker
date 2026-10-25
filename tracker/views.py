import json

from django.shortcuts import render

from .cryptocurrency import (
    fetch_coin_chart_data,
    fetch_coin_details,
    fetch_top_coins,
    resolve_coin_id,
)


def _data_notice(status, label):
    """Return a short message when data is stale or unavailable."""
    state = status.get("state")
    reason = status.get("reason")

    if state == "stale":
        return f"Showing previously loaded {label.lower()}."

    if state == "unavailable":
        if reason == "rate_limited":
            return f"{label} temporarily unavailable. Please try again shortly."
        if reason == "not_found":
            return "Coin not found. Try its full name or symbol."
        if reason == "missing_query":
            return "Enter a coin name or symbol, such as Bitcoin or BTC."
        if reason == "ambiguous_symbol":
            return "Several coins use that symbol. Please search by full name."
        if reason == "ambiguous_name":
            return "Several coins share that name. Please use the exact coin ID."
        return f"Error fetching {label.lower()}. Please try again shortly."

    return ""


def home(request):
    coin_data = fetch_top_coins()

    context = {
        "winners": coin_data["winners"],
        "losers": coin_data["losers"],
        "popular": coin_data["popular"],
        "market_notice": _data_notice(
            coin_data["status"], "Market data"
        ),
    }
    return render(request, "tracker/home.html", context)


def coin_view(request):
    query = request.GET.get("query", "").strip()

    coin_id, lookup_status = resolve_coin_id(query)

    coin_data = {}
    coin_status = lookup_status
    chart_labels = []
    chart_data = []
    chart_notice = ""
    news_notice = ""

    if coin_id is not None:
        coin_data, coin_status = fetch_coin_details(
            coin_id, with_status=True
        )

    if coin_data:
        chart_labels, chart_data, chart_status = fetch_coin_chart_data(
            coin_data["id"], with_status=True
        )
        chart_notice = _data_notice(chart_status, "Chart data")

        news_status = coin_data.get("news_status", {})

        if not coin_data.get("news"):
            if news_status.get("state") in {"unavailable", "stale"}:
                news_notice = "Error fetching news."
            else:
                news_notice = "No current news available."
        elif news_status.get("state") == "stale":
            news_notice = "Showing previously loaded news."
    else:
        chart_notice = "Chart data unavailable."
        news_notice = "News unavailable until coin data can be loaded."

    context = {
        "query": query,
        "coin": coin_data,
        "coin_notice": _data_notice(coin_status, "Coin data"),
        "chart_notice": chart_notice,
        "news_notice": news_notice,
        "labels": json.dumps(chart_labels),
        "data": json.dumps(chart_data),
        "has_chart_data": bool(chart_labels and chart_data),
    }
    return render(request, "tracker/coin_view.html", context)