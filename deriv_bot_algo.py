import asyncio
import json
import os
import websockets
import logging
import requests
import random
import secrets
import hashlib
import base64
import webbrowser
import http.server
import urllib.parse
import threading
import time

# --- Configuration ---
app_id = '32WzmZD0GdX5NdJKlPO7e'
api_token = 'pat_9c0c77c7b2ee5b2dc437671e543f4325dfd5b136c2cc46491e37dbb8414c9171'
deriv_account_id = 'DOT90416964'

APP_ID = os.getenv('DERIV_APP_ID', app_id) 
API_TOKEN = os.getenv('DERIV_API_TOKEN', api_token) 
DERIV_REST_OTP_URL = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"

# OAuth2 PKCE Configuration
OAUTH_CLIENT_ID = api_token  # Replace with your registered OAuth2 Client ID
OAUTH_REDIRECT_URI = 'http://localhost:8080/callback' # Replace with your registered redirect URI
OAUTH_AUTHORIZATION_URL = 'https://auth.deriv.com/oauth2/auth'
OAUTH_TOKEN_URL = 'https://auth.deriv.com/oauth2/token'

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
tradeable_pool = 0.0
calculated_target_stake = BASE_ENTRY_FLOOR  
consecutive_losses = 0  
last_trade_direction = "PUT"
pkce_code_verifier = None
oauth_state = None
oauth_authorization_code = None
oauth_access_token = None
oauth_refresh_token = None
oauth_token_expiry = 0 # Unix timestamp of when the token expires
reconnect_delay = 1 # Initial reconnect delay in seconds
max_reconnect_delay = 60 # Maximum reconnect delay in seconds
request_id_counter = 0

# --- REBATE ARCHITECTURE COUNTERS ---
session_rebate_pool = 0.0  
all_time_rebate_pool = 0.0

def calculate_base_percentage_stake():
    global tradeable_pool
    computed_base = tradeable_pool * RISK_PERCENTAGE
    if computed_base < BASE_ENTRY_FLOOR:
        return BASE_ENTRY_FLOOR
    return round(computed_base, 2)

def generate_random_choice_ticks():
    return round(secrets.choice([secrets.SystemRandom().uniform(1, 4) for _ in range(4)]))

def generate_random_choice_symbol():
    return SUPPORTED_SYMBOLS[round(secrets.choice([secrets.SystemRandom().uniform(1, 4) for _ in range(4)]))]

def generate_pkce_params():
    code_verifier = secrets.token_urlsafe(64)  # Generate a random string (43-128 characters)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().replace('=', '')
    state = secrets.token_urlsafe(16)  # Generate a random state for CSRF protection
    return code_verifier, code_challenge, state

def initiate_oauth_flow():
    global pkce_code_verifier, oauth_state
    code_verifier, code_challenge, state = generate_pkce_params()
    pkce_code_verifier = code_verifier
    oauth_state = state

    auth_url = (
        f"{OAUTH_AUTHORIZATION_URL}?"
        f"response_type=code&"
        f"client_id={OAUTH_CLIENT_ID}&"
        f"redirect_uri={OAUTH_REDIRECT_URI}&"
        f"scope=trade+account_manage&"
        f"state={state}&"
        f"code_challenge={code_challenge}&"
        f"code_challenge_method=S256"
    )
    logging.info(f"Please open this URL in your browser to authenticate: {auth_url}")
    webbrowser.open(auth_url)

# --- OAuth Callback Server ---
class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global oauth_authorization_code, oauth_state
        parsed_url = urllib.parse.urlparse(self.path)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        code = query_params.get('code', [None])[0]
        state = query_params.get('state', [None])[0]

        if code and state:
            if state == oauth_state:
                oauth_authorization_code = code
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"Authentication successful! You can close this window.")
                logging.info("Authorization code received. You can close this browser tab.")
            else:
                self.send_response(403)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"CSRF attack detected: State mismatch!")
                logging.error("CSRF attack detected: State mismatch!")
        else:
            self.send_response(400)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"Authorization failed: No code or state received.")
            logging.error("Authorization failed: No code or state received.")

        # Stop the server after handling the request
        self.server.shutdown()

def start_oauth_callback_server():
    redirect_host = urllib.parse.urlparse(OAUTH_REDIRECT_URI).hostname
    redirect_port = urllib.parse.urlparse(OAUTH_REDIRECT_URI).port
    
    if not redirect_host or not redirect_port:
        logging.error(f"Invalid OAUTH_REDIRECT_URI: {OAUTH_REDIRECT_URI}. Hostname or port missing.")
        return

    server_address = (redirect_host, redirect_port)
    httpd = http.server.HTTPServer(server_address, OAuthCallbackHandler)
    logging.info(f"Starting OAuth callback server on {redirect_host}:{redirect_port}...")
    httpd.serve_forever()
    logging.info("OAuth callback server stopped.")

async def exchange_code_for_token(code: str, code_verifier: str) -> str | None:
    global oauth_access_token, oauth_refresh_token, oauth_token_expiry
    token_data = {
        "grant_type": "authorization_code",
        "client_id": OAUTH_CLIENT_ID,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": OAUTH_REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: requests.post(OAUTH_TOKEN_URL, data=token_data, headers=headers, timeout=10)
        )
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        token_response = response.json()
        oauth_access_token = token_response.get("access_token")
        oauth_refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in", 0)
        oauth_token_expiry = time.time() + expires_in

        if oauth_access_token:
            logging.info("Access token obtained successfully.")
            return oauth_access_token
        else:
            logging.error(f"Failed to get access token: {token_response.get('error_description', token_response)}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error during token exchange: {e}")
        return None

async def refresh_access_token() -> str | None:
    global oauth_access_token, oauth_refresh_token, oauth_token_expiry
    if not oauth_refresh_token:
        logging.error("No refresh token available. Cannot refresh access token.")
        return None

    token_data = {
        "grant_type": "refresh_token",
        "client_id": OAUTH_CLIENT_ID,
        "refresh_token": oauth_refresh_token,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    try:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: requests.post(OAUTH_TOKEN_URL, data=token_data, headers=headers, timeout=10)
        )
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        token_response = response.json()
        oauth_access_token = token_response.get("access_token")
        expires_in = token_response.get("expires_in", 0)
        oauth_token_expiry = time.time() + expires_in

        if oauth_access_token:
            logging.info("Access token refreshed successfully.")
            return oauth_access_token
        else:
            logging.error(f"Failed to refresh access token: {token_response.get('error_description', token_response)}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error during token refresh: {e}")
        return None
 

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
    global request_id_counter
    request_id_counter += 1
    payload_dict = json.loads(payload)
    payload_dict["req_id"] = request_id_counter
    payload_with_req_id = json.dumps(payload_dict)

    # Get the underlying asyncio transport layer object
    transport = websocket.transport
    
    if transport is not None:
        # Check current memory buffer size in bytes (1 MB threshold)
        if transport.get_write_buffer_size() > 1024 * 1024:
            logging.warning("Network congestion detected. Throttling payload.")
            return # Drop or defer the packet here
            
    # Regular non-blocking queueing, blocks only if TCP buffer is completely stuck
    await websocket.send(payload_with_req_id)

def handle_settlement_data(contract):
    try: 
        """Processes streamed contract packets pushed automatically via data feeds."""
        global last_contract_id, max_historical_loss, total_net_pnl, session_net_pnl, all_time_rebate_pool, session_rebate_pool, calculated_target_stake, consecutive_losses, tradeable_pool, initial_capital
        
        # Ignore open packets; wait for the final message package
        if not contract or not contract.get('is_sold'): 
            return

        profit = float(contract.get('profit', 0))
        
        total_net_pnl += profit
        session_net_pnl += profit
        
        session_sign = "+" if profit >= 0 else ""
        
        if profit > 0:
            # Maintain tradeable_pool by deducting from winnings
            tradeable_pool = min(initial_capital * 0.70, tradeable_pool + profit)
            logging.info(f"[RESULT] WIN (+${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Tradeable Pool: ${tradeable_pool:.2f}")
            calculated_target_stake = calculate_base_percentage_stake()
            consecutive_losses = 0  # Reset consecutive losses on a win
        else:
            # Deduct from tradeable_pool on loss
            tradeable_pool += profit  # profit is negative here
            logging.info(f"[RESULT] LOSS (${profit:.2f}) | Account Balance: ${account_balance} | Session: {session_sign}${session_net_pnl:.2f} | Tradeable Pool: ${tradeable_pool:.2f}")
            calculated_target_stake = calculate_base_percentage_stake()
            consecutive_losses += 1  # Increment consecutive losses on a loss

        logging.info("[TOTAL NET PNL] VALUE: %s", total_net_pnl)
        
        # Unlock pipeline for the next trade iteration
        last_contract_id = None
    except Exception as e:
            logging.error(f"handle settlement data: {e}")

async def process_ticks(websocket):
    global last_contract_id, calculated_target_stake, account_balance, last_trade_direction

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
                             
                    # Alternate trade direction
                    trade_direction = "CALL" if last_trade_direction == "PUT" else "PUT"
                    last_trade_direction = trade_direction
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
        "Deriv-App-ID": APP_ID,
        "Authorization": f"Bearer {API_TOKEN}",
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
                logging.info(f"Deriv Bot Meta: Obtained WebSocket URL: {target_url}")
                return target_url
        else:
            logging.error(f"Deriv Bot Meta: REST Error {response.status_code} - {response.text}")
    except requests.exceptions.Timeout:
        logging.error("Deriv Bot Meta: HTTP Request timed out (Server dropped connection).")
    except Exception as e:
        logging.error(f"Deriv Bot Meta: Failed to fetch dynamic REST OTP: {e}")
    
    logging.info("Deriv Bot Meta: Retrying OTP token acquisition in 5 seconds...")   

async def main():
    global oauth_authorization_code, oauth_access_token, reconnect_delay
    
    # Initiate OAuth flow and wait for authorization code
    initiate_oauth_flow()
    
    # Start a separate thread for the HTTP server
    server_thread = threading.Thread(target=start_oauth_callback_server)
    server_thread.daemon = True  # Allow the main program to exit even if the thread is still running
    server_thread.start()

    # Wait for the authorization code to be received by the callback server
    while oauth_authorization_code is None:
        await asyncio.sleep(1)
    
    logging.info("Authorization code obtained. Proceeding with token exchange.")
    
    # Exchange authorization code for access token
    oauth_access_token = await exchange_code_for_token(oauth_authorization_code, pkce_code_verifier)
    if not oauth_access_token:
        logging.error("Failed to obtain access token. Exiting.")
        return # Exit if token exchange fails
    
    while True: 
        # Check if token needs refreshing (e.g., 5 minutes before expiry)
        if oauth_access_token and time.time() + 300 > oauth_token_expiry:
            logging.info("Access token expiring soon. Attempting to refresh...")
            new_access_token = await refresh_access_token()
            if not new_access_token:
                logging.error("Failed to refresh access token. Re-initiating OAuth flow.")
                # For simplicity, re-initiate the whole flow. In a real app, you might have more sophisticated error handling.
                oauth_authorization_code = None
                oauth_access_token = None
                oauth_refresh_token = None
                oauth_token_expiry = 0
                initiate_oauth_flow()
                server_thread = threading.Thread(target=start_oauth_callback_server)
                server_thread.daemon = True
                server_thread.start()
                while oauth_authorization_code is None:
                    await asyncio.sleep(1)
                oauth_access_token = await exchange_code_for_token(oauth_authorization_code, pkce_code_verifier)
                if not oauth_access_token:
                    logging.error("Failed to obtain access token after refresh attempt. Exiting.")
                    return

        websocket_url = "wss://ws.derivws.com/websockets/v3?app_id=1089" # Example public endpoint, will need to be authenticated later
        
        try:
            logging.info(f"Attempting to connect to WebSocket at: {websocket_url}")
            async with websockets.connect(websocket_url) as ws:
                reconnect_delay = 1 # Reset delay on successful connection
                try:
                    # Authorize with the obtained access token
                    await send_data_safe(ws, json.dumps({"authorize": oauth_access_token}))
                    async for msg in ws:
                        auth_res = json.loads(msg)
                        if auth_res.get('msg_type') == 'authorize': 
                            global account_balance, initial_capital, tradeable_pool
                            account_balance = float(auth_res['authorize']['balance'])
                            initial_capital = account_balance
                            tradeable_pool = initial_capital * 0.70
                            logging.info(f"[INIT] Initial Capital: ${initial_capital:.2f} | Tradeable Pool: ${tradeable_pool:.2f}")
                            break
                    
                    await send_data_safe(ws, json.dumps({"balance": 1, "subscribe": 1}))
                    await send_data_safe(ws, json.dumps({"ticks": "R_10", "subscribe": 1}))
                    await send_data_safe(ws, json.dumps({"ticks": "R_25", "subscribe": 1}))
                    await send_data_safe(ws, json.dumps({"ticks": "R_50", "subscribe": 1}))
                    await send_data_safe(ws, json.dumps({"ticks": "R_75", "subscribe": 1}))
                    await send_data_safe(ws, json.dumps({"ticks": "R_100", "subscribe": 1}))
                    await process_ticks(ws)
                finally:
                    # Send forget_all for all subscriptions before closing the socket
                    logging.info("Sending forget_all for all active subscriptions...")
                    await send_data_safe(ws, json.dumps({"forget_all": "ticks"}))
                    await send_data_safe(ws, json.dumps({"forget_all": "balance"}))
                    # Give a small moment for the messages to be sent
                    await asyncio.sleep(0.1)
        except Exception as e:
            logging.error(f"Interface connection lost: {e}. Attempting to reconnect in {reconnect_delay} seconds...")
            reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
            await asyncio.sleep(reconnect_delay)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("System gracefully halted.")