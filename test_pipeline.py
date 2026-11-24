"""
Test Pipeline Script
Verifies direct connection to Binance, EMA calculation, signal detection, and simulation.
"""

from src.binance_client import BinanceDataClient
from src.strategy import generate_crossover_signals
from src.simulator import PaperTradingSimulator

def main():
    print("==================================================")
    print("1. Connecting to Binance REST API...")
    client = BinanceDataClient()
    
    current_price = client.get_current_price("BTCUSDT")
    print(f"--> Live Real-Time BTCUSDT Price: ${current_price:,.2f}")
    
    print("\n2. Fetching historical 15m candles (Last 3 days)...")
    df = client.get_klines("BTCUSDT", interval="15m", lookback="3 days ago UTC")
    print(f"--> Successfully received {len(df)} candles directly from Binance!")
    print(df.tail(3))
    
    print("\n3. Calculating EMA 20 & EMA 50 Crossovers...")
    df_signals = generate_crossover_signals(df, fast_period=20, slow_period=50)
    
    signals_count = df_signals["signal"].value_counts().to_dict()
    print(f"--> Generated signals: {signals_count}")
    
    print("\n4. Running Paper Trading Execution Simulation...")
    simulator = PaperTradingSimulator(initial_capital=10000.0)
    metrics = simulator.run(df_signals)
    
    print("--------------------------------------------------")
    print("SIMULATION PERFORMANCE RESULTS:")
    print(f"Starting Balance:   ${metrics['initial_capital']:,.2f}")
    print(f"Final Equity:       ${metrics['final_equity']:,.2f}")
    print(f"Net Profit / Loss:  ${metrics['total_pnl']:+,.2f} ({metrics['total_return_pct']:+.2f}%)")
    print(f"Total Completed:    {metrics['total_trades']} trades")
    print(f"Win Rate:           {metrics['win_rate']:.1f}% ({metrics['winning_trades']} wins / {metrics['losing_trades']} losses)")
    print(f"Max Drawdown:       {metrics['max_drawdown']:.2f}%")
    print(f"Current Position:   {'IN TRADE (LONG)' if metrics['is_in_position'] else 'CASH (WAITING)'}")
    print("--------------------------------------------------")
    
    if metrics["trades"]:
        print("\nLast Executed Trade:")
        last_trade = metrics["trades"][-1]
        print(f"  Entry: {last_trade['entry_time']} @ ${last_trade['entry_price']:,.2f}")
        print(f"  Exit:  {last_trade['exit_time']} @ ${last_trade['exit_price']:,.2f}")
        print(f"  PnL:   ${last_trade['pnl']:+,.2f} ({last_trade['pnl_pct']:+.2f}%)")

    print("\n[SUCCESS] Pipeline verified end-to-end!")

if __name__ == "__main__":
    main()
