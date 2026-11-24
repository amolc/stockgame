import os
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

# Point to Binance's dedicated global public data cluster (bypasses ISP timeouts)
Client.API_URL = os.getenv("BINANCE_API_URL", "https://data-api.binance.vision/api")


class BinanceService:
    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False):
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        self.testnet = testnet or (os.getenv("BINANCE_TESTNET", "false").lower() in ("true", "1", "yes"))
        
        self.client = Client(
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )

    def fetch_klines(self, symbol: str = "BTCUSDT", interval: str = "15m", lookback: str = "3 days ago UTC") -> pd.DataFrame:
        raw_klines = self.client.get_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=lookback
        )
        if not raw_klines:
            raise ValueError(f"No kline data returned from Binance for {symbol} ({interval})")

        columns = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ]
        df = pd.DataFrame(raw_klines, columns=columns)
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        df.drop(columns=["open_time"], inplace=True)
        
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
            
        return df

    def get_ticker_price(self, symbol: str = "BTCUSDT") -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
