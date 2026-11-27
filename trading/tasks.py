import logging
from typing import Optional
from django.utils import timezone
from trading.models import BotConfig
from trading.services.binance_service import BinanceService
from trading.services.strategy_service import StrategyService
from trading.services.execution_service import ExecutionService

logger = logging.getLogger(__name__)


def run_trading_cycle_for_bot(
    bot: BotConfig,
    binance: Optional[BinanceService] = None,
    kline_cache: Optional[dict] = None,
) -> dict:
    """
    Executes a single cycle of the trading strategy for a given BotConfig instance:
    1. Fetches latest Binance candles (reusing cycle cache when shared across accounts)
    2. Calculates EMAs
    3. Evaluates latest completed candle for crossover
    4. Executes/Simulates trade in DB
    """
    if not bot.is_active:
        return {"bot": bot.name, "status": "skipped (inactive)"}
    if bot.account_id and not bot.account.is_active:
        return {"bot": bot.name, "status": "skipped (account paused)"}

    if binance is None:
        binance = BinanceService.for_bot(bot)

    cache_key = (binance.demo, binance.testnet, binance.market, bot.symbol, bot.interval, bot.lookback)
    if kline_cache is not None and cache_key in kline_cache:
        df = kline_cache[cache_key]
    else:
        df = binance.fetch_klines(
            symbol=bot.symbol,
            interval=bot.interval,
            lookback=bot.lookback,
        )
        if kline_cache is not None:
            kline_cache[cache_key] = df

    # Evaluate EMA Crossover
    signal_data = StrategyService.evaluate_latest_signal(
        df,
        fast_period=bot.fast_ema_period,
        slow_period=bot.slow_ema_period,
    )

    # Process execution and save trade
    exec_result = ExecutionService.process_signal(bot, signal_data, binance=binance)

    # Update last run timestamp
    bot.last_run_at = timezone.now()
    bot.save(update_fields=['last_run_at', 'updated_at'])

    return {
        "bot": bot.name,
        "account": bot.account.name if bot.account_id else "Default",
        "mode": bot.mode,
        "symbol": bot.symbol,
        "interval": bot.interval,
        "latest_price": signal_data.get("active_price"),
        "signal": signal_data.get("signal"),
        "executed": exec_result.get("executed"),
        "duplicate": exec_result.get("duplicate", False),
        "message": exec_result.get("message"),
    }


def run_all_active_bots(account_id: Optional[int] = None):
    """
    Finds all active bots (optionally scoped to one account) and runs a trading cycle for each.
    """
    if not BotConfig.objects.exists():
        default_bot = BotConfig.objects.create(
            name="BTCUSDT Default EMA Bot",
            symbol="BTCUSDT",
            interval="15m",
            fast_ema_period=20,
            slow_ema_period=50,
            initial_capital=10000.00,
            current_cash=10000.00,
            is_active=True,
        )
        active_bots = [default_bot]
    else:
        qs = BotConfig.objects.select_related('account').filter(is_active=True).order_by('account_id', 'id')
        if account_id is not None:
            qs = qs.filter(account_id=account_id)
        active_bots = [
            b for b in qs
            if not (b.account_id and not b.account.is_active)
        ]

    client_cache: dict[Optional[int], BinanceService] = {}
    kline_cache: dict = {}
    results = []
    for bot in active_bots:
        try:
            acct_key = bot.account_id
            if acct_key not in client_cache:
                client_cache[acct_key] = BinanceService.for_bot(bot)
            res = run_trading_cycle_for_bot(
                bot,
                binance=client_cache[acct_key],
                kline_cache=kline_cache,
            )
            results.append(res)
        except Exception as e:
            logger.error(f"Error running bot {bot.name}: {e}")
            results.append({
                "bot": bot.name,
                "account": bot.account.name if bot.account_id else "Default",
                "symbol": bot.symbol,
                "error": str(e),
            })

    return results
