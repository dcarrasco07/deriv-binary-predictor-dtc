require('dotenv').config();

const WebSocket = require('ws');
const DerivAPI = require('@deriv/deriv-api/dist/DerivAPI');

// ─── Environment Variables ──────────────────────────────────────────────────
const app_id           = '32WzmZD0GdX5NdJKlPO7e';
const api_token        = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1';
const deriv_account_id = 'DOT90416964';
const BET_AMOUNT        = 1;

if (!app_id || !api_token || !deriv_account_id) {
    console.error('[BOT] Missing env vars: APP_ID, API_TOKEN, and DERIV_ACCOUNT_ID must all be set.');
    process.exit(1); 
}

// ─── Risk Management & Martingale Settings ──────────────────────────────────
const MAX_DAILY_NET_LOSS      = 30; 
const CONTRACT_DURATION_MINUTES = 1;               
const TICK_HISTORY_COUNT      = 10;              
const POLL_INTERVAL_MS        = 120000;            // Slightly longer to allow contract settlement
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
    if (!lastContractId) return;

    try {
        const response = await api.basic.send({
            proposal_open_contract: 1,
            contract_id: lastContractId
        });

        const contract = response.proposal_open_contract;
        // if conntract is sold
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

    //random
    const randomDirection = Math.random() > 0.5 ? 'PUT' : 'CALL';
    return { direction: randomDirection, pattern: 'Bits' };
}

async function executeTrade(symbol, direction, stake) {
    // 1. Get a Proposal first
    let proposalResponse = await api.basic.send({
        proposal: 1,
        amount: parseFloat(stake.toFixed(2)),
        basis: 'stake',
        contract_type: direction,
        currency: 'USD',
        duration: CONTRACT_DURATION_MINUTES,
        duration_unit: 'm',
        underlying_symbol: symbol,
    });

    // Fallback if 'symbol' is invalid (older/newer API versions might expect 'underlying_symbol')
    if (proposalResponse.error && (proposalResponse.error.code === 'InvalidSymbol' || proposalResponse.error.message.includes('underlying_symbol'))) {
        proposalResponse = await api.basic.send({
            proposal: 1,
            amount: parseFloat(stake.toFixed(2)),
            basis: 'stake',
            contract_type: direction,
            currency: 'USD',
            duration: CONTRACT_DURATION_MINUTES,
            duration_unit: 'm',
            underlying_symbol: symbol,
        });
    }

    if (proposalResponse.error) {
        throw new Error(`Proposal failed: ${proposalResponse.error.message}`);
    }

    const proposalId = proposalResponse.proposal.id;

    // 2. Buy the contract using the proposal ID
    const buyResponse = await api.basic.send({
        buy: proposalId,
        price: parseFloat(stake.toFixed(2)),
    });

    if (buyResponse.error) {
        throw new Error(`Buy failed: ${buyResponse.error.message}`);
    }

    console.log(`[TRADE] Placed ${direction} | ID: ${buyResponse.buy.contract_id} | Stake: $${stake.toFixed(2)}`);
    return buyResponse.buy.contract_id;
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
    
    // Authorization is required for trading and getting proposals for most symbols
    await api.basic.authorize(api_token);
    
    console.log('[BOT] Connected and Authorized to Deriv.');
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