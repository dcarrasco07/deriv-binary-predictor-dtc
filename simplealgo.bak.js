require('dotenv').config();

const WebSocket = require('ws');
const DerivAPI = require('@deriv/deriv-api/dist/DerivAPI');
const https = require('https');
const { randomInt } = require('crypto');


// ─── Environment Variables ──────────────────────────────────────────────────
const app_id           = '32WzmZD0GdX5NdJKlPO7e';
const api_token        = 'pat_bc78db629feabf69a853ede8323ef15e2b35301f4af90273bfdd0c380edddda1';
const deriv_account_id = 'ROT91151098';
const BET_AMOUNT        = 1;

//const api_token = 'pat_e20186217b7a6fe596656cb50430f440b88a30bbb9f83760dc86ec451117a6f1';
//const deriv_account_id = 'DOT90416964'

if (!app_id || !api_token || !deriv_account_id) {
    console.error('[BOT] Missing env vars: APP_ID, API_TOKEN, and DERIV_ACCOUNT_ID must all be set.');
    process.exit(1); 
}

// ─── Risk Management & Martingale Settings ──────────────────────────────────
const MAX_DAILY_NET_LOSS      = 30; 
const TICK_DURATION           = 1;               
const TICK_HISTORY_COUNT      = 10;              
const MARTINGALE_MULTIPLIER   = 2.5;
const SCAN_SYMBOLS            = ['R_100']; 

let currentStake   = BET_AMOUNT;
let lastContractId = null;
let isProcessing   = false;
let tickHistory    = {};

let lastPattern = "00";
let actualLastPattern = "00";
let signal = "0";
let actualsignal = "0";
let tradeSignal = 0;
let finalTradeDirection = "";

start();

 

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
        // start of the day in UTC
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
        signal = contract.contract_type === "PUT" ? "1" : "0";
        //console.log("this is the last pattern detected.");
        //console.log(lastPattern)
        // if conntract is sold
        if (contract.is_sold) {
            const profit = parseFloat(contract.profit);
            if (profit > 0) {
                console.log(`[RESULT] WIN (+$${profit.toFixed(2)}). Resetting stake to $${BET_AMOUNT}`);
                actualsignal = contract.contract_type === "PUT" ? "1" : "0";
                currentStake = BET_AMOUNT;
            } else {
                currentStake = currentStake * MARTINGALE_MULTIPLIER;
                actualsignal = contract.contract_type === "PUT" ? "0" : "1";
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

async function executeTrade(symbol, direction, stake) {
    // 1. Get a Proposal first
    let proposalResponse = await api.basic.send({
        proposal: 1,
        amount: parseFloat(stake.toFixed(2)),
        basis: 'stake',
        contract_type: direction,
        currency: 'USD',
        duration: TICK_DURATION,
        duration_unit: 't',
        underlying_symbol: symbol,
    });

    //console.log('Proposal Response:', proposalResponse); // Debug log

    // Fallback if 'symbol' is invalid
    if (proposalResponse.error && (proposalResponse.error.code === 'InvalidSymbol' || proposalResponse.error.message.includes('underlying_symbol'))) {
        proposalResponse = await api.basic.send({
            proposal: 1,
            amount: parseFloat(stake.toFixed(2)),
            basis: 'stake',
            contract_type: direction,
            currency: 'USD',
            duration: TICK_DURATION,
            duration_unit: 't',
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

    //console.log('Buy Response:', buyResponse); // Debug log

    if (buyResponse.error) {
        if (buyResponse.error.code === 'InsufficientBalance') {
            console.error('[BOT] Insufficient Balance. Resetting stake to initial BET_AMOUNT.');
            currentStake = BET_AMOUNT; // Reset stake
        }
        throw new Error(`Buy failed: ${buyResponse.error.message}`);
    }

    console.log(`[TRADE] Placed ${direction} | ID: ${buyResponse.buy.contract_id} | Stake: $${stake.toFixed(2)}`);
    return buyResponse.buy.contract_id;
}

// ─── Connection & Cycle ──────────────────────────────────────────────────────

async function initializeDerivAPI() {
    try {
        const otpResponse = await new Promise((resolve, reject) => {
            const options = {
                hostname: 'api.derivws.com',
                path: `/trading/v1/options/accounts/${deriv_account_id}/otp`,
                method: 'POST',
                headers: {
                    'Deriv-App-ID': app_id,
                    'Authorization': `Bearer ${api_token}`,
                    'Content-Type': 'application/json'
                }
            };

            const req = https.request(options, (res) => {
                let data = '';
                res.on('data', (chunk) => {
                    data += chunk;
                });
                res.on('end', () => {
                    resolve({
                        status: res.statusCode,
                        json: () => JSON.parse(data)
                    });
                });
            });

            req.on('error', (error) => {
                reject(error);
            });
            req.end();
        }).catch(error => {
            console.error('[BOT] HTTPS request error during OTP request:', error);
            throw error;
        });
        console.log('OTP Response Status:', otpResponse.status);
        const { data } = await otpResponse.json();
        console.log('OTP Response Data:', data);
        wsConnection = new WebSocket(data.url);
        api = new DerivAPI({ connection: wsConnection });
        
        // Authorization
        await api.basic.authorize(api_token);

        // Subscribe to ticks
        for (const symbol of SCAN_SYMBOLS) {
            api.basic.send({ ticks: symbol, subscribe: 1 });
        }

        // Tick listener
        wsConnection.on('message', (data) => {
            const msg = JSON.parse(data);
            if (msg.msg_type === 'tick') {
                const symbol = msg.tick.symbol;
                const price = parseFloat(msg.tick.quote);
                if (!tickHistory[symbol]) tickHistory[symbol] = [];
                tickHistory[symbol].push(price);
                if (tickHistory[symbol].length > TICK_HISTORY_COUNT) {
                    tickHistory[symbol].shift();
                }
                tradingCycle();
            }
        });
        
        console.log('[BOT] Connected, Authorized, and Subscribed to ticks.');
    } catch (error) {
        console.error('[BOT] Error during Deriv API initialization:', error);
    }
}

async function tradingCycle() {
    if (isProcessing) return;
    isProcessing = true;

    try {
        if (!wsConnection || wsConnection.readyState !== 1) {
            await initializeDerivAPI();
        }

        // 1. Check if we are waiting for a trade result
        if (lastContractId) {
            await checkLastTradeResult();
            return; // Don't place new trades until the last one is settled
        }

        // 3. Scan Symbols
        for (const symbol of SCAN_SYMBOLS) {
            console.log("signal:" + signal);
            if (signal) {
                console.log(`[SIGNAL] Pattern ${signal} detected on ${symbol}`);

                try {
                    // Default to signal.direction
                    console.log("last pattern: " + lastPattern);
                    if (actualLastPattern.length >= 2) {
                        const lastTwo = actualLastPattern.slice(-2);
                        if (lastTwo === '00') {
                            finalTradeDirection = 'CALL';
                            console.log('[TRADE_DECISION] Overriding signal: 00 pattern -> CALL');
                        } else if (lastTwo === '01') {
                            let randomIntNum = randomInt(0,1);
                            finalTradeDirection = randomIntNum === 0 ? "CALL" : "PUT";
                            //finalTradeDirection = 'CALL';
                            console.log('[TRADE_DECISION] Overriding signal: 01 pattern -> PUT');
                        } else if (lastTwo === '11') {
                            let randomIntNum = randomInt(0,1);
                            finalTradeDirection = randomIntNum === 0 ? "CALL" : "PUT";
                            //finalTradeDirection = 'PUT';
                            console.log('[TRADE_DECISION] Overriding signal: 11 pattern -> CALL');
                        } else if (lastTwo === '10') {
                            let randomIntNum = randomInt(0,1);
                            finalTradeDirection = randomIntNum === 0 ? "CALL" : "PUT";
                            //finalTradeDirection = 'PUT';    
                            console.log('[TRADE_DECISION] Overriding signal: 10 pattern -> CALL');
                        }
                    }

                    //minCurrentStake = currentStake < 8 ? currentStake : 4;
                    lastContractId = await executeTrade(symbol, finalTradeDirection, currentStake); // Use finalTradeDirection
                    lastPattern += signal;
                    actualLastPattern += actualsignal;
                    console.log("+=======================================+");
                    console.log(lastPattern);
                    console.log(actualLastPattern);

                    // Log previous trades based on patterns
                    if (actualLastPattern.endsWith('00')) {
                        console.log('[TRADE_PATTERN] Previous trade: SELL (00)');
                    } else if (actualLastPattern.endsWith('01')) {
                        console.log('[TRADE_PATTERN] Previous trade: SELL (01)');
                    } else if (actualLastPattern.endsWith('11')) {
                        console.log('[TRADE_PATTERN] Previous trade: BUY (11)');
                    } else if (actualLastPattern.endsWith('10')) {
                        console.log('[TRADE_PATTERN] Previous trade: BUY (10)');
                    }
                    break;        
                } catch (err) {
                    console.error('[BOT] Trade failed:', err);
                }
            }
        }
    } catch (err) {
        console.error('[BOT] Cycle error:', err.message);
    } finally {
        isProcessing = false;
    }
}

// ─── Entry Point ─────────────────────────────────────────────────────────────

async function start() {
    console.log('[BOT] Starting bot...');
    await initializeDerivAPI();
    console.log(`[BOT] Running. Base stake: $${BET_AMOUNT} | Martingale: ${MARTINGALE_MULTIPLIER}x | Duration: ${TICK_DURATION} ticks`);
}

module.exports = {
    app_id,
    api_token,
    deriv_account_id,
    BET_AMOUNT,
    MAX_DAILY_NET_LOSS,
    TICK_DURATION,
    TICK_HISTORY_COUNT,
    MARTINGALE_MULTIPLIER,
    SCAN_SYMBOLS,
    currentStake,
    lastContractId,
    isProcessing,
    tickHistory,
    session,
    api,
    wsConnection,
    fetchTodayNetPnL,
    checkLastTradeResult,
    executeTrade,
    initializeDerivAPI,
    tradingCycle,
    start
};