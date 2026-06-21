import json
import websocket
import threading
import time
import requests
import asyncio

class DerivBotMeta:
    """
    A meta-trading bot for Deriv.com, designed for automated trading.
    Utilizes modern dynamic REST OTP tokens to authenticate connection paths.
    """
    def __init__(self, app_id: str, api_token: str, account_id: str):
        self.app_id = app_id
        self.api_token = api_token
        self.account_id = account_id
        
        self.websocket_app = None
        self.is_connected = False
        self.balance = 0.0
        self.active_symbols = {}
        self.contract_types = {}
        self.open_contracts = []
        self.transaction_history = []
        self.tick_history = {}  # To store historical tick data for SMA calculation
        self.sma_period = 10    # Period for Simple Moving Average
        
        self.is_trading = False
        self.lock = threading.Lock()

    async def get_authenticated_url(self) -> str:
        while True:
            """Queries the modern REST API to get a dynamic authenticated WebSocket URL."""
            url = f"https://api.derivws.com/trading/v1/options/accounts/{self.account_id}/otp"
            headers = {
                "Deriv-App-ID": self.app_id,
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json"
            }
            try:
                response = requests.post(url, headers=headers, timeout=10)
                if not url: await asyncio.sleep(5); continue
                if response.status_code == 200:
                    return response.json().get('data', {}).get('url')
                else:
                    print(f"Deriv Bot Meta: REST Error {response.status_code} - {response.text}")
            except Exception as e:
                print(f"Deriv Bot Meta: Failed to fetch dynamic REST OTP: {e}")
            return None

    def _on_open(self, ws):
        """Callback when WebSocket connection is opened."""
        print("Deriv Bot Meta: Secure WebSocket connection initialized successfully.")
        self.is_connected = True
        
        # Subscribe to immediate balance and market feed data assets
        self._send_request({"balance": 1, "subscribe": 1})
        self._send_request({"active_symbols": "brief"})

    def _on_message(self, ws, message):
        """Callback when a message is received from the WebSocket."""
        try:
            res = json.loads(message)
            with self.lock:
                if "msg_type" in res:
                    # 1. Live Balance Tracking Updates
                    if res.get("msg_type") == "balance":
                        balance_data = res.get("balance")
                        if balance_data:
                            try:
                                self.balance = float(balance_data.get("balance"))
                                print(f"--- LIVE ACCOUNT BALANCE: ${self.balance:.2f} ---")
                            except (TypeError, ValueError) as e:
                                print(f"Deriv Bot Meta: Error processing balance value: {e}. Full response: {res}")
                        else:
                            print(f"Deriv Bot Meta: 'balance' key missing in balance message. Full response: {res}")

                    elif res.get("msg_type") == "active_symbols":
                        active_symbols_data = res.get("active_symbols")
                        if active_symbols_data:
                            print("Deriv Bot Meta: Received active symbols asset index.")
                            for symbol_info in active_symbols_data:
                                symbol_name = symbol_info.get("underlying_symbol")
                                if symbol_name:
                                    self.active_symbols["underlying_symbol_name"] = symbol_info
                                else:
                                    print(f"Deriv Bot Meta: 'symbol' key missing in active_symbols item. Item: {symbol_info}")
                        else:
                            print(f"Deriv Bot Meta: 'active_symbols' key missing in active_symbols message. Full response: {res}")

                    # 2. Execution Proposal Handlers
                    elif res.get("msg_type") == "proposal":
                        proposal_data = res.get("proposal")
                        if proposal_data:
                            try:
                                print(f"Deriv Bot Meta: Proposal received: {proposal_data.get('display_value')}")
                                print("Deriv Bot Meta: Transmitting Purchase Order...")
                                self._send_request({
                                    "buy": proposal_data.get("id"), 
                                    "price": proposal_data.get("ask_price")
                                })
                            except KeyError as e:
                                print(f"Deriv Bot Meta: KeyError accessing proposal data: {e}. Full response: {res}")
                        else:
                            print(f"Deriv Bot Meta: 'proposal' key missing in proposal message. Full response: {res}")

                    elif res["msg_type"] == "buy" and res["buy"]:
                        print(f"Deriv Bot Meta: Contract processed! ID: {res['buy']['contract_id']}")
                        self.open_contracts.append(res["buy"])

                    # 3. Market Ticks & Indicators Strategy Block
                    elif res["msg_type"] == "tick" and res["tick"]:
                        symbol = res["tick"]["symbol"]
                        price = float(res["tick"]["quote"])
                        
                        sma = self._calculate_sma(symbol, price)
                        if sma != 0.0 and not self.is_trading:
                            if price > sma:
                                print(f"Deriv Bot Meta: Price > SMA. Initiating CALL setup.")
                                self.is_trading = True
                                self.propose_contract(symbol, "CALL", 5, "t", 10.0)
                            elif price < sma:
                                print(f"Deriv Bot Meta: Price < SMA. Initiating PUT setup.")
                                self.is_trading = True
                                self.propose_contract(symbol, "PUT", 5, "t", 10.0)

                    # 4. Lifecycle Transaction Pipeline (Release State Locks)
                    elif res["msg_type"] == "transaction" and res["transaction"]:
                        action = res["transaction"]["action"]
                        print(f"Deriv Bot Meta: Position update -> {action.upper()}")
                        if action in ["sell", "contract_load"]:
                            print("Deriv Bot Meta: Contract concluded. Re-engaging scanning systems.")
                            self.is_trading = False

                    elif res["msg_type"] == "error":
                        print(f"Deriv Bot Meta: Error Message: {res['error']['message']}")
                        if "buy" in res["error"] or "proposal" in res["error"]:
                            self.is_trading = False
        except Exception as e:
            print(f"Deriv Bot Meta: Exception parsing operational packet: {e}")
            self.is_trading = False

    def _on_error(self, ws, error):
        print(f"Deriv Bot Meta: WebSocket Error Event: {error}")
        self.is_connected = False

    def _on_close(self, ws, close_status_code, close_msg):
        print("Deriv Bot Meta: Socket channel terminated.")
        self.is_connected = False
        self.is_trading = False

    def _send_request(self, request: dict):
        if self.websocket_app and self.is_connected:
            self.websocket_app.send(json.dumps(request))

    def _calculate_sma(self, symbol: str, current_price: float) -> float:
        if symbol not in self.tick_history:
            self.tick_history[symbol] = []
        self.tick_history[symbol].append(current_price)
        if len(self.tick_history[symbol]) > self.sma_period:
            self.tick_history[symbol] = self.tick_history[symbol][-self.sma_period:]
        if len(self.tick_history[symbol]) == self.sma_period:
            return sum(self.tick_history[symbol]) / self.sma_period
        return 0.0

    async def connect(self):
        print("Deriv Bot Meta: Starting connection process.")
        while True:
            websocket.enableTrace(False)
            dynamic_url = await self.get_authenticated_url()
            
            if not dynamic_url:
                print("Deriv Bot Meta: Terminating startup. Dynamic token url missing.")
                await asyncio.sleep(5) # Wait before retrying to avoid busy-looping
                continue

            print(f"Deriv Bot Meta: Routing to Target Endpoint: {dynamic_url}")
            self.websocket_app = websocket.WebSocketApp(
                dynamic_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close
            )
            self.wst = threading.Thread(target=self.websocket_app.run_forever)
            self.wst.daemon = True
            self.wst.start()
            print("Deriv Bot Meta: WebSocket thread started.")

            timeout = 10
            start_time = time.time()
            while not self.is_connected and (time.time() - start_time) < timeout:
                await asyncio.sleep(0.5)
            
            if self.is_connected:
                print("Deriv Bot Meta: Successfully connected to WebSocket.")
                break # Exit loop if connected
            else:
                print("Deriv Bot Meta: Failed to connect to WebSocket within timeout. Retrying in 5 seconds...")
                await asyncio.sleep(5) # Wait before retrying to avoid rate limits

    def disconnect(self):
        if self.websocket_app:
            print("Deriv Bot Meta: Disconnecting WebSocket.")
            self.websocket_app.close()

    def subscribe_to_ticks(self, symbol: str):
        print(f"Deriv Bot Meta: Subscribing to ticks for {symbol}.")
        self._send_request({"ticks": symbol})

    def propose_contract(self, symbol: str, contract_type: str, duration: int, duration_unit: str, amount: float):
        print(f"Deriv Bot Meta: Proposing contract for {symbol} ({contract_type}).")
        request = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": duration,
            "duration_unit": duration_unit,
            "underlying_symbol": symbol
        }
        self._send_request(request)

    async def run(self):
        print("Deriv Bot Meta: Bot run method started.")
        await self.connect()
        if self.is_connected:
            self.subscribe_to_ticks("R_50")
            print("Deriv Bot Meta: Entering main loop.")
            try:
                while self.is_connected:
                    await asyncio.sleep(1) # Use asyncio.sleep for async context
            except KeyboardInterrupt:
                print("Deriv Bot Meta: Execution aborted by user.")
            except Exception as e:
                print(f"Deriv Bot Meta: An unexpected error occurred in main loop: {e}")
            finally:
                self.disconnect()
                print("Deriv Bot Meta: Bot run method finished.")

if __name__ == "__main__":
    print("Deriv Bot Meta: Script starting...")
    APP_ID = '33C4vGAjJZRb5JW73usCd'
    API_TOKEN = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1'
    ACCOUNT_ID = 'DOT90416964'

    bot = DerivBotMeta(app_id=APP_ID, api_token=API_TOKEN, account_id=ACCOUNT_ID)
    try:
        asyncio.run(bot.run())
    except Exception as e:
        print(f"Deriv Bot Meta: An error occurred during asyncio run: {e}")