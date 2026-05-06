require('dotenv').config();

const WebSocket = require('ws');
const DerivAPI = require('@deriv/deriv-api/dist/DerivAPI');

// ─── Environment Variables ──────────────────────────────────────────────────
const app_id           = process.env.APP_ID;
const api_token        = process.env.API_TOKEN        || '';
const deriv_account_id = process.env.DERIV_ACCOUNT_ID || '';
const BET_AMOUNT        = parseFloat(process.env.BET_AMOUNT)        || 0.20;
const MIN_PAYOUT_RATE   = parseFloat(process.env.MIN_PAYOUT_RATE)   || 0.90;
const DRY_RUN           = process.env.DRY_RUN === 'true';

if (!app_id || !api_token || !deriv_account_id) {
    console.error('[BOT] Missing env vars: APP_ID, API_TOKEN, and DERIV_ACCOUNT_ID must all be set.');
    process.exit(1); 
}

// ─── Risk Management & Martingale Settings ──────────────────────────────────
const MAX_DAILY_NET_LOSS      = parseFloat(process.env.MAX_DAILY_NET_LOSS) || 30; 
const CONTRACT_DURATION_TICKS = 5;               
const TICK_HISTORY_COUNT      = 10;              
const POLL_INTERVAL_MS        = 3000;            // Slightly longer to allow contract settlement
const MARTINGALE_MULTIPLIER   = 2;
const SCAN_SYMBOLS            = ['R_100']; 

let currentStake   = BET_AMOUNT;
let lastContractId = null;

// ─── Session Tracking ────────────────────────────────────────────────────────
let session = {
    date: new Date().toDateString(),
    trades: 0,
};

let api;
let wsConnection; 

// ─── Logic Functions ─────────────────────────────────────────────────────────

async function fetchTodayNetPnL() {
    try {
        const startOfDay = new Date();
        startOfDay.setUTCHours(0, 0, 0, 0);
        const response = await api.basic.send({
            profit_table: 1,
            date_from: Math.floor(startOfDay.getTime() / 1000),
            limit: 100,
        });
        const transactions = response.profit_table?.transactions ?? [];
        return transactions.reduce((sum, t) => sum + parseFloat(t.profit ?? 0), 0);
    } catch (err) {
        return 0; 
    }
}

async function checkLastTradeResult() {
    if (!lastContractId || DRY_RUN) return;

    try {
        const response = await api.basic.send({
            proposal_open_contract: 1,
            contract_id: lastContractId
        });

        const contract = response.proposal_open_contract;
        if (contract.is_sold) {
            const profit = parseFloat(contract.profit);
            if (profit > 0) {
                console.log(`[RESULT] WIN (+$${profit.toFixed(2)}). Resetting stake to $${BET_AMOUNT}`);
                currentStake = BET_AMOUNT;
            } else {
                currentStake = currentStake * MARTINGALE_MULTIPLIER;
                console.log(`[RESULT] LOSS ($${profit.toFixed(2)}). Martingale stake: $${currentStake.toFixed(2)}`);
            }
            lastContractId = null; // Clear so we can trade again
        } else {
            console.log(`[BOT] Waiting for contract ${lastContractId} to settle...`);
        }
    } catch (err) {
        console.error('[BOT] Error checking result:', err.message);
    }
}

async function getSignal(symbol) {
    const response = await api.basic.send({
        ticks_history: symbol,
        count: TICK_HISTORY_COUNT,
        end: 'latest',
        style: 'ticks',
    });

    const prices = response.history?.prices;
    if (!prices || prices.length < 3) return null;

    const bits = [];
    for (let i = 1; i < prices.length; i++) {
        bits.push(parseFloat(prices[i]) > parseFloat(prices[i - 1]) ? 1 : 0);
    }

    const recentBits = bits.slice(-2).join('');
    
    // Pattern Logic:
    // 00, 01 -> PUT
    // 11, 10 -> CALL
    if (recentBits === '00' || recentBits === '01') {
        return { direction: 'PUT', pattern: recentBits };
    } 
    if (recentBits === '11' || recentBits === '10') {
        return { direction: 'CALL', pattern: recentBits };
    }

    return null;
}

async function executeTrade(symbol, direction, stake) {
    if (DRY_RUN) {
        console.log(`[DRY RUN] Pattern Match! Placing ${direction} @ $${stake.toFixed(2)}`);
        // In dry run, we simulate a win to keep stake at base
        lastContractId = null; 
        return 'dry-run';
    }

    const proposal = await api.basic.send({
        proposal: 1,
        amount: parseFloat(stake.toFixed(2)),
        basis: 'stake',
        contract_type: direction,
        currency: 'USD',
        duration: CONTRACT_DURATION_TICKS,
        duration_unit: 't',
        underlying_symbol: symbol,
    });

    if (proposal.error) throw new Error(proposal.error.message);

    const buy = await api.basic.send({
        buy: proposal.proposal.id,
        price: parseFloat(stake.toFixed(2)),
    });

    if (buy.error) throw new Error(buy.error.message);

    console.log(`[TRADE] Placed ${direction} | ID: ${buy.buy.contract_id} | Stake: $${stake.toFixed(2)}`);
    return buy.buy.contract_id;
}

// ─── Connection & Cycle ──────────────────────────────────────────────────────

async function initializeDerivAPI() {
    const otpResponse = await fetch(`https://api.derivws.com/trading/v1/options/accounts/${deriv_account_id}/otp`, {
        method: 'POST',
        headers: { 'Deriv-App-ID': app_id, 'Authorization': `Bearer ${api_token}` },
    });
    const { data } = await otpResponse.json();
    wsConnection = new WebSocket(data.url);
    api = new DerivAPI({ connection: wsConnection });
    console.log('[BOT] Connected to Deriv.');
}

async function tradingCycle() {
    if (!wsConnection || wsConnection.readyState !== 1) await initializeDerivAPI();

    // 1. Check if we are waiting for a trade result
    if (lastContractId) {
        await checkLastTradeResult();
        return; // Don't place new trades until the last one is settled
    }

    // 2. Risk Check
    const netPnL = await fetchTodayNetPnL();
    if (netPnL <= -MAX_DAILY_NET_LOSS) {
        console.log(`[STOP] Daily Loss Limit reached ($${netPnL}).`);
        return;
    }

    // 3. Scan Symbols
    for (const symbol of SCAN_SYMBOLS) {
        const signal = await getSignal(symbol);
        if (signal) {
            console.log(`[SIGNAL] Pattern ${signal.pattern} detected on ${symbol}`);
            try {
                lastContractId = await executeTrade(symbol, signal.direction, currentStake);
                break; 
            } catch (err) {
                console.error(`[BOT] Trade failed: ${err.message}`);
            }
        }
    }
}

// ─── Entry Point ─────────────────────────────────────────────────────────────

async function start() {
    await initializeDerivAPI();
    setInterval(tradingCycle, POLL_INTERVAL_MS);
    console.log(`[BOT] Running. Base stake: $${BET_AMOUNT} | Martingale: ${MARTINGALE_MULTIPLIER}x`);
}

start().catch(console.error);