import asyncio
import json
import os
import websockets
import logging
import requests
import random
import secrets

# --- Configuration ---
app_id = '32WzmZD0GdX5NdJKlPO7e'
api_token = 'pat_a2ff9ed4e3c95be3518ea1d560c94eff196faedca95c306603c0dedde7e3f7c1'
deriv_account_id = 'DOT90416964'

APP_ID = os.getenv('DERIV_APP_ID', app_id) 
API_TOKEN = os.getenv('DERIV_API_TOKEN', api_token) 
DERIV_REST_OTP_URL = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"

# Risk & Scaling Settings
SUPPORTED_SYMBOLS = ['R_10', 'R_25', 'R_50', 'R_75', 'R_100']
RISK_PERCENTAGE = 0.0001   # 0.01% of the total wallet account balance
BASE_ENTRY_FLOOR = 0.35        # Deriv API absolute entry option floor
MARTINGALE_MULTIPLIER = 2.5
PROFIT_SHAVE_RATE = 1       # Shaves off exactly 100% of clean wins as configured
CURRENCY = 'USD'

# Storage Files
os.makedirs('data', exist_ok=True)
DB_FILE = os.path.join('data', 'predictor_data.db')
MARKDOWN_SUMMARY_FILE = os.path.join('data', 'live_dashboard.md')

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
last_trade_direction = None

# --- REBATE ARCHITECTURE COUNTERS ---
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

def generate_random_choice_symbol():
    return SUPPORTED_SYMBOLS[round(secrets.choice([secrets.SystemRandom().uniform(1, 4) for _ in range(4)]))]
 

async def execute_trade(websocket, direction, stake):
    try:
        proposal_req = {
            "proposal": 1,
            "amount": float(f"{stake:2f}"),
            "basis": "stake",
            "contract_type": direction,
            "currency": CURRENCY,
            "duration": generate_random_choice_ticks(),
            "duration_unit": "t",
            "underlying_symbol": generate_random_choice_symbol()
        }
        await send_data_safe(websocket, json.dumps(proposal_req))
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'proposal':
                if 'error' in res: raise Exception(res['error']['message'])
                proposal_id = res['proposal']['id']
                break
        
        await send_data_safe(websocket,json.dumps({"buy": proposal_id, "price": float(f"{stake:.2f}")}))
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'buy':
                if 'error' in res: raise Exception(res['error']['message'])
                contract_id = res['buy']['contract_id']
                logging.info(f"[TRADE] Dispatched Offset Position {direction} | ID: {contract_id} | Placed Stake: ${stake:.2f}")
                
                # --- FIXED: Establish an automatic push stream connection straight to the broker ---
                await send_data_safe(websocket,json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
                return contract_id
    except Exception as e:
        logging.error(f"[BOT] Trade Request Blocked: {e}")
        return None

async def send_data_safe(websocket, payload):
    # Get the underlying asyncio transport layer object
    transport = websocket.transport
    
    if transport is not None:
        # Check current memory buffer size in bytes (1 MB threshold)
        if transport.get_write_buffer_size() > 1024 * 1024:
            logging.warning("Network congestion detected. Throttling payload.")
            return # Drop or defer the packet here
            
    # Regular non-blocking queueing, blocks only if TCP buffer is completely stuck
    await websocket.send(payload)

def handle_settlement_data(contract):
    try: 
        """Processes streamed contract packets pushed automatically via data feeds."""
        global last_contract_id, max_historical_loss, total_net_pnl, session_net_pnl, all_time_rebate_pool, session_rebate_pool, calculated_target_stake, consecutive_losses, capital_pool
        
        # Ignore open packets; wait for the final message package
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
            
            # Replenish capital_pool up to 70% of initial_capital
            if capital_pool < initial_capital * 0.70:
                replenish_amount = min(shaved_allocation, (initial_capital * 0.70) - capital_pool)
                capital_pool += replenish_amount
                session_rebate_pool -= replenish_amount # Deduct from rebate pool as it's moved to capital pool
                    
            logging.info(f"[RESULT] WIN (+${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Rebate Pool: ${session_rebate_pool} | Capital Pool: ${capital_pool:.2f} | Shaved 100% into Pool.")
            calculated_target_stake = calculate_base_percentage_stake()
            consecutive_losses = 0  # Reset consecutive losses on a win
        else:
            # Deduct loss from capital_pool
            capital_pool += profit  # profit is negative for a loss
            logging.info(f"[RESULT] LOSS (${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Rebate Pool: ${session_rebate_pool} | Capital Pool: ${capital_pool:.2f}")
            consecutive_losses += 1  # Increment consecutive losses on a loss
            calculated_target_stake = round(calculated_target_stake * MARTINGALE_MULTIPLIER, 2)

        logging.info("[TOTAL NET PNL] VALUE: %s", total_net_pnl)
        
        # Unlock pipeline for the next trade iteration
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
                current_quote = float(payload['tick']['quote'])
                
                if not last_contract_id:
                    # --- FIXED: Allow execution if the queue is building up ("") or if it hits target match rules ---
                    logging.info(f"[FUNDS ROUTER] Dispatching trade frame. | Raw Target: ${calculated_target_stake:.2f}")
                             
                    global last_trade_direction
                    if last_trade_direction == "PUT":
                        trade_direction = "CALL"
                    elif last_trade_direction == "CALL":
                        trade_direction = "PUT"
                    else:
                        trade_direction = random.choice(["PUT", "CALL"]) # Initial trade
                    
                    last_contract_id = await execute_trade(websocket, trade_direction, calculated_target_stake)
                    last_trade_direction = trade_direction
                    
        except Exception as e:
            logging.error(f"Error processing payload frame: {e}")

# def get_authenticated_ws_url():
#     try:
#         response = requests.post(DERIV_REST_OTP_URL, headers={"Deriv-App-ID": APP_ID, "Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}, timeout=10)
#         if response.status_code == 200: return response.json().get('data', {}).get('url')
#     except Exception as e: logging.error(f"OTP rest failed: {e}")
#     return None

async def get_authenticated_ws_url() -> str:
    """Queries the modern REST API to get a dynamic authenticated WebSocket URL."""
    url = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"
    
    # FIXED: Both Deriv-App-ID AND Authorization must exist in tandem
    headers = {
        "Deriv-App-ID": app_id,
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    try:
        loop = asyncio.get_running_loop()
        # Run synchronous post inside executor to preserve the asyncio loop
        response = await loop.run_in_executor(
            None, lambda: requests.post(url, headers=headers, timeout=10)
        )
        
        if response.status_code == 200:
            target_url = response.json().get('data', {}).get('url')
            if target_url:
                return target_url
        else:
            print(f"Deriv Bot Meta: REST Error {response.status_code} - {response.text}")
    except requests.exceptions.Timeout:
        print("Deriv Bot Meta: HTTP Request timed out (Server dropped connection).")
    except Exception as e:
        print(f"Deriv Bot Meta: Failed to fetch dynamic REST OTP: {e}")
    
    print("Deriv Bot Meta: Retrying OTP token acquisition in 5 seconds...")   

async def main():
    while True: 
        url = await get_authenticated_ws_url()
        if not url: await asyncio.sleep(2)
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
                await ws.send(json.dumps({"ticks": "R_10", "subscribe": 1}))
                await ws.send(json.dumps({"ticks": "R_25", "subscribe": 1}))
                await ws.send(json.dumps({"ticks": "R_50", "subscribe": 1}))
                await ws.send(json.dumps({"ticks": "R_75", "subscribe": 1}))
                await ws.send(json.dumps({"ticks": "R_100", "subscribe": 1}))
                await process_ticks(ws)
        except Exception as e:
            logging.error(f"Interface connection lost: {e}. Attempting to reconnect in 5 seconds...")
            await main()
            await asyncio.sleep(5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("System gracefully halted.")