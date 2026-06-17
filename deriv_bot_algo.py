import asyncio
import json
import os
import websockets
import logging
import requests
import sqlite3
import secrets
import time

# --- Configuration ---
app_id = '32WzmZD0GdX5NdJKlPO7e'
api_token = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1'
deriv_account_id = 'DOT90416964'

APP_ID = os.getenv('DERIV_APP_ID', app_id) 
API_TOKEN = os.getenv('DERIV_API_TOKEN', api_token) 
DERIV_REST_OTP_URL = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"

# Risk & Scaling Settings
RISK_PERCENTAGE = 0.0001   # 0.01% of the total wallet account balance
BET_AMOUNT = 0.35
MARTINGALE_MULTIPLIER = 2.5     # Standard 2.5x Martingale scale step
MAX_STAKE_CEILING = 100.00     # Hard maximum stake barrier limit allowed
BASE_ENTRY_FLOOR = 0.35        # Deriv API absolute entry option floor
PROFIT_SHAVE_RATE = 0.30       # Shaves off exactly 30% of clean wins
CURRENCY = 'USD'
TICK_DURATION = 1
SYMBOL = 'R_100'  

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

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS ticks (epoch INTEGER PRIMARY KEY, quote REAL)')
    cursor.execute('CREATE TABLE IF NOT EXISTS trade_results (id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id INTEGER UNIQUE, profit_loss REAL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rebate_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,          
            amount REAL,          
            contract_id INTEGER,  
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def recover_state_from_db():
    global max_historical_loss, total_net_pnl, session_net_pnl, all_time_rebate_pool, session_rebate_pool
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT MIN(profit_loss) FROM trade_results")
    row_loss = cursor.fetchone()
    max_historical_loss = abs(row_loss[0]) if row_loss and row_loss[0] is not None else 0.0
    
    cursor.execute("SELECT SUM(profit_loss) FROM trade_results")
    row_pnl = cursor.fetchone()
    total_net_pnl = float(row_pnl[0]) if row_pnl and row_pnl[0] is not None else 0.0
    
    cursor.execute('''
        SELECT SUM(amount) FROM rebate_ledger WHERE action = 'CREDIT'
    ''')
    row_pool = cursor.fetchone()
    all_time_rebate_pool = float(row_pool[0]) if row_pool and row_pool[0] is not None else 0.0
    conn.close()
    
    session_net_pnl = 0.0
    session_rebate_pool = 0.0
    logging.info("Dynamic Offset Session Engine Online. Current Session Pool set to accumulation mode.")

def log_rebate_ledger_entry(action, amount, contract_id=None):
    try:
        db_conn = sqlite3.connect(DB_FILE)
        db_cursor = db_conn.cursor()
        db_cursor.execute('''
            INSERT INTO rebate_ledger (action, amount, contract_id)
            VALUES (?, ?, ?)
        ''', (action, float(amount), contract_id))
        db_conn.commit()
        db_conn.close()
    except Exception as e:
        logging.error(f"Failed to record transaction to ledger system: {e}")

def calculate_base_percentage_stake():
    global account_balance, session_rebate_pool
    if account_balance <= 0:
        return BASE_ENTRY_FLOOR
    computed_base = (account_balance - session_rebate_pool) * RISK_PERCENTAGE * BET_AMOUNT
    if computed_base < BASE_ENTRY_FLOOR:
        return BASE_ENTRY_FLOOR
    return round(computed_base, 2)

def write_readable_markdown_summary(last_quote):
    global max_historical_loss, total_net_pnl, session_net_pnl, all_time_rebate_pool, session_rebate_pool, calculated_target_stake, account_balance
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(MARKDOWN_SUMMARY_FILE, 'w') as f:
            f.write(f"# DERIV OFFSET REBATE ACCUMULATION DASHBOARD\n\n")
            f.write(f"* **Last Update Sync**: `{timestamp}`\n")
            f.write(f"* **Active Asset Price**: `{last_quote}` (`{SYMBOL}`)\n\n")
            f.write(f"---\n\n")
            
            f.write(f"## ACTIVE RISK SHIELD AND OFFSET ANALYSIS\n")
            f.write(f"* **Session Accumulating Rebate Pool**: `[ ${session_rebate_pool:.4f} {CURRENCY} ]` (Never Deducted)\n")
            f.write(f"* **Raw Dynamic Target Size**: `${calculated_target_stake:.2f} {CURRENCY}`\n")
            f.write(f"* **Final Dispatched Placed Stake**: **`${calculated_target_stake:.2f} {CURRENCY}`**\n")
            f.write(f"* **All-Time Cumulative Rebate History**: `${all_time_rebate_pool:.4f} {CURRENCY}`\n\n")
            
            f.write(f"## FINANCIAL PERFORMANCE TRACKING OVERVIEW\n")
            pnl_sign = "+" if total_net_pnl >= 0 else ""
            session_sign = "+" if session_net_pnl >= 0 else ""
            f.write(f"* **Current Session Net Profit/Loss**: `{session_sign}${session_net_pnl:.2f} {CURRENCY}`\n")
            f.write(f"* **Total Net Profit/Loss (All-Time)**: `{pnl_sign}${total_net_pnl:.2f} {CURRENCY}`\n")
            f.write(f"* **Largest Single Loss Encountered**: `-${max_historical_loss:.2f} {CURRENCY}`\n\n")
            f.write(f"---\n")
    except Exception as e:
        logging.error(f"Error writing markdown summary: {e}")

async def execute_trade(websocket, direction, stake):
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
                if 'error' in res: raise Exception(res['error']['message'])
                proposal_id = res['proposal']['id']
                break
        
        await websocket.send(json.dumps({"buy": proposal_id, "price": float(f"{stake:.2f}")}))
        async for message in websocket:
            res = json.loads(message)
            if res.get('msg_type') == 'buy':
                if 'error' in res: raise Exception(res['error']['message'])
                contract_id = res['buy']['contract_id']
                logging.info(f"[TRADE] Dispatched Offset Position {direction} | ID: {contract_id} | Placed Stake: ${stake:.2f}")
                
                # --- FIXED: Establish an automatic push stream connection straight to the broker ---
                await websocket.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1}))
                return contract_id
    except Exception as e:
        logging.error(f"[BOT] Trade Request Blocked: {e}")
        return None

def handle_settlement_data(contract):
    """Processes streamed contract packets pushed automatically via data feeds."""
    global last_contract_id, max_historical_loss, total_net_pnl, session_net_pnl, all_time_rebate_pool, session_rebate_pool, calculated_target_stake
    
    # Ignore open packets; wait for the final message package
    if not contract or not contract.get('is_sold'): 
        return

    profit = float(contract.get('profit', 0))
    contract_id = contract.get('contract_id')
    
    db_conn = sqlite3.connect(DB_FILE)
    db_cursor = db_conn.cursor()
    db_cursor.execute("INSERT OR IGNORE INTO trade_results (contract_id, profit_loss) VALUES (?, ?)", (contract_id, profit))
    db_conn.commit()
    db_conn.close()
    
    total_net_pnl += profit
    session_net_pnl += profit
    
    session_sign = "+" if profit >= 0 else ""
    
    if profit > 0:
        shaved_allocation = profit * PROFIT_SHAVE_RATE
        
        session_rebate_pool += shaved_allocation
        all_time_rebate_pool += shaved_allocation
        
        log_rebate_ledger_entry('CREDIT', shaved_allocation, contract_id)
        
        logging.info(f"[RESULT] WIN (+${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Rebate Pool: ${session_rebate_pool} | Shaved 30% (+${shaved_allocation:.4f}) into Pool.")
        calculated_target_stake = calculate_base_percentage_stake()
    else:
        logging.info(f"[RESULT] LOSS (${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Rebate Pool: ${session_rebate_pool} ")
        loss_magnitude = abs(profit)
        if loss_magnitude > max_historical_loss:
            max_historical_loss = loss_magnitude
        
        if calculated_target_stake >= MAX_STAKE_CEILING:
            calculated_target_stake = calculate_base_percentage_stake()
            logging.info(f"[CEILING RESET] Loss encountered at Max Ceiling. Hard resetting dynamic target back to baseline balance percentage: ${calculated_target_stake:.2f}")
        else:
            # --- FIXED: Multiply against previous base scale path cleanly ---
            next_calculated_step = calculated_target_stake * MARTINGALE_MULTIPLIER
            if next_calculated_step >= MAX_STAKE_CEILING:
                calculated_target_stake = MAX_STAKE_CEILING
                logging.warning(f"[CEILING MET] Scaling hit maximum threshold. Locking next trade target at ceiling: ${calculated_target_stake:.2f}")
            else:
                calculated_target_stake = round(next_calculated_step, 2)
    
    # Unlock pipeline for the next trade iteration
    last_contract_id = None

async def process_ticks(websocket):
    global last_contract_id, calculated_target_stake, account_balance

    async for message in websocket:
        try:
            payload = json.loads(message)
            
            if 'balance' in payload:
                account_balance = float(payload['balance']['balance'])
                if not last_contract_id:
                    calculated_target_stake = calculate_base_percentage_stake()
            
            # --- FIXED: Capture automated push stream packets instantly as they bypass the tick engine ---
            elif payload.get('msg_type') == 'proposal_open_contract':
                contract = payload.get('proposal_open_contract')
                handle_settlement_data(contract)

            elif payload.get('msg_type') == 'tick' and 'tick' in payload:
                current_quote = float(payload['tick']['quote'])
                write_readable_markdown_summary(current_quote)
                
                if not last_contract_id:
                    logging.info(f"[FUNDS ROUTER] Dispatching trade frame. Raw Target: ${calculated_target_stake:.2f}")
                    
                    trade_direction = "CALL"
                    last_contract_id = await execute_trade(websocket, trade_direction, calculated_target_stake)
                    
        except Exception as e:
            logging.error(f"Error processing payload frame: {e}")

def get_authenticated_ws_url():
    try:
        response = requests.post(DERIV_REST_OTP_URL, headers={"Deriv-App-ID": APP_ID, "Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}, timeout=10)
        if response.status_code == 200: return response.json().get('data', {}).get('url')
    except Exception as e: logging.error(f"OTP rest failed: {e}")
    return None

async def main():
    init_db()
    recover_state_from_db()
    while True:
        url = get_authenticated_ws_url()
        if not url: await asyncio.sleep(5); continue
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
                await ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))
                await process_ticks(ws)
        except Exception as e:
            logging.error(f"Interface connection lost: {e}"); await asyncio.sleep(5)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("System gracefully halted.")