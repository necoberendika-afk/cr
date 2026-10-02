# clovercoin

A green-themed Discord cryptocurrency **simulator** built with Python, `discord.py`, SQLite, matplotlib, and CoinGecko's public market-data API. The live market price is displayed as a reference; users trade at the bot's simulated trading price using virtual USD only. No wallets, private keys, real funds, or blockchain transactions are involved.

## Features

- Slash commands: `/buy`, `/sell`, `/viewchart`, `/viewcoins`, `/balance`, `/portfolio`, `/coininfo`, `/transactions`, `/leaderboard`, `/help`.
- Admin commands: `/rugpull`, `/skyrocket`, `/addmoney`, `/setprice`, `/addcoin`, `/removecoin`, `/setbalance`, `/adminstats`, `/adminhelp`.
- Admin allowlist is configured in `config.py` and contains IDs `794922940671066142` and `1533231895905571017`.
- New accounts start with $1,000 virtual USD.
- SQLite persistence, atomic trades, Decimal calculations, price history, and an administrative audit trail.
- CoinGecko USD market references, refreshed every 60 seconds by default.
- PNG charts generated in memory with a dark green design.
- The clover emoji 🍀 is the only emoji used in bot-authored UI text.

## Requirements

- Python 3.10+ (Python 3.12 recommended)
- A Discord application and bot token
- Internet access for Discord and CoinGecko price references

## Discord setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application named `clovercoin`.
2. Open **Bot**, create the bot user, and copy its token. Keep this token private.
3. Under **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes. Grant only the bot permissions it needs, such as View Channels, Send Messages, Embed Links, and Attach Files. Use the generated URL to invite it to your server.
4. You do not need privileged message-content or member intents for these slash commands.

## Install on Linux (including Arch, Fedora, Garuda, and Void where Python packages are available)

```bash
cd clovercoin
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and replace the placeholder with your real bot token. The bot loads `.env` automatically through `python-dotenv`. Do not share `.env` or commit it to Git. Start the bot with:

```bash
python bot.py
```

## Install on Windows

```powershell
cd clovercoin
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`, then run the bot from the activated virtual environment:

```powershell
python bot.py
```

## Commands

### Public

- `/buy coinname amount` — spend virtual USD; `amount` is dollars to spend.
- `/sell coinname amount` — sell a quantity of a coin.
- `/viewchart coinname period` — chart period: `1h`, `24h`, `7d`, or `30d`.
- `/viewcoins` — browse active coins.
- `/balance` — virtual USD balance.
- `/portfolio` — holdings and total simulated portfolio value.
- `/coininfo coinname` — simulated price and live market reference.
- `/transactions` — recent trades.
- `/leaderboard` — top virtual portfolios.
- `/help` — command list.

### Admin (only the two IDs in `config.py`)

- `/rugpull coinname` — sets the simulated trading price to $0 and locks it against live-price refreshes.
- `/skyrocket coinname` — adds exactly $100,000 to the simulated price.
- `/addmoney username amount` — credits a user's virtual USD.
- `/setprice coinname price` — sets a simulated price and locks it from live-price refreshes.
- `/addcoin name ticker initial_price` — adds a custom coin with a manually seeded reference price.
- `/removecoin coinname` — disables new trades without deleting historical records or holdings.
- `/setbalance username amount` — sets virtual cash balance.
- `/adminstats` — counts users, active coins, and trades.
- `/adminhelp` — administrative command list.

Every admin handler checks the caller's numeric Discord user ID. Admin changes are stored in the SQLite audit table; optional channel logging is configured with `ADMIN_LOG_CHANNEL_ID`.

## Price behavior and notes

- The bot queries CoinGecko's public `/simple/price` endpoint for seeded coins. Network failure or rate limiting is logged and the last stored prices remain available.
- For normal coins, the simulated trading price follows the fetched market reference. Admin-modified prices are locked as simulated overrides so a subsequent refresh will not undo `/rugpull` or `/skyrocket`.
- A custom coin added with `/addcoin` is fictional/manual and does not automatically map to CoinGecko. Its reference price starts at the supplied initial price.
- Charts display recorded simulated trading prices, not a separate historical CoinGecko feed. A new database starts with very little chart history; charts become more useful as the bot runs.
- `/sell` quantity means units of the coin, while `/buy` amount means USD to spend.
- Use the bot only as a virtual economy. Displayed market data is for reference and may be delayed or unavailable.

## Troubleshooting

- **Slash commands don't show:** ensure the bot was invited with `applications.commands`, wait a little after startup, and confirm the terminal reports command sync. Check bot invite permissions and restart after correcting configuration.
- **Market data fails:** confirm internet access; CoinGecko may rate-limit public API requests. The bot keeps stored prices and retries on the next interval.
- **Chart dependency errors:** activate the same virtual environment used to install `requirements.txt` and reinstall the dependencies.
- **Token error:** make sure `DISCORD_TOKEN` is set in the same terminal/session that starts `bot.py`.
- **Admin denied:** check that the command caller's numeric Discord user ID exactly matches an ID in `config.py`.

## Project layout

```text
clovercoin/
├── bot.py
├── config.py
├── database.py
├── market.py
├── charts.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```
