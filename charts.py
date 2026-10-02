"""Create Discord-ready chart images in memory."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from database import price_history

PERIODS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}

def create_chart(coin, period: str):
    period = period if period in PERIODS else "24h"
    since = (datetime.now(timezone.utc) - PERIODS[period]).isoformat(timespec="seconds")
    rows = price_history(coin['coin_id'], since)
    if not rows:
        rows = price_history(coin['coin_id'], "1970-01-01T00:00:00+00:00")
    if not rows:
        return None, "There is not enough price history to draw a chart yet."
    dates = [datetime.fromisoformat(r['recorded_at']) for r in rows]
    prices = [float(r['price']) for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=140)
    fig.patch.set_facecolor("#0b1410")
    ax.set_facecolor("#0b1410")
    ax.plot(dates, prices, color="#39d98a", linewidth=2.2)
    ax.fill_between(dates, prices, min(0, min(prices)), color="#238636", alpha=0.18)
    ax.set_title(f"{coin['name']} ({coin['symbol']}) · {period}", color="#e6f4ea", pad=14)
    ax.set_ylabel("Price (USD)", color="#c6d6ca")
    ax.tick_params(colors="#c6d6ca", labelsize=8)
    for spine in ax.spines.values(): spine.set_color("#31533d")
    ax.grid(True, color="#294232", alpha=0.55, linewidth=0.6)
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    change = ((prices[-1] - prices[0]) / prices[0] * 100) if prices[0] else None
    return buf, (f"{change:+.2f}% over available {period} data" if change is not None else f"Price remained at $0 over available {period} data")