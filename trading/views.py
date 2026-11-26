import json
from decimal import Decimal, InvalidOperation
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from trading.models import BotConfig, Trade, SignalLog
from trading.services.binance_service import BinanceService
from trading.services.strategy_service import StrategyService
from trading.tasks import run_all_active_bots


def _compute_kpis() -> dict:
    """Compute portfolio and trade summary metrics from the database."""
    closed_trades = Trade.objects.filter(status='CLOSED')
    total_trades = closed_trades.count()
    winning_trades = closed_trades.filter(is_win=True).count()
    win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0
    total_pnl = sum((float(t.pnl) for t in closed_trades if t.pnl is not None), 0.0)
    return {
        'active_bots': BotConfig.objects.filter(is_active=True).count(),
        'total_bots': BotConfig.objects.count(),
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'win_rate': round(win_rate, 1),
        'total_pnl': round(total_pnl, 2),
    }


def dashboard_view(request):
    """Main web dashboard rendering active bots, trades, and strategy metrics."""
    bots = BotConfig.objects.all().order_by('id')
    trades = Trade.objects.all().order_by('-entry_time')[:30]
    signals = SignalLog.objects.all().order_by('-timestamp')[:20]
    kpis = _compute_kpis()

    context = {
        'bots': bots,
        'trades': trades,
        'signals': signals,
        'total_trades_count': Trade.objects.count(),
        'total_signals_count': SignalLog.objects.count(),
        'active_bots_count': kpis['active_bots'],
        'total_bots_count': kpis['total_bots'],
        'total_trades': kpis['total_trades'],
        'winning_trades': kpis['winning_trades'],
        'win_rate': kpis['win_rate'],
        'total_pnl': kpis['total_pnl'],
        'interval_choices': BotConfig.INTERVAL_CHOICES,
        'mode_choices': BotConfig.MODE_CHOICES,
    }
    return render(request, 'trading/dashboard.html', context)


def api_status_view(request):
    """JSON API returning live Binance prices, Testnet balances, chart series, and DB state."""
    include_chart = request.GET.get('chart', '1') != '0'
    binance = BinanceService()
    bots = list(BotConfig.objects.all().order_by('id'))

    price_cache = {}
    balance_cache = {}
    market_error = None
    bots_data = []

    for bot in bots:
        sym = bot.symbol
        if sym not in price_cache:
            try:
                price_cache[sym] = binance.get_ticker_price(sym)
            except Exception as exc:
                price_cache[sym] = None
                market_error = str(exc)

        if sym not in balance_cache:
            try:
                balance_cache[sym] = binance.get_account_balances(sym)
            except Exception as exc:
                balance_cache[sym] = {"usdt": None, "asset": None}
                if not market_error:
                    market_error = str(exc)

        live_price = price_cache[sym]
        balances = balance_cache[sym]
        open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first()

        open_pos_data = None
        if open_trade:
            qty = float(open_trade.quantity)
            cost = float(open_trade.cost)
            entry_price = float(open_trade.entry_price)
            if live_price is not None:
                current_val = qty * live_price
                if open_trade.side == 'SHORT':
                    unrealized_pnl = (entry_price - live_price) * qty
                else:
                    unrealized_pnl = current_val - cost
                unrealized_pnl_pct = (unrealized_pnl / cost * 100.0) if cost else 0.0
            else:
                current_val = cost
                unrealized_pnl = 0.0
                unrealized_pnl_pct = 0.0

            open_pos_data = {
                'trade_id': open_trade.id,
                'side': open_trade.side,
                'entry_price': entry_price,
                'quantity': qty,
                'cost': cost,
                'current_value': round(current_val, 2),
                'unrealized_pnl': round(unrealized_pnl, 2),
                'unrealized_pnl_pct': round(unrealized_pnl_pct, 2),
                'entry_time': open_trade.entry_time.isoformat(),
            }

        bots_data.append({
            'id': bot.id,
            'name': bot.name,
            'symbol': bot.symbol,
            'interval': bot.interval,
            'lookback': bot.lookback,
            'fast_ema': bot.fast_ema_period,
            'slow_ema': bot.slow_ema_period,
            'order_amount_usdt': float(bot.order_amount_usdt),
            'current_cash': float(bot.current_cash),
            'mode': bot.mode,
            'is_active': bot.is_active,
            'live_price': live_price,
            'real_usdt_balance': balances['usdt'],
            'real_asset_balance': balances['asset'],
            'has_open_position': open_trade is not None,
            'open_position': open_pos_data,
            'last_run_at': bot.last_run_at.isoformat() if bot.last_run_at else None,
        })

    chart_data = None
    primary_bot = next((b for b in bots if b.is_active), bots[0] if bots else None)
    if include_chart and primary_bot is not None:
        try:
            raw_df = binance.fetch_klines(
                symbol=primary_bot.symbol,
                interval=primary_bot.interval,
                lookback=primary_bot.lookback,
            )
            df = StrategyService.calculate_emas(
                raw_df,
                fast_period=primary_bot.fast_ema_period,
                slow_period=primary_bot.slow_ema_period,
            ).tail(120)
            fast_col = f"EMA_{primary_bot.fast_ema_period}"
            slow_col = f"EMA_{primary_bot.slow_ema_period}"

            recent_for_markers = Trade.objects.filter(
                bot_config=primary_bot,
                symbol=primary_bot.symbol,
            ).order_by('-entry_time')[:20]

            chart_data = {
                'symbol': primary_bot.symbol,
                'interval': primary_bot.interval,
                'fast_period': primary_bot.fast_ema_period,
                'slow_period': primary_bot.slow_ema_period,
                'timestamps': [ts.isoformat() for ts in df.index],
                'unix_times': [int(ts.timestamp()) for ts in df.index],
                'open': [float(v) for v in df['open']],
                'high': [float(v) for v in df['high']],
                'low': [float(v) for v in df['low']],
                'close': [float(v) for v in df['close']],
                'fast_ema': [float(v) for v in df[fast_col]],
                'slow_ema': [float(v) for v in df[slow_col]],
                'trade_markers': [
                    {
                        'id': tr.id,
                        'entry_time': tr.entry_time.isoformat(),
                        'entry_price': float(tr.entry_price),
                        'exit_time': tr.exit_time.isoformat() if tr.exit_time else None,
                        'exit_price': float(tr.exit_price) if tr.exit_price is not None else None,
                    }
                    for tr in recent_for_markers
                ],
            }
        except Exception as exc:
            if not market_error:
                market_error = str(exc)

    recent_trades = [
        {
            'id': t.id,
            'symbol': t.symbol,
            'status': t.status,
            'side': t.side,
            'entry_time': t.entry_time.strftime('%Y-%m-%d %H:%M:%S'),
            'entry_price': float(t.entry_price),
            'quantity': float(t.quantity),
            'cost': float(t.cost),
            'exit_time': t.exit_time.strftime('%Y-%m-%d %H:%M:%S') if t.exit_time else None,
            'exit_price': float(t.exit_price) if t.exit_price is not None else None,
            'pnl': float(t.pnl) if t.pnl is not None else None,
            'pnl_pct': float(t.pnl_pct) if t.pnl_pct is not None else None,
            'is_win': t.is_win,
            'notes': t.notes,
        }
        for t in Trade.objects.all().order_by('-entry_time')[:30]
    ]

    recent_signals = [
        {
            'id': s.id,
            'timestamp': s.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': s.symbol,
            'signal': s.signal_type,
            'price': float(s.price),
            'fast_ema': float(s.fast_ema),
            'slow_ema': float(s.slow_ema),
            'executed': s.executed,
            'message': s.message,
        }
        for s in SignalLog.objects.all().order_by('-timestamp')[:20]
    ]

    return JsonResponse({
        'ok': True,
        'binance_authenticated': binance.is_authenticated,
        'binance_testnet': binance.testnet,
        'market_error': market_error,
        'kpis': _compute_kpis(),
        'bots': bots_data,
        'total_trades': Trade.objects.count(),
        'total_signals': SignalLog.objects.count(),
        'recent_trades': recent_trades,
        'recent_signals': recent_signals,
        'chart': chart_data,
    })


def trigger_cycle_view(request):
    """Manually trigger one trading cycle across all active bots."""
    try:
        results = run_all_active_bots()
        has_error = any('error' in r for r in results)
        return JsonResponse({'ok': not has_error, 'results': results})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc), 'results': []}, status=500)


@require_POST
def update_bot_view(request, bot_id: int):
    """Update BotConfig trading parameters (including variable fixed order_amount_usdt)."""
    bot = get_object_or_404(BotConfig, id=bot_id)
    try:
        payload = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON payload.'}, status=400)

    symbol = str(payload.get('symbol', bot.symbol)).strip().upper()
    interval = str(payload.get('interval', bot.interval)).strip()
    mode = str(payload.get('mode', bot.mode)).strip().upper()

    valid_intervals = {k for k, _ in BotConfig.INTERVAL_CHOICES}
    valid_modes = {k for k, _ in BotConfig.MODE_CHOICES}

    if not symbol or not symbol.isalnum():
        return JsonResponse({'ok': False, 'error': 'Symbol must be an alphanumeric pair like BTCUSDT.'}, status=400)
    if interval not in valid_intervals:
        return JsonResponse({'ok': False, 'error': f'Invalid interval: {interval}.'}, status=400)
    if mode not in valid_modes:
        return JsonResponse({'ok': False, 'error': f'Invalid mode: {mode}.'}, status=400)

    try:
        fast_ema = int(payload.get('fast_ema_period', bot.fast_ema_period))
        slow_ema = int(payload.get('slow_ema_period', bot.slow_ema_period))
        order_amount = Decimal(str(payload.get('order_amount_usdt', bot.order_amount_usdt)))
    except (ValueError, TypeError, InvalidOperation):
        return JsonResponse({'ok': False, 'error': 'EMA periods and order size must be valid numbers.'}, status=400)

    if fast_ema < 2 or slow_ema <= fast_ema:
        return JsonResponse({'ok': False, 'error': 'Fast EMA must be >= 2 and strictly less than Slow EMA.'}, status=400)
    if order_amount < Decimal('10.00'):
        return JsonResponse({'ok': False, 'error': 'Order size must be at least $10.00 USDT.'}, status=400)

    is_active = payload.get('is_active', bot.is_active)
    if isinstance(is_active, str):
        is_active = is_active.lower() in ('true', '1', 'yes', 'on')

    bot.symbol = symbol
    bot.interval = interval
    bot.fast_ema_period = fast_ema
    bot.slow_ema_period = slow_ema
    bot.order_amount_usdt = order_amount
    bot.mode = mode
    bot.is_active = bool(is_active)
    bot.save()

    return JsonResponse({
        'ok': True,
        'bot': {
            'id': bot.id,
            'symbol': bot.symbol,
            'interval': bot.interval,
            'fast_ema': bot.fast_ema_period,
            'slow_ema': bot.slow_ema_period,
            'order_amount_usdt': float(bot.order_amount_usdt),
            'mode': bot.mode,
            'is_active': bot.is_active,
        }
    })


@require_POST
def toggle_bot_view(request, bot_id: int):
    """Toggle a bot's is_active flag."""
    bot = get_object_or_404(BotConfig, id=bot_id)
    bot.is_active = not bot.is_active
    bot.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'ok': True, 'id': bot.id, 'is_active': bot.is_active})
