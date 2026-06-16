import asyncio
import json
import os
import websockets
import logging
import requests
import sqlite3
import secrets  # Core cryptographically secure entropy source
import time

# --- Configuration ---
app_id = '32WzmZD0GdX5NdJKlPO7e'
api_token = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1'
deriv_account_id = 'DOT90416964'

APP_ID = os.getenv('DERIV_APP_ID', app_id) 
API_TOKEN = os.getenv('DERIV_API_TOKEN', api_token) 

DERIV_REST_OTP_URL = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"

# Risk Management & Martingale Settings
BET_AMOUNT = 0.35
CURRENCY = 'USD'
MARTINGALE_MULTIPLIER = 1.2
MAX_STAKE_CEILING = 142.0  
TICK_DURATION = 1
SYMBOL = 'R_100'  

# Storage Files
os.makedirs('data', exist_ok=True)
DB_FILE = os.path.join('data', 'predictor_data.db')
MARKDOWN_SUMMARY_FILE = os.path.join('data', 'live_dashboard.md')

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()])

# --- Shared Global Memory Spaces ---
current_stake = BET_AMOUNT
last_contract_id = None
is_processing = False

# --- Metrics Memory ---
max_historical_loss = 0.0
total_net_pnl = 0.0      
session_net_pnl = 0.0    

# --- Database Core Setup ---
def init_db():
    """Initializes the persistent SQLite schema for tracking state."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ticks (
            epoch INTEGER PRIMARY KEY,
            quote REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            action TEXT,
            transaction_time INTEGER,
            captured_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trade_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER UNIQUE,
            profit_loss REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# --- Rebuild State on Fresh Boot ---
def recover_state_from_db():
    """Queries history to calculate maximum lifetime loss and total running profit."""
    global max_historical_loss, total_net_pnl, session_net_pnl, current_stake
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT MIN(profit_loss) FROM trade_results")
    row_loss = cursor.fetchone()
    if row_loss and row_loss[0] is not None:
        max_historical_loss = abs(row_loss[0])
    else:
        max_historical_loss = 0.0
        
    cursor.execute("SELECT SUM(profit_loss) FROM trade_results")
    row_pnl = cursor.fetchone()
    if row_pnl and row_pnl[0] is not None:
        total_net_pnl = float(row_pnl[0])
    else:
        total_net_pnl = 0.0
        
    conn.close()
    
    session_net_pnl = 0.0
    current_stake = BET_AMOUNT
    logging.info("Crypto Random Execution engine online. Session risk tracking reset to $0.00.")

def get_authenticated_ws_url():
    """Fetches WebSocket URL using the JS architecture logic pattern via REST."""
    headers = {
        "Deriv-App-ID": APP_ID,
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        logging.info("[BOT] Requesting authorized dynamic pipeline mapping...")
        response = requests.post(DERIV_REST_OTP_URL, headers=headers, timeout=10)
        if response.status_code == 200:
            res_json = response.json()
            ws_url = res_json.get('data', {}).get('url')
            if ws_url:
                return ws_url
    except Exception as e:
        logging.error(f"[BOT] Network error handling API proxy layout: {e}")
    return None

def write_readable_markdown_summary(last_quote):
    """Generates a streamlined dashboard focusing entirely on financial risk performance."""
    global max_historical_loss, total_net_pnl, session_net_pnl, current_stake
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        with open(MARKDOWN_SUMMARY_FILE, 'w') as f:
            f.write(f"# DERIV WIN-STREAK REVERSE REWARD ENGINE\n\n")
            f.write(f"* **Last Update Sync**: `{timestamp}`\n")
            f.write(f"* **Monitored Account**: `{deriv_account_id}`\n")
            f.write(f"* **Active Asset Price**: `{last_quote}`\n")
            f.write(f"* **Underlying Database Engine**: `{DB_FILE}`\n\n")
            f.write(f"---\n\n")
            
            f.write(f"## GLOBAL TRADING PERFORMANCE RISK ANALYSIS\n")
            pnl_sign = "+" if total_net_pnl >= 0 else ""
            session_sign = "+" if session_net_pnl >= 0 else ""
            f.write(f"* **Total Net Profit/Loss (All-Time History)**: `{pnl_sign}${total_net_pnl:.2f} {CURRENCY}`\n")
            f.write(f"* **Current Session Net Profit/Loss**: `{session_sign}${session_net_pnl:.2f} {CURRENCY}`\n")
            f.write(f"* **Largest Loss Encountered (All-Time)**: `-${max_historical_loss:.2f} {CURRENCY}`\n")
            f.write(f"* **Current Active Position Stake Target**: `${current_stake:.2f} {CURRENCY}` (`WIN MULTIPLY MODE`)\n\n")
            f.write(f"---\n")
    except Exception as e:
        logging.error(f"Error writing report markdown file: {e}")

async def execute_trade(websocket, direction, stake):
    """Executes proposal creation and purchase steps mapped precisely from the JS client logic."""
    try:
        proposal_req = {
            "proposal": 1,
            "amount": float(f"{stake:.2f}"),
            "basis": "stake",
            "contract_type": direction,
            "currency": CURRENCY,
            "duration": TICK_DURATION,
            "duration_unit": "t",
            "underlying_symbol": SYMBOL
        }
        await websocket.send(json.dumps(proposal_req))
        
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'proposal':
                if 'error' in res:
                    raise Exception(f"Proposal failed: {res['error']['message']}")
                proposal_id = res['proposal']['id']
                break
        
        buy_req = {
            "buy": proposal_id,
            "price": float(f"{stake:.2f}")
        }
        await websocket.send(json.dumps(buy_req))
        
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'buy':
                if 'error' in res:
                    if res['error'].get('code') == 'InsufficientBalance':
                        logging.error("[BOT] Insufficient Balance. Resetting stake to initial BET_AMOUNT.")
                        global current_stake
                        current_stake = BET_AMOUNT
                    raise Exception(f"Buy failed: {res['error']['message']}")
                
                contract_id = res['buy']['contract_id']
                logging.info(f"[TRADE] Placed Random {direction} | ID: {contract_id} | Stake: ${stake:.2f}")
                return contract_id
    except Exception as e:
        logging.error(f"[BOT] Trade execution error: {e}")
        return None

async def check_last_trade_result(websocket):
    """Determines contract results, applying Martingale logic to WINS instead of losses."""
    global last_contract_id, current_stake, max_historical_loss, total_net_pnl, session_net_pnl, MARTINGALE_MULTIPLIER
    if not last_contract_id:
        return

    try:
        req = {"proposal_open_contract": 1, "contract_id": last_contract_id}
        await websocket.send(json.dumps(req))
        
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'proposal_open_contract':
                contract = res.get('proposal_open_contract')
                if not contract:
                    return
                
                if contract.get('is_sold'):
                    profit = float(contract.get('profit', 0))
                    
                    db_conn = sqlite3.connect(DB_FILE)
                    db_cursor = db_conn.cursor()
                    db_cursor.execute("INSERT OR IGNORE INTO trade_results (contract_id, profit_loss) VALUES (?, ?)", (last_contract_id, profit))
                    db_conn.commit()
                    db_conn.close()
                    
                    total_net_pnl += profit
                    session_net_pnl += profit
                    
                    if profit > 0:
                        # --- MARTINGALE ON WIN LOGIC ---
                        if current_stake >= MAX_STAKE_CEILING:
                            current_stake = BET_AMOUNT
                            logging.info(f"[RESULT] WIN (+${profit:.2f}) AT CEILING. Target accomplished! Resetting progression loop to base: ${BET_AMOUNT}")
                        else:
                            next_calculated_martingale = current_stake * MARTINGALE_MULTIPLIER
                            
                            if next_calculated_martingale >= MAX_STAKE_CEILING:
                                current_stake = MAX_STAKE_CEILING
                                logging.info(f"[RESULT] WIN (+${profit:.2f}). Win-streak scaling hit maximum threshold. Locking next position at ceiling: ${current_stake:.2f}")
                            else:
                                current_stake = next_calculated_martingale
                                logging.info(f"[RESULT] WIN (+${profit:.2f}). Scaling up Martingale layer stack on winning trajectory to: ${current_stake:.2f}")
                    else:
                        # --- HARD RESET ON LOSS LOGIC ---
                        current_stake = BET_AMOUNT
                        logging.info(f"[RESULT] LOSS (${profit:.2f}). Win-streak broken. Hard resetting stake back to base entry floor: ${BET_AMOUNT}")
                        
                        loss_magnitude = abs(profit)
                        if loss_magnitude > max_historical_loss:
                            max_historical_loss = loss_magnitude
                            
                    last_contract_id = None 
                break
    except Exception as e:
        logging.error(f"[BOT] Error checking result framework: {e}")

async def process_ticks(websocket):
    global last_contract_id, current_stake

    async for message in websocket:
        try:
            payload = json.loads(message)
            
            if payload.get('msg_type') == 'tick' and 'tick' in payload:
                tick_data = payload['tick']
                current_quote = float(tick_data['quote'])
                
                db_conn = sqlite3.connect(DB_FILE)
                db_cursor = db_conn.cursor()
                db_cursor.execute("INSERT OR IGNORE INTO ticks (epoch, quote) VALUES (?, ?)", (int(tick_data['epoch']), current_quote))
                db_conn.commit()
                db_conn.close()
                
                write_readable_markdown_summary(current_quote)
                
                # --- AUTOMATED UNBIASED RANDOM DISPATCH LAYER ---
                if last_contract_id:
                    await check_last_trade_result(websocket)
                else:
                    trade_direction = secrets.choice(["CALL", "PUT"])
                    logging.info(f"[RANDOMIZATION-ENGINE] System selected trajectory: {trade_direction}")
                    last_contract_id = await execute_trade(websocket, trade_direction, current_stake)

            elif payload.get('msg_type') == 'transaction' and 'transaction' in payload:
                t_data = payload['transaction']
                db_conn = sqlite3.connect(DB_FILE)
                db_cursor = db_conn.cursor()
                db_cursor.execute('''
                    INSERT INTO transactions (symbol, action, transaction_time)
                    VALUES (?, ?, ?)
                ''', (t_data.get('symbol'), t_data.get('action'), t_data.get('transaction_time')))
                db_conn.commit()
                db_conn.close()

        except Exception as e:
            logging.error(f"Error processing packet frame: {e}")

async def main():
    retry_delay = 5
    init_db()
    recover_state_from_db()
    
    logging.info('[BOT] Starting reverse win-streak random trade bot...')
    logging.info(f'[BOT] Running. Base stake: ${BET_AMOUNT} | Martingale Ceiling Max: ${MAX_STAKE_CEILING} | Duration: {TICK_DURATION} ticks')
    
    while True:
        authenticated_ws_url = get_authenticated_ws_url()
        if not authenticated_ws_url:
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
            continue 

        try:
            async with websockets.connect(authenticated_ws_url) as websocket:
                auth_packet = {"authorize": API_TOKEN}
                await websocket.send(json.dumps(auth_packet))
                
                async for message in websocket:
                    auth_res = json.loads(message)
                    if auth_res.get('msg_type') == 'authorize':
                        if 'error' in auth_res:
                            raise Exception(f"Authorization Rejected: {auth_res['error']['message']}")
                        logging.info("[BOT] Connected, Authorized, and Subscribed to ticks.")
                        break
                
                retry_delay = 5
                await websocket.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
                await websocket.send(json.dumps({"transaction": 1, "subscribe": 1}))
                await process_ticks(websocket) 
        except Exception as e:
            logging.error(f"WebSocket interface execution error: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("System gracefully halted.")