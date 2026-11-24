import pandas as pd
from typing import Dict, Any, Optional


class StrategyService:
    @staticmethod
    def calculate_emas(df: pd.DataFrame, fast_period: int = 20, slow_period: int = 50) -> pd.DataFrame:
        df = df.copy()
        df[f"EMA_{fast_period}"] = df["close"].ewm(span=fast_period, adjust=False).mean()
        df[f"EMA_{slow_period}"] = df["close"].ewm(span=slow_period, adjust=False).mean()
        return df

    @classmethod
    def evaluate_latest_signal(cls, df: pd.DataFrame, fast_period: int = 20, slow_period: int = 50) -> Dict[str, Any]:
        """
        Evaluates whether a crossover signal occurred on the most recently completed candle.
        We look at row -2 (the latest finished candle) and row -3 (the previous candle)
        to prevent repainting / false signals from the currently forming candle.
        """
        df_calc = cls.calculate_emas(df, fast_period=fast_period, slow_period=slow_period)
        
        if len(df_calc) < max(fast_period, slow_period) + 2:
            return {"signal": "NONE", "reason": "Insufficient data"}

        fast_col = f"EMA_{fast_period}"
        slow_col = f"EMA_{slow_period}"

        # row -2 is the latest fully closed candle
        latest_closed = df_calc.iloc[-2]
        prev_closed = df_calc.iloc[-3]
        
        fast_now = latest_closed[fast_col]
        slow_now = latest_closed[slow_col]
        fast_prev = prev_closed[fast_col]
        slow_prev = prev_closed[slow_col]

        signal = "NONE"
        # Golden Cross: Fast crosses above Slow
        if fast_now > slow_now and fast_prev <= slow_prev:
            signal = "BUY"
        # Death Cross: Fast crosses below Slow
        elif fast_now < slow_now and fast_prev >= slow_prev:
            signal = "SELL"

        return {
            "signal": signal,
            "timestamp": latest_closed.name,
            "price": float(latest_closed["close"]),
            "fast_ema": float(fast_now),
            "slow_ema": float(slow_now),
            "fast_prev": float(fast_prev),
            "slow_prev": float(slow_prev),
            "active_price": float(df_calc.iloc[-1]["close"])
        }
