"""
Strategy & Signal Generation Engine
Detects EMA Crossovers (Golden Cross / Death Cross) and tags buy/sell signals.
"""

import pandas as pd
from src.indicators import add_emas


def generate_crossover_signals(df: pd.DataFrame, fast_period: int = 20, slow_period: int = 50) -> pd.DataFrame:
    """
    Generate BUY and SELL signals based on Fast/Slow EMA crossovers.
    
    - BUY (Golden Cross): Fast EMA crosses above Slow EMA.
    - SELL (Death Cross): Fast EMA crosses below Slow EMA.
    
    :param df: DataFrame with OHLCV data
    :param fast_period: Period for fast EMA (default: 20)
    :param slow_period: Period for slow EMA (default: 50)
    :return: DataFrame with added 'EMA_<fast>', 'EMA_<slow>', 'signal', and 'signal_price' columns
    """
    df = df.copy()
    
    # Ensure EMAs are computed
    df = add_emas(df, periods=[fast_period, slow_period])
    
    fast_col = f"EMA_{fast_period}"
    slow_col = f"EMA_{slow_period}"
    
    fast = df[fast_col]
    slow = df[slow_col]
    
    fast_prev = fast.shift(1)
    slow_prev = slow.shift(1)
    
    # Detect exact crossover points
    buy_condition = (fast > slow) & (fast_prev <= slow_prev)
    sell_condition = (fast < slow) & (fast_prev >= slow_prev)
    
    df["signal"] = None
    df.loc[buy_condition, "signal"] = "BUY"
    df.loc[sell_condition, "signal"] = "SELL"
    
    # Record signal execution price
    df["signal_price"] = None
    df.loc[buy_condition | sell_condition, "signal_price"] = df.loc[buy_condition | sell_condition, "close"]
    
    return df
