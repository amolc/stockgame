from django.shortcuts import render
from django.http import JsonResponse
from trading.models import BotConfig, Trade, SignalLog
from trading.tasks import run_all_active_bots


def dashboard_view(request):
    """
    Main web dashboard rendering active bots, trades, and strategy metrics.
    """
    bots = BotConfig.objects.all()
    trades = Trade.objects.all().order_by('-entry_time')[:20]
    signals = SignalLog.objects.all().order_by('-timestamp')[:30]
    
    total_trades = Trade.objects.filter(status='CLOSED').count()
    winning_trades = Trade.objects.filter(status='CLOSED', is_win=True).count()
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    
    total_pnl = sum([t.pnl for t in Trade.objects.filter(status='CLOSED') if t.pnl is not None]) or 0.0

    context = {
        'bots': bots,
        'trades': trades,
        'signals': signals,
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'win_rate': round(win_rate, 1),
        'total_pnl': round(total_pnl, 2),
    }
    return render(request, 'trading/dashboard.html', context)


def api_status_view(request):
    """
    JSON API endpoint returning real-time bot and trade status.
    """
    bots_data = []
    for bot in BotConfig.objects.all():
        open_trade = Trade.objects.filter(bot_config=bot, status='OPEN').first()
        bots_data.append({
            'id': bot.id,
            'name': bot.name,
            'symbol': bot.symbol,
            'interval': bot.interval,
            'fast_ema': bot.fast_ema_period,
            'slow_ema': bot.slow_ema_period,
            'current_cash': float(bot.current_cash),
            'is_active': bot.is_active,
            'has_open_position': open_trade is not None,
            'open_position': {
                'entry_price': float(open_trade.entry_price),
                'quantity': float(open_trade.quantity),
                'entry_time': open_trade.entry_time.isoformat(),
            } if open_trade else None,
            'last_run_at': bot.last_run_at.isoformat() if bot.last_run_at else None
        })

    return JsonResponse({
        'ok': True,
        'bots': bots_data,
        'total_trades': Trade.objects.count(),
        'recent_signals': [
            {
                'timestamp': s.timestamp.isoformat(),
                'symbol': s.symbol,
                'signal': s.signal_type,
                'price': float(s.price),
                'executed': s.executed,
                'message': s.message
            }
            for s in SignalLog.objects.all()[:10]
        ]
    })


def trigger_cycle_view(request):
    """
    Allows manually triggering a trading cycle via HTTP POST or GET.
    """
    results = run_all_active_bots()
    return JsonResponse({'ok': True, 'results': results})
