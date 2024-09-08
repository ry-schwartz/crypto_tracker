import requests, os
from datetime import datetime, timedelta

def fetch_top_coins():
    url = 'https://api.coingecko.com/api/v3/coins/markets'
    params = {
        'vs_currency': 'usd',
        'order': 'market_cap_desc',
        'per_page': 100,
        'page': 1,
        'price_change_percentage': '24h'
    }

    response = requests.get(url, params = params)

    if response.status_code == 200:
        data = response.json()
    else:
        print(f"Error fetching data from CoinGecko API: {response.status_code}")
        data = []

    if isinstance(data, list):  # Ensure data is a list
        winners = sorted(data, key=lambda x: x['price_change_percentage_24h'], reverse=True)[:3]
        losers = sorted(data, key=lambda x: x['price_change_percentage_24h'])[:3]
        popular = data[:5]

        return {
            'winners': format_coin_data(winners),
            'losers': format_coin_data(losers),
            'popular': format_coin_data(popular)
        }
    else:
        print("Unexpected data format from CoinGecko API")
        return {
            'winners': [],
            'losers': [],
            'popular': []
        }

def format_coin_data(coins):
    formatted_data = []

    for coin in coins:
        formatted_data.append({
            'id': coin['id'],
            'name': coin['name'],
            'image': coin['image'],
            'price': coin['current_price'],
            'percentage_change_24h': round(coin['price_change_percentage_24h'], 2),
            'trend': 'up' if coin['price_change_percentage_24h'] > 0 else 'down'
        })

    return formatted_data

def format_volume(volume):
    if volume >= 1_000_000_000:
        return f'{volume / 1_000_000_000:.1f}B'
    elif volume >= 1_000_000:
        return f'{volume / 1_000_000:.1f}M'
    else:
        return f'{volume:,.0f}'

def fetch_coin_details(coin_id):
    url = f'https://api.coingecko.com/api/v3/coins/{coin_id}'
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        if 'error' in data:
            return None
        
        coin_details = {
        'name': data.get('name', 'N/A'),
        'image': data.get('image', {}).get('large', 'N/A'),
        'price': f"{data.get('market_data', {}).get('current_price', {}).get('usd', 'N/A')}",
        'volume': format_volume(data.get('market_data', {}).get('total_volume', {}).get('usd', 0)),
        'percentage_change_30d': round(data.get('market_data', {}).get('price_change_percentage_30d', 0), 2),
        'trend': 'up' if data.get('market_data', {}).get('price_change_percentage_30d', 0) > 0 else 'down',
        'news': fetch_coin_news(coin_id)
        }
    else:
        print("Error fetching data from CoinGecko API")
        coin_details = {}

    return coin_details

def fetch_coin_news(coin_id):
    url = 'http://api.mediastack.com/v1/news'
    api_key = os.getenv('NEWS_API_KEY') 
    params = {
        'access_key': api_key,
        'keywords': coin_id.lower(),
        'languages': 'en',
        'sort': 'published_desc',
        'limit': 10
    }

    response = requests.get(url, params=params)

    if response.status_code == 200:
        news_data = response.json()

        news_articles = []

        for article in news_data.get('data', []):
            news_articles.append({
                'title': article['title'][:40],
                'link': article['url'],
                'time_since': time_since_posting(article['published_at'])
            })
    else:
        print('Error fetching news from Mediastack API:', response.text)
        news_articles = []

    return news_articles

def time_since_posting(published_date):
    # Converts published date to the time since posting format
    try:
        # Adjust the format string to match Mediastack's date format, including the timezone offset
        published_time = datetime.fromisoformat(published_date)
        time_diff = datetime.now(published_time.tzinfo) - published_time
        if time_diff.days > 0 and time_diff.days == 1:
            return f"{time_diff.days} day ago"
        elif time_diff.days > 1:
            return f"{time_diff.days} days ago"
        else:
            hours = time_diff.seconds // 3600
            if hours > 0:
                return f"{hours} hours ago"
            else:
                return f"{time_diff.seconds // 60} minutes ago"
    except ValueError:
        # Handle any parsing errors
        print(published_date)

def fetch_coin_chart_data(coin_id):
    url = f'https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart'
    params = {
        'vs_currency': 'usd',
        'days': '30' 
    }

    response = requests.get(url, params=params)

    if response.status_code == 200:
        data = response.json()
    else:
        print('Error fetching chart data from CoinGecko')
        data = {'prices': []}
    
    dates = [datetime.fromtimestamp(point[0] / 1000).strftime('%Y-%m-%d') for point in data['prices']]
    prices = [point[1] for point in data['prices']]

    return dates, prices

if __name__ == '__main__':
    print('---------------------------------------------------------------')
    print('---------------------------------------------------------------')
    coin_id = 'bitcoin'
    details = fetch_coin_details(coin_id)
    print(f"Coin Details for {coin_id.capitalize()}:")
    print(f"Name: {details['name']}")
    print(f"Image: {details['image']}")
    print(f"Price: ${details['price']}")
    print(f"Volume: {details['volume']}")
    print(f"30 Day Percentage Change: {details['percentage_change_30d']}%")
    print(f"Trend: {details['trend']}")

    print('---------------------------------------------------------------')
    print('---------------------------------------------------------------')
    print("\nNews Articles:")
    if details['news']:
        for article in details['news']:
            print(f"Title: {article['title']}")
            print(f"Link: {article['link']}")
            print(f"Time Since Posted: {article['time_since']}")
            print("-" * 20)
    else:
        print('No news articles')

    print('---------------------------------------------------------------')
    print('---------------------------------------------------------------')
    chart_details = fetch_coin_chart_data(coin_id)
    print(f'Chart Details: {chart_details}')