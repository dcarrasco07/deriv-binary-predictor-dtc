require('dotenv').config();

const WebSocket = require('ws');
const DerivAPI = require('@deriv/deriv-api/dist/DerivAPI');

const app_id           = process.env.APP_ID;
const api_token        = process.env.API_TOKEN        || '';
const deriv_account_id = process.env.DERIV_ACCOUNT_ID || '';
const BET_AMOUNT        = parseFloat(process.env.BET_AMOUNT)        || 1;
const MIN_PAYOUT_RATE   = parseFloat(process.env.MIN_PAYOUT_RATE)   || 0.90; // skip trades paying < 90%
const DRY_RUN           = process.env.DRY_RUN === 'true';

if (!app_id || !api_token || !deriv_account_id) {
    console.error('[BOT] Missing env vars: APP_ID, API_TOKEN, and DERIV_ACCOUNT_ID must all be set.');
    console.error('[BOT] On Railway: add them under your service → Variables tab.');
    process.exit(1); 
}

// ─── Risk management & Tuning ────────────────────────────────────────────────
const MAX_DAILY_NET_LOSS      = parseFloat(process.env.MAX_DAILY_NET_LOSS) || 30; 
const MAX_DAILY_TRADES        = 20;
const CONTRACT_DURATION_TICKS = 4;               // 5-tick contracts
const TICK_HISTORY_COUNT      = 10;              // Buffer of ticks to fetch
const POLL_INTERVAL_MS        = 2000;            // Poll every 2 seconds

const SCAN_SYMBOLS = ['R_100']; // Volatility 10 Index 

// ─── Daily session tracking ───────────────────────────────────────────────────
let session = {
    date:   new Date().toDateString(),
    trades: 0,
};

async function fetchTodayNetPnL() {
    try {
        const startOfDay = new Date();
        startOfDay.setUTCHours(0, 0, 0, 0);

        const response = await api.basic.send({
            profit_table:  1,
            description:   1,
            sort:          'DESC',
            date_from:     Math.floor(startOfDay.getTime() / 1000),
            date_to:       Math.floor(Date.now() / 1000),
            limit:         100,
        });

        if (response.error) {
            console.warn('[BOT] profit_table error:', response.error.message);
            return 0;
        }

        const transactions = response.profit_table?.transactions ?? [];
        const netPnL = transactions.reduce((sum, t) => sum + parseFloat(t.profit ?? 0), 0);
        return netPnL;
    } catch (err) {
        console.warn('[BOT] Could not fetch profit table:', err.message);
        return 0; 
    }
}

function resetSessionIfNewDay() {
    const today = new Date().toDateString();
    if (session.date !== today) {
        session = { date: today, trades: 0 };
        console.log('[BOT] New trading day — session reset.');
    }
}

// ─── DerivAPI connection ──────────────────────────────────────────────────────

let api;
let wsConnection; 

async function initializeDerivAPI() {
    const otpResponse = await fetch(
        `https://api.derivws.com/trading/v1/options/accounts/${deriv_account_id}/otp`,
        {
            method:  'POST',
            headers: {
                'Deriv-App-ID':  app_id,
                'Authorization': `Bearer ${api_token}`,
            },
        }
    );

    if (!otpResponse.ok) {
        const body = await otpResponse.json();
        throw new Error(`OTP request failed (${otpResponse.status}): ${JSON.stringify(body)}`);
    }

    const { data } = await otpResponse.json();
    if (!data.url) throw new Error('No WebSocket URL in OTP response.');

    wsConnection = new WebSocket(data.url);
    api = new DerivAPI({ connection: wsConnection });
    console.log('[BOT] DerivAPI connected.');
}

async function ensureConnected() {
    if (!wsConnection || wsConnection.readyState === 2 || wsConnection.readyState === 3) {
        console.log('[BOT] Connection lost — reconnecting …');
        await initializeDerivAPI();
    }
}

// ─── Signal generation ────────────────────────────────────────────────────────

async function getSignal(symbol) {
    const response = await api.basic.send({
        ticks_history:     symbol,
        adjust_start_time: 1,
        count:             TICK_HISTORY_COUNT,
        end:               'latest',
        style:             'ticks',
    });

    if (response.error) throw new Error(`ticks_history error: ${response.error.message}`);

    const prices = response.history?.prices;
    if (!prices || prices.length < 3) {
        console.log('[BOT] Not enough ticks to evaluate pattern.');
        return null;
    }

    // Convert raw tick prices into a binary sequence (1 = UP, 0 = DOWN/FLAT)
    const bits = [];
    for (let i = 1; i < prices.length; i++) {
        bits.push(parseFloat(prices[i]) > parseFloat(prices[i - 1]) ? 1 : 0);
    }

    // Grab the most recent 2 tick movements
    const recentBits = bits.slice(-2).join('');
    
    // Check for the 00, 01, or 10 pattern
    if (recentBits === '00' || recentBits === '01' || recentBits === '10') {
        console.log(`[${new Date().toISOString()}] ${symbol} | Pattern: ${recentBits} -> PUT`);
        return 'PUT';
    }

    // Optional: log when it doesn't match (i.e., '11')
    // console.log(`[${new Date().toISOString()}] ${symbol} | Pattern: ${recentBits} -> NONE`);

    return null;
}

// ─── Trade execution ──────────────────────────────────────────────────────────

async function executeTrade(symbol, direction) {
    if (DRY_RUN) {
        console.log(`[DRY RUN] Would place ${direction} on ${symbol} for $${BET_AMOUNT} — no order sent.`);
        session.trades++;
        return 'dry-run';
    }

    const proposalResponse = await api.basic.send({
        proposal:          1,
        amount:            BET_AMOUNT,
        basis:             'stake',
        contract_type:     direction,      
        currency:          'USD',
        duration:          CONTRACT_DURATION_TICKS,
        duration_unit:     't', // 't' for ticks
        underlying_symbol: symbol,
    });

    if (proposalResponse.error) {
        throw new Error(`Proposal error: ${proposalResponse.error.message}`);
    }

    const { ask_price, payout, id: proposalId } = proposalResponse.proposal;
    const payoutRate = (payout - ask_price) / ask_price; 
    
    if (payoutRate < MIN_PAYOUT_RATE) {
        console.log(`[BOT] Skipping — payout ${(payoutRate * 100).toFixed(1)}% below minimum ${(MIN_PAYOUT_RATE * 100).toFixed(0)}%`);
        return null;
    }

    const buyResponse = await api.basic.send({
        buy:   proposalId,
        price: BET_AMOUNT,
    });

    if (buyResponse.error) {
        throw new Error(`Buy error: ${buyResponse.error.message}`);
    }

    const { contract_id, buy_price } = buyResponse.buy;
    console.log(`[TRADE] ${direction} | Contract: ${contract_id} | Cost: $${buy_price}`);
    session.trades++;
    return contract_id;
}

// ─── Main trading cycle ───────────────────────────────────────────────────────

async function tradingCycle() {
    await ensureConnected();
    resetSessionIfNewDay();

    const todayNetPnL = await fetchTodayNetPnL();
    
    if (todayNetPnL <= -MAX_DAILY_NET_LOSS) {
        console.log(`[BOT] Daily net loss limit hit ($${MAX_DAILY_NET_LOSS}). Sitting out this cycle.`);
        return;
    }

    let traded = false;
    for (const symbol of SCAN_SYMBOLS) {
        let signal;
        try {
            signal = await getSignal(symbol);
        } catch (err) {
            console.error(`[BOT] Signal error on ${symbol}:`, err.message);
            continue;
        }

        if (signal === 'CALL' || signal === 'PUT') {
            try {
                const result = await executeTrade(symbol, signal);
                if (result !== null) { traded = true; break; } 
            } catch (err) {
                console.error(`[BOT] Trade error on ${symbol}:`, err.message);
            }
        }
    }
}

// ─── Graceful shutdown ────────────────────────────────────────────────────────

function shutdown(signal) {
    console.log(`[BOT] Received ${signal} — shutting down gracefully.`);
    if (wsConnection) wsConnection.close();
    process.exit(0);
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('uncaughtException',  err => console.error('[BOT] Uncaught exception:', err.message));
process.on('unhandledRejection', err => console.error('[BOT] Unhandled rejection:', err));

// ─── Entry point ──────────────────────────────────────────────────────────────

async function connectWithRetry(maxAttempts = 5) {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            await initializeDerivAPI();
            return; 
        } catch (err) {
            console.error(`[BOT] Connection attempt ${attempt}/${maxAttempts} failed: ${err.message}`);
            if (attempt === maxAttempts) throw err;
            const wait = attempt * 5000; 
            console.log(`[BOT] Retrying in ${wait / 1000}s …`);
            await new Promise(r => setTimeout(r, wait));
        }
    }
}

async function run() {
    if (DRY_RUN) console.log('[BOT] *** DRY RUN MODE — signals logged, no trades placed ***');

    await connectWithRetry();
    console.log(`[BOT] Started. Polling ticks every ${POLL_INTERVAL_MS / 1000} seconds.`);

    await tradingCycle();

    setInterval(async () => {
        try {
            await tradingCycle();
        } catch (err) {
            console.error('[BOT] Cycle error:', err.message);
        }
    }, POLL_INTERVAL_MS);
}

run().catch(err => {
    console.error('[BOT] Failed to start:', err.message);
    process.exit(1);
});