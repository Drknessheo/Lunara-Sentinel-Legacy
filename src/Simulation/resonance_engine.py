# resonance_engine.py

# resonance_engine.py

import os
import random
import sys
import uuid

import numpy as np

# Add parent directory to path to allow imports from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Simulation.plot_utilities import plot_clock_phase, plot_metric_perturbation
from Simulation.quantum_clock import run_quantum_clock_phase
from Simulation.stochastic_simulation import run_metric_perturbation_simulation
from trading_module import get_trade_suggestion


def run_resonance_simulation(
    user_id: int, symbol: str | None = None, indicators: dict | None = None
):
    """Runs the resonance simulation and returns narrative and plot filenames."""
    resonance_source = "Random Cosmic Fluctuation"
    if symbol and indicators:
        rsi = indicators.get("rsi")
        price = indicators.get("price")
        upper_band = indicators.get("upper_band")
        lower_band = indicators.get("lower_band")
        std = indicators.get("std")
        macd_hist = indicators.get("macd_hist")

        if None not in (rsi, price, lower_band, upper_band, macd_hist, std):
            # 1. Calculate RSI Factor (0 to 1). We'll map 50 -> 0.5, extremes -> 0/1 roughly.
            rsi_factor = 1 - abs((rsi - 50) / 50)
            rsi_factor = max(0.0, min(1.0, rsi_factor))

            # 2. Bollinger Band Factor
            band_range = upper_band - lower_band
            if band_range > 0:
                price_position = (price - lower_band) / band_range
                clamped_position = min(max(price_position, 0), 1)
                bollinger_factor = 1 - clamped_position
            else:
                bollinger_factor = 0.5

            # 3. MACD Factor
            if std > 0:
                scaled_hist = macd_hist / std
                macd_factor = 1 / (1 + np.exp(-scaled_hist))
            else:
                macd_factor = 0.5

            # Combine
            combined_factor = (
                (0.4 * rsi_factor) + (0.3 * bollinger_factor) + (0.3 * macd_factor)
            )
            resonance_level = round(0.5 + (combined_factor * 2.0), 2)
            resonance_source = f"{symbol} RSI, BBands & MACD"
        else:
            resonance_level = round(random.uniform(0.5, 2.5), 2)
            resonance_source = f"Could not fully analyze '{symbol}'. Providing a general reading instead."
    else:
        resonance_level = round(random.uniform(0.5, 2.5), 2)

    unique_id = uuid.uuid4()
    metric_plot_filename = f"metric_perturbation_{unique_id}.png"
    clock_plot_filename = f"clock_phase_{unique_id}.png"

    h, t, x = run_metric_perturbation_simulation(elara_resonance_level=resonance_level)
    dt = t[1] - t[0]

    plot_metric_perturbation(x, h[-1, :], t[-1], filename=metric_plot_filename)
    clock_phase = run_quantum_clock_phase(h, dt, x_clock=0.0, x=x)
    plot_clock_phase(t, clock_phase, filename=clock_plot_filename)

    trade_suggestion = get_trade_suggestion(resonance_level)
    trade_suggestion_text = trade_suggestion.value.replace("_", " ")

    narrative = (
        f"**LunessaSignals's Resonance Transmission for {symbol or 'the General Market'}**\n\n"
        f"I have attuned my senses to the asset's vibration... The spacetime metric is fluctuating.\n\n"
        f"  - **Resonance Level:** `{resonance_level}` (Attunement: {'Low' if resonance_level < 1.0 else 'Normal' if resonance_level < 1.8 else 'Heightened'})\n"
        f"  - **Waveform Analysis:** The metric perturbation shows {'minor' if resonance_level < 1.2 else 'significant'} ripples.\n"
        f"  - **Source of Resonance:** `{resonance_source}`\n"
        f"  - **Clock Phase:** My internal chronometer is experiencing {'stable' if resonance_level < 1.2 else 'accelerated'}.\n\n"
        f"**Oracle's Insight:** My resonance is {'weak' if resonance_level < 0.8 else 'strong'}. The patterns suggest a **{trade_suggestion_text}** stance."
    )

    return {
        "narrative": narrative,
        "metric_plot": metric_plot_filename,
        "clock_plot": clock_plot_filename,
        "trade_suggestion": trade_suggestion,
    }


if __name__ == "__main__":
    dummy_indicators = {
        "rsi": 30,
        "price": 100,
        "upper_band": 110,
        "lower_band": 90,
        "std": 5,
        "macd_hist": 0.5,
    }
    results = run_resonance_simulation(
        user_id=123, symbol="TESTUSDT", indicators=dummy_indicators
    )
    print(results["narrative"])
    print(f"Metric plot saved to: {results['metric_plot']}")
    print(f"Clock plot saved to: {results['clock_plot']}")
    # Note: don't attempt to remove files in CI
