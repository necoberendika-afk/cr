"""Configuration for clovercoin. Values can be overridden through environment variables."""
import os

BOT_NAME = "clovercoin"
STARTING_BALANCE = 1000.00
ADMIN_IDS = {794922940671066142, 1533231895905571017}
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "clovercoin.sqlite3")
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0") or 0)
PRICE_REFRESH_SECONDS = int(os.getenv("PRICE_REFRESH_SECONDS", "60"))
COINGECKO_API = "https://api.coingecko.com/api/v3"
EMBED_GREEN = 0x238636
EMBED_DARK_GREEN = 0x123524
EMBED_ERROR = 0x8B3030
