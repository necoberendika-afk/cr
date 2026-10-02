"""SQLite persistence and transactional operations for clovercoin."""
import sqlite3
import threading
from decimal import Decimal
from datetime import datetime, timezone
from config import DATABASE_PATH, STARTING_BALANCE

DB_LOCK = threading.RLock()

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def connect():
    conn = sqlite3.connect(DATABASE_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with DB_LOCK, connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, balance TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coins(
            coin_id TEXT PRIMARY KEY, name TEXT NOT NULL, symbol TEXT NOT NULL UNIQUE,
            price TEXT NOT NULL, reference_price TEXT NOT NULL DEFAULT '0',
            is_active INTEGER NOT NULL DEFAULT 1, admin_override INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS holdings(
            user_id INTEGER NOT NULL, coin_id TEXT NOT NULL, quantity TEXT NOT NULL,
            PRIMARY KEY(user_id, coin_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(coin_id) REFERENCES coins(coin_id)
        );
        CREATE TABLE IF NOT EXISTS price_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT, coin_id TEXT NOT NULL,
            price TEXT NOT NULL, recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_coin_time ON price_history(coin_id, recorded_at);
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            coin_id TEXT NOT NULL, action TEXT NOT NULL, quantity TEXT NOT NULL,
            unit_price TEXT NOT NULL, total_usd TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
            action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
        );
        ''')
        seeds = [
            ("bitcoin", "Bitcoin", "BTC", "65000"),
            ("ethereum", "Ethereum", "ETH", "3200"),
            ("solana", "Solana", "SOL", "150"),
            ("dogecoin", "Dogecoin", "DOGE", "0.15"),
            ("cardano", "Cardano", "ADA", "0.45"),
            ("ripple", "XRP", "XRP", "0.55"),
            ("the-open-network", "Toncoin", "TON", "6.5"),
            ("avalanche-2", "Avalanche", "AVAX", "35"),
        ]
        for coin_id, name, symbol, price in seeds:
            c.execute("INSERT OR IGNORE INTO coins(coin_id,name,symbol,price,reference_price,updated_at) VALUES(?,?,?,?,?,?)",
                      (coin_id, name, symbol, price, price, now_iso()))
            c.execute("INSERT INTO price_history(coin_id,price,recorded_at) SELECT ?,?,? WHERE NOT EXISTS (SELECT 1 FROM price_history WHERE coin_id=?)",
                      (coin_id, price, now_iso(), coin_id))

def ensure_user(user_id: int):
    with DB_LOCK, connect() as c:
        c.execute("INSERT OR IGNORE INTO users(user_id,balance,created_at) VALUES(?,?,?)",
                  (user_id, str(Decimal(str(STARTING_BALANCE)).quantize(Decimal('0.01'))), now_iso()))

def get_user(user_id: int):
    ensure_user(user_id)
    with connect() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def get_coin(query: str):
    q = query.strip().lower()
    with connect() as c:
        return c.execute("SELECT * FROM coins WHERE lower(coin_id)=? OR lower(name)=? OR lower(symbol)=?", (q,q,q)).fetchone()

def list_coins(active_only=True):
    with connect() as c:
        sql = "SELECT * FROM coins" + (" WHERE is_active=1" if active_only else "") + " ORDER BY name COLLATE NOCASE"
        return c.execute(sql).fetchall()

def record_price(coin_id: str, price: Decimal):
    with DB_LOCK, connect() as c:
        c.execute("UPDATE coins SET price=?,updated_at=? WHERE coin_id=?", (str(price), now_iso(), coin_id))
        c.execute("INSERT INTO price_history(coin_id,price,recorded_at) VALUES(?,?,?)", (coin_id,str(price),now_iso()))

def price_history(coin_id: str, since_iso: str):
    with connect() as c:
        return c.execute("SELECT price,recorded_at FROM price_history WHERE coin_id=? AND recorded_at>=? ORDER BY recorded_at", (coin_id,since_iso)).fetchall()

def record_admin(admin_id: int, action: str, details: str):
    with DB_LOCK, connect() as c:
        c.execute("INSERT INTO admin_audit(admin_id,action,details,created_at) VALUES(?,?,?,?)", (admin_id,action,details,now_iso()))

def trade(user_id: int, coin_id: str, action: str, quantity: Decimal, unit_price: Decimal):
    """Apply a buy/sell atomically; monetary arithmetic is Decimal-based."""
    quantity = quantity.quantize(Decimal('0.00000001'))
    total = (quantity * unit_price).quantize(Decimal('0.01'))
    if quantity <= 0 or unit_price < 0 or total <= 0:
        raise ValueError("Quantity and trade value must be greater than zero.")
    with DB_LOCK, connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR IGNORE INTO users(user_id,balance,created_at) VALUES(?,?,?)", (user_id,str(Decimal(str(STARTING_BALANCE)).quantize(Decimal('0.01'))),now_iso()))
        user = c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        holding = c.execute("SELECT quantity FROM holdings WHERE user_id=? AND coin_id=?", (user_id,coin_id)).fetchone()
        balance = Decimal(user['balance'])
        held = Decimal(holding['quantity']) if holding else Decimal('0')
        if action == 'buy':
            if balance < total:
                raise ValueError(f"Insufficient virtual USD. You have ${balance:,.2f}; this trade costs ${total:,.2f}.")
            balance -= total
            held += quantity
        elif action == 'sell':
            if held < quantity:
                raise ValueError(f"Insufficient holdings. You own {held.normalize()} units of this coin.")
            balance += total
            held -= quantity
        else:
            raise ValueError("Unknown trade action.")
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (str(balance.quantize(Decimal('0.01'))),user_id))
        c.execute("INSERT INTO holdings(user_id,coin_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,coin_id) DO UPDATE SET quantity=excluded.quantity", (user_id,coin_id,str(held)))
        c.execute("INSERT INTO transactions(user_id,coin_id,action,quantity,unit_price,total_usd,created_at) VALUES(?,?,?,?,?,?,?)", (user_id,coin_id,action,str(quantity),str(unit_price),str(total),now_iso()))
        return balance, held, total

def get_portfolio(user_id: int):
    ensure_user(user_id)
    with connect() as c:
        balance = Decimal(c.execute("SELECT balance FROM users WHERE user_id=?",(user_id,)).fetchone()['balance'])
        rows = c.execute("SELECT h.coin_id,h.quantity,c.name,c.symbol,c.price FROM holdings h JOIN coins c ON c.coin_id=h.coin_id WHERE h.user_id=? AND CAST(h.quantity AS REAL)>0",(user_id,)).fetchall()
        positions = [(r['name'],r['symbol'],Decimal(r['quantity']),Decimal(r['price'])) for r in rows]
        total = balance + sum((qty*price for _,_,qty,price in positions),Decimal('0'))
        return balance, positions, total
