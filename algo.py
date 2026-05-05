import asyncio
import os
from deriv_api import DerivAPI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
API_TOKEN = os.getenv("DERIV_API_TOKEN")
APP_ID = os.getenv("DERIV_APP_ID", "1089")

# --- CONFIGURATION ---
SYMBOL = "R_100"        # Volatility 100 Index
STAKE = 10              # USD
DURATION = 5            # Ticks
# ---------------------

tick_history = []

async def analyze_and_trade(api, price):
    global tick_history
    
    # Initialize logic
    if not hasattr(analyze_and_trade, "last_price"):
        analyze_and_trade.last_price = price
        return

    # 1. Calculate Rise (1) or Fall (0)
    direction = 1 if price > analyze_and_trade.last_price else 0
    tick_history.append(direction)
    analyze_and_trade.last_price = price

    # Keep only last 2 ticks
    if len(tick_history) > 2:
        tick_history.pop(0)

    # 2. Check Pattern
    if len(tick_history) == 2:
        pattern = tick_history
        # Visual feedback of the "Matrix" (Pattern History)
        print(f"Tick: {price} | Pattern: {pattern} | Checking...", end="\r")

        # Logic: Trade if pattern is 00, 01, or 10 (Everything except 11)
        if pattern != [1, 1]:
            print(f"\n[!] SIGNAL DETECTED: {pattern}. Requesting Quote...")
            
            try:
                # 3. Request Quote (Proposal)
                proposal = await api.proposal({
                    "proposal": 1,
                    "amount": STAKE,
                    "basis": "stake",
                    "contract_type": "PUT",  # PUT = Lower
                    "currency": "USD",
                    "duration": DURATION,
                    "duration_unit": "t",
                    "symbol": SYMBOL
                })
                
                # 4. Check for Server Rejection
                if 'error' in proposal:
                    print(f"❌ SERVER REJECTED QUOTE: {proposal['error']['code']}")
                    print(f"   Reason: {proposal['error']['message']}")
                    return

                # 5. Execute Trade
                proposal_id = proposal['proposal']['id']
                buy_log = await api.buy({"buy": proposal_id, "price": STAKE})
                
                if 'error' in buy_log:
                     print(f"❌ BUY FAILED: {buy_log['error']['message']}")
                else:
                    print(f"✅ TRADE OPENED: Ref {buy_log['buy']['contract_id']}\n")
                    
            except Exception as e:
                print(f"❌ CRASH: {str(e)}")
        else:
            # Pattern is [1, 1]
            pass

async def main():
    if not API_TOKEN:
        print("❌ ERROR: API Token missing. Check your .env file.")
        return

    api = DerivAPI(app_id=APP_ID)
    
    try:
        await api.authorize(API_TOKEN)
        print(f"Connected to Deriv Account. Monitoring {SYMBOL}...")
    except Exception as e:
        print(f"❌ AUTH FAILED: {e}")
        return

    source_ticks = await api.subscribe({"ticks": SYMBOL})
    
    source_ticks.subscribe(
        lambda tick_data: asyncio.create_task(
            analyze_and_trade(api, tick_data['tick']['quote'])
        )
    )

    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
