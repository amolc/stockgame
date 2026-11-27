import asyncio
import logging
import os
import re
import ssl
import threading
import time
from typing import Any, Callable, Optional

import aiohttp
import pandas as pd
from binance import AsyncClient
from binance.ws.streams import BinanceSocketManager

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except ImportError:
    certifi = None

logger = logging.getLogger(__name__)


def _make_session_params() -> dict[str, Any]:
    """Use certifi CA bundle for aiohttp/WebSocket SSL verification (fixes macOS Python SSL errors)."""
    if certifi is not None:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    else:
        ssl_ctx = ssl.create_default_context()
    return {"connector": aiohttp.TCPConnector(ssl=ssl_ctx)}

_BAN_UNTIL_RE = re.compile(r"banned until (\d+)")


def _ws_enabled() -> bool:
    return os.getenv("BINANCE_USE_WEBSOCKET", "true").lower() in ("true", "1", "yes")


def _extract_error_cooldown(exc: Exception, default_ttl: float = 30.0) -> float:
    """Return how many seconds to back off after a Binance REST error."""
    msg = str(exc)
    if "-1003" in msg:
        m = _BAN_UNTIL_RE.search(msg)
        if m:
            try:
                ban_until_sec = int(m.group(1)) / 1000.0
                remaining = ban_until_sec - time.time()
                if remaining > 0:
                    return min(remaining, 3600.0)
            except ValueError:
                pass
        return 120.0
    if "-1121" in msg:
        # Invalid symbol (e.g., stock ticker on Binance): don't keep retrying
        return 300.0
    return default_ttl


class BinanceWebSocketManager:
    """
    Process-wide singleton managing Binance WebSocket streams and in-memory caches:
      - Market data: multiplex `@miniTicker` (live prices) and `@kline_<interval>` (live candles)
      - User data: `futures_user_socket` / `user_socket` (live USDT balance & positions)
      - Cooldown cache for REST fallbacks & errors to prevent IP bans (-1003).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        # Desired subscriptions per env_key = (demo, testnet, market)
        self._price_symbols: dict[tuple[bool, bool, str], set[str]] = {}
        self._kline_streams: dict[tuple[bool, bool, str], set[tuple[str, str]]] = {}
        self._market_tasks: dict[tuple[bool, bool, str], asyncio.Future] = {}
        self._market_wakeups: dict[tuple[bool, bool, str], asyncio.Event] = {}

        # User data stream tasks per acct_key = (api_key, demo, testnet, market)
        self._user_tasks: dict[tuple[str, bool, bool, str], asyncio.Future] = {}
        self._user_ws_connected: set[tuple[str, bool, bool, str]] = set()

        # In-memory caches
        # (env_key, symbol) -> (monotonic_ts, float_price)
        self._prices: dict[tuple[tuple[bool, bool, str], str], tuple[float, float]] = {}
        # (env_key, symbol, interval) -> pd.DataFrame
        self._klines: dict[tuple[tuple[bool, bool, str], str, str], pd.DataFrame] = {}
        # acct_key -> {"usdt": float|None, "positions": dict[str, float], "spot": dict[str, float], "updated_at": float}
        self._account_snapshots: dict[tuple[str, bool, bool, str], dict[str, Any]] = {}

        # Error cooldowns: any_key -> (expires_monotonic, error_message)
        self._error_cooldowns: dict[Any, tuple[float, str]] = {}

    # ------------------------------------------------------------------
    # Event loop lifecycle
    # ------------------------------------------------------------------
    def _ensure_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        if not _ws_enabled():
            return None
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            ready = threading.Event()

            def _runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()

            self._thread = threading.Thread(target=_runner, name="binance-ws-loop", daemon=True)
            self._thread.start()
            ready.wait(timeout=5.0)
            return self._loop

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------
    def subscribe_ticker(self, env_key: tuple[bool, bool, str], symbol: str) -> None:
        sym = symbol.upper()
        loop = self._ensure_loop()
        if loop is None:
            return
        with self._lock:
            syms = self._price_symbols.setdefault(env_key, set())
            if sym in syms:
                return
            syms.add(sym)
            self._ensure_market_task_locked(env_key, loop)

    def subscribe_kline(self, env_key: tuple[bool, bool, str], symbol: str, interval: str) -> None:
        sym = symbol.upper()
        loop = self._ensure_loop()
        if loop is None:
            return
        with self._lock:
            klines = self._kline_streams.setdefault(env_key, set())
            syms = self._price_symbols.setdefault(env_key, set())
            added = (sym, interval) not in klines or sym not in syms
            klines.add((sym, interval))
            syms.add(sym)
            if added:
                self._ensure_market_task_locked(env_key, loop)

    def subscribe_user_stream(
        self,
        acct_key: tuple[str, bool, bool, str],
        api_secret: str,
    ) -> None:
        api_key, demo, testnet, market = acct_key
        if not api_key or not api_secret:
            return
        loop = self._ensure_loop()
        if loop is None:
            return
        with self._lock:
            fut = self._user_tasks.get(acct_key)
            if fut is not None and not fut.done():
                return
            self._user_tasks[acct_key] = asyncio.run_coroutine_threadsafe(
                self._run_user_stream(acct_key, api_secret),
                loop,
            )

    def _ensure_market_task_locked(
        self,
        env_key: tuple[bool, bool, str],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        fut = self._market_tasks.get(env_key)
        if fut is None or fut.done():
            self._market_tasks[env_key] = asyncio.run_coroutine_threadsafe(
                self._run_market_stream(env_key),
                loop,
            )
        else:
            ev = self._market_wakeups.get(env_key)
            if ev is not None:
                loop.call_soon_threadsafe(ev.set)

    # ------------------------------------------------------------------
    # Cache accessors
    # ------------------------------------------------------------------
    def get_cached_price(self, env_key: tuple[bool, bool, str], symbol: str, max_age: float = 120.0) -> Optional[float]:
        with self._lock:
            entry = self._prices.get((env_key, symbol.upper()))
            if entry and (time.monotonic() - entry[0]) < max_age:
                return entry[1]
            return None

    def set_price(self, env_key: tuple[bool, bool, str], symbol: str, price: float) -> None:
        with self._lock:
            self._prices[(env_key, symbol.upper())] = (time.monotonic(), float(price))

    def get_cached_klines(
        self,
        env_key: tuple[bool, bool, str],
        symbol: str,
        interval: str,
    ) -> Optional[pd.DataFrame]:
        with self._lock:
            df = self._klines.get((env_key, symbol.upper(), interval))
            return df.copy() if df is not None else None

    def set_klines(
        self,
        env_key: tuple[bool, bool, str],
        symbol: str,
        interval: str,
        df: pd.DataFrame,
    ) -> None:
        with self._lock:
            self._klines[(env_key, symbol.upper(), interval)] = df.copy()
            if not df.empty:
                self._prices[(env_key, symbol.upper())] = (time.monotonic(), float(df["close"].iloc[-1]))

    def get_account_balance_for_symbol(
        self,
        acct_key: tuple[str, bool, bool, str],
        symbol: str,
        is_futures: bool,
        rest_ttl: float = 60.0,
    ) -> Optional[dict]:
        with self._lock:
            snap = self._account_snapshots.get(acct_key)
            if snap is None:
                return None
            age = time.monotonic() - snap["updated_at"]
            if acct_key not in self._user_ws_connected and age > rest_ttl:
                return None
            sym = symbol.upper()
            if is_futures:
                asset_bal = snap["positions"].get(sym)
            else:
                base_asset = sym[:-4] if sym.endswith("USDT") else sym
                asset_bal = snap["spot"].get(base_asset)
            return {"usdt": snap["usdt"], "asset": asset_bal}

    def set_account_snapshot(
        self,
        acct_key: tuple[str, bool, bool, str],
        usdt: Optional[float],
        positions: Optional[dict[str, float]] = None,
        spot: Optional[dict[str, float]] = None,
        position_details: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        with self._lock:
            self._account_snapshots[acct_key] = {
                "usdt": usdt,
                "positions": dict(positions or {}),
                "position_details": list(position_details or []),
                "spot": dict(spot or {}),
                "updated_at": time.monotonic(),
            }

    def get_account_positions(self, acct_key: tuple[str, bool, bool, str]) -> list[dict[str, Any]]:
        with self._lock:
            snap = self._account_snapshots.get(acct_key)
            if snap is None:
                return []
            return list(snap.get("position_details") or [])

    def get_error_cooldown(self, key: Any) -> Optional[str]:
        with self._lock:
            entry = self._error_cooldowns.get(key)
            if entry is None:
                return None
            expires_at, msg = entry
            if time.monotonic() < expires_at:
                return msg
            del self._error_cooldowns[key]
            return None

    def set_error_cooldown(self, key: Any, exc: Exception, default_ttl: float = 30.0) -> None:
        ttl = _extract_error_cooldown(exc, default_ttl=default_ttl)
        with self._lock:
            self._error_cooldowns[key] = (time.monotonic() + ttl, str(exc))

    def clear(self) -> None:
        """Clear in-memory caches (useful for tests)."""
        with self._lock:
            self._prices.clear()
            self._klines.clear()
            self._account_snapshots.clear()
            self._error_cooldowns.clear()

    # ------------------------------------------------------------------
    # Async WebSocket workers
    # ------------------------------------------------------------------
    def _build_stream_list(self, env_key: tuple[bool, bool, str]) -> list[str]:
        with self._lock:
            syms = sorted(self._price_symbols.get(env_key, set()))
            klines = sorted(self._kline_streams.get(env_key, set()))
        streams = [f"{s.lower()}@miniTicker" for s in syms]
        streams.extend(f"{s.lower()}@kline_{iv}" for s, iv in klines)
        return streams

    async def _run_market_stream(self, env_key: tuple[bool, bool, str]) -> None:
        demo, testnet, market = env_key
        is_futures = market == "futures"
        ev = asyncio.Event()
        with self._lock:
            self._market_wakeups[env_key] = ev

        backoff = 1.0
        while True:
            # Small debounce so a burst of initial subscriptions joins one multiplex socket
            await asyncio.sleep(0.15)
            ev.clear()
            streams = self._build_stream_list(env_key)
            if not streams:
                await ev.wait()
                continue

            client: Optional[AsyncClient] = None
            try:
                # Note: demo futures uses mainnet public market data streams (wss://fstream.binance.com/)
                use_demo = demo and not is_futures
                client = await AsyncClient.create(
                    testnet=testnet,
                    demo=use_demo,
                    session_params=_make_session_params(),
                )
                bm = BinanceSocketManager(client)
                socket = (
                    bm.futures_multiplex_socket(streams)
                    if is_futures
                    else bm.multiplex_socket(streams)
                )
                logger.info("WS market stream connecting (%s): %d streams", env_key, len(streams))
                async with socket as ts:
                    backoff = 1.0
                    while not ev.is_set():
                        recv_task = asyncio.create_task(ts.recv())
                        wake_task = asyncio.create_task(ev.wait())
                        done, pending = await asyncio.wait(
                            {recv_task, wake_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for p in pending:
                            p.cancel()
                        if wake_task in done:
                            break
                        msg = recv_task.result()
                        self._handle_market_message(env_key, msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WS market stream error (%s): %s; retrying in %.1fs", env_key, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
            finally:
                if client is not None:
                    try:
                        await client.close_connection()
                    except Exception:
                        pass

    def _handle_market_message(self, env_key: tuple[bool, bool, str], msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        data = msg.get("data", msg)
        if not isinstance(data, dict):
            return

        event_type = data.get("e")
        if event_type == "24hrMiniTicker":
            sym = data.get("s")
            close_str = data.get("c")
            if sym and close_str is not None:
                self.set_price(env_key, sym, float(close_str))
        elif event_type == "kline":
            k = data.get("k") or {}
            sym = k.get("s") or data.get("s")
            interval = k.get("i")
            if not sym or not interval:
                return
            close_price = float(k["c"])
            self.set_price(env_key, sym, close_price)

            open_time_ms = k.get("t")
            if open_time_ms is None:
                return
            ts = pd.to_datetime(int(open_time_ms), unit="ms", utc=True)
            row = [
                float(k["o"]),
                float(k["h"]),
                float(k["l"]),
                close_price,
                float(k["v"]),
            ]
            with self._lock:
                df = self._klines.get((env_key, sym.upper(), interval))
                if df is not None:
                    df.loc[ts, ["open", "high", "low", "close", "volume"]] = row
                    if not df.index.is_monotonic_increasing:
                        df.sort_index(inplace=True)
                    if len(df) > 1500:
                        self._klines[(env_key, sym.upper(), interval)] = df.iloc[-1500:]

    async def _run_user_stream(
        self,
        acct_key: tuple[str, bool, bool, str],
        api_secret: str,
    ) -> None:
        api_key, demo, testnet, market = acct_key
        is_futures = market == "futures"
        backoff = 2.0

        while True:
            client: Optional[AsyncClient] = None
            try:
                client = await AsyncClient.create(
                    api_key=api_key,
                    api_secret=api_secret,
                    testnet=testnet,
                    demo=demo,
                    session_params=_make_session_params(),
                )
                bm = BinanceSocketManager(client)
                socket = bm.futures_user_socket() if is_futures else bm.user_socket()
                logger.info("WS user stream connecting (market=%s demo=%s testnet=%s)", market, demo, testnet)
                async with socket as us:
                    with self._lock:
                        self._user_ws_connected.add(acct_key)
                    backoff = 2.0
                    while True:
                        msg = await us.recv()
                        self._handle_user_message(acct_key, is_futures, msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WS user stream error (market=%s): %s; retrying in %.1fs", market, exc, backoff)
                with self._lock:
                    self._user_ws_connected.discard(acct_key)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)
            finally:
                with self._lock:
                    self._user_ws_connected.discard(acct_key)
                if client is not None:
                    try:
                        await client.close_connection()
                    except Exception:
                        pass

    def _handle_user_message(
        self,
        acct_key: tuple[str, bool, bool, str],
        is_futures: bool,
        msg: dict,
    ) -> None:
        if not isinstance(msg, dict):
            return
        event_type = msg.get("e")
        with self._lock:
            snap = self._account_snapshots.setdefault(
                acct_key,
                {"usdt": None, "positions": {}, "spot": {}, "updated_at": time.monotonic()},
            )
            if is_futures and event_type == "ACCOUNT_UPDATE":
                acc = msg.get("a") or {}
                for b in acc.get("B", []):
                    if b.get("a") == "USDT":
                        # cw = cross wallet balance, wb = wallet balance
                        val = b.get("cw", b.get("wb"))
                        if val is not None:
                            snap["usdt"] = float(val)
                for p in acc.get("P", []):
                    sym = (p.get("s") or "").upper()
                    if not sym:
                        continue
                    amt = abs(float(p.get("pa", 0)))
                    if amt > 0:
                        snap["positions"][sym] = amt
                    else:
                        snap["positions"].pop(sym, None)
                snap["updated_at"] = time.monotonic()
            elif not is_futures and event_type == "outboundAccountPosition":
                for b in msg.get("B", []):
                    asset = (b.get("a") or "").upper()
                    free_val = float(b.get("f", 0))
                    if asset == "USDT":
                        snap["usdt"] = free_val
                    elif asset:
                        snap["spot"][asset] = free_val
                snap["updated_at"] = time.monotonic()


# Process-wide singleton
ws_manager = BinanceWebSocketManager()
