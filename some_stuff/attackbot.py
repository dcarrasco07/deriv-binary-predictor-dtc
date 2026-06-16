import requests
import asyncio
from deriv_api import DerivAPI as api

# Demo Credentials
api_token = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1'
deriv_account_id = 'DOT90416964'

# 
app_id           = '32WzmZD0GdX5NdJKlPO7e'
# api_token        = 'pat_bc78db629feabf69a853ede8323ef15e2b35301f4af90273bfdd0c380edddda1'
# deriv_account_id = 'ROT91151098'
BET_AMOUNT        = 1

async def start():
    url = f"https://api.derivws.com/trading/v1/options/accounts/{deriv_account_id}/otp"
    headers = {
        'Deriv-App-ID': app_id,
        'Authorization': f'Bearer {api_token}',
    }
    response = requests.post(url, headers=headers)
    response.raise_for_status()
    print(response.json())

    proposal = api.send({
        "proposal": 1,
        "amount": BET_AMOUNT,
        "basis": "payout",
        "contract_type": "CALL",
        "currency": "USD",
        "duration": 5,
        "duration_unit": "t", # 5 ticks
        "symbol": "R_100",
        "request": 2
    })

    proposal_id = proposal.get('proposal').get('id')

    return

    buy_response = await api.buy({"buy": proposal_id})
    
    # 3. Extract the last contract ID
    last_contract_id = buy_response.get('buy').get('contract_id')
    print(f"Successfully bought contract! Last Contract ID: {last_contract_id}")

    await api.clear()

    print("proposal accepted")


if __name__ == '__main__':
    asyncio.run(start())