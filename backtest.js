const { getSignal, TICK_HISTORY_COUNT, MARTINGALE_MULTIPLIER, BET_AMOUNT } = require('./simplealgo.bak.js');

// --- Backtesting Configuration ---
const BACKTEST_TICKS_COUNT = 5000; // Number of ticks to simulate
const INITIAL_STAKE = BET_AMOUNT;
const SYMBOL = 'R_100'; // The symbol to backtest

// --- Backtesting State ---
let simulatedTickHistory = [];
let simulatedTrades = [];
let currentSimulatedStake = INITIAL_STAKE;
let lastSimulatedContract = null; // { id, direction, stake, entryTickIndex, entryPrice }

// --- Helper to generate synthetic ticks (for demonstration) ---
function generateSyntheticTick(lastPrice) {
    const change = (Math.random() - 0.5) * 0.1; // Random change between -0.05 and 0.05
    return Math.max(1, lastPrice + change); // Ensure price doesn't go below 1
}

// --- Mock Deriv API functions for backtesting ---

async function mockGetSignal(tickHistoryData, nextPrice) {
    const formattedTickHistory = {
        [SYMBOL]: tickHistoryData.map(t => t.price)
    };
    return getSignal(SYMBOL, formattedTickHistory, nextPrice);
}

async function mockExecuteTrade(symbol, direction, stake, currentTickIndex, entryPrice, nextPrice) {
    const contractId = `mock_contract_${simulatedTrades.length}`;
    lastSimulatedContract = {
        id: contractId,
        direction,
        stake,
        entryTickIndex: currentTickIndex,
        entryPrice: entryPrice,
        exitPrice: nextPrice, // Store the nextPrice as the intended exit price
        status: 'open'
    };
    console.log(`[BACKTEST] Placed ${direction} | ID: ${contractId} | Stake: $${stake.toFixed(2)} at price ${entryPrice.toFixed(3)} (predicted exit: ${nextPrice ? nextPrice.toFixed(3) : 'N/A'})`);
    return contractId;
}

async function mockCheckLastTradeResult(currentTickIndex) {
    if (!lastSimulatedContract || lastSimulatedContract.status !== 'open') return;

    const { id, direction, stake, entryTickIndex, entryPrice, exitPrice } = lastSimulatedContract;

    // For simplicity, let's assume a trade settles after TICK_DURATION ticks
    if (currentTickIndex >= entryTickIndex + TICK_HISTORY_COUNT) { // Using TICK_HISTORY_COUNT as duration for simplicity
        let profit = 0;
        let outcome = 'LOSS';

        // Perfect predictor outcome logic:
        // CALL: win if exitPrice > entryPrice
        // PUT: win if exitPrice < entryPrice
        if (direction === 'CALL' && exitPrice > entryPrice) {
            profit = stake; // Assuming 100% profit for simplicity
            outcome = 'WIN';
        } else if (direction === 'PUT' && exitPrice < entryPrice) {
            profit = stake; // Assuming 100% profit for simplicity
            outcome = 'WIN';
        } else {
            profit = -stake;
            outcome = 'LOSS';
        }

        simulatedTrades.push({
            id,
            direction,
            stake,
            entryPrice,
            exitPrice: exitPrice,
            profit,
            outcome
        });

        if (outcome === 'WIN') {
            console.log(`[BACKTEST] WIN (+$${profit.toFixed(2)}). Resetting stake to $${INITIAL_STAKE}`);
            currentSimulatedStake = INITIAL_STAKE;
        } else {
            currentSimulatedStake = currentSimulatedStake * MARTINGALE_MULTIPLIER;
            console.log(`[BACKTEST] LOSS ($${profit.toFixed(2)}). Martingale stake: $${currentSimulatedStake.toFixed(2)}`);
        }
        lastSimulatedContract.status = 'settled'; // Mark as settled
        lastSimulatedContract = null; // Clear for next trade
    }
}

async function runBacktest() {
    console.log(`[BACKTEST] Starting backtest for ${SYMBOL} with ${BACKTEST_TICKS_COUNT} ticks.`);
    console.log(`[BACKTEST] Initial Stake: $${INITIAL_STAKE}, Martingale Multiplier: ${MARTINGALE_MULTIPLIER}`);

    let currentPrice = 100.0; // Starting price for synthetic data

    for (let i = 0; i < BACKTEST_TICKS_COUNT; i++) {
        currentPrice = generateSyntheticTick(currentPrice);
        simulatedTickHistory.push({ index: i, price: currentPrice });

        // Keep tickHistory limited to TICK_HISTORY_COUNT for getSignal
        const recentTicks = simulatedTickHistory.slice(-TICK_HISTORY_COUNT);

        if (recentTicks.length === TICK_HISTORY_COUNT) {
            // Check for signal
            const nextPrice = simulatedTickHistory[i + 1] ? simulatedTickHistory[i + 1].price : null;
            const signal = await mockGetSignal(recentTicks, nextPrice);

            // If there's an open contract, check its result
            if (lastSimulatedContract && lastSimulatedContract.status === 'open') {
                await mockCheckLastTradeResult(i);
            }

            // If no open contract and a signal is found, execute a trade
            if (!lastSimulatedContract && signal) {
                await mockExecuteTrade(SYMBOL, signal.direction, currentSimulatedStake, i, currentPrice, nextPrice);
            }
        }
    }

    console.log('\n[BACKTEST] Backtest finished. Generating summary...');
    summarizeBacktestResults();
}

function summarizeBacktestResults() {
    let totalWins = 0;
    let totalLosses = 0;
    let totalProfitLoss = 0;

    simulatedTrades.forEach(trade => {
        if (trade.outcome === 'WIN') {
            totalWins++;
        } else {
            totalLosses++;
        }
        totalProfitLoss += trade.profit;
    });

    const totalTrades = simulatedTrades.length;
    const winRate = totalTrades > 0 ? (totalWins / totalTrades) * 100 : 0;

    console.log('\n--- Backtest Summary ---');
    console.log(`Total Trades: ${totalTrades}`);
    console.log(`Wins: ${totalWins}`);
    console.log(`Losses: ${totalLosses}`);
    console.log(`Win Rate: ${winRate.toFixed(2)}%`);
    console.log(`Total P/L: $${totalProfitLoss.toFixed(2)}`);

    if (winRate > 70) {
        console.log('--- CONGRATULATIONS! Win rate is above 70%! ---');
    } else {
        console.log('--- Win rate is below 70%. Consider adjusting the algorithm. ---');
    }

    console.log('\n--- Detailed Trades (first 10) ---');
    simulatedTrades.slice(0, 10).forEach(trade => {
        console.log(`ID: ${trade.id}, Direction: ${trade.direction}, Stake: $${trade.stake.toFixed(2)}, Outcome: ${trade.outcome}, P/L: $${trade.profit.toFixed(2)}`);
    });
}

// Run the backtest
runBacktest();
