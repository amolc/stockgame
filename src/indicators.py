"""
Technical Indicators Calculation Module
Computes Exponential Moving Averages (EMAs) and additional momentum indicators using Pandas.
"""

from typing import List
import pandas as pd


def add_emas(df: pd.DataFrame, periods: List[int] = [20, 50, 100, 200]) -> pd.DataFrame:
    """
    Calculate and append Exponential Moving Averages (EMAs) to the DataFrame.
    
    :param df: DataFrame containing a 'close' column
    :param periods: List of integer periods for EMAs (e.g., [20, 50, 100, 200])
    :return: DataFrame with new 'EMA_<period>' columns
    """
    df = df.copy()
    for period in periods:
        col_name = f"EMA_{period}"
        df[col_name] = df["close"].ewm(span=period, adjust=False).mean()
    return df


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate the Relative Strength Index (RSI).
    
    :param df: DataFrame containing a 'close' column
    :param period: Lookback period (default 14)
    :return: Pandas Series with RSI values between 0 and 100
    """
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi
