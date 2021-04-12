import time
from datetime import datetime
from upbitlib.upbit import Upbit
from pytz import timezone
import json

# 업비트 기반 변동성 돌파 전략 https://m.post.naver.com/viewer/postView.nhn?volumeNo=15975365&memberNo=40921089

with open('auth.json', 'r') as f:
    auth_dict = json.load(f)

assert auth_dict.get('access_key') != None and auth_dict.get('secret_key') != None

UPBIT_API_KEY = auth_dict['access_key']  # 업비트에서 발급 받은 Key 입력
UPBIT_SEC_KEY = auth_dict['secret_key']  # 업비트에서 발급 받은 Secret 입력

SELECTED_COINS = ['BTC', 'ETH', 'XRP', 'ETC', 'OMG', 'ZEC', 'XMR', 'XLM', 'ADA',
                  'EOS', 'ONT', 'MFT', 'BAT', 'LOOM', 'BCH', 'ZIL', 'IOST']
GROWING_PERIOD = 5  # 5 days
BETTING_BUDGET = 10000  # 코인별 최대 1만원
MAX_NUM_COIN = 4  # 하루 최대 코인 4개 투자
SPREAD_GAP = 0.002
PARAM = 0.5
THRESHOLD = 0.5

# API 초기화
upbit = Upbit(UPBIT_API_KEY, UPBIT_SEC_KEY)


def candidate_coins():
    if SELECTED_COINS:
        return map(lambda x: 'KRW-{0}'.format(x), SELECTED_COINS)
    candidate_coin = map(lambda x: x['market'], upbit.get_markets())
    return filter(lambda x: x.startswith('KRW'), candidate_coin)


def is_growing_market(market):
    prices = upbit.get_candles_daily(market, '', GROWING_PERIOD)
    return prices[0]['trade_price'] > prices[-1]['trade_price']


def get_market_noise(market):
    prices = upbit.get_candles_daily(market, '', 20)[1:]
    price_noise = list(map(lambda p: 1 - abs(p['trade_price'] - p['opening_price']) / (
        p['high_price'] - p['low_price']), prices))
    return sum(price_noise) / len(price_noise)


def get_betting_ratio(market):
    '''
    3일~20일의 18개의 이동 평균선을 계산
    이동평균선 스코어 = 각 이동평균선을 넘은 개수/18
    e.g., 3일의 이동 평균선 = (1일전 종가 + 2일전 종가 + 3일전 종가)/3
          => 만약 현재 가격이 3일의 이동 평균 가격 보다 높으면 score 1/18 더한다
    '''
    prices = upbit.get_candles_daily(market, '', 21)
    score = 0
    if len(prices) < 21:
        return 0

    for period in range(3, 20):
        sum_prices = 0
        for j in range(0, period):
            sum_prices += prices[j+1]['trade_price']

        if sum_prices/period < prices[0]['opening_price']:
            score += 1/18.0
    return score


def fix_price(price):
    _unit = {
        10: 0.01,
        10**1: 0.1,
        10**2: 1,
        10**3: 5,
        10**4: 10,
        5*10**4: 50,
        10**5: 100,
        10**6: 500,
        2*10**6: 1000
    }

    for p in _unit:
        if price > p:
            price = (price // _unit[p]) * _unit[p]
    return price


def buy(market, budget):
    for retry in range(3):
        ticker = upbit.get_ticker(market)
        last_price = fix_price(ticker[0]['trade_price'] * (1 + SPREAD_GAP))
        amount = budget / last_price

        result = upbit.place_order(market, 'bid', amount, last_price)
        if result and result['uuid']:
            for i in range(5):
                order_info = upbit.get_order(result['uuid'])
                if order_info and float(order_info['remaining_volume']) <= 0.0:
                    return
                time.sleep(1)

            upbit.cancel_order(result['uuid'])


def sell(market, amount):
    for retry in range(3):
        ticker = upbit.get_ticker(market)
        if not ticker:
            return

        total_price = float(amount) * float(ticker[0]['trade_price'])
        if total_price < 10000:
            return

        last_price = fix_price(ticker[0]['trade_price'] * (1 - SPREAD_GAP))
        result = upbit.place_order(market, 'ask', amount, last_price)

        if result and result['uuid']:
            for i in range(5):
                order_info = upbit.get_order(result['uuid'])
                if order_info and float(order_info['remaining_volume']) <= 0.0:
                    return
                time.sleep(1)

            upbit.cancel_order(result['uuid'])


def dump_all():
    accounts_list = upbit.get_accounts()
    accounts_list = filter(lambda z: z['currency'] != 'KRW', accounts_list)
    for wallet in accounts_list:
        sell('KRW-{0}'.format(wallet['currency']), wallet['balance'])


if __name__ == '__main__':
    trade_markets = list(candidate_coins())
    
    print('[INFO] Market candidates: %s'%trade_markets)
    
    already_buy = {}
    coin_noise = {}
    coin_betting_ratio = {}
    coin_investable = MAX_NUM_COIN

    print("[INFO] Fetching market noise, betting ratio information from UPbit...")
    for market in trade_markets:
        try:
            coin_noise[market] = get_market_noise(market)
            coin_betting_ratio[market] = get_betting_ratio(market)
        except Exception as e:
            trade_markets.remove(market)
            print('[WARNING] Removing market %s'%market)
    
    valid_markets = set.intersection(set(coin_noise.keys()), set(coin_betting_ratio.keys()), set(trade_markets))
    
    coin_noise = {k: coin_noise[k] for k in valid_markets}
    coin_betting_ratio = {k: coin_betting_ratio[k] for k in valid_markets}
    trade_markets = [i for i in trade_markets if i in valid_markets]
    
    print('[INFO] Valid markets selected: %s'%(trade_markets))
    trade_markets = sorted(list(
        filter(lambda m: coin_betting_ratio[m] > 0, trade_markets)))[-int(THRESHOLD*len(list(
            filter(lambda m: coin_betting_ratio[m] > 0, trade_markets)))):]

    print('[INFO] Starting trade loop:')
    while True:
        for market in trade_markets:
            if market in already_buy:
                continue

            if coin_investable <= 0:
                break

            candles = upbit.get_candles_daily(
                market, '', 2)  # Today, Yesterday

            _range = candles[1]['high_price'] - candles[1]['low_price']

            today_opening = candles[0]['opening_price']
            today_current = candles[0]['trade_price']

            k = _range * coin_noise[market] * PARAM

            over_ratio = today_current / (today_opening + k)

            if over_ratio > 1.0:
                buy(market, BETTING_BUDGET * coin_betting_ratio[market])
                already_buy[market] = True
                coin_investable -= 1
                # 만약 현재 시가 기준으로 전날 등락폭 대비해서 올랐으면 사자

        time.sleep(1)

        t = datetime.now(timezone('Asia/Seoul'))
        if t.hour == 23 and t.minute > 45:  # 저녁 12시 전에 판매 한다.
            dump_all()
            exit(0)
