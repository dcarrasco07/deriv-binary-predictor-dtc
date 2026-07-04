import asyncio
import json
import os
import websockets
import logging
import requests
import random
import secrets
import math
from collections import deque

# --- Configuration ---
app_id = '32WzmZD0GdX5NdJKlPO7e'
# api_token = 'pat_c5cbe64d305674c56b3812e62e49cd171dbf5366004a1bf8e6fb9628a249d44c'
# deriv_account_id = 'ROT91151098'

api_token = 'pat_a2ff9ed4e3c95be3518ea1d560c94eff196faedca95c306603c0dedde7e3f7c1'
deriv_account_id = 'DOT90416964'

APP_ID = os.getenv('DERIV_APP_ID', app_id) 
API_TOKEN = os.getenv('DERIV_API_TOKEN', api_token) 
DERIV_REST_OTP_URL = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"

# Risk & Scaling Settings
SUPPORTED_SYMBOLS = ['R_10', 'R_25', 'R_50', 'R_75', 'R_100']
RISK_PERCENTAGE = 0.0001   
BASE_ENTRY_FLOOR = 0.35        
MARTINGALE_MULTIPLIER = 2.5
PROFIT_SHAVE_RATE = 1       
MAX_MARTINGALE_PERCENTAGE = 0.03 
CURRENCY = 'USD'

# --- STATISTICAL ARBITRAGE PARAMETERS ---
WINDOW_SIZE = 20          # Number of historical ticks to look back
Z_SCORE_THRESHOLD = 2.0   # Trigger trade if price is outside +/- 2.0 standard deviations

# --- Memory Buffers for Historical Data ---
# Stores recent prices for each symbol to calculate rolling volatility metrics
tick_history = {symbol: deque(maxlen=WINDOW_SIZE) for symbol in SUPPORTED_SYMBOLS}

# Storage Files
os.makedirs('data', exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Shared Global Memory Spaces ---
last_contract_id = None
max_historical_loss = 0.0
total_net_pnl = 0.0      
session_net_pnl = 0.0    
account_balance = 0.0        
initial_capital = 0.0
capital_pool = 0.0
calculated_target_stake = BASE_ENTRY_FLOOR  
consecutive_losses = 0  

session_rebate_pool = 0.0  
all_time_rebate_pool = 0.0

def calculate_base_percentage_stake():
    global capital_pool
    computed_base = capital_pool * RISK_PERCENTAGE
    if computed_base < BASE_ENTRY_FLOOR:
        return BASE_ENTRY_FLOOR
    return round(computed_base, 2)

def generate_random_choice_ticks():
    return round(secrets.choice([secrets.SystemRandom().uniform(1, 4) for _ in range(4)]))

def calculate_z_score(prices, current_price):
    """Calculates how many standard deviations the current price is from the rolling mean."""
    n = len(prices)
    if n < WINDOW_SIZE:
        return 0.0
    
    mean = sum(prices) / n
    variance = sum((x - mean) ** 2 for x in prices) / n
    std_dev = math.sqrt(variance)
    
    if std_dev == 0:
        return 0.0
        
    return (current_price - mean) / std_dev

async def execute_trade(websocket, symbol, direction, stake):
    try:
        proposal_req = {
            "proposal": 1,
            "amount": float(f"{round(stake, 2):.2f}"),
            "basis": "stake",
            "contract_type": direction,
            "currency": CURRENCY,
            "duration": generate_random_choice_ticks(),
            "duration_unit": "t",
            "underlying_symbol": symbol
        }
        await send_data_safe(websocket, json.dumps(proposal_req))
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'proposal':
                if 'error' in res: raise Exception(res['error']['message'])
                proposal_id = res['proposal']['id']
                break
        
        await send_data_safe(websocket, json.dumps({"buy": proposal_id, "price": float(f"{stake:.2f}")}))
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'buy':
                if 'error' in res: raise Exception(res['error']['message'])
                contract_id = res['buy']['contract_id']
                logging.info(f"[ARBITRAGE] Dispatched Mean Reversion {direction} on {symbol} | ID: {contract_id} | Stake: ${stake:.2f}")
                
                await send_data_safe(websocket, json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
                return contract_id
    except Exception as e:
        logging.error(f"[BOT] Trade Request Blocked: {e}")
        return None

async def send_data_safe(websocket, payload):
    transport = websocket.transport
    if transport is not None:
        if transport.get_write_buffer_size() > 1024 * 1024:
            logging.warning("Network congestion detected. Throttling payload.")
            return 
    await websocket.send(payload)

def handle_settlement_data(contract):
    try: 
        global last_contract_id, max_historical_loss, total_net_pnl, session_net_pnl, all_time_rebate_pool, session_rebate_pool, calculated_target_stake, consecutive_losses, capital_pool
        
        if not contract or not contract.get('is_sold'): 
            return

        profit = float(contract.get('profit', 0))
        total_net_pnl += profit
        session_net_pnl += profit
        session_sign = "+" if profit >= 0 else ""
        
        if profit > 0:
            shaved_allocation = profit * PROFIT_SHAVE_RATE
            session_rebate_pool += shaved_allocation
            all_time_rebate_pool += shaved_allocation
            
            if capital_pool < initial_capital * 0.70:
                replenish_amount = min(shaved_allocation, (initial_capital * 0.70) - capital_pool)
                capital_pool += replenish_amount
                session_rebate_pool -= replenish_amount 
                    
            logging.info(f"[RESULT] WIN (+${profit:.2f}) | Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Capital Pool: ${capital_pool:.2f}")
            calculated_target_stake = calculate_base_percentage_stake()
            consecutive_losses = 0  
        else:
            capital_pool += profit  
            logging.info(f"[RESULT] LOSS (${profit:.2f}) | Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Capital Pool: ${capital_pool:.2f}")
            consecutive_losses += 1  
            calculated_target_stake = round(calculated_target_stake * MARTINGALE_MULTIPLIER, 2)
            calculated_target_stake = min(calculated_target_stake, capital_pool * MAX_MARTINGALE_PERCENTAGE)

        last_contract_id = None
    except Exception as e:
        logging.error(f"handle settlement data: {e}")

async def process_ticks(websocket):
    global last_contract_id, calculated_target_stake, account_balance

    async for message in websocket:
        try:
            payload = json.loads(message)
            
            if 'balance' in payload:
                account_balance = float(payload['balance']['balance'])
                if not last_contract_id:
                    calculated_target_stake = calculate_base_percentage_stake()
            
            elif payload.get('msg_type') == 'proposal_open_contract':
                contract = payload.get('proposal_open_contract')
                handle_settlement_data(contract)

            elif payload.get('msg_type') == 'tick' and 'tick' in payload:
                symbol = payload['tick']['symbol']
                current_quote = float(payload['tick']['quote'])
                
                # Append current tick to our moving window
                tick_history[symbol].append(current_quote)
                
                # Ensure we have gathered enough ticks to calculate clean data metrics
                if len(tick_history[symbol]) < WINDOW_SIZE:
                    continue
                
                # Only check signals if the bot isn't currently inside an open contract
                if not last_contract_id:
                    z_score = calculate_z_score(list(tick_history[symbol]), current_quote)
                    
                    trade_direction = None
                    # Price is extremely overbought -> Expecting it to drop back down
                    if z_score >= Z_SCORE_THRESHOLD:
                        trade_direction = "PUT"
                    # Price is extremely oversold -> Expecting a snap-back bounce upwards
                    elif z_score <= -Z_SCORE_THRESHOLD:
                        trade_direction = "CALL"
                        
                    if trade_direction:
                        logging.info(f"[SIGNAL DETECTED] {symbol} | Z-Score: {z_score:.2f} -> Executing {trade_direction}")
                        last_contract_id = await execute_trade(websocket, symbol, trade_direction, calculated_target_stake)
                    
        except Exception as e:
            logging.error(f"Error processing payload frame: {e}")

async def get_authenticated_ws_url() -> str:
    url = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"
    headers = {
        "Deriv-App-ID": app_id,
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: requests.post(url, headers=headers, timeout=10))
        if response.status_code == 200:
            target_url = response.json().get('data', {}).get('url')
            if target_url: return target_url
    except Exception as e:
        print(f"Deriv Bot Meta: Failed to fetch dynamic REST OTP: {e}")
    await asyncio.sleep(5)
    return None

async def main():
    while True: 
        url = await get_authenticated_ws_url()
        if not url: 
            await asyncio.sleep(2)
            continue
        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({"authorize": API_TOKEN}))
                async for msg in ws:
                    auth_res = json.loads(msg)
                    if auth_res.get('msg_type') == 'authorize': 
                        global account_balance, initial_capital, capital_pool
                        account_balance = float(auth_res['authorize']['balance'])
                        initial_capital = account_balance
                        capital_pool = initial_capital * 0.70
                        break
                
                await ws.send(json.dumps({"balance": 1, "subscribe": 1}))
                for symbol in SUPPORTED_SYMBOLS:
                    await ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))
                    
                await process_ticks(ws)
        except Exception as e:
            logging.error(f"Interface connection lost: {e}. Attempting to reconnect...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try: 
        asyncio.run(main())
    except KeyboardInterrupt: 
        logging.info("System gracefully halted.")