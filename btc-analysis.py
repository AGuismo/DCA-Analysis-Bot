import ccxt
import pandas as pd
import requests
import os
import sys
from datetime import datetime, timedelta, timezone

# --- Config ---
EXCHANGE_ID = os.environ.get("EXCHANGE_ID", "binance")
SYMBOL = "BTC/USDT"
TIMEFRAME = "15m"
LOCAL_TZ = os.environ.get("TIMEZONE", "Asia/Bangkok")
PERIODS = [7, 15, 30, 60]  # Analyze these lookback periods
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# --- Fetch helper ---
def fetch_ohlcv_last_n_days(exchange, symbol, timeframe, days):
    # Add a small buffer to ensure we cover the range fully
    since = int((datetime.now(timezone.utc) - timedelta(days=days + 1)).timestamp() * 1000)
    all_rows = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1500)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        since = last_ts + 1
        # stop if we're mostly caught up
        if last_ts >= int(datetime.now(timezone.utc).timestamp() * 1000) - 3600 * 1000:
            break
    return all_rows

def analyze_period(df, days, local_tz):
    # Filter for the specific lookback period based on the max timestamp in df
    # (assuming df end is "now", so we subtract days from the end time)
    end_time = df["ts"].max()
    start_time = end_time - pd.Timedelta(days=days)
    period_df = df[df["ts"] >= start_time].copy()
    
    # Analysis 1: Most common daily-low time
    idx = period_df.groupby("local_date")["low"].idxmin()
    daily_lows = period_df.loc[idx, ["local_date", "local_time", "low"]]

    most_common = (
        daily_lows["local_time"]
        .value_counts()
        .sort_index()
        .rename("days_won")
        .reset_index()
        .rename(columns={"index": "time"})
    )
    most_common["share"] = most_common["days_won"] / most_common["days_won"].sum()
    top_common = most_common.sort_values("days_won", ascending=False).head(5)

    # Analysis 2: Lowest average Low by time-of-day
    # (Existing arithmetic mean of 'low')
    avg_low_by_time = (
        period_df.groupby("local_time")["low"]
        .mean()
        .reset_index()
        .rename(columns={"local_time": "time", "low": "avg_low"})
        .sort_values("avg_low", ascending=True)
    )
    # Fix: Define top_avg here so it can be returned
    top_avg = avg_low_by_time.head(5)

    # Analysis 3: Advanced DCA Metrics
    # A. Calculate Daily Average (Mean of O,H,L,C for the day)
    period_df["candle_avg"] = period_df[["open", "high", "low", "close"]].mean(axis=1)
    
    # map daily average back to each row
    daily_means = period_df.groupby("local_date")["candle_avg"].transform("mean")
    period_df["diff_from_daily_avg"] = (period_df["close"] - daily_means) / daily_means * 100

    # B. Group by time
    dca_group = period_df.groupby("local_time")

    # Harmonic Mean function for DCA price
    def harmonic_mean(series):
        return len(series) / (1 / series).sum()

    dca_stats = dca_group.agg(
        dca_price=("close", harmonic_mean),
        avg_discount=("diff_from_daily_avg", "mean")
    ).reset_index().rename(columns={"local_time": "time"})
    
    # Sort by best DCA price (lowest)
    top_dca = dca_stats.sort_values("dca_price", ascending=True).head(5)

    return top_common, top_avg, top_dca, period_df["ts"].min(), period_df["ts"].max()

def send_to_discord(report_content):
    if not DISCORD_WEBHOOK_URL:
        print("No DISCORD_WEBHOOK_URL found. Skipping Discord notification.")
        return

    # Discord has a 2000 char limit. Simple truncation strategy or splitting.
    # For this report, we'll try to keep it concise, or send chunks.
    # A simple approach: Send chunks of 1900 chars
    
    chunks = [report_content[i:i+1900] for i in range(0, len(report_content), 1900)]
    
    for i, chunk in enumerate(chunks):
        payload = {
            "content": f"```\n{chunk}\n```" if i == 0 else f"```\n{chunk}\n```"
        }
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload)
            r.raise_for_status()
            print(f"Sent chunk {i+1}/{len(chunks)} to Discord")
        except Exception as e:
            print(f"Failed to send to Discord: {e}")

def main():
    report_lines = []
    
    def log(s):
        print(s)
        report_lines.append(s)

    log(f"Fetching max required data ({max(PERIODS)} days)...")
    exchange = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})
    
    # Fetch enough data for the largest period
    rows = fetch_ohlcv_last_n_days(exchange, SYMBOL, TIMEFRAME, max(PERIODS))

    # Process into main DataFrame
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts")

    # Pre-calculate local times
    df["local_ts"] = df["ts"].dt.tz_convert(LOCAL_TZ)
    df["local_date"] = df["local_ts"].dt.date
    df["local_time"] = df["local_ts"].dt.strftime("%H:%M")

    log(f"Timezone: {LOCAL_TZ}")

    for days in PERIODS:
        log(f"\n{'='*40}")
        log(f" ANALYSIS FOR LAST {days} DAYS")
        log(f"{'='*40}")
        
        try:
            top_common, top_avg, top_dca, start, end = analyze_period(df, days, LOCAL_TZ)
            
            log(f"Range: {start} -> {end}")
            
            log("\n(1) Most frequent DAILY-LOW time:")
            log(top_common.to_string(index=False))

            log("\n(2) Best Realistic DCA Price (Harmonic Mean):")
            # Show price and the average discount relative to daily mean
            log(top_dca.to_string(index=False))
            
        except Exception as e:
            log(f"Could not analyze {days} days: {e}")

    # After loop, send to discord
    full_report = "\n".join(report_lines)
    send_to_discord(full_report)

if __name__ == "__main__":
    main()
