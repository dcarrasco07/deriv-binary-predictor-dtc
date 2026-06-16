import asyncio
import json
import os
import websockets
import logging
import requests
import sqlite3
from collections import deque
import time

# --- Configuration ---
app_id = '32WzmZD0GdX5NdJKlPO7e'
# api_token = 'pat_bc78db629feabf69a853ede8323ef15e2b35301f4af90273bfdd0c380edddda1'
# deriv_account_id = 'ROT91151098'

api_token = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1'
deriv_account_id = 'DOT90416964'

APP_ID = os.getenv('DERIV_APP_ID', app_id) 
API_TOKEN = os.getenv('DERIV_API_TOKEN', api_token) 

DERIV_REST_OTP_URL = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"

# Risk Management & Martingale Settings from JS
BET_AMOUNT = 0.35
CURRENCY = 'USD'
MARTINGALE_MULTIPLIER = 2.5
TICK_DURATION = 1
SYMBOL = 'R_100'  
WINDOW_SIZES = range(2, 101) 

# Storage Files
os.makedirs('data', exist_ok=True)
DB_FILE = os.path.join('data', 'predictor_data.db')
MARKDOWN_SUMMARY_FILE = os.path.join('data', 'live_dashboard.md')

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()])

# --- Shared Global Memory Spaces ---
last_raw_tick = None
windowed_movements = {}
predictions_correct = {}
pattern_stats = {}

current_stake = BET_AMOUNT
last_contract_id = None
is_processing = False

# JavaScript Pattern Memory Management Simulators
last_pattern = "00"
actual_last_pattern = "00"
signal_flag = "0"
actual_signal = "0"

# --- Metrics Memory ---
max_historical_loss = 0.0

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
        CREATE TABLE IF NOT EXISTS pattern_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_size INTEGER,
            pattern_str TEXT,
            result_str TEXT,
            is_correct INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
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

# --- Rebuild State from SQLite on Crash Recovery ---
def recover_state_from_db():
    """Queries SQLite history to cleanly rebuild runtime movement matrices and historical loss thresholds."""
    global last_raw_tick, windowed_movements, predictions_correct, pattern_stats, max_historical_loss
    
    windowed_movements = {size: deque(maxlen=size) for size in WINDOW_SIZES}
    predictions_correct = {size: {'correct': 0, 'total': 0} for size in WINDOW_SIZES}
    pattern_stats = {size: {} for size in WINDOW_SIZES}
    last_raw_tick = None

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT MIN(profit_loss) FROM trade_results")
    row_loss = cursor.fetchone()
    if row_loss and row_loss[0] is not None:
        max_historical_loss = abs(row_loss[0])
    else:
        max_historical_loss = 0.0
    
    cursor.execute('''
        SELECT window_size, pattern_str, result_str, SUM(is_correct), COUNT(*) 
        FROM pattern_records 
        GROUP BY window_size, pattern_str, result_str
    ''')
    for row in cursor.fetchall():
        w_size, p_str, r_str, corrects, totals = row
        predictions_correct[w_size]['correct'] += corrects
        predictions_correct[w_size]['total'] += totals
        pattern_stats[w_size][(p_str, r_str)] = {'correct': corrects, 'total': totals}
        
    cursor.execute("SELECT epoch, quote FROM ticks ORDER BY epoch DESC LIMIT 105")
    historical_ticks = cursor.fetchall()[::-1]
    
    if historical_ticks:
        for epoch, quote in historical_ticks:
            current_tick = {'epoch': epoch, 'quote': quote}
            if last_raw_tick is not None:
                step_direction = '1' if current_tick['quote'] < last_raw_tick['quote'] else '0'
                for size in WINDOW_SIZES:
                    windowed_movements[size].append(step_direction)
            last_raw_tick = current_tick
            
    conn.close()
    logging.info("Crash survival matrix fully restored from persistent SQLite storage engine.")

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
        logging.info(f"OTP Response Status: {response.status_code}")
        if response.status_code == 200:
            res_json = response.json()
            logging.info(f"OTP Response Data: {res_json}")
            ws_url = res_json.get('data', {}).get('url')
            if ws_url:
                return ws_url
    except Exception as e:
        logging.error(f"[BOT] Network error handling API proxy layout: {e}")
    return None

def make_prediction_from_movements(movement_window):
    if len(movement_window) < 1:
        return None
    return 0 if movement_window[-1] == movement_window[0] else 1

def write_readable_markdown_summary(last_quote):
    """Generates a highly readable markdown dashboard page from SQLite aggregation maps."""
    global predictions_correct, pattern_stats, max_historical_loss
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        with open(MARKDOWN_SUMMARY_FILE, 'w') as f:
            f.write(f"# DERIV STRATEGY RUNTIME ENGINE DASHBOARD\n\n")
            f.write(f"* **Last Update Sync**: `{timestamp}`\n")
            f.write(f"* **Monitored Account**: `{deriv_account_id}`\n")
            f.write(f"* **Active Asset Price**: `{last_quote}`\n")
            f.write(f"* **Underlying Database Engine**: `{DB_FILE}`\n\n")
            f.write(f"---\n\n")
            
            for size in WINDOW_SIZES:
                stats = predictions_correct[size]
                total = stats['total']
                if total == 0:
                    continue
                    
                correct = stats['correct']
                rate = (correct / total) * 100
                
                f.write(f"## WINDOW CONFIG SIZE: {size} (Prev Movements Count)\n")
                f.write(f"* **Evaluations Cumulative**: {total}\n")
                f.write(f"* **Successful Correct Hits**: {correct}\n")
                f.write(f"* **Window Prediction Accuracy**: `{rate:.2f}%`\n\n")
                f.write(f"### Pattern Sequencer Layouts Breakdown\n")
                
                size_patterns = pattern_stats[size]
                for (pattern_str, result_str), p_stats in sorted(size_patterns.items()):
                    p_total = p_stats['total']
                    p_correct = p_stats['correct']
                    p_rate = (p_correct / p_total) * 100
                    
                    f.write(f"* Pattern `{pattern_str}` (window) => `{result_str}` (result) -> Accuracy: **{p_rate:.2f}%** ({p_correct}/{p_total} hits)\n")
                
                f.write(f"\n---\n\n")
            
            f.write(f"## GLOBAL TRADING PERFORMANCE RISK ANALYSIS\n")
            f.write(f"* **Largest Loss Encountered (All-Time)**: `-${max_historical_loss:.2f} {CURRENCY}`\n\n")
            f.write(f"---\n")
            
    except Exception as e:
        logging.error(f"Error drawing down readable report markdown file: {e}")

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
                logging.info(f"[TRADE] Placed {direction} | ID: {contract_id} | Stake: ${stake:.2f}")
                return contract_id
    except Exception as e:
        logging.error(f"[BOT] Trade execution error: {e}")
        return None

async def check_last_trade_result(websocket):
    """Determines winning loops or executes Martingale multiplications via API basic messaging rules."""
    global last_contract_id, current_stake, signal_flag, actual_signal, last_pattern, actual_last_pattern, max_historical_loss
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
                
                signal_flag = "1" if contract.get('contract_type') == "PUT" else "0"
                
                if contract.get('is_sold'):
                    profit = float(contract.get('profit', 0))
                    
                    db_conn = sqlite3.connect(DB_FILE)
                    db_cursor = db_conn.cursor()
                    db_cursor.execute("INSERT OR IGNORE INTO trade_results (contract_id, profit_loss) VALUES (?, ?)", (last_contract_id, profit))
                    db_conn.commit()
                    db_conn.close()
                    
                    if profit > 0:
                        logging.info(f"[RESULT] WIN (+${profit:.2f}). Resetting stake to ${BET_AMOUNT}")
                        actual_signal = "1" if contract.get('contract_type') == "PUT" else "0"
                        current_stake = BET_AMOUNT
                    else:
                        logging.info(f"[RESULT] LOSS (${profit:.2f}). Martingale stake: ${current_stake * MARTINGALE_MULTIPLIER:.2f}")
                        current_stake = current_stake * MARTINGALE_MULTIPLIER
                        actual_signal = "0" if contract.get('contract_type') == "PUT" else "1"
                        
                        loss_magnitude = abs(profit)
                        if loss_magnitude > max_historical_loss:
                            max_historical_loss = loss_magnitude
                    
                    last_pattern += signal_flag
                    actual_last_pattern += actual_signal
                    
                    last_contract_id = None 
                break
    except Exception as e:
        logging.error(f"[BOT] Error checking result framework: {e}")

async def process_ticks(websocket):
    global last_raw_tick, windowed_movements, predictions_correct, pattern_stats, last_contract_id, current_stake

    async for message in websocket:
        try:
            payload = json.loads(message)
            stats_updated = False
            active_prediction = None
            
            if payload.get('msg_type') == 'tick' and 'tick' in payload:
                tick_data = payload['tick']
                current_tick = {
                    'epoch': int(tick_data['epoch']),
                    'quote': float(tick_data['quote'])
                }
                
                db_conn = sqlite3.connect(DB_FILE)
                db_cursor = db_conn.cursor()
                db_cursor.execute("INSERT OR IGNORE INTO ticks (epoch, quote) VALUES (?, ?)", (current_tick['epoch'], current_tick['quote']))

                if last_raw_tick is not None:
                    step_direction = '1' if current_tick['quote'] < last_raw_tick['quote'] else '0'

                    for size in WINDOW_SIZES:
                        window_deque = windowed_movements[size]
                        
                        if len(window_deque) == size:
                            predicted_outcome = make_prediction_from_movements(window_deque)
                            current_pattern = "".join(window_deque)
                            actual_outcome = int(step_direction)

                            if predicted_outcome is not None:
                                # Captures the prediction generated by the active streaming engine window loops
                                active_prediction = predicted_outcome
                                is_hit = (predicted_outcome == actual_outcome)
                                result_str = str(actual_outcome)
                                
                                db_cursor.execute('''
                                    INSERT INTO pattern_records (window_size, pattern_str, result_str, is_correct)
                                    VALUES (?, ?, ?, ?)
                                ''', (size, current_pattern, result_str, 1 if is_hit else 0))
                                
                                predictions_correct[size]['total'] += 1
                                if is_hit:
                                    predictions_correct[size]['correct'] += 1
                                    
                                stat_key = (current_pattern, result_str)
                                if stat_key not in pattern_stats[size]:
                                    pattern_stats[size][stat_key] = {'correct': 0, 'total': 0}
                                pattern_stats[size][stat_key]['total'] += 1
                                if is_hit:
                                    pattern_stats[size][stat_key]['correct'] += 1
                                    
                                stats_updated = True

                        window_deque.append(step_direction)

                last_raw_tick = current_tick
                db_conn.commit()
                db_conn.close()
                
                if stats_updated:
                    write_readable_markdown_summary(current_tick['quote'])
                
                # --- AUTOMATED LIVE TRADING EVALUATION SEQUENCE ---
                if last_contract_id:
                    await check_last_trade_result(websocket)
                elif active_prediction is not None:
                    # Maps 0 -> CALL, 1 -> PUT based explicitly on Python generated prediction analytics
                    trade_direction = "CALL" if active_prediction == 0 else "PUT"
                    logging.info(f"[SIGNAL] Executing trade from Prediction Matrix logic value: -> {trade_direction}")
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
    
    logging.info('[BOT] Starting bot...')
    logging.info(f'[BOT] Running. Base stake: ${BET_AMOUNT} | Martingale: {MARTINGALE_MULTIPLIER}x | Duration: {TICK_DURATION} ticks')
    
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