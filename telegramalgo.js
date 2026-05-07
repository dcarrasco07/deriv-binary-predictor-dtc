const { Telegraf } = require('telegraf');
const DerivAPI = require('@deriv/deriv-api');
const WebSocket = require('ws');
require('dotenv').config();

const bot = new Telegraf(process.env.TELEGRAM_TOKEN);
let api;

// Global state for live monitoring
let stats = {
    balance: 0,
    currency: '',
    wins: 0,
    losses: 0,
    netPnl: 0
};

async function initializeDerivAPI() {
    try {
        const otpResponse = await fetch(`https://api.derivws.com/trading/v1/options/accounts/${process.env.DERIV_ACCOUNT_ID}/otp`, {
            method: 'POST',
            headers: { 
                'Deriv-App-ID': process.env.APP_ID, 
                'Authorization': `Bearer ${process.env.DERIV_TOKEN}` 
            },
        });
        const { data } = await otpResponse.json();

        const connection = new WebSocket(data.url);
        api = new DerivAPI({ connection });

        // 1. Authorize
        await api.basic.authorize(process.env.DERIV_TOKEN);
        console.log('[BOT] Authorized.');

        // 2. Subscribe to Balance
        // In the basic API, we send the request with 'subscribe: 1'
        api.basic.balance({ subscribe: 1 }).then(() => {
            console.log('[BOT] Subscribed to Balance');
        });

        // 3. Subscribe to Transactions (Monitoring Wins/Losses)
        api.basic.transaction({ subscribe: 1 }).then(() => {
            console.log('[BOT] Subscribed to Transactions');
        });

        // Handle incoming stream messages
        connection.on('message', (data) => {
            const response = JSON.parse(data);

            // Update Balance logic
            if (response.msg_type === 'balance') {
                stats.balance = response.balance.balance;
                stats.currency = response.balance.currency;
            }

            // Update Win/Loss logic
            if (response.msg_type === 'transaction') {
                const t = response.transaction;
                if (t.action === 'sell') {
                    const amount = parseFloat(t.amount);
                    stats.netPnl += amount;
                    
                    if (amount > 0) stats.wins++;
                    else if (amount < 0) stats.losses++;
                    
                    // Auto-notify on Telegram when a trade closes
                    sendTradeAlert(amount);
                }
            }
        });

    } catch (error) {
        console.error('[ERROR]', error);
    }
}

let authorizedChatId; // Variable to store your ID dynamically

bot.start((ctx) => {
    authorizedChatId = ctx.chat.id; // Saves your ID when you type /start
    ctx.reply(`🚀 Bot Linked! Alerts will be sent to this chat (ID: ${authorizedChatId})`);
});

function sendTradeAlert(pnl) {
    if (!authorizedChatId) {
        console.log("⚠️ Alert skipped: No chat_id. Please type /start in Telegram first.");
        return;
    }

    const icon = pnl > 0 ? '✅ WIN' : '❌ LOSS';
    bot.telegram.sendMessage(authorizedChatId, 
        `🔔 *Trade Alert: ${icon}*\nAmount: ${pnl} ${stats.currency}\nNet P/L: ${stats.netPnl.toFixed(2)}`,
        { parse_mode: 'Markdown' }
    ).catch(e => console.error("TG Error:", e.description));
}

// --- Commands ---
bot.command('status', (ctx) => {
    const winRate = (stats.wins + stats.losses) > 0 
        ? ((stats.wins / (stats.wins + stats.losses)) * 100).toFixed(2) 
        : 0;

    ctx.replyWithMarkdown(
        `📊 *Live Monitoring*\n` +
        `----------------------------\n` +
        `💰 *Balance:* ${stats.balance} ${stats.currency}\n` +
        `💵 *Total P/L:* ${stats.netPnl.toFixed(2)}\n` +
        `✅ *Wins:* ${stats.wins}\n` +
        `❌ *Losses:* ${stats.losses}\n` +
        `📈 *Win Rate:* ${winRate}%`
    );
});

bot.launch();
initializeDerivAPI();