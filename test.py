import secrets
import os

# Valid 24/7 Synthetic Indices Array for Random Selection
SUPPORTED_SYMBOLS = ['R_10', 'R_25', 'R_50', 'R_75', 'R_100']

def generate_random_base_stake():
    """Generates a secure random initial base entry layer stake from 0.35 to 20."""
    crypto_flat_float = secrets.choice([secrets.SystemRandom().uniform(1, 2) for _ in range(2)])
    return round(crypto_flat_float, 2)

def generate_random_market_parameters():
    """Randomizes target index asset layers and contract tick length settings independently of active stake sizes."""
    
    # Randomize Tick Duration from 1 to 5 ticks
    active_duration = round(secrets.choice([secrets.SystemRandom().uniform(1, 4) for _ in range(4)]))
    
    # Randomize Active Target Symbol 
    active_symbol = SUPPORTED_SYMBOLS[round(secrets.choice([secrets.SystemRandom().uniform(1, 4) for _ in range(4)]))]
    
    return active_symbol, active_duration

if __name__ == "__main__":
    print("Simulating Deriv Bot's CPNRG (Crypto Random Number Generation):")
    
    # Simulate generating a random base stake
    stake = generate_random_base_stake()
    print(f"Generated Random Base Stake: ${stake:.2f}")
    
    # Simulate generating random market parameters
    symbol, duration = generate_random_market_parameters()
    print(f"Generated Random Market Parameters: Symbol='{symbol}', Duration='{duration}' ticks")
