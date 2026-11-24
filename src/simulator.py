"""
Paper Trading & Execution Simulation Engine
Simulates order execution based on strategy signals, tracking virtual balance,
open positions, trade histories, and performance metrics (PnL, Win Rate, Drawdown).
"""

from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np


class PaperTradingSimulator:
    def __init__(self, initial_capital: float = 10000.0, fee_pct: float = 0.001):
        """
        :param initial_capital: Starting virtual cash in USDT (e.g., 10,000)
        :param fee_pct: Trading fee per order (0.001 = 0.1% standard Binance spot fee)
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.fee_pct = fee_pct
        
        self.position: Optional[Dict[str, Any]] = None  # None or { 'entry_time', 'entry_price', 'qty', 'cost' }
        self.trades: List[Dict[str, Any]] = []
        self.equity_history: List[Dict[str, Any]] = []

    def run(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Execute simulation over a DataFrame containing a 'signal' column ('BUY', 'SELL', or None).
        
        :param df: DataFrame with 'close' and 'signal' columns
        :return: Performance summary dictionary
        """
        self.cash = self.initial_capital
        self.position = None
        self.trades = []
        self.equity_history = []

        for timestamp, row in df.iterrows():
            current_price = float(row["close"])
            signal = row.get("signal")
            
            # 1. Process BUY Signal: Only buy if currently in cash (no open position)
            if signal == "BUY" and self.position is None and self.cash > 10:
                fee = self.cash * self.fee_pct
                investable = self.cash - fee
                qty = investable / current_price
                
                self.position = {
                    "entry_time": timestamp,
                    "entry_price": current_price,
                    "qty": qty,
                    "cost": self.cash,
                    "fee_paid": fee
                }
                self.cash = 0.0

            # 2. Process SELL Signal: Only sell if holding a position
            elif signal == "SELL" and self.position is not None:
                gross_revenue = self.position["qty"] * current_price
                exit_fee = gross_revenue * self.fee_pct
                net_revenue = gross_revenue - exit_fee
                
                cost = self.position["cost"]
                pnl = net_revenue - cost
                pnl_pct = (pnl / cost) * 100.0
                
                self.trades.append({
                    "entry_time": self.position["entry_time"],
                    "entry_price": self.position["entry_price"],
                    "exit_time": timestamp,
                    "exit_price": current_price,
                    "quantity": self.position["qty"],
                    "cost": cost,
                    "net_revenue": net_revenue,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "is_win": pnl > 0
                })
                
                self.cash = net_revenue
                self.position = None

            # Calculate current total equity (cash + value of open BTC)
            if self.position is not None:
                current_equity = self.position["qty"] * current_price * (1.0 - self.fee_pct)
            else:
                current_equity = self.cash

            self.equity_history.append({
                "datetime": timestamp,
                "equity": current_equity,
                "price": current_price
            })

        # Calculate performance statistics
        return self._calculate_metrics(df.iloc[-1]["close"] if not df.empty else 0.0)

    def _calculate_metrics(self, latest_price: float) -> Dict[str, Any]:
        equity_df = pd.DataFrame(self.equity_history)
        final_equity = equity_df["equity"].iloc[-1] if not equity_df.empty else self.initial_capital
        
        total_pnl = final_equity - self.initial_capital
        total_return_pct = (total_pnl / self.initial_capital) * 100.0
        
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t["is_win"])
        losing_trades = total_trades - winning_trades
        win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0
        
        # Profit Factor
        gross_profit = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self.trades if t["pnl"] < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
        
        # Max Drawdown
        if not equity_df.empty:
            peak = equity_df["equity"].cummax()
            drawdown = (equity_df["equity"] - peak) / peak * 100.0
            max_drawdown = abs(drawdown.min())
        else:
            max_drawdown = 0.0

        return {
            "initial_capital": self.initial_capital,
            "final_equity": final_equity,
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "is_in_position": self.position is not None,
            "current_position": self.position,
            "trades": self.trades,
            "equity_df": equity_df
        }
