"""CoinGecko market-reference refresh. Trading uses the bot's separate simulated price."""
import aiohttp
from decimal import Decimal, InvalidOperation
from config import COINGECKO_API
from database import list_coins, DB_LOCK, connect, now_iso

async def refresh_market():
    coins = list_coins(active_only=False)
    ids = [r['coin_id'] for r in coins]
    if not ids:
        return 0
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent":"clovercoin/1.0"}) as session:
        async with session.get(f"{COINGECKO_API}/simple/price", params={"ids":",".join(ids),"vs_currencies":"usd"}) as response:
            if response.status == 429:
                raise RuntimeError("CoinGecko rate limit reached; will retry later.")
            response.raise_for_status()
            data = await response.json()
    updated = 0
    with DB_LOCK, connect() as c:
        for row in coins:
            value = data.get(row['coin_id'], {}).get('usd')
            if value is None:
                continue
            try:
                ref = Decimal(str(value))
            except InvalidOperation:
                continue
            c.execute("UPDATE coins SET reference_price=?, updated_at=? WHERE coin_id=?", (str(ref),now_iso(),row['coin_id']))
            # Only follow live reference prices when an admin hasn't intentionally changed the simulator price.
            if not row['admin_override'] and row['is_active']:
                c.execute("UPDATE coins SET price=? WHERE coin_id=?", (str(ref),row['coin_id']))
                c.execute("INSERT INTO price_history(coin_id,price,recorded_at) VALUES(?,?,?)",(row['coin_id'],str(ref),now_iso()))
            updated += 1
    return updated
