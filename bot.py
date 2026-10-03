"""
clovercoin: Discord crypto market-reference bot & trading economy.
Theme: Minimal Emerald & Clover Terminal.
Compatible with both slash commands (/cmd) and prefix commands (~cmd).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import math
import random
import re
import traceback
from typing import Optional, Literal

from aiohttp import web
from dotenv import load_dotenv
load_dotenv()

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    BOT_NAME,
    DISCORD_TOKEN,
    ADMIN_IDS,
    ADMIN_LOG_CHANNEL_ID,
    PRICE_REFRESH_SECONDS,
)
import database as db
from database import DB_LOCK, connect, now_iso
from market import refresh_market
from charts import create_chart
import os

# --- Web Server Setup (Replaces express) ---
async def handle(request):
    return web.Response(text="Hello World!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Use process.env.PORT equivalent in Python
    port = int(os.getenv("PORT", 4000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Example app listening on port {port}")

# --- Bot Setup ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="~", intents=intents)

@bot.event
async def on_ready():
    # Start the web server in the background task loop
    bot.loop.create_task(start_web_server())
    print(f"Logged in as WEBSITE: {bot.user} (clovercoin)git add bot.py")
# ==============================================================================
# VISUAL CONSTANTS & ASSETS
# ==============================================================================
COLOR_EMERALD = 0x00A86B       # Primary Shamrock Green
COLOR_DARK    = 0x14532D       # Irish Forest
COLOR_MUTED   = 0x1E3A2F       # Dark Pine for alerts

# Your custom banner image
BANNER_IMAGE_URL = "https://cdn.discordapp.com/attachments/1554954298188767254/1555611844717314068/undertext_coin.png?backend=b2&ex=6ac127ed&is=6abfd66d&hm=ee7de83a67466c9e4e88fa5ba331e8d96728c5d0b593ff9272124dde1e6ecfaf"

DECIMAL_ZERO = Decimal("0")
DECIMAL_SATOSHI = Decimal("0.00000001")

# Fast in-memory cache for autocomplete and zero-latency lookups
COIN_CACHE: list[dict] = []




# ==============================================================================
# DATA CONVERTERS & FORMATTERS
# ==============================================================================
def row_to_dict(row) -> Optional[dict]:
    """Safely convert SQLite row objects to standard dicts."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {k: row[k] for k in row.keys()}

def reload_coin_cache():
    """Sync the in-memory autocomplete cache from the database."""
    global COIN_CACHE
    try:
        raw = db.list_coins()
        COIN_CACHE = [row_to_dict(r) for r in raw if r]
    except Exception as exc:
        print(f"[cache] Failed to refresh memory cache: {exc}")

def reset_all_coins_to_reference():
    """Reset every coin's price back to its original reference price and remove overrides."""
    with DB_LOCK, connect() as c:
        c.execute("UPDATE coins SET price = reference_price, admin_override = 0")
        rows = c.execute("SELECT coin_id, price FROM coins").fetchall()
        now = now_iso()
        for r in rows:
            c.execute(
                "INSERT INTO price_history(coin_id, price, recorded_at) VALUES(?, ?, ?)",
                (r["coin_id"], str(r["price"]), now)
            )
    reload_coin_cache()

def money(value: Decimal | float | int | str) -> str:
    """Format numeric values into standard or micro-crypto notation."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return "$0.00"

    abs_d = abs(d)
    if abs_d == 0:
        return "$0.00"
    if abs_d >= Decimal("1.00"):
        return f"${d:,.2f}"
    if abs_d >= Decimal("0.01"):
        return f"${d:,.4f}"
    return f"${d:,.8f}".rstrip("0").rstrip(".")

def fmt_qty(value: Decimal | float | str) -> str:
    """Format crypto token quantities without scientific notation."""
    d = Decimal(str(value)).normalize()
    _, digits, exp = d.as_tuple()
    if exp < 0:
        return f"{d:.{abs(exp)}f}"
    return f"{d:f}"

def format_change(current: Decimal, original: Decimal) -> str:
    """Calculate and format percentage change cleanly."""
    if original <= 0:
        return "0.00%"
    pct = ((current - original) / original) * Decimal("100")
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:,.2f}%"

def progress_bar(ratio: float, length: int = 12) -> str:
    """Render a clean high-contrast ASCII progress bar."""
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * length))
    return "▰" * filled + "▱" * (length - filled)

def parse_decimal(value: float | int | str, label: str) -> Decimal:
    """Strict numeric validation parser."""
    try:
        res = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a valid number.")
    if not res.is_finite() or res.is_nan():
        raise ValueError(f"{label} must be a finite numerical value.")
    return res

def clover_embed(
    title: str,
    description: str = "",
    *,
    dark: bool = False,
    hide_banner: bool = False
) -> discord.Embed:
    """Generate consistent emerald-themed embeds ending with Clovers market system banner."""
    e = discord.Embed(
        title=f"🍀 {title}",
        description=description,
        color=COLOR_DARK if dark else COLOR_EMERALD
    )
    e.set_footer(text=f"{BOT_NAME} · Clovers market system")
    e.timestamp = datetime.now(timezone.utc)

    if BANNER_IMAGE_URL and not hide_banner:
        e.set_image(url=BANNER_IMAGE_URL)

    return e

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ==============================================================================
# DATABASE SCHEMA MIGRATION
# ==============================================================================
def patch_db_schema():
    with DB_LOCK, connect() as c:
        for col, col_def in [
            ("last_daily", "TEXT"),
            ("daily_streak", "INTEGER DEFAULT 0"),
        ]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
            except Exception:
                pass


# ==============================================================================
# DISCORD CLIENT (HYBRID ~ AND / SUPPORT)
# ==============================================================================
intents = discord.Intents.default()
intents.message_content = True  # Required for ~cmd prefix commands to read arguments

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("~", "-"),
    intents=intents,
    help_command=None
)

async def resolve_coin(ctx: commands.Context, raw_input: str) -> Optional[dict]:
    """Smart resolver that accepts tickers, names, or autocomplete formatted strings."""
    q = raw_input.strip()

    # Extract ticker if inside parenthesis: "Bitcoin (BTC)" -> "BTC"
    match = re.search(r"\(([^)]+)\)", q)
    extracted = match.group(1).strip() if match else q
    target = extracted.lower()

    # 1. Fast cache lookup
    for c in COIN_CACHE:
        if c.get("symbol", "").lower() == target or c.get("name", "").lower() == target or c.get("coin_id", "").lower() == target:
            if not c.get("is_active", 1):
                break
            return c

    # 2. Direct database query fallback
    coin = row_to_dict(db.get_coin(extracted))
    if not coin:
        coin = row_to_dict(db.get_coin(q))

    if not coin or not coin.get("is_active", 1):
        msg = f"Coin `{raw_input}` was not found or is currently disabled. Use `~viewcoins` to view available coins."
        await ctx.send(msg, ephemeral=True)
        return None

    return coin

async def coin_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Instant, zero-latency in-memory autocomplete for slash commands."""
    try:
        q = current.lower().strip()
        choices = []
        for c in COIN_CACHE:
            if not c.get("is_active", 1):
                continue
            symbol = c.get("symbol", "")
            name = c.get("name", "")
            price = money(c.get("price", 0))

            if not q or q in symbol.lower() or q in name.lower():
                choices.append(
                    app_commands.Choice(
                        name=f"{name} ({symbol}) — {price}"[:100],
                        value=symbol
                    )
                )
            if len(choices) >= 25:
                break
        return choices
    except Exception:
        return []


# ==============================================================================
# BACKGROUND PRICE SYNC
# ==============================================================================
@tasks.loop(seconds=max(30, PRICE_REFRESH_SECONDS))
async def market_loop():
    try:
        count = await refresh_market()
        reload_coin_cache()
        print(f"[market] Refreshed {count} reference prices.")
    except Exception as exc:
        print(f"[market] Sync cycle deferred: {exc}")

@market_loop.before_loop
async def before_market_loop():
    await bot.wait_until_ready()


# ==============================================================================
# UI VIEWS & CONTROLS
# ==============================================================================
class CoinSelectMenu(discord.ui.Select):
    def __init__(self, page_coins: list[dict]):
        options = []
        for c in page_coins[:25]:
            sym = str(c["symbol"])
            options.append(
                discord.SelectOption(
                    label=f"{c['name']} ({sym})"[:100],
                    description=f"Sim: {money(c['price'])} | Live: {money(c['reference_price'])}"[:100],
                    value=sym,
                )
            )
        super().__init__(placeholder="Select a coin to inspect...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        sym = self.values[0]
        coin = row_to_dict(db.get_coin(sym))
        if not coin:
            await interaction.response.send_message("Coin data unavailable.", ephemeral=True)
            return

        p_sim = Decimal(str(coin["price"]))
        p_ref = Decimal(str(coin["reference_price"]))
        diff = format_change(p_sim, p_ref)

        e = clover_embed(
            f"{coin['name']} ({coin['symbol']})",
            f"**Trading Price:** `{money(p_sim)}`\n"
            f"**Market Reference:** `{money(p_ref)}`\n"
            f"**Variance:** `{diff}`\n\n"
            f"• Override Active: `{'Yes' if coin.get('admin_override') else 'No'}`\n"
            f"• Updated: <t:{int(datetime.fromisoformat(coin['updated_at']).timestamp())}:R>"
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

class MarketBrowseView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], chunks: list[list[dict]], user_id: int):
        super().__init__(timeout=180)
        self.pages = pages
        self.chunks = chunks
        self.user_id = user_id
        self.index = 0
        self.sync_items()

    def sync_items(self):
        self.clear_items()
        if len(self.pages) > 1:
            prev_btn = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary, disabled=(self.index == 0))
            prev_btn.callback = self.on_prev
            self.add_item(prev_btn)

            indicator = discord.ui.Button(label=f"{self.index + 1} / {len(self.pages)}", style=discord.ButtonStyle.success, disabled=True)
            self.add_item(indicator)

            next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, disabled=(self.index >= len(self.pages) - 1))
            next_btn.callback = self.on_next
            self.add_item(next_btn)

        if self.chunks and self.index < len(self.chunks):
            self.add_item(CoinSelectMenu(self.chunks[self.index]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Run `~viewcoins` or `/viewcoins` to browse independently.", ephemeral=True)
            return False
        return True

    async def on_prev(self, interaction: discord.Interaction):
        self.index -= 1
        self.sync_items()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_next(self, interaction: discord.Interaction):
        self.index += 1
        self.sync_items()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


class ConfirmRugpullView(discord.ui.View):
    def __init__(self, coin: dict, admin_id: int):
        super().__init__(timeout=30)
        self.coin = coin
        self.admin_id = admin_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin_id

    @discord.ui.button(label="Confirm Price Reset to $0.00", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        old_price = Decimal(str(self.coin["price"]))
        with DB_LOCK, connect() as c:
            c.execute("UPDATE coins SET admin_override=1 WHERE coin_id=?", (self.coin["coin_id"],))
        db.record_price(self.coin["coin_id"], DECIMAL_ZERO)
        reload_coin_cache()

        e = clover_embed(
            "Market Action: Price Reset",
            f"**{self.coin['name']} ({self.coin['symbol']})** simulated price reset from `{money(old_price)}` to `$0.00`.",
            dark=True
        )
        await interaction.response.edit_message(embed=e, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Action cancelled.", embed=None, view=None)


# ==============================================================================
# AUDITING & PERMISSIONS
# ==============================================================================
async def deny_admin(ctx: commands.Context) -> bool:
    if is_admin(ctx.author.id):
        return True
    msg = "You are not authorized to use administrative commands."
    await ctx.send(msg, ephemeral=True)
    return False

async def audit(ctx: commands.Context, action: str, details: str):
    db.record_admin(ctx.author.id, action, details)
    if ADMIN_LOG_CHANNEL_ID:
        channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            try:
                e = clover_embed(
                    f"Admin Log: {action}",
                    f"**Admin:** {ctx.author.mention} (`{ctx.author.id}`)\n"
                    f"**Details:** {details}\n"
                    f"**Time:** <t:{int(datetime.now(timezone.utc).timestamp())}:F>"
                )
                await channel.send(embed=e)
            except discord.HTTPException:
                pass


# ==============================================================================
# BOT INITIALIZATION
# ==============================================================================
@bot.event
async def on_ready():
    if not getattr(bot, "_clover_ready", False):
        db.init_db()
        patch_db_schema()

        try:
            await refresh_market()
        except Exception as exc:
            print(f"[init] Market refresh deferred: {exc}")

        # Reset every coin to its original live reference price
        reset_all_coins_to_reference()
        print("[init] 🍀 Successfully reset all coins to original reference prices.")

        try:
            synced = await bot.tree.sync()
            print(f"[init] Synced {len(synced)} slash commands. (Prefix '~' also active)")
        except Exception as exc:
            print(f"[init] Sync failed: {exc}")

        if not market_loop.is_running():
            market_loop.start()

        bot._clover_ready = True

    print(f"Logged in as {bot.user} (clovercoin)")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="crypto markets | ~help"))


# ==============================================================================
# USER TRADING COMMANDS (HYBRID: ~buy OR /buy)
# ==============================================================================
@bot.hybrid_command(name="buy", description="Purchase cryptocurrency using virtual USD balance")
@app_commands.describe(
    coinname="Coin name or ticker symbol",
    amount="Amount of virtual USD to spend",
    quantity="Exact token quantity to buy (optional)"
)
@app_commands.autocomplete(coinname=coin_autocomplete)
async def buy(
    ctx: commands.Context,
    coinname: str,
    amount: Optional[float] = None,
    quantity: Optional[float] = None
):
    if amount is None and quantity is None:
        await ctx.send("Specify either an `amount` in virtual USD or a token `quantity` to buy.\nExample: `~buy BTC 500`", ephemeral=True)
        return

    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    price = Decimal(str(coin["price"]))
    if price <= DECIMAL_ZERO:
        await ctx.send("This coin is priced at $0.00 and cannot be traded.", ephemeral=True)
        return

    db.ensure_user(ctx.author.id)
    user_row = row_to_dict(db.get_user(ctx.author.id))
    balance = Decimal(str(user_row["balance"]))

    if quantity is not None:
        target_qty = parse_decimal(quantity, "Quantity").quantize(DECIMAL_SATOSHI, rounding=ROUND_DOWN)
        spend = (target_qty * price).quantize(Decimal("0.01"))
    else:
        spend = parse_decimal(amount, "Amount").quantize(Decimal("0.01"))
        target_qty = (spend / price).quantize(DECIMAL_SATOSHI, rounding=ROUND_DOWN)

    if target_qty <= DECIMAL_ZERO:
        await ctx.send("Order size too small (minimum 0.00000001 units).", ephemeral=True)
        return

    if spend > balance:
        await ctx.send(
            f"Insufficient funds.\n• Required: `{money(spend)}`\n• Available: `{money(balance)}`",
            ephemeral=True
        )
        return

    try:
        new_balance, held, total = db.trade(ctx.author.id, coin["coin_id"], "buy", target_qty, price)
    except ValueError as exc:
        await ctx.send(str(exc), ephemeral=True)
        return

    e = clover_embed(
        "Order Filled: Buy",
        f"Bought **{fmt_qty(target_qty)} {coin['symbol']}** at `{money(price)}` per unit."
    )
    e.add_field(name="Total Spent", value=f"`{money(total)}`", inline=True)
    e.add_field(name="Holding", value=f"`{fmt_qty(held)} {coin['symbol']}`", inline=True)
    e.add_field(name="Cash Balance", value=f"`{money(new_balance)}`", inline=True)
    await ctx.send(embed=e)


@bot.hybrid_command(name="sell", description="Sell crypto holdings back for virtual USD")
@app_commands.describe(
    coinname="Coin name or ticker symbol",
    amount="Exact quantity to sell (or choose a percentage)",
    percentage="Percentage of current holdings to sell (25, 50, 75, 100)"
)
@app_commands.autocomplete(coinname=coin_autocomplete)
async def sell(
    ctx: commands.Context,
    coinname: str,
    amount: Optional[float] = None,
    percentage: Optional[Literal[25, 50, 75, 100]] = None
):
    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    price = Decimal(str(coin["price"]))
    if price <= DECIMAL_ZERO:
        await ctx.send("This coin is priced at $0.00. Proceeds would be $0.00.", ephemeral=True)
        return

    db.ensure_user(ctx.author.id)
    with connect() as c:
        row = c.execute("SELECT quantity FROM holdings WHERE user_id=? AND coin_id=?", (ctx.author.id, coin["coin_id"])).fetchone()
        row_d = row_to_dict(row)
        owned = Decimal(str(row_d["quantity"])) if row_d else DECIMAL_ZERO

    if owned <= DECIMAL_ZERO:
        await ctx.send(f"You do not own any **{coin['name']} ({coin['symbol']})**.", ephemeral=True)
        return

    if percentage is not None:
        fraction = Decimal(percentage) / Decimal("100")
        qty = (owned * fraction).quantize(DECIMAL_SATOSHI, rounding=ROUND_DOWN)
    elif amount is not None:
        qty = parse_decimal(amount, "Amount").quantize(DECIMAL_SATOSHI, rounding=ROUND_DOWN)
    else:
        await ctx.send("Specify either an `amount` or a `percentage` (25, 50, 75, 100) to sell.", ephemeral=True)
        return

    if qty > owned:
        await ctx.send(f"Sale amount exceeds available position (`{fmt_qty(owned)} {coin['symbol']}`).", ephemeral=True)
        return

    try:
        new_balance, remaining_held, total_proceeds = db.trade(ctx.author.id, coin["coin_id"], "sell", qty, price)
    except ValueError as exc:
        await ctx.send(str(exc), ephemeral=True)
        return

    e = clover_embed(
        "Order Filled: Sell",
        f"Sold **{fmt_qty(qty)} {coin['symbol']}** at `{money(price)}` per unit."
    )
    e.add_field(name="Proceeds", value=f"`{money(total_proceeds)}`", inline=True)
    e.add_field(name="Remaining", value=f"`{fmt_qty(remaining_held)} {coin['symbol']}`", inline=True)
    e.add_field(name="Cash Balance", value=f"`{money(new_balance)}`", inline=True)
    await ctx.send(embed=e)


# ==============================================================================
# PORTFOLIO & MARKET INSPECTION (HYBRID)
# ==============================================================================
@bot.hybrid_command(name="portfolio", description="View your portfolio, holdings, and asset allocation")
@app_commands.describe(user="View another trader's public portfolio (optional)")
async def portfolio_cmd(ctx: commands.Context, user: Optional[discord.User] = None):
    target = user or ctx.author
    db.ensure_user(target.id)
    balance, positions, total_net = db.get_portfolio(target.id)

    cash_ratio = float(balance / total_net) if total_net > 0 else 1.0
    crypto_ratio = 1.0 - cash_ratio
    bar = progress_bar(crypto_ratio)

    e = clover_embed(
        f"{target.display_name}'s Portfolio",
        f"**Net Worth:** `{money(total_net)}`\n"
        f"**Liquid Cash:** `{money(balance)}`\n"
        f"**Invested Assets:** `{money(total_net - balance)}`\n\n"
        f"**Asset Allocation:**\n"
        f"`[{bar}]` {round(crypto_ratio * 100, 1)}% Crypto"
    )
    if positions:
        lines = []
        for name, symbol, qty, price in positions:
            pos_val = qty * price
            pct = (pos_val / total_net * 100) if total_net > 0 else Decimal("0")
            lines.append(f"**{symbol}** · {fmt_qty(qty)} @ {money(price)} = `{money(pos_val)}` ({pct:.1f}%)")
        e.add_field(name="Holdings", value="\n".join(lines), inline=False)
    else:
        e.description += "\n\n*No crypto holdings yet. Use `~buy` to start investing.*"

    await ctx.send(embed=e)


@bot.hybrid_command(name="viewcoins", description="Browse live simulated prices and market reference rates")
async def viewcoins(ctx: commands.Context):
    coins = [row_to_dict(c) for c in db.list_coins() if c]
    if not coins:
        await ctx.send("No active coins available in the database.", ephemeral=True)
        return

    pages = []
    chunk_size = 6
    chunks = [coins[i:i + chunk_size] for i in range(0, len(coins), chunk_size)]
    total_pages = len(chunks)

    for idx, chunk in enumerate(chunks):
        e = clover_embed("Market Overview", "Reference prices track live exchanges; trading prices reflect the simulator.")
        for c in chunk:
            p_ref = Decimal(str(c["reference_price"]))
            p_sim = Decimal(str(c["price"]))
            diff = format_change(p_sim, p_ref)
            override = " [Override]" if c.get("admin_override") else ""

            e.add_field(
                name=f"{c['name']} ({c['symbol']}){override}",
                value=f"Trading: `{money(p_sim)}`\nReference: `{money(p_ref)}`\nVariance: `{diff}`",
                inline=True
            )
        e.set_footer(text=f"Page {idx + 1} of {total_pages} · Clovers market system")
        pages.append(e)

    view = MarketBrowseView(pages, chunks, ctx.author.id)
    await ctx.send(embed=pages[0], view=view)


@bot.hybrid_command(name="viewchart", description="Render high-definition price charts")
@app_commands.describe(coinname="Coin name or ticker symbol", period="Chart timeframe (1h, 24h, 7d, 30d)")
@app_commands.autocomplete(coinname=coin_autocomplete)
async def viewchart(
    ctx: commands.Context,
    coinname: str,
    period: Literal["1h", "24h", "7d", "30d"] = "24h"
):
    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    await ctx.defer()
    chart_buf, note = await asyncio.to_thread(create_chart, coin, period)
    if chart_buf is None:
        await ctx.send(
            embed=clover_embed("Chart Notice", note or "Insufficient historical price points.", dark=True),
            ephemeral=True
        )
        return

    file = discord.File(chart_buf, filename="chart.png")

    # Main embed with the chart image
    e = clover_embed(
        f"{coin['name']} ({coin['symbol']}) · {period.upper()}",
        f"**Trading Price:** `{money(coin['price'])}`\n"
        f"**Live Reference:** `{money(coin['reference_price'])}`\n"
        f"**Change:** {note}",
        hide_banner=True
    )
    e.set_image(url="attachment://chart.png")

    # Footer divider banner embed
    banner_e = discord.Embed(color=COLOR_EMERALD)
    banner_e.set_image(url=BANNER_IMAGE_URL)

    await ctx.send(embeds=[e, banner_e], file=file)


@bot.hybrid_command(name="balance", description="Check your virtual USD balance")
async def balance_cmd(ctx: commands.Context):
    db.ensure_user(ctx.author.id)
    user = row_to_dict(db.get_user(ctx.author.id))
    bal = Decimal(str(user["balance"]))
    e = clover_embed("Account Balance", f"Available Balance: **{money(bal)}**")
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="daily", description="Claim daily virtual USD stipend")
async def daily_cmd(ctx: commands.Context):
    user_id = ctx.author.id
    db.ensure_user(user_id)

    with connect() as c:
        row = row_to_dict(c.execute("SELECT balance, last_daily, daily_streak FROM users WHERE user_id=?", (user_id,)).fetchone())
        last_daily_raw = row.get("last_daily")
        streak = row.get("daily_streak") or 0
        current_bal = Decimal(str(row["balance"]))

    now = datetime.now(timezone.utc)
    if last_daily_raw:
        delta = now - datetime.fromisoformat(last_daily_raw)
        if delta.total_seconds() < 86400:
            remaining = 86400 - delta.total_seconds()
            hrs = int(remaining // 3600)
            mins = int((remaining % 3600) // 60)
            await ctx.send(f"Daily reward available in **{hrs}h {mins}m**.", ephemeral=True)
            return
        streak = streak + 1 if delta.total_seconds() < 172800 else 1
    else:
        streak = 1

    reward = Decimal("1500.00") + Decimal(str(min(streak * 200, 4000)))
    new_bal = current_bal + reward

    with DB_LOCK, connect() as c:
        c.execute("UPDATE users SET balance=?, last_daily=?, daily_streak=? WHERE user_id=?", (str(new_bal), now_iso(), streak, user_id))

    e = clover_embed(
        "Daily Reward Claimed",
        f"Received **{money(reward)}** (Streak: {streak} days).\nNew Balance: **{money(new_bal)}**"
    )
    await ctx.send(embed=e)


@bot.hybrid_command(name="pay", description="Transfer virtual USD to another member")
@app_commands.describe(recipient="User to send funds to", amount="Amount of virtual USD")
async def pay(
    ctx: commands.Context,
    recipient: discord.User,
    amount: float
):
    if recipient.id == ctx.author.id or recipient.bot:
        await ctx.send("Invalid recipient.", ephemeral=True)
        return

    val = parse_decimal(amount, "Amount").quantize(Decimal("0.01"))
    db.ensure_user(ctx.author.id)
    db.ensure_user(recipient.id)

    with DB_LOCK, connect() as c:
        c.execute("BEGIN IMMEDIATE")
        sender = row_to_dict(c.execute("SELECT balance FROM users WHERE user_id=?", (ctx.author.id,)).fetchone())
        sender_bal = Decimal(str(sender["balance"]))
        if val > sender_bal:
            await ctx.send(f"Insufficient funds. Balance: `{money(sender_bal)}`.", ephemeral=True)
            return

        rec = row_to_dict(c.execute("SELECT balance FROM users WHERE user_id=?", (recipient.id,)).fetchone())
        rec_bal = Decimal(str(rec["balance"]))

        c.execute("UPDATE users SET balance=? WHERE user_id=?", (str(sender_bal - val), ctx.author.id))
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (str(rec_bal + val), recipient.id))

    e = clover_embed(
        "Transfer Complete",
        f"Sent **{money(val)}** to {recipient.mention}.\nRemaining Balance: `{money(sender_bal - val)}`"
    )
    await ctx.send(embed=e)


@bot.hybrid_command(name="leaderboard", description="View the highest net worth portfolios")
async def leaderboard(ctx: commands.Context):
    with connect() as c:
        users = [row_to_dict(r) for r in c.execute("SELECT user_id, balance FROM users").fetchall()]
        coin_prices = {r["coin_id"]: Decimal(str(r["price"])) for r in c.execute("SELECT coin_id, price FROM coins")}
        holdings = [row_to_dict(r) for r in c.execute("SELECT user_id, coin_id, quantity FROM holdings").fetchall()]

    totals = {r["user_id"]: Decimal(str(r["balance"])) for r in users}
    for h in holdings:
        val = Decimal(str(h["quantity"])) * coin_prices.get(h["coin_id"], DECIMAL_ZERO)
        totals[h["user_id"]] = totals.get(h["user_id"], DECIMAL_ZERO) + val

    ranking = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:10]

    lines = []
    for idx, (uid, net_val) in enumerate(ranking, 1):
        member = bot.get_user(uid)
        name = discord.utils.escape_markdown(member.display_name if member else f"User {uid}")
        lines.append(f"**{idx}.** {name} — `{money(net_val)}`")

    e = clover_embed("Portfolio Leaderboard", "\n".join(lines) if lines else "No registered portfolios.")
    await ctx.send(embed=e)


@bot.hybrid_command(name="transactions", description="View your recent trade history")
async def transactions_cmd(ctx: commands.Context):
    with connect() as c:
        rows = [row_to_dict(r) for r in c.execute(
            """
            SELECT t.action, t.quantity, t.unit_price, t.total_usd, t.created_at, co.symbol
            FROM transactions t
            JOIN coins co ON co.coin_id = t.coin_id
            WHERE t.user_id = ?
            ORDER BY t.id DESC LIMIT 8
            """,
            (ctx.author.id,)
        ).fetchall()]

    if not rows:
        await ctx.send("No trades recorded yet.", ephemeral=True)
        return

    lines = []
    for r in rows:
        action = r["action"].upper()
        ts = int(datetime.fromisoformat(r["created_at"]).timestamp())
        lines.append(
            f"**{action} {r['symbol']}** · {fmt_qty(r['quantity'])} @ {money(r['unit_price'])}\n"
            f"└ Total: `{money(r['total_usd'])}` · <t:{ts}:R>"
        )

    e = clover_embed("Recent Transactions", "\n\n".join(lines))
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="help", description="Command handbook and directory")
async def help_cmd(ctx: commands.Context):
    e = clover_embed(
        "Command Reference",
        "Prefix with `~` or use Slash Commands `/`\n\n"
        "**Trading & Portfolio**\n"
        "• `~buy <coin> <amount>` — Purchase crypto with virtual USD\n"
        "• `~sell <coin> <amount>` — Liquidate holdings for virtual USD\n"
        "• `~portfolio [user]` — View holdings and asset allocation\n"
        "• `~balance` — Check virtual cash balance\n"
        "• `~transactions` — View recent trading activity\n\n"
        "**Market Data**\n"
        "• `~viewcoins` — Browse market prices and reference rates\n"
        "• `~viewchart <coin> [1h|24h|7d|30d]` — View price charts\n"
        "• `~leaderboard` — View top simulated traders\n\n"
        "**Economy**\n"
        "• `~daily` — Claim daily virtual USD bonus\n"
        "• `~pay <user> <amount>` — Transfer virtual cash to another member"
    )
    await ctx.send(embed=e, ephemeral=True)


# ==============================================================================
# ADMINISTRATIVE COMMANDS (HYBRID)
# ==============================================================================
@bot.hybrid_command(name="adminhelp", description="Show admin commands")
async def adminhelp(ctx: commands.Context):
    if not await deny_admin(ctx):
        return
    e = clover_embed(
        "Admin Commands",
        "• `~resetprices` — Reset all coins back to original reference rates\n"
        "• `~setprice <coin> <price>` — Manually override simulated price\n"
        "• `~revertprice <coin>` — Remove override and snap to live market\n"
        "• `~rugpull <coin>` — Set simulated price to $0.00\n"
        "• `~skyrocket <coin>` — Add +$100,000 to simulated price\n"
        "• `~addmoney <user> <amount>` — Credit virtual USD\n"
        "• `~setbalance <user> <amount>` — Set user's exact balance\n"
        "• `~addcoin <name> <ticker> <price>` — Add a custom token\n"
        "• `~removecoin <coin>` — Disable a token from trading\n"
        "• `~adminstats` — Economic and server statistics"
    )
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="resetprices", description="ADMIN: Reset every coin to its original market reference price")
async def resetprices_cmd(ctx: commands.Context):
    if not await deny_admin(ctx):
        return
    reset_all_coins_to_reference()
    await audit(ctx, "RESETPRICES", "Reset all coins to original market reference prices.")
    e = clover_embed(
        "Prices Reset",
        "All coins have been reset to their original live market reference prices. All overrides removed."
    )
    await ctx.send(embed=e)


@bot.hybrid_command(name="rugpull", description="ADMIN: Reset simulated coin price to zero")
@app_commands.autocomplete(coinname=coin_autocomplete)
async def rugpull(ctx: commands.Context, coinname: str):
    if not await deny_admin(ctx):
        return
    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    view = ConfirmRugpullView(coin, ctx.author.id)
    e = clover_embed(
        "Confirm Price Action",
        f"Reset **{coin['name']} ({coin['symbol']})** price to **$0.00**?\n"
        f"Current Price: `{money(coin['price'])}`",
        dark=True
    )
    await ctx.send(embed=e, view=view, ephemeral=True)


@bot.hybrid_command(name="skyrocket", description="ADMIN: Add $100,000 to a coin's simulated price")
@app_commands.autocomplete(coinname=coin_autocomplete)
async def skyrocket(ctx: commands.Context, coinname: str):
    if not await deny_admin(ctx):
        return
    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    old_p = Decimal(str(coin["price"]))
    new_p = old_p + Decimal("100000")

    with DB_LOCK, connect() as c:
        c.execute("UPDATE coins SET admin_override=1 WHERE coin_id=?", (coin["coin_id"],))
    db.record_price(coin["coin_id"], new_p)
    reload_coin_cache()

    await audit(ctx, "SKYROCKET", f"{coin['symbol']}: {money(old_p)} -> {money(new_p)}")
    e = clover_embed(
        "Simulated Surge Applied",
        f"**{coin['name']} ({coin['symbol']})** price increased from `{money(old_p)}` to **{money(new_p)}**."
    )
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="setprice", description="ADMIN: Set a simulated price manually")
@app_commands.autocomplete(coinname=coin_autocomplete)
async def setprice(
    ctx: commands.Context,
    coinname: str,
    price: float
):
    if not await deny_admin(ctx):
        return
    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    old_p = Decimal(str(coin["price"]))
    new_p = parse_decimal(price, "Price")

    with DB_LOCK, connect() as c:
        c.execute("UPDATE coins SET admin_override=1 WHERE coin_id=?", (coin["coin_id"],))
    db.record_price(coin["coin_id"], new_p)
    reload_coin_cache()

    await audit(ctx, "SETPRICE", f"{coin['symbol']}: {money(old_p)} -> {money(new_p)}")
    e = clover_embed("Price Updated", f"**{coin['name']} ({coin['symbol']})**: `{money(old_p)}` → **{money(new_p)}**")
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="revertprice", description="ADMIN: Revert simulated price to live market rate")
@app_commands.autocomplete(coinname=coin_autocomplete)
async def revertprice(ctx: commands.Context, coinname: str):
    if not await deny_admin(ctx):
        return
    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    ref_price = Decimal(str(coin["reference_price"]))
    with DB_LOCK, connect() as c:
        c.execute("UPDATE coins SET admin_override=0 WHERE coin_id=?", (coin["coin_id"],))
    db.record_price(coin["coin_id"], ref_price)
    reload_coin_cache()

    await audit(ctx, "REVERT_PRICE", f"{coin['symbol']} reverted to {money(ref_price)}")
    e = clover_embed("Override Removed", f"**{coin['name']} ({coin['symbol']})** reverted to live reference rate: **{money(ref_price)}**.")
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="addmoney", description="ADMIN: Credit virtual USD to an account")
async def addmoney(
    ctx: commands.Context,
    recipient: discord.User,
    amount: float
):
    if not await deny_admin(ctx):
        return
    val = parse_decimal(amount, "Amount")
    db.ensure_user(recipient.id)

    with DB_LOCK, connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row = row_to_dict(c.execute("SELECT balance FROM users WHERE user_id=?", (recipient.id,)).fetchone())
        new_bal = (Decimal(str(row["balance"])) + val).quantize(Decimal("0.01"))
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (str(new_bal), recipient.id))

    await audit(ctx, "ADDMONEY", f"Credited {money(val)} to {recipient.id}")
    e = clover_embed("Funds Credited", f"Added **{money(val)}** to {recipient.mention}.\nNew Balance: `{money(new_bal)}`")
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="setbalance", description="ADMIN: Set an exact virtual USD balance")
async def setbalance(
    ctx: commands.Context,
    recipient: discord.User,
    amount: float
):
    if not await deny_admin(ctx):
        return
    val = parse_decimal(amount, "Amount").quantize(Decimal("0.01"))
    db.ensure_user(recipient.id)

    with DB_LOCK, connect() as c:
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (str(val), recipient.id))

    await audit(ctx, "SETBALANCE", f"Set {recipient.id} balance to {money(val)}")
    e = clover_embed("Balance Updated", f"Set {recipient.mention}'s balance to **{money(val)}**.")
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="addcoin", description="ADMIN: Register a new coin in the simulator")
async def addcoin(
    ctx: commands.Context,
    name: str,
    ticker: str,
    initial_price: float
):
    if not await deny_admin(ctx):
        return
    name = name.strip()
    ticker = ticker.upper().strip()

    if not name or len(name) > 35 or len(ticker) < 2 or len(ticker) > 10 or not ticker.isalnum():
        await ctx.send("Invalid name (1-35 characters) or alphanumeric ticker (2-10 characters).", ephemeral=True)
        return

    coin_id = f"custom-{ticker.lower()}"
    p = parse_decimal(initial_price, "Initial Price")

    try:
        with DB_LOCK, connect() as c:
            c.execute(
                """
                INSERT INTO coins(coin_id, name, symbol, price, reference_price, is_active, admin_override, updated_at)
                VALUES(?, ?, ?, ?, ?, 1, 1, ?)
                """,
                (coin_id, name, ticker, str(p), str(p), now_iso())
            )
            c.execute("INSERT INTO price_history(coin_id, price, recorded_at) VALUES(?, ?, ?)", (coin_id, str(p), now_iso()))
    except Exception:
        await ctx.send(f"A coin with ticker `{ticker}` or ID already exists.", ephemeral=True)
        return

    reload_coin_cache()
    await audit(ctx, "ADDCOIN", f"Added {name} ({ticker}) at {money(p)}")
    e = clover_embed("Coin Registered", f"Added **{name} ({ticker})** at `{money(p)}`.")
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="removecoin", description="ADMIN: Disable a coin from trading")
@app_commands.autocomplete(coinname=coin_autocomplete)
async def removecoin(ctx: commands.Context, coinname: str):
    if not await deny_admin(ctx):
        return
    coin = await resolve_coin(ctx, coinname)
    if not coin:
        return

    with DB_LOCK, connect() as c:
        c.execute("UPDATE coins SET is_active=0 WHERE coin_id=?", (coin["coin_id"],))

    reload_coin_cache()
    await audit(ctx, "REMOVECOIN", f"Disabled {coin['symbol']}")
    e = clover_embed("Coin Disabled", f"**{coin['name']} ({coin['symbol']})** has been disabled. Historical records remain.")
    await ctx.send(embed=e, ephemeral=True)


@bot.hybrid_command(name="adminstats", description="ADMIN: View simulation database metrics")
async def adminstats(ctx: commands.Context):
    if not await deny_admin(ctx):
        return
    with connect() as c:
        total_users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        total_coins = c.execute("SELECT COUNT(*) n FROM coins WHERE is_active=1").fetchone()["n"]
        total_trades = c.execute("SELECT COUNT(*) n FROM transactions").fetchone()["n"]
        total_cash = c.execute("SELECT SUM(CAST(balance AS REAL)) s FROM users").fetchone()["s"] or 0.0

    e = clover_embed(
        "Simulation Metrics",
        f"• **Registered Accounts:** `{total_users:,}`\n"
        f"• **Active Coins:** `{total_coins:,}`\n"
        f"• **Trades Recorded:** `{total_trades:,}`\n"
        f"• **Circulating Cash:** `{money(total_cash)}`\n"
        f"• **Bot Latency:** `{int(bot.latency * 1000)}ms`"
    )
    await ctx.send(embed=e, ephemeral=True)


# ==============================================================================
# UNIFIED ERROR HANDLER
# ==============================================================================
@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    orig = getattr(error, "original", error)
    if isinstance(error, commands.CommandNotFound):
        return

    print(f"\n[error] Uncaught exception in `{ctx.command.name if ctx.command else 'unknown'}`:")
    traceback.print_exception(type(orig), orig, orig.__traceback__)

    desc = "An error occurred while executing this command."
    if isinstance(orig, ValueError):
        desc = str(orig)
    elif isinstance(orig, commands.MissingRequiredArgument):
        desc = f"Missing argument: `{orig.param.name}`. Example: `~{ctx.command.name} <{orig.param.name}>`"
    elif isinstance(orig, commands.CommandOnCooldown):
        desc = f"Cooldown active. Please wait **{orig.retry_after:.1f}s**."

    e = clover_embed("Notice", desc, dark=True)
    try:
        await ctx.send(embed=e, ephemeral=True)
    except discord.HTTPException:
        pass


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN in environment or .env file.")
    db.init_db()
    patch_db_schema()
    reload_coin_cache()
    bot.run(DISCORD_TOKEN)