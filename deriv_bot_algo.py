import asyncio
import json
import os
import websockets
import logging
import requests
import secrets

# --- Configuration ---
app_id = '32WzmZD0GdX5NdJKlPO7e'
api_token = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1'
deriv_account_id = 'DOT90416964'

APP_ID = os.getenv('DERIV_APP_ID', app_id) 
API_TOKEN = os.getenv('DERIV_API_TOKEN', api_token) 
DERIV_REST_OTP_URL = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"

# Risk & Scaling Settings
SUPPORTED_SYMBOLS = ['R_10', 'R_25', 'R_50', 'R_75', 'R_100']
RISK_PERCENTAGE = 0.0001   # 0.01% of the total wallet account balance
BASE_ENTRY_FLOOR = 0.35        # Deriv API absolute entry option floor
MARTINGALE_MULTIPLIER = 1
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
calculated_target_stake = BASE_ENTRY_FLOOR  

# --- REBATE ARCHITECTURE COUNTERS ---
session_rebate_pool = 0.0  
all_time_rebate_pool = 0.0

def calculate_base_percentage_stake():
    global account_balance, session_rebate_pool
    computed_base = (account_balance - session_rebate_pool) * RISK_PERCENTAGE * 2
    if computed_base < BASE_ENTRY_FLOOR:
        return BASE_ENTRY_FLOOR
    return round(computed_base, 2)

def generate_random_choice_ticks():
    return round(secrets.choice([secrets.SystemRandom().uniform(1, 4) for _ in range(4)]))

def generate_random_choice_stake():
    stake = secrets.SystemRandom().uniform(1, 2)
    logging.info(f"stake: {stake}")
    return stake

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
        global last_contract_id, max_historical_loss, total_net_pnl, session_net_pnl, all_time_rebate_pool, session_rebate_pool, calculated_target_stake
        
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
                    
            logging.info(f"[RESULT] WIN (+${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Rebate Pool: ${session_rebate_pool} | Shaved 100% into Pool.")
            calculated_target_stake = calculate_base_percentage_stake()
        else:
            logging.info(f"[RESULT] LOSS (${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Rebate Pool: ${session_rebate_pool} ")

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
                             
                    trade_direction = secrets.SystemRandom().choice(["PUT", "CALL"])
                    last_contract_id = await execute_trade(websocket, trade_direction, calculated_target_stake)
                    
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
                        global account_balance
                        account_balance = float(auth_res['authorize']['balance'])
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
            asyncio.run(main())
            await asyncio.sleep(5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("System gracefully halted.")