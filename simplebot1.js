// ─── Martingale State ────────────────────────────────────────────────────────
let currentStake = BET_AMOUNT;
let lastContractId = null;
const MARTINGALE_MULTIPLIER = 1.5;

// ─── New Signal Logic ────────────────────────────────────────────────────────
async function getSignal(symbol) {
    const response = await api.basic.send({
        ticks_history: symbol,
        adjust_start_time: 1,
        count: TICK_HISTORY_COUNT,
        end: 'latest',
        style: 'ticks',
    });

    if (response.error) throw new Error(`ticks_history error: ${response.error.message}`);

    const prices = response.history?.prices;
    if (!prices || prices.length < 3) return null;

    const bits = [];
    for (let i = 1; i < prices.length; i++) {
        bits.push(parseFloat(prices[i]) > parseFloat(prices[i - 1]) ? 1 : 0);
    }

    const recentBits = bits.slice(-2).join('');
    
    // Updated Logic:
    // 00, 01 -> PUT
    // 11, 10 -> CALL
    if (recentBits === '00' || recentBits === '01') {
        console.log(`[SIGNAL] ${recentBits} -> PUT`);
        return 'PUT';
    } 
    if (recentBits === '11' || recentBits === '10') {
        console.log(`[SIGNAL] ${recentBits} -> CALL`);
        return 'CALL';
    }

    return null;
}

// ─── Check Trade Result ──────────────────────────────────────────────────────
async function checkLastTradeWin(contractId) {
    if (!contractId || DRY_RUN) return true; // Default to reset if no real trade

    try {
        const response = await api.basic.send({ proposal_open_contract: 1, contract_id: contractId });
        const status = response.proposal_open_contract.status;
        
        // If still open, wait a bit and check again (ticks need time to settle)
        if (status === 'open') {
            await new Promise(r => setTimeout(r, 2000));
            return checkLastTradeWin(contractId);
        }

        const profit = parseFloat(response.proposal_open_contract.profit);
        return profit > 0;
    } catch (err) {
        console.error('[BOT] Error checking contract result:', err.message);
        return true; // Reset on error to be safe
    }
}

// ─── Updated Trading Cycle ───────────────────────────────────────────────────
async function tradingCycle() {
    await ensureConnected();
    resetSessionIfNewDay();

    // 1. If we just finished a trade, determine the next stake
    if (lastContractId) {
        console.log('[BOT] Checking previous trade result...');
        const won = await checkLastTradeWin(lastContractId);
        
        if (won) {
            console.log('[RESULT] WIN! Resetting stake.');
            currentStake = BET_AMOUNT;
        } else {
            currentStake = currentStake * MARTINGALE_MULTIPLIER;
            console.log(`[RESULT] LOSS. Increasing stake to: $${currentStake.toFixed(2)}`);
        }
        lastContractId = null; // Reset tracker
    }

    const todayNetPnL = await fetchTodayNetPnL();
    if (todayNetPnL <= -MAX_DAILY_NET_LOSS) return;

    for (const symbol of SCAN_SYMBOLS) {
        const signal = await getSignal(symbol);
        if (signal) {
            try {
                // Pass currentStake instead of BET_AMOUNT
                const result = await executeTrade(symbol, signal, currentStake);
                if (result !== null) {
                    lastContractId = result; 
                    break; 
                }
            } catch (err) {
                console.error(`[BOT] Trade error:`, err.message);
            }
        }
    }
}

// Note: Update your executeTrade function signature to accept 'stakeAmount'
async function executeTrade(symbol, direction, stakeAmount) {
    // ... inside executeTrade, use stakeAmount instead of BET_AMOUNT ...
}