import logging
from django.utils import timezone
from trading.models import BotConfig
from trading.services.binance_service import BinanceService
from trading.services.strategy_service import StrategyService
from trading.services.execution_service import ExecutionService

logger = logging.getLogger(__name__)


def run_trading_cycle_for_bot(bot: BotConfig) -> dict:
    """
    Executes a single cycle of the trading strategy for a given BotConfig instance:
    1. Fetches latest Binance candles
    2. Calculates EMAs
    3. Evaluates latest completed candle for crossover
    4. Executes/Simulates trade in DB
    """
    if not bot.is_active:
        return {"bot": bot.name, "status": "skipped (inactive)"}

    binance = BinanceService()
    
    # 1. Fetch market candles
    df = binance.fetch_klines(
        symbol=bot.symbol,
        interval=bot.interval,
        lookback=bot.lookback
    )

    # 2. Evaluate EMA Crossover
    signal_data = StrategyService.evaluate_latest_signal(
        df,
        fast_period=bot.fast_ema_period,
        slow_period=bot.slow_ema_period
    )

    # 3. Process execution and save trade
    exec_result = ExecutionService.process_signal(bot, signal_data)

    # 4. Update last run timestamp
    bot.last_run_at = timezone.now()
    bot.save(update_fields=['last_run_at', 'updated_at'])

    return {
        "bot": bot.name,
        "symbol": bot.symbol,
        "interval": bot.interval,
        "latest_price": signal_data.get("active_price"),
        "signal": signal_data.get("signal"),
        "executed": exec_result.get("executed"),
        "message": exec_result.get("message")
    }


def run_all_active_bots():
    """
    Finds all active bots in the database and runs a trading cycle for each.
    """
    active_bots = BotConfig.objects.filter(is_active=True)
    if not active_bots.exists():
        # Create a default bot if none exists
        default_bot = BotConfig.objects.create(
            name="BTCUSDT Default EMA Bot",
            symbol="BTCUSDT",
            interval="15m",
            fast_ema_period=20,
            slow_ema_period=50,
            initial_capital=10000.00,
            current_cash=10000.00,
            is_active=True
        )
        active_bots = [default_bot]

    results = []
    for bot in active_bots:
        try:
            res = run_trading_cycle_for_bot(bot)
            results.append(res)
        except Exception as e:
            logger.error(f"Error running bot {bot.name}: {e}")
            results.append({"bot": bot.name, "error": str(e)})

    return results
