import json
import logging
import os

logger = logging.getLogger(__name__)

MEMORY_FILE = "memory.json"


def load_memory() -> dict:
    """Loads the memory.json file.

    Returns:
        dict: The contents of the memory.json file, or an empty dict if it doesn't exist.
    """
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        logger.error(f"Error loading memory file: {e}")
        return {}


def save_memory(memory: dict):
    """Saves the given dictionary to the memory.json file.

    Args:
        memory (dict): The dictionary to save.
    """
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2)
    except IOError as e:
        logger.error(f"Error saving memory file: {e}")


def log_trade_outcome(symbol: str, pnl_percent: float):
    """Logs the outcome of a trade to memory.json for learning.

    Args:
        symbol (str): The symbol of the coin that was traded.
        pnl_percent (float): The profit or loss percentage of the trade.
    """
    memory = load_memory()

    if symbol not in memory:
        memory[symbol] = {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl_percent": 0.0,
            "avg_pnl_percent": 0.0,
        }

    stats = memory[symbol]
    stats["trades"] += 1
    stats["total_pnl_percent"] += pnl_percent

    if pnl_percent > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

    stats["avg_pnl_percent"] = stats["total_pnl_percent"] / stats["trades"]

    save_memory(memory)
    logger.info(f"Updated memory for {symbol}: {stats}")
