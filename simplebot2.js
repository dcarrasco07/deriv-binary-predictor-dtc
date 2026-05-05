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
    process.exit(1); // intentional — nothing works without credentials
}

// ─── Risk management ─────────────────────────────────────────────────────────
const MAX_DAILY_NET_LOSS    = parseFloat(process.env.MAX_DAILY_NET_LOSS) || 30; // stop if down $10 today
const MAX_DAILY_TRADES      = 20;
const CONTRACT_DURATION_MIN = 2;               // 2-minute contracts
const CANDLE_GRANULARITY    = 120;             // 2-minute candles
const TRAINING_CANDLES      = 500;             // history used to build pattern table
const POLL_INTERVAL_MS      = 2 * 60 * 1000;  // re-evaluate every 2 minutes

// Pattern predictor tuning — also readable from .env
const PATTERN_WINDOW  = parseInt(process.env.PATTERN_WINDOW)   || 2;
const MIN_SAMPLES     = parseInt(process.env.MIN_SAMPLES)       || 10;
const MIN_CONFIDENCE  = parseFloat(process.env.MIN_CONFIDENCE)  || 0.50;

const SCAN_SYMBOLS = ['R_10']; // Volatility 10 Index (synthetic, 24/7, ~92% payout)

// ─── Daily session tracking ───────────────────────────────────────────────────
let session = {
    date:   new Date().toDateString(),
    trades: 0,
};

/**
 * Fetches today's actual net P&L from Deriv's profit_table API.
 * profit = sell_price - buy_price per contract (positive = win, negative = loss).
 */
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
        return 0; // fail open — don't block trading on a fetch error
    }
}

function resetSessionIfNewDay() {
    const today = new Date().toDateString();
    if (session.date !== today) {
        session = { date: today, trades: 0 };
        console.log('[BOT] New trading day — session reset.');
    }
}

// ─── Pattern Predictor ────────────────────────────────────────────────────────

/**
 * Converts a candle array into a binary sequence.
 * Each element is 1 (up candle: close > prev close) or 0 (down/flat candle).
 * Returns an array of length candles.length - 1.
 */
function candlesToBits(candles) {
    const bits = [];
    for (let i = 1; i < candles.length; i++) {
        bits.push(parseFloat(candles[i].close) > parseFloat(candles[i - 1].close) ? 1 : 0);
    }
    return bits;
}

/**
 * PatternPredictor — pure binary sequence learner.
 *
 * Scans every consecutive window of `windowSize` bits in the history and
 * records how often the NEXT bit is 1 (up) vs 0 (down).  When asked to
 * predict, it looks up the current window in the frequency table and returns
 * CALL / PUT / null depending on how confidently one direction dominates.
 *
 * No indicators, no price levels — only the raw up/down sequence matters.
 */
class PatternPredictor {
    constructor(windowSize = PATTERN_WINDOW, minSamples = MIN_SAMPLES, minConfidence = MIN_CONFIDENCE) {
        this.windowSize    = windowSize;
        this.minSamples    = minSamples;
        this.minConfidence = minConfidence;
        this.table         = {}; // key: bit-string → { up: n, down: n }
    }

    /** Build frequency table from a complete bit sequence. */
    train(bits) {
        this.table = {};
        for (let i = this.windowSize; i < bits.length; i++) {
            const key  = bits.slice(i - this.windowSize, i).join('');
            const next = bits[i];
            if (!this.table[key]) this.table[key] = { up: 0, down: 0 };
            if (next === 1) this.table[key].up++;
            else            this.table[key].down++;
        }
    }

    /**
     * Predict the next bit from the tail of `bits`.
     * Returns 'CALL', 'PUT', or null (no confident signal).
     */
    predict(bits) {
        if (bits.length < this.windowSize) return null;
        const key   = bits.slice(-this.windowSize).join('');
        const entry = this.table[key];
        if (!entry) return null;

        const total = entry.up + entry.down;
        if (total < this.minSamples) return null;

        const probUp   = entry.up   / total;
        const probDown = entry.down / total;

        if (probUp   >= this.minConfidence) return 'CALL';
        if (probDown >= this.minConfidence) return 'PUT';
        return null;
    }

    /** Log the full pattern table — useful for dry-run analysis. */
    logTable() {
        const rows = Object.entries(this.table)
            .map(([key, { up, down }]) => ({ key, total: up + down, probUp: up / (up + down), up, down }))
            .sort((a, b) => b.total - a.total);
        console.log('\n[PREDICTOR] Pattern table (window=' + this.windowSize + '):');
        console.log('  Pattern  Seen   P(up)   Up   Down');
        for (const r of rows) {
            const flag = r.probUp >= this.minConfidence ? ' ← CALL'
                       : (1 - r.probUp) >= this.minConfidence ? ' ← PUT' : '';
            console.log(`  ${r.key}     ${String(r.total).padEnd(6)} ${(r.probUp * 100).toFixed(1)}%   ${r.up}    ${r.down}${flag}`);
        }
    }
}

// ─── DerivAPI connection ──────────────────────────────────────────────────────

let api;
let wsConnection; // keep reference so we can check readyState

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

// Reconnects if the WebSocket has closed or is closing.
// OTP-based connections expire, so this is expected to happen.
async function ensureConnected() {
    // readyState: 0=CONNECTING, 1=OPEN, 2=CLOSING, 3=CLOSED
    if (!wsConnection || wsConnection.readyState === 2 || wsConnection.readyState === 3) {
        console.log('[BOT] Connection lost — reconnecting …');
        await initializeDerivAPI();
    }
}

// ─── Signal generation ────────────────────────────────────────────────────────

const predictor = new PatternPredictor();

async function getSignal(symbol) {
    const response = await api.basic.send({
        ticks_history:     symbol,
        adjust_start_time: 1,
        count:             TRAINING_CANDLES,
        end:               'latest',
        style:             'candles',
        granularity:       CANDLE_GRANULARITY,
    });

    if (response.error) throw new Error(`ticks_history error: ${response.error.message}`);

    const candles = response.candles;
    if (!candles || candles.length < PATTERN_WINDOW + MIN_SAMPLES + 2) {
        console.log('[BOT] Not enough candles to build pattern table.');
        return null;
    }

    // Convert full history to bits, train on all but the very last bit,
    // then predict what the next candle will be.
    const bits          = candlesToBits(candles);           // length = candles.length - 1
    const trainingBits  = bits.slice(0, -1);               // all except the most recent outcome
    const predictionBits = bits.slice(-(PATTERN_WINDOW));  // current window to look up

    predictor.train(trainingBits);
    const signal = predictor.predict(predictionBits);

    // Log the current pattern and its stats
    const key   = predictionBits.join('');
    const entry = predictor.table[key];
    const total = entry ? entry.up + entry.down : 0;
    const probUp = entry && total > 0 ? (entry.up / total * 100).toFixed(1) : 'n/a';
    console.log(
        `[${new Date().toISOString()}] ${symbol} | ` +
        `Pattern: ${key} | Seen: ${total}x | P(up): ${probUp}% | Signal: ${signal ?? 'NONE'}`
    );

    if (DRY_RUN && signal) predictor.logTable();

    return signal;
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
        contract_type:     direction,      // "CALL" or "PUT"
        currency:          'USD',
        duration:          CONTRACT_DURATION_MIN,
        duration_unit:     'm',
        underlying_symbol: symbol,
    });

    if (proposalResponse.error) {
        throw new Error(`Proposal error: ${proposalResponse.error.message}`);
    }

    // Check actual payout rate before committing
    const { ask_price, payout, id: proposalId } = proposalResponse.proposal;
    const payoutRate = (payout - ask_price) / ask_price; // net return as a fraction
    console.log(`[BOT] Payout check — ask: $${ask_price} | payout: $${payout} | rate: ${(payoutRate * 100).toFixed(1)}%`);
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

    // if (session.trades >= MAX_DAILY_TRADES) {
    //     console.log(`[BOT] Max daily trades (${MAX_DAILY_TRADES}) reached. Sitting out this cycle.`);
    //     return;
    // }

    const todayNetPnL = await fetchTodayNetPnL();
    console.log(`[BOT] Today's net P&L: $${todayNetPnL.toFixed(2)}`);
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

        console.log(`[BOT] ${symbol} signal: ${signal ?? 'NONE'}`);

        if (signal === 'CALL' || signal === 'PUT') {
            try {
                const result = await executeTrade(symbol, signal);
                if (result !== null) { traded = true; break; } // one trade per cycle
            } catch (err) {
                console.error(`[BOT] Trade error on ${symbol}:`, err.message);
            }
        }
    }
    if (!traded) console.log('[BOT] No qualifying trade this cycle.');

    console.log(`[BOT] Session: ${session.trades}/${MAX_DAILY_TRADES} trades | Net P&L today: $${todayNetPnL.toFixed(2)}`);
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
            return; // success
        } catch (err) {
            console.error(`[BOT] Connection attempt ${attempt}/${maxAttempts} failed: ${err.message}`);
            if (attempt === maxAttempts) throw err;
            const wait = attempt * 5000; // 5s, 10s, 15s …
            console.log(`[BOT] Retrying in ${wait / 1000}s …`);
            await new Promise(r => setTimeout(r, wait));
        }
    }
}

async function run() {
    if (DRY_RUN) console.log('[BOT] *** DRY RUN MODE — signals logged, no trades placed ***');

    await connectWithRetry();
    console.log(`[BOT] Started. Evaluating every ${CONTRACT_DURATION_MIN} minutes.`);

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
