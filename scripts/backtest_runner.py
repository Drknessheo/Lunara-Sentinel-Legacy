import json
import os
import sys

import pandas as pd

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
from strategy_engine import evaluate


def run_backtest(csv_path, settings_path=None, symbol="TEST"):
    df = pd.read_csv(csv_path)
    # Expect a 'close' column
    if "close" not in df.columns:
        print("CSV must contain a close column")
        return

    if settings_path and os.path.exists(settings_path):
        with open(settings_path, "r") as fh:
            settings = json.load(fh)
    else:
        settings = {}

    actions = []
    for i in range(len(df)):
        window = df.iloc[max(0, i - 50) : i + 1]
        slip = {"symbol": symbol, "indicators": {}}
        decision = evaluate(slip, settings, market_df=window)
        actions.append(
            {"index": i, "decision": decision, "price": float(df.iloc[i]["close"])}
        )

    buys = [a for a in actions if a["decision"] == "buy"]
    sells = [a for a in actions if a["decision"] == "sell"]
    print(f"Backtest summary: {len(buys)} buys, {len(sells)} sells over {len(df)} bars")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: backtest_runner.py historical.csv [settings.json]")
        sys.exit(1)
    csv = sys.argv[1]
    settings = sys.argv[2] if len(sys.argv) > 2 else None
    run_backtest(csv, settings)
