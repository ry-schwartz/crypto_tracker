import json
from django.shortcuts import render
from django.contrib import messages
from .cryptocurrency import fetch_top_coins, fetch_coin_details, fetch_coin_chart_data

def home(request):
    coin_data = fetch_top_coins()
    context = {
        'winners': coin_data['winners'],
        'losers': coin_data['losers'],
        'popular': coin_data['popular'],
    }
    return render(request, 'tracker/home.html', context)

def coin_view(request):
    coin_id = request.GET.get('query')
    
    if coin_id:
        coin_data = fetch_coin_details(coin_id)
        chart_labels, chart_data = fetch_coin_chart_data(coin_id)
    else:
        coin_data = {}
        chart_labels, chart_data = [], []
        messages.error(request, f"No data found for '{coin_id}'. Please enter a valid coin name.")
    
    context = {
        'coin': coin_data,
        'labels': json.dumps(chart_labels),
        'data': json.dumps(chart_data)
    }
    return render(request, 'tracker/coin_view.html', context)