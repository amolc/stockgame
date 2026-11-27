import json
from decimal import Decimal, InvalidOperation
from functools import wraps
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from trading.models import DEFAULT_BOT_SYMBOLS, BotConfig, SignalLog, Trade, TradingAccount
from trading.services.binance_service import BinanceService
from trading.services.strategy_service import StrategyService
from trading.tasks import run_all_active_bots


def _superadmin_required(api: bool = False):
    """Require an authenticated staff/superuser session for Super Admin views and APIs."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated:
                if api:
                    return JsonResponse({"ok": False, "error": "Authentication required."}, status=401)
                return redirect(f"/login/?next={request.get_full_path()}")
            if not (user.is_staff or user.is_superuser):
                if api:
                    return JsonResponse({"ok": False, "error": "Super Admin privileges required."}, status=403)
                return redirect("/login/")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def _parse_json_or_post(request) -> dict:
    if request.body:
        return json.loads(request.body.decode("utf-8"))
    return request.POST.dict()


def _parse_bool(val, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _resolve_account_id(request):
    raw = (request.GET.get("account") or "").strip()
    if not raw or raw.lower() == "all":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _compute_kpis(account_id=None, bot_id=None) -> dict:
    """Compute portfolio and trade summary metrics from the database (optionally per account or bot)."""
    bot_qs = BotConfig.objects.all()
    trade_qs = Trade.objects.filter(status='CLOSED')
    if bot_id is not None:
        bot_qs = bot_qs.filter(id=bot_id)
        trade_qs = trade_qs.filter(bot_config_id=bot_id)
    elif account_id is not None:
        bot_qs = bot_qs.filter(account_id=account_id)
        trade_qs = trade_qs.filter(bot_config__account_id=account_id)

    total_trades = trade_qs.count()
    winning_trades = trade_qs.filter(is_win=True).count()
    win_rate = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0
    total_pnl = sum((float(t.pnl) for t in trade_qs if t.pnl is not None), 0.0)
    return {
        'active_bots': bot_qs.filter(is_active=True).count(),
        'total_bots': bot_qs.count(),
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'win_rate': round(win_rate, 1),
        'total_pnl': round(total_pnl, 2),
    }


@never_cache
def login_view(request):
    """Super Admin sign-in page."""
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return redirect("trading:dashboard")

    error = None
    next_url = request.GET.get("next") or request.POST.get("next") or "/"
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None and (user.is_staff or user.is_superuser):
            login(request, user)
            return redirect(next_url)
        error = "Invalid Super Admin credentials."

    return render(request, "trading/login.html", {"error": error, "next": next_url})


def logout_view(request):
    logout(request)
    return redirect("trading:login")


@_superadmin_required(api=False)
def dashboard_view(request):
    """Main Super Admin web dashboard rendering accounts, bots, trades, and strategy metrics."""
    account_id = _resolve_account_id(request)
    accounts = list(TradingAccount.objects.all().order_by('id'))

    bot_qs = BotConfig.objects.select_related('account').order_by('account_id', 'id')
    trade_qs = Trade.objects.select_related('bot_config', 'bot_config__account').order_by('-entry_time')
    signal_qs = SignalLog.objects.select_related('bot_config', 'bot_config__account').order_by('-timestamp')

    if account_id is not None:
        bot_qs = bot_qs.filter(account_id=account_id)
        trade_qs = trade_qs.filter(bot_config__account_id=account_id)
        signal_qs = signal_qs.filter(bot_config__account_id=account_id)

    bots = list(bot_qs)
    selected_bot = None
    raw_bot_id = request.GET.get('bot_id')
    if raw_bot_id:
        try:
            req_bot_id = int(raw_bot_id)
            selected_bot = next((b for b in bots if b.id == req_bot_id), None)
        except ValueError:
            selected_bot = None

    if selected_bot is not None:
        trade_qs = trade_qs.filter(bot_config=selected_bot)
        signal_qs = signal_qs.filter(bot_config=selected_bot)

    trades = list(trade_qs[:30])
    signals = list(signal_qs[:20])
    kpis = _compute_kpis(account_id=account_id, bot_id=selected_bot.id if selected_bot else None)

    context = {
        'accounts': accounts,
        'selected_account_id': account_id,
        'selected_bot': selected_bot,
        'selected_bot_id': selected_bot.id if selected_bot else None,
        'bots': bots,
        'trades': trades,
        'signals': signals,
        'total_trades_count': trade_qs.count(),
        'total_signals_count': signal_qs.count(),
        'active_bots_count': kpis['active_bots'],
        'total_bots_count': kpis['total_bots'],
        'total_trades': kpis['total_trades'],
        'winning_trades': kpis['winning_trades'],
        'win_rate': kpis['win_rate'],
        'total_pnl': kpis['total_pnl'],
        'interval_choices': BotConfig.INTERVAL_CHOICES,
        'mode_choices': BotConfig.MODE_CHOICES,
        'environment_choices': TradingAccount.ENVIRONMENT_CHOICES,
        'market_choices': TradingAccount.MARKET_CHOICES,
        'default_symbols': DEFAULT_BOT_SYMBOLS,
    }
    return render(request, 'trading/dashboard.html', context)


@_superadmin_required(api=False)
def accounts_view(request):
    """Dedicated Super Admin page listing and managing all TradingAccounts."""
    accounts = list(TradingAccount.objects.all().order_by('id'))
    account_rows = [
        {
            'account': acct,
            'bots_active': acct.bots.filter(is_active=True).count(),
            'bots_total': acct.bots.count(),
        }
        for acct in accounts
    ]
    context = {
        'accounts': accounts,
        'account_rows': account_rows,
        'environment_choices': TradingAccount.ENVIRONMENT_CHOICES,
        'market_choices': TradingAccount.MARKET_CHOICES,
        'default_symbols': DEFAULT_BOT_SYMBOLS,
    }
    return render(request, 'trading/accounts.html', context)


@_superadmin_required(api=False)
def account_detail_view(request, account_id: int):
    """Super Admin detail page showing a single TradingAccount, its bots, trades, and signals."""
    account = get_object_or_404(TradingAccount, id=account_id)
    bots = list(BotConfig.objects.filter(account=account).select_related('account').order_by('id'))
    trade_qs = Trade.objects.filter(bot_config__account=account).select_related('bot_config', 'bot_config__account').order_by('-entry_time')
    signal_qs = SignalLog.objects.filter(bot_config__account=account).select_related('bot_config', 'bot_config__account').order_by('-timestamp')
    active_count = sum(1 for b in bots if b.is_active)
    context = {
        'account': account,
        'bots': bots,
        'trades': list(trade_qs[:30]),
        'signals': list(signal_qs[:20]),
        'total_trades_count': trade_qs.count(),
        'total_signals_count': signal_qs.count(),
        'active_bots_count': active_count,
        'total_bots_count': len(bots),
        'interval_choices': BotConfig.INTERVAL_CHOICES,
        'mode_choices': BotConfig.MODE_CHOICES,
        'environment_choices': TradingAccount.ENVIRONMENT_CHOICES,
        'market_choices': TradingAccount.MARKET_CHOICES,
        'default_symbols': DEFAULT_BOT_SYMBOLS,
    }
    return render(request, 'trading/account_detail.html', context)


@_superadmin_required(api=True)
def api_status_view(request):
    """JSON API returning accounts, live Binance prices, balances, chart series, and DB state."""
    include_chart = request.GET.get('chart', '1') != '0'
    account_id = _resolve_account_id(request)
    requested_bot_id = request.GET.get('bot_id')

    accounts = list(TradingAccount.objects.all().order_by('id'))
    bot_qs = BotConfig.objects.select_related('account').order_by('account_id', 'id')
    trade_qs = Trade.objects.select_related('bot_config', 'bot_config__account').order_by('-entry_time')
    signal_qs = SignalLog.objects.select_related('bot_config', 'bot_config__account').order_by('-timestamp')

    if account_id is not None:
        bot_qs = bot_qs.filter(account_id=account_id)
        trade_qs = trade_qs.filter(bot_config__account_id=account_id)
        signal_qs = signal_qs.filter(bot_config__account_id=account_id)

    bots = list(bot_qs)

    client_cache: dict[object, BinanceService] = {}
    price_cache: dict[tuple, float | None] = {}
    balance_cache: dict[tuple, dict] = {}
    account_usdt_cache: dict[int, float | None] = {}
    market_errors: list[str] = []

    def get_client_for_bot(bot: BotConfig) -> BinanceService:
        key = bot.account_id if bot.account_id is not None else "default"
        if key not in client_cache:
            client_cache[key] = BinanceService.for_bot(bot)
        return client_cache[key]

    failed_accounts: set[object] = set()
    bots_data = []
    for bot in bots:
        binance = get_client_for_bot(bot)
        sym = bot.symbol
        env_key = (binance.demo, binance.testnet, binance.market, sym)

        if env_key not in price_cache:
            try:
                price_cache[env_key] = binance.get_ticker_price(sym)
            except Exception as exc:
                price_cache[env_key] = None
                market_errors.append(f"{sym}: {exc}")

        acct_err_key = bot.account_id if bot.account_id is not None else "default"
        bal_key = (bot.account_id, sym)
        if bal_key not in balance_cache:
            if acct_err_key in failed_accounts:
                balance_cache[bal_key] = {"usdt": None, "asset": None}
            else:
                try:
                    bal = binance.get_account_balances(sym)
                    balance_cache[bal_key] = bal
                    if bot.account_id is not None and bal.get("usdt") is not None:
                        account_usdt_cache[bot.account_id] = bal["usdt"]
                except Exception as exc:
                    balance_cache[bal_key] = {"usdt": None, "asset": None}
                    failed_accounts.add(acct_err_key)
                    market_errors.append(f"{bot.account.name if bot.account_id else 'Default'}: {exc}")

        live_price = price_cache[env_key]
        balances = balance_cache[bal_key]
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
            'account_id': bot.account_id,
            'account_name': bot.account.name if bot.account_id else "Default (.env)",
            'account_env': bot.account.environment if bot.account_id else ("DEMO" if binance.demo else ("TESTNET" if binance.testnet else "LIVE")),
            'account_market': bot.account.market if bot.account_id else binance.market,
            'account_active': bot.account.is_active if bot.account_id else True,
            'name': bot.name,
            'symbol': bot.symbol,
            'interval': bot.interval,
            'lookback': bot.lookback,
            'fast_ema': bot.fast_ema_period,
            'slow_ema': bot.slow_ema_period,
            'order_amount_usdt': float(bot.order_amount_usdt),
            'order_qty': float(bot.order_qty) if bot.order_qty is not None else None,
            'current_cash': float(bot.current_cash),
            'mode': bot.mode,
            'mirror_to_demo': bot.mirror_to_demo,
            'is_active': bot.is_active,
            'live_price': live_price,
            'real_usdt_balance': balances['usdt'],
            'real_asset_balance': balances['asset'],
            'has_open_position': open_trade is not None,
            'open_position': open_pos_data,
            'last_run_at': bot.last_run_at.isoformat() if bot.last_run_at else None,
        })

    # Build accounts summary (fetch USDT balance once per account if not yet cached)
    accounts_data = []
    for acct in accounts:
        if (
            acct.id not in account_usdt_cache
            and acct.id not in failed_accounts
            and acct.environment != "PAPER"
            and acct.has_credentials
        ):
            try:
                acct_client = client_cache.get(acct.id) or BinanceService.from_account(acct)
                client_cache[acct.id] = acct_client
                bal = acct_client.get_account_balances("BTCUSDT")
                account_usdt_cache[acct.id] = bal.get("usdt")
            except Exception as exc:
                account_usdt_cache[acct.id] = None
                failed_accounts.add(acct.id)
                market_errors.append(f"{acct.name}: {exc}")

        accounts_data.append({
            'id': acct.id,
            'name': acct.name,
            'slug': acct.slug,
            'environment': acct.environment,
            'market': acct.market,
            'leverage': acct.leverage,
            'is_active': acct.is_active,
            'masked_api_key': acct.masked_api_key,
            'has_credentials': acct.has_credentials,
            'usdt_balance': account_usdt_cache.get(acct.id),
            'bots_active': acct.bots.filter(is_active=True).count(),
            'bots_total': acct.bots.count(),
            'last_health_status': acct.last_health_status,
            'last_health_message': acct.last_health_message,
            'last_checked_at': acct.last_checked_at.isoformat() if acct.last_checked_at else None,
        })

    primary_bot = None
    if requested_bot_id:
        try:
            req_id = int(requested_bot_id)
            primary_bot = next((b for b in bots if b.id == req_id), None)
        except ValueError:
            primary_bot = None
    if primary_bot is None:
        primary_bot = next((b for b in bots if b.is_active), bots[0] if bots else None)

    chart_data = None
    if include_chart and primary_bot is not None:
        try:
            primary_binance = get_client_for_bot(primary_bot)
            raw_df = primary_binance.fetch_klines(
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
                'bot_id': primary_bot.id,
                'account_name': primary_bot.account.name if primary_bot.account_id else "Default",
                'market': primary_binance.market,
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
            market_errors.append(f"Chart ({primary_bot.symbol}): {exc}")

    primary_client = get_client_for_bot(primary_bot) if primary_bot else BinanceService()
    scope_bot = request.GET.get('scope') == 'bot' and primary_bot is not None
    if scope_bot:
        trade_qs = trade_qs.filter(bot_config=primary_bot)
        signal_qs = signal_qs.filter(bot_config=primary_bot)

    recent_trades = [
        {
            'id': t.id,
            'account_name': t.bot_config.account.name if t.bot_config and t.bot_config.account_id else "Default",
            'symbol': t.symbol,
            'status': t.status,
            'side': t.side,
            'entry_time': timezone.localtime(t.entry_time).isoformat(),
            'entry_price': float(t.entry_price),
            'quantity': float(t.quantity),
            'cost': float(t.cost),
            'exit_time': timezone.localtime(t.exit_time).isoformat() if t.exit_time else None,
            'exit_price': float(t.exit_price) if t.exit_price is not None else None,
            'pnl': float(t.pnl) if t.pnl is not None else None,
            'pnl_pct': float(t.pnl_pct) if t.pnl_pct is not None else None,
            'is_win': t.is_win,
            'notes': t.notes,
        }
        for t in trade_qs[:30]
    ]

    recent_signals = [
        {
            'id': s.id,
            'account_name': s.bot_config.account.name if s.bot_config and s.bot_config.account_id else "Default",
            'timestamp': timezone.localtime(s.timestamp).isoformat(),
            'symbol': s.symbol,
            'signal': s.signal_type,
            'price': float(s.price),
            'fast_ema': float(s.fast_ema),
            'slow_ema': float(s.slow_ema),
            'executed': s.executed,
            'message': s.message,
        }
        for s in signal_qs[:20]
    ]

    # Deduplicate market error summary while preserving order
    unique_errors = list(dict.fromkeys(market_errors))

    exchange_positions = []
    if account_id is not None and account_id in client_cache and account_id not in failed_accounts:
        try:
            pos_list = client_cache[account_id].get_exchange_positions()
            if isinstance(pos_list, list):
                exchange_positions = pos_list
        except Exception:
            exchange_positions = []

    return JsonResponse({
        'ok': True,
        'selected_account_id': account_id,
        'primary_bot_id': primary_bot.id if primary_bot else None,
        'binance_authenticated': primary_client.is_authenticated,
        'binance_testnet': primary_client.is_demo,
        'market_error': " | ".join(unique_errors) if unique_errors else None,
        'kpis': _compute_kpis(account_id=account_id, bot_id=primary_bot.id if scope_bot else None),
        'accounts': accounts_data,
        'bots': bots_data,
        'exchange_positions': exchange_positions,
        'total_trades': trade_qs.count(),
        'total_signals': signal_qs.count(),
        'recent_trades': recent_trades,
        'recent_signals': recent_signals,
        'chart': chart_data,
    })


@_superadmin_required(api=True)
@require_POST
def trigger_cycle_view(request):
    """Manually trigger one trading cycle across active bots (optionally for one account)."""
    account_id = _resolve_account_id(request)
    if account_id is None and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            if body.get("account_id"):
                account_id = int(body["account_id"])
        except Exception:
            pass
    try:
        results = run_all_active_bots(account_id=account_id)
        has_error = any('error' in r for r in results)
        return JsonResponse({'ok': not has_error, 'results': results})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc), 'results': []}, status=500)


@_superadmin_required(api=True)
@require_POST
def create_account_view(request):
    """Create a new TradingAccount and optionally seed the 6 standard symbols."""
    try:
        payload = _parse_json_or_post(request)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON payload.'}, status=400)

    name = str(payload.get('name', '')).strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Account name is required.'}, status=400)
    if TradingAccount.objects.filter(name__iexact=name).exists():
        return JsonResponse({'ok': False, 'error': f'Account "{name}" already exists.'}, status=400)

    environment = str(payload.get('environment', 'DEMO')).strip().upper()
    market = str(payload.get('market', 'futures')).strip().lower()
    valid_envs = {k for k, _ in TradingAccount.ENVIRONMENT_CHOICES}
    valid_markets = {k for k, _ in TradingAccount.MARKET_CHOICES}
    if environment not in valid_envs:
        return JsonResponse({'ok': False, 'error': f'Invalid environment: {environment}.'}, status=400)
    if market not in valid_markets:
        return JsonResponse({'ok': False, 'error': f'Invalid market: {market}.'}, status=400)

    try:
        leverage = int(payload.get('leverage', 5))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Leverage must be an integer.'}, status=400)
    if leverage < 1 or leverage > 125:
        return JsonResponse({'ok': False, 'error': 'Leverage must be between 1 and 125.'}, status=400)

    account = TradingAccount(
        name=name,
        environment=environment,
        market=market,
        leverage=leverage,
        api_key=str(payload.get('api_key', '')).strip(),
        is_active=_parse_bool(payload.get('is_active'), True),
    )
    raw_secret = str(payload.get('api_secret', '')).strip()
    if raw_secret:
        account.set_api_secret(raw_secret)
    account.save()

    created_bots = []
    if _parse_bool(payload.get('seed_symbols'), True):
        created_bots = account.ensure_default_bots()

    return JsonResponse({
        'ok': True,
        'account': {
            'id': account.id,
            'name': account.name,
            'environment': account.environment,
            'market': account.market,
            'leverage': account.leverage,
            'masked_api_key': account.masked_api_key,
            'is_active': account.is_active,
        },
        'created_symbols': [b.symbol for b in created_bots],
    })


@_superadmin_required(api=True)
@require_POST
def update_account_view(request, account_id: int):
    """Update TradingAccount settings and credentials (blank api_secret keeps existing)."""
    account = get_object_or_404(TradingAccount, id=account_id)
    try:
        payload = _parse_json_or_post(request)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON payload.'}, status=400)

    name = str(payload.get('name', account.name)).strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Account name cannot be empty.'}, status=400)
    if TradingAccount.objects.filter(name__iexact=name).exclude(id=account.id).exists():
        return JsonResponse({'ok': False, 'error': f'Account "{name}" already exists.'}, status=400)

    environment = str(payload.get('environment', account.environment)).strip().upper()
    market = str(payload.get('market', account.market)).strip().lower()
    if environment not in {k for k, _ in TradingAccount.ENVIRONMENT_CHOICES}:
        return JsonResponse({'ok': False, 'error': f'Invalid environment: {environment}.'}, status=400)
    if market not in {k for k, _ in TradingAccount.MARKET_CHOICES}:
        return JsonResponse({'ok': False, 'error': f'Invalid market: {market}.'}, status=400)

    try:
        leverage = int(payload.get('leverage', account.leverage))
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Leverage must be an integer.'}, status=400)
    if leverage < 1 or leverage > 125:
        return JsonResponse({'ok': False, 'error': 'Leverage must be between 1 and 125.'}, status=400)

    account.name = name
    account.environment = environment
    account.market = market
    account.leverage = leverage
    if 'api_key' in payload:
        account.api_key = str(payload.get('api_key') or '').strip()
    raw_secret = str(payload.get('api_secret') or '').strip()
    if raw_secret:
        account.set_api_secret(raw_secret)
    if 'is_active' in payload:
        account.is_active = _parse_bool(payload.get('is_active'), account.is_active)

    account.save()

    return JsonResponse({
        'ok': True,
        'account': {
            'id': account.id,
            'name': account.name,
            'environment': account.environment,
            'market': account.market,
            'leverage': account.leverage,
            'masked_api_key': account.masked_api_key,
            'is_active': account.is_active,
        },
    })


@_superadmin_required(api=True)
@require_POST
def test_account_view(request, account_id: int):
    """Test Binance API credentials for a TradingAccount and persist health status."""
    account = get_object_or_404(TradingAccount, id=account_id)
    ok, msg, usdt_bal = BinanceService.from_account(account).test_connection()
    account.last_health_status = "OK" if ok else "ERROR"
    account.last_health_message = msg
    account.last_checked_at = timezone.now()
    account.save(update_fields=['last_health_status', 'last_health_message', 'last_checked_at'])
    return JsonResponse({
        'ok': ok,
        'status': account.last_health_status,
        'message': msg,
        'usdt_balance': usdt_bal,
    })


@_superadmin_required(api=True)
@require_POST
def toggle_account_view(request, account_id: int):
    """Toggle a TradingAccount's master is_active switch."""
    account = get_object_or_404(TradingAccount, id=account_id)
    account.is_active = not account.is_active
    account.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'ok': True, 'id': account.id, 'is_active': account.is_active})


@_superadmin_required(api=True)
@require_POST
def seed_account_symbols_view(request, account_id: int):
    """Ensure all 6 default symbol bots exist for the given account."""
    account = get_object_or_404(TradingAccount, id=account_id)
    created = account.ensure_default_bots()
    return JsonResponse({
        'ok': True,
        'account_id': account.id,
        'created_count': len(created),
        'created_symbols': [b.symbol for b in created],
    })


@_superadmin_required(api=True)
@require_POST
def toggle_account_bots_view(request, account_id: int):
    """Bulk start or pause all bots belonging to an account."""
    account = get_object_or_404(TradingAccount, id=account_id)
    try:
        payload = _parse_json_or_post(request)
    except json.JSONDecodeError:
        payload = {}
    action = str(payload.get('action', 'pause')).strip().lower()
    active_flag = action in ('start', 'activate', 'on', 'true')
    updated = account.bots.update(is_active=active_flag, updated_at=timezone.now())
    return JsonResponse({
        'ok': True,
        'account_id': account.id,
        'is_active': active_flag,
        'updated_count': updated,
    })


@_superadmin_required(api=True)
@require_POST
def create_bot_view(request):
    """Create a new symbol BotConfig under an account."""
    try:
        payload = _parse_json_or_post(request)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON payload.'}, status=400)

    account_id = payload.get('account_id')
    if not account_id:
        return JsonResponse({'ok': False, 'error': 'account_id is required.'}, status=400)
    account = get_object_or_404(TradingAccount, id=account_id)

    symbol = str(payload.get('symbol', '')).strip().upper()
    if not symbol or not symbol.isalnum():
        return JsonResponse({'ok': False, 'error': 'Symbol must be an alphanumeric pair like BTCUSDT.'}, status=400)
    if BotConfig.objects.filter(account=account, symbol=symbol).exists():
        return JsonResponse({'ok': False, 'error': f'{symbol} already exists on {account.name}.'}, status=400)

    interval = str(payload.get('interval', '15m')).strip()
    mode = str(payload.get('mode', 'PAPER')).strip().upper()
    if interval not in {k for k, _ in BotConfig.INTERVAL_CHOICES}:
        return JsonResponse({'ok': False, 'error': f'Invalid interval: {interval}.'}, status=400)
    if mode not in {k for k, _ in BotConfig.MODE_CHOICES}:
        return JsonResponse({'ok': False, 'error': f'Invalid mode: {mode}.'}, status=400)

    try:
        fast_ema = int(payload.get('fast_ema_period', 20))
        slow_ema = int(payload.get('slow_ema_period', 50))
        order_amount = Decimal(str(payload.get('order_amount_usdt', '50.00')))
        raw_qty = payload.get('order_qty')
        order_qty = Decimal(str(raw_qty)) if raw_qty is not None and str(raw_qty).strip() != '' else None
    except (ValueError, TypeError, InvalidOperation):
        return JsonResponse({'ok': False, 'error': 'EMA periods, order size, and qty must be valid numbers.'}, status=400)

    if fast_ema < 2 or slow_ema <= fast_ema:
        return JsonResponse({'ok': False, 'error': 'Fast EMA must be >= 2 and strictly less than Slow EMA.'}, status=400)
    if order_amount < Decimal('10.00'):
        return JsonResponse({'ok': False, 'error': 'Order size must be at least $10.00 USDT.'}, status=400)
    if order_qty is not None and order_qty <= Decimal('0'):
        return JsonResponse({'ok': False, 'error': 'Order qty must be greater than 0.'}, status=400)

    bot = BotConfig.objects.create(
        account=account,
        name=str(payload.get('name') or f"{account.name} - {symbol} EMA Bot").strip(),
        symbol=symbol,
        interval=interval,
        fast_ema_period=fast_ema,
        slow_ema_period=slow_ema,
        order_amount_usdt=order_amount,
        order_qty=order_qty,
        mode=mode,
        mirror_to_demo=_parse_bool(payload.get('mirror_to_demo'), account.environment in ('DEMO', 'TESTNET')),
        is_active=_parse_bool(payload.get('is_active'), True),
    )
    return JsonResponse({'ok': True, 'bot_id': bot.id, 'symbol': bot.symbol})


@_superadmin_required(api=True)
@require_POST
def update_bot_view(request, bot_id: int):
    """Update BotConfig trading parameters (including variable fixed order_amount_usdt)."""
    bot = get_object_or_404(BotConfig, id=bot_id)
    try:
        payload = _parse_json_or_post(request)
    except json.JSONDecodeError:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON payload.'}, status=400)

    symbol = str(payload.get('symbol', bot.symbol)).strip().upper()
    interval = str(payload.get('interval', bot.interval)).strip()
    mode = str(payload.get('mode', bot.mode)).strip().upper()

    valid_intervals = {k for k, _ in BotConfig.INTERVAL_CHOICES}
    valid_modes = {k for k, _ in BotConfig.MODE_CHOICES}

    if not symbol or not symbol.isalnum():
        return JsonResponse({'ok': False, 'error': 'Symbol must be an alphanumeric pair like BTCUSDT.'}, status=400)
    if bot.account_id and symbol != bot.symbol:
        if BotConfig.objects.filter(account_id=bot.account_id, symbol=symbol).exclude(id=bot.id).exists():
            return JsonResponse({'ok': False, 'error': f'{symbol} already exists on {bot.account.name}.'}, status=400)
    if interval not in valid_intervals:
        return JsonResponse({'ok': False, 'error': f'Invalid interval: {interval}.'}, status=400)
    if mode not in valid_modes:
        return JsonResponse({'ok': False, 'error': f'Invalid mode: {mode}.'}, status=400)

    try:
        fast_ema = int(payload.get('fast_ema_period', bot.fast_ema_period))
        slow_ema = int(payload.get('slow_ema_period', bot.slow_ema_period))
        order_amount = Decimal(str(payload.get('order_amount_usdt', bot.order_amount_usdt)))
        raw_qty = payload.get('order_qty', bot.order_qty)
        if raw_qty is None or str(raw_qty).strip() == '':
            order_qty = None
        else:
            order_qty = Decimal(str(raw_qty))
    except (ValueError, TypeError, InvalidOperation):
        return JsonResponse({'ok': False, 'error': 'EMA periods, order size, and qty must be valid numbers.'}, status=400)

    if fast_ema < 2 or slow_ema <= fast_ema:
        return JsonResponse({'ok': False, 'error': 'Fast EMA must be >= 2 and strictly less than Slow EMA.'}, status=400)
    if order_amount < Decimal('10.00'):
        return JsonResponse({'ok': False, 'error': 'Order size must be at least $10.00 USDT.'}, status=400)
    if order_qty is not None and order_qty <= Decimal('0'):
        return JsonResponse({'ok': False, 'error': 'Order qty must be greater than 0.'}, status=400)

    bot.symbol = symbol
    bot.interval = interval
    bot.fast_ema_period = fast_ema
    bot.slow_ema_period = slow_ema
    bot.order_amount_usdt = order_amount
    bot.order_qty = order_qty
    bot.mode = mode
    bot.mirror_to_demo = _parse_bool(payload.get('mirror_to_demo'), bot.mirror_to_demo)
    bot.is_active = _parse_bool(payload.get('is_active'), bot.is_active)
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
            'order_qty': float(bot.order_qty) if bot.order_qty is not None else None,
            'mode': bot.mode,
            'mirror_to_demo': bot.mirror_to_demo,
            'is_active': bot.is_active,
        }
    })


@_superadmin_required(api=True)
@require_POST
def toggle_bot_view(request, bot_id: int):
    """Toggle a bot's is_active flag."""
    bot = get_object_or_404(BotConfig, id=bot_id)
    bot.is_active = not bot.is_active
    bot.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'ok': True, 'id': bot.id, 'is_active': bot.is_active})
