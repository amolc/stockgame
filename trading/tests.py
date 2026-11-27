import json
from datetime import datetime, timezone as dt_tz
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import Client as DjangoClient, TestCase
from django.urls import reverse

from trading.models import DEFAULT_BOT_SYMBOLS, BotConfig, SignalLog, Trade, TradingAccount
from trading.services.execution_service import ExecutionService
from trading.tasks import run_all_active_bots


class ExecutionServiceTests(TestCase):
    def setUp(self):
        self.bot = BotConfig.objects.create(
            name="Test EMA Bot",
            symbol="BTCUSDT",
            interval="15m",
            fast_ema_period=20,
            slow_ema_period=50,
            initial_capital=Decimal("1000.00"),
            current_cash=Decimal("1000.00"),
            order_amount_usdt=Decimal("50.00"),
            fee_pct=Decimal("0.0010"),
            mode="PAPER",
            is_active=True,
        )

    def test_insufficient_data_does_not_crash(self):
        res = ExecutionService.process_signal(
            self.bot,
            {"signal": "NONE", "reason": "Insufficient data"},
        )
        self.assertFalse(res["executed"])
        self.assertEqual(res["message"], "Insufficient data")
        self.assertEqual(SignalLog.objects.count(), 0)

    def test_fixed_order_size_paper_buy_sell_preserves_remaining_cash(self):
        t1 = datetime(2026, 11, 25, 10, 0, tzinfo=dt_tz.utc)
        buy_res = ExecutionService.process_signal(
            self.bot,
            {
                "signal": "BUY",
                "timestamp": t1,
                "price": 50000.0,
                "fast_ema": 50100.0,
                "slow_ema": 50000.0,
            },
        )
        self.assertTrue(buy_res["executed"])
        self.bot.refresh_from_db()
        # Only $50 deducted from $1000 -> $950 left
        self.assertEqual(self.bot.current_cash, Decimal("950.00"))
        trade = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertEqual(trade.cost, Decimal("50.0000"))

        # Same candle timestamp is deduplicated
        dup_res = ExecutionService.process_signal(
            self.bot,
            {
                "signal": "BUY",
                "timestamp": t1,
                "price": 50000.0,
                "fast_ema": 50100.0,
                "slow_ema": 50000.0,
            },
        )
        self.assertTrue(dup_res.get("duplicate"))
        self.assertEqual(SignalLog.objects.count(), 1)

        # Next candle SELL at higher price adds net_revenue back to $950
        t2 = datetime(2026, 11, 25, 10, 15, tzinfo=dt_tz.utc)
        sell_res = ExecutionService.process_signal(
            self.bot,
            {
                "signal": "SELL",
                "timestamp": t2,
                "price": 55000.0,
                "fast_ema": 54800.0,
                "slow_ema": 54900.0,
            },
        )
        self.assertTrue(sell_res["executed"])
        self.bot.refresh_from_db()
        trade.refresh_from_db()
        self.assertEqual(trade.status, "CLOSED")
        self.assertTrue(trade.is_win)
        # SELL reverses the position: long is closed and a new SHORT is opened
        short = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertEqual(short.side, "SHORT")
        self.assertGreater(self.bot.current_cash, Decimal("900.00"))

    def test_live_mode_executes_binance_orders_with_mock(self):
        self.bot.mode = "LIVE"
        self.bot.save(update_fields=["mode"])

        mock_binance = MagicMock()
        mock_binance.is_authenticated = True
        mock_binance.round_quantity.side_effect = lambda sym, qty: Decimal("0.00100")
        mock_binance.place_market_order.side_effect = [
            {"orderId": 12345, "executedQty": "0.00100", "cummulativeQuoteQty": "50.00"},
            {"orderId": 12346, "executedQty": "0.00100", "cummulativeQuoteQty": "52.00"},
            {"orderId": 12347, "executedQty": "0.00100", "cummulativeQuoteQty": "52.00"},
        ]

        t1 = datetime(2026, 11, 25, 11, 0, tzinfo=dt_tz.utc)
        buy_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "BUY", "timestamp": t1, "price": 50000.0, "fast_ema": 50100.0, "slow_ema": 50000.0},
            binance=mock_binance,
        )
        self.assertTrue(buy_res["executed"])
        trade = Trade.objects.get(bot_config=self.bot)
        self.assertIn("Binance #12345", trade.notes)

        t2 = datetime(2026, 11, 25, 11, 15, tzinfo=dt_tz.utc)
        sell_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "SELL", "timestamp": t2, "price": 52000.0, "fast_ema": 51800.0, "slow_ema": 51900.0},
            binance=mock_binance,
        )
        self.assertTrue(sell_res["executed"])
        trade.refresh_from_db()
        self.assertEqual(trade.status, "CLOSED")
        self.assertEqual(trade.pnl, Decimal("1.9480"))
        self.assertIn("Binance #12346", trade.notes)
        short = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertEqual(short.side, "SHORT")

    def test_paper_mode_mirrors_signals_to_demo_account(self):
        self.assertTrue(self.bot.mirror_to_demo)

        mock_binance = MagicMock()
        mock_binance.is_authenticated = True
        mock_binance.is_demo = True
        mock_binance.round_quantity.side_effect = lambda sym, qty: Decimal("0.00100")
        mock_binance.place_market_order.side_effect = [
            {"orderId": 900, "executedQty": "0.00100", "cummulativeQuoteQty": "50.00"},
            {"orderId": 901, "executedQty": "0.00100", "cummulativeQuoteQty": "52.00"},
            {"orderId": 902, "executedQty": "0.00100", "cummulativeQuoteQty": "52.00"},
        ]

        t1 = datetime(2026, 11, 25, 14, 0, tzinfo=dt_tz.utc)
        buy_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "BUY", "timestamp": t1, "price": 50000.0, "fast_ema": 50100.0, "slow_ema": 50000.0},
            binance=mock_binance,
        )
        self.assertTrue(buy_res["executed"])
        trade = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertIn("Demo #900", trade.notes)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.current_cash, Decimal("950.00"))
        self.assertEqual(trade.cost, Decimal("50.0000"))

        t2 = datetime(2026, 11, 25, 14, 15, tzinfo=dt_tz.utc)
        sell_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "SELL", "timestamp": t2, "price": 52000.0, "fast_ema": 51800.0, "slow_ema": 51900.0},
            binance=mock_binance,
        )
        self.assertTrue(sell_res["executed"])
        trade.refresh_from_db()
        self.assertEqual(trade.status, "CLOSED")
        self.assertIn("Demo #901", trade.notes)
        short = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertEqual(short.side, "SHORT")
        self.assertIn("Demo #902", short.notes)
        self.assertEqual(mock_binance.place_market_order.call_count, 3)

    def test_paper_mode_does_not_mirror_to_mainnet(self):
        mock_binance = MagicMock()
        mock_binance.is_authenticated = True
        mock_binance.is_demo = False

        t1 = datetime(2026, 11, 25, 15, 0, tzinfo=dt_tz.utc)
        res = ExecutionService.process_signal(
            self.bot,
            {"signal": "BUY", "timestamp": t1, "price": 50000.0, "fast_ema": 50100.0, "slow_ema": 50000.0},
            binance=mock_binance,
        )
        self.assertTrue(res["executed"])
        self.assertEqual(mock_binance.place_market_order.call_count, 0)
        trade = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertIn("(Paper)", trade.notes)

    def test_switching_to_live_syncs_open_paper_position_to_binance(self):
        t1 = datetime(2026, 11, 25, 13, 0, tzinfo=dt_tz.utc)
        buy_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "BUY", "timestamp": t1, "price": 50000.0, "fast_ema": 50100.0, "slow_ema": 50000.0},
        )
        self.assertTrue(buy_res["executed"])
        trade = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertIn("(Paper)", trade.notes)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.current_cash, Decimal("950.00"))

        self.bot.mode = "LIVE"
        self.bot.save(update_fields=["mode"])

        mock_binance = MagicMock()
        mock_binance.is_authenticated = True
        mock_binance.round_quantity.side_effect = lambda sym, qty: Decimal("0.00100")
        mock_binance.place_market_order.return_value = {
            "orderId": 555,
            "executedQty": "0.00100",
            "cummulativeQuoteQty": "49.50",
        }

        t2 = datetime(2026, 11, 25, 13, 15, tzinfo=dt_tz.utc)
        res = ExecutionService.process_signal(
            self.bot,
            {"signal": "NONE", "timestamp": t2, "price": 49500.0, "fast_ema": 49400.0, "slow_ema": 49500.0},
            binance=mock_binance,
        )
        self.assertFalse(res["executed"])

        self.assertEqual(mock_binance.place_market_order.call_count, 1)
        self.assertEqual(mock_binance.place_market_order.call_args.args[1], "BUY")

        trade.refresh_from_db()
        self.bot.refresh_from_db()
        self.assertIn("Binance #555", trade.notes)
        self.assertEqual(trade.quantity, Decimal("0.00100000"))
        self.assertEqual(trade.cost, Decimal("49.5000"))
        self.assertEqual(trade.entry_price, Decimal("49500.0000"))
        self.assertEqual(self.bot.current_cash, Decimal("950.50"))

        # Polling the same candle again must not place a second sync order.
        dup_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "NONE", "timestamp": t2, "price": 49500.0, "fast_ema": 49400.0, "slow_ema": 49500.0},
            binance=mock_binance,
        )
        self.assertTrue(dup_res.get("duplicate"))
        self.assertEqual(mock_binance.place_market_order.call_count, 1)

    def test_paper_short_profit_on_price_drop(self):
        t1 = datetime(2026, 11, 25, 12, 0, tzinfo=dt_tz.utc)
        sell_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "SELL", "timestamp": t1, "price": 50000.0, "fast_ema": 49900.0, "slow_ema": 50000.0},
        )
        self.assertTrue(sell_res["executed"])
        short = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertEqual(short.side, "SHORT")

        t2 = datetime(2026, 11, 25, 12, 15, tzinfo=dt_tz.utc)
        buy_res = ExecutionService.process_signal(
            self.bot,
            {"signal": "BUY", "timestamp": t2, "price": 45000.0, "fast_ema": 45100.0, "slow_ema": 45000.0},
        )
        self.assertTrue(buy_res["executed"])
        short.refresh_from_db()
        self.assertEqual(short.status, "CLOSED")
        self.assertTrue(short.is_win)
        self.assertGreater(short.pnl, Decimal("0"))
        # BUY reversed the short into a fresh LONG
        long_trade = Trade.objects.get(bot_config=self.bot, status="OPEN")
        self.assertEqual(long_trade.side, "LONG")


class DashboardViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("superadmin", "admin@test.local", "pass1234")
        self.client = DjangoClient()
        self.client.force_login(self.admin)
        self.bot = BotConfig.objects.create(
            name="Dashboard Bot",
            symbol="BTCUSDT",
            interval="15m",
            fast_ema_period=20,
            slow_ema_period=50,
            order_amount_usdt=Decimal("50.00"),
            mode="PAPER",
            is_active=True,
        )

    def test_dashboard_renders(self):
        resp = self.client.get(reverse("trading:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Recent Executed Trades")

    def test_update_and_toggle_bot_endpoints(self):
        url = reverse("trading:update_bot", args=[self.bot.id])
        resp = self.client.post(
            url,
            data=json.dumps({
                "symbol": "ethusdt",
                "interval": "5m",
                "fast_ema_period": 10,
                "slow_ema_period": 30,
                "order_amount_usdt": 125,
                "mode": "LIVE",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.symbol, "ETHUSDT")
        self.assertEqual(self.bot.interval, "5m")
        self.assertEqual(self.bot.order_amount_usdt, Decimal("125.00"))
        self.assertEqual(self.bot.mode, "LIVE")

        # Reject invalid order size < $10
        bad = self.client.post(
            url,
            data=json.dumps({"order_amount_usdt": 5}),
            content_type="application/json",
        )
        self.assertEqual(bad.status_code, 400)

        # Toggle active flag
        tresp = self.client.post(reverse("trading:toggle_bot", args=[self.bot.id]))
        self.assertEqual(tresp.status_code, 200)
        self.bot.refresh_from_db()
        self.assertFalse(self.bot.is_active)

    @patch("trading.views.BinanceService")
    def test_api_status_returns_chart_and_kpis(self, mock_bs_cls):
        idx = pd.date_range("2026-11-25", periods=5, freq="15min", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "high": [102.0, 103.0, 104.0, 105.0, 106.0],
                "low": [99.0, 100.0, 101.0, 102.0, 103.0],
                "close": [101.0, 102.0, 103.0, 104.0, 105.0],
                "volume": [10.0, 10.0, 10.0, 10.0, 10.0],
            },
            index=idx,
        )
        inst = mock_bs_cls.return_value
        mock_bs_cls.for_bot.return_value = inst
        inst.is_authenticated = True
        inst.is_demo = True
        inst.demo = False
        inst.testnet = True
        inst.market = "futures"
        inst.get_ticker_price.return_value = 105.0
        inst.get_account_balances.return_value = {"usdt": 5000.0, "asset": 0.1}
        inst.fetch_klines.return_value = df

        resp = self.client.get(reverse("trading:api_status"))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kpis"]["active_bots"], 1)
        self.assertEqual(payload["kpis"]["total_bots"], 1)
        self.assertEqual(payload["bots"][0]["order_amount_usdt"], 50.0)
        self.assertEqual(len(payload["chart"]["timestamps"]), 5)


class BotTasksAndCountTests(TestCase):
    def test_paused_bot_does_not_spawn_duplicate_default_bot(self):
        paused_bot = BotConfig.objects.create(
            name="Paused Bot",
            symbol="BTCUSDT",
            interval="15m",
            is_active=False,
        )
        results = run_all_active_bots()
        self.assertEqual(results, [])
        self.assertEqual(BotConfig.objects.count(), 1)
        self.assertEqual(BotConfig.objects.first().id, paused_bot.id)

    @patch("trading.tasks.run_trading_cycle_for_bot")
    def test_creates_default_bot_only_when_no_bots_exist(self, mock_run_cycle):
        mock_run_cycle.return_value = {"bot": "BTCUSDT Default EMA Bot", "signal": "NONE", "message": "ok"}
        self.assertEqual(BotConfig.objects.count(), 0)
        results = run_all_active_bots()
        self.assertEqual(BotConfig.objects.count(), 1)
        self.assertEqual(len(results), 1)
        mock_run_cycle.assert_called_once()

    @patch("trading.views.BinanceService")
    def test_dashboard_and_kpis_report_active_and_total_bots(self, mock_bs_cls):
        BotConfig.objects.create(name="Bot Active", symbol="BTCUSDT", is_active=True)
        BotConfig.objects.create(name="Bot Paused", symbol="ETHUSDT", is_active=False)

        inst = mock_bs_cls.return_value
        mock_bs_cls.for_bot.return_value = inst
        inst.is_authenticated = False
        inst.is_demo = False
        inst.demo = False
        inst.testnet = False
        inst.market = "futures"
        inst.get_ticker_price.return_value = 50000.0
        inst.get_account_balances.return_value = {"usdt": None, "asset": None}

        User = get_user_model()
        admin = User.objects.create_superuser("superadmin2", "admin2@test.local", "pass1234")
        client = DjangoClient()
        client.force_login(admin)
        dash_resp = client.get(reverse("trading:dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "1 / 2 Active Bot(s)")

        status_resp = client.get(reverse("trading:api_status") + "?chart=0")
        self.assertEqual(status_resp.status_code, 200)
        kpis = status_resp.json()["kpis"]
        self.assertEqual(kpis["active_bots"], 1)
        self.assertEqual(kpis["total_bots"], 2)



class MultiAccountSuperAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("superadmin3", "admin3@test.local", "pass1234")
        self.client = DjangoClient()

    def test_requires_superadmin_login(self):
        resp = self.client.get(reverse("trading:dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

        api_resp = self.client.get(reverse("trading:api_status"))
        self.assertEqual(api_resp.status_code, 401)

    def test_account_secret_encryption_and_seed_6_symbols(self):
        acct = TradingAccount(name="nitin", environment="DEMO", market="futures", leverage=5, api_key="KEY1234567890")
        acct.set_api_secret("SECRET_ABC_XYZ")
        acct.save()

        acct.refresh_from_db()
        self.assertTrue(acct.api_secret_encrypted.startswith("enc:v1:"))
        self.assertNotIn("SECRET_ABC_XYZ", acct.api_secret_encrypted)
        self.assertEqual(acct.get_api_secret(), "SECRET_ABC_XYZ")
        self.assertEqual(acct.masked_api_key, "KEY1...7890")

        created = acct.ensure_default_bots()
        self.assertEqual(len(created), 6)
        self.assertEqual(
            set(acct.bots.values_list("symbol", flat=True)),
            set(DEFAULT_BOT_SYMBOLS),
        )
        # Idempotent on second call
        self.assertEqual(acct.ensure_default_bots(), [])

    @patch("trading.views.BinanceService")
    def test_superadmin_account_endpoints(self, mock_bs_cls):
        self.client.force_login(self.admin)
        mock_bs_cls.from_account.return_value.test_connection.return_value = (True, "Demo Futures connected", 5000.0)

        create_resp = self.client.post(
            reverse("trading:create_account"),
            data=json.dumps({
                "name": "amol",
                "environment": "DEMO",
                "market": "futures",
                "leverage": 5,
                "api_key": "AMOLKEY12345",
                "api_secret": "AMOLSECRET123",
                "seed_symbols": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(create_resp.status_code, 200)
        payload = create_resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["created_symbols"]), 6)

        acct_id = payload["account"]["id"]
        test_resp = self.client.post(reverse("trading:test_account", args=[acct_id]))
        self.assertEqual(test_resp.status_code, 200)
        self.assertTrue(test_resp.json()["ok"])

        acct = TradingAccount.objects.get(id=acct_id)
        self.assertEqual(acct.last_health_status, "OK")

        # Update without changing secret preserves encrypted secret
        upd_resp = self.client.post(
            reverse("trading:update_account", args=[acct_id]),
            data=json.dumps({"name": "amol", "environment": "DEMO", "market": "futures", "leverage": 10}),
            content_type="application/json",
        )
        self.assertEqual(upd_resp.status_code, 200)
        acct.refresh_from_db()
        self.assertEqual(acct.leverage, 10)
        self.assertEqual(acct.get_api_secret(), "AMOLSECRET123")

        # Bulk pause bots on account
        pause_resp = self.client.post(
            reverse("trading:toggle_account_bots", args=[acct_id]),
            data=json.dumps({"action": "pause"}),
            content_type="application/json",
        )
        self.assertEqual(pause_resp.status_code, 200)
        self.assertEqual(acct.bots.filter(is_active=True).count(), 0)

    @patch("trading.tasks.BinanceService")
    def test_paused_account_bots_are_skipped_in_cycle(self, mock_bs_cls):
        acct1 = TradingAccount.objects.create(name="acct1", is_active=True)
        acct2 = TradingAccount.objects.create(name="acct2", is_active=False)
        BotConfig.objects.create(account=acct1, name="A1 BTC", symbol="BTCUSDT", is_active=True)
        BotConfig.objects.create(account=acct2, name="A2 BTC", symbol="BTCUSDT", is_active=True)

        idx = pd.date_range("2026-11-25", periods=60, freq="15min", tz="UTC")
        df = pd.DataFrame(
            {
                "open": [100.0] * 60,
                "high": [101.0] * 60,
                "low": [99.0] * 60,
                "close": [100.0 + i * 0.1 for i in range(60)],
                "volume": [10.0] * 60,
            },
            index=idx,
        )
        inst = mock_bs_cls.for_bot.return_value
        inst.demo = True
        inst.testnet = False
        inst.market = "futures"
        inst.is_authenticated = False
        inst.is_demo = True
        inst.fetch_klines.return_value = df

        results = run_all_active_bots()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["account"], "acct1")


class BinanceWebSocketCacheTests(TestCase):
    def setUp(self):
        from trading.services.binance_ws import ws_manager
        self.ws_manager = ws_manager
        self.ws_manager.clear()

    def tearDown(self):
        self.ws_manager.clear()

    @patch.dict("os.environ", {"BINANCE_USE_WEBSOCKET": "false"})
    @patch("trading.services.binance_service.Client")
    def test_ticker_and_klines_use_ws_cache_without_repeat_rest_calls(self, mock_client_cls):
        from trading.services.binance_service import BinanceService

        mock_client = mock_client_cls.return_value
        mock_client.futures_symbol_ticker.return_value = {"price": "95000.5"}
        mock_client.futures_klines.return_value = [
            [1700000000000, "94000", "95200", "93800", "95000", "12.5", 0, 0, 0, 0, 0, 0],
        ]

        svc = BinanceService(api_key="", api_secret="", demo=False, testnet=False, market="futures", allow_env_credentials=False)
        self.assertAlmostEqual(svc.get_ticker_price("BTCUSDT"), 95000.5)
        self.assertAlmostEqual(svc.get_ticker_price("BTCUSDT"), 95000.5)
        mock_client.futures_symbol_ticker.assert_called_once()

        # Simulate WebSocket miniTicker update
        env_key = (False, False, "futures")
        self.ws_manager._handle_market_message(env_key, {
            "stream": "btcusdt@miniTicker",
            "data": {"e": "24hrMiniTicker", "s": "BTCUSDT", "c": "95123.45"},
        })
        self.assertAlmostEqual(svc.get_ticker_price("BTCUSDT"), 95123.45)

        # Initial REST kline fetch, then served from WS cache
        df1 = svc.fetch_klines("BTCUSDT", interval="15m")
        self.assertEqual(len(df1), 1)
        mock_client.futures_klines.assert_called_once()

        # Simulate WebSocket kline update for a new candle
        self.ws_manager._handle_market_message(env_key, {
            "stream": "btcusdt@kline_15m",
            "data": {
                "e": "kline",
                "s": "BTCUSDT",
                "k": {"t": 1700000900000, "s": "BTCUSDT", "i": "15m", "o": "95000", "h": "95500", "l": "94900", "c": "95400", "v": "8.0"},
            },
        })
        df2 = svc.fetch_klines("BTCUSDT", interval="15m")
        self.assertEqual(len(df2), 2)
        self.assertAlmostEqual(df2["close"].iloc[-1], 95400.0)
        mock_client.futures_klines.assert_called_once()

    @patch.dict("os.environ", {"BINANCE_USE_WEBSOCKET": "false"})
    @patch("trading.services.binance_service.Client")
    def test_account_balances_cached_and_updated_by_user_ws(self, mock_client_cls):
        from trading.services.binance_service import BinanceService

        mock_client = mock_client_cls.return_value
        mock_client.futures_account_balance.return_value = [{"asset": "USDT", "availableBalance": "4000.0"}]
        mock_client.futures_position_information.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.05"},
            {"symbol": "ETHUSDT", "positionAmt": "0.0"},
        ]

        svc = BinanceService(api_key="k1", api_secret="s1", demo=True, testnet=False, market="futures", allow_env_credentials=False)
        b1 = svc.get_account_balances("BTCUSDT")
        b2 = svc.get_account_balances("ETHUSDT")
        self.assertEqual(b1, {"usdt": 4000.0, "asset": 0.05})
        self.assertEqual(b2, {"usdt": 4000.0, "asset": None})
        mock_client.futures_account_balance.assert_called_once()
        mock_client.futures_position_information.assert_called_once()

        # Simulate Futures User Data Stream ACCOUNT_UPDATE
        acct_key = ("k1", True, False, "futures")
        self.ws_manager._handle_user_message(acct_key, True, {
            "e": "ACCOUNT_UPDATE",
            "a": {
                "B": [{"a": "USDT", "cw": "4250.75"}],
                "P": [{"s": "ETHUSDT", "pa": "1.25"}],
            },
        })
        self.assertEqual(svc.get_account_balances("ETHUSDT"), {"usdt": 4250.75, "asset": 1.25})
        mock_client.futures_account_balance.assert_called_once()

    @patch.dict("os.environ", {"BINANCE_USE_WEBSOCKET": "false"})
    @patch("trading.services.binance_service.Client")
    def test_invalid_symbol_error_triggers_cooldown(self, mock_client_cls):
        from trading.services.binance_service import BinanceService

        mock_client = mock_client_cls.return_value
        mock_client.futures_symbol_ticker.side_effect = Exception("APIError(code=-1121): Invalid symbol.")

        svc = BinanceService(api_key="", api_secret="", demo=False, testnet=False, market="futures", allow_env_credentials=False)
        with self.assertRaises(Exception):
            svc.get_ticker_price("TQQQUSDT")
        with self.assertRaises(RuntimeError):
            svc.get_ticker_price("TQQQUSDT")
        mock_client.futures_symbol_ticker.assert_called_once()


class AccountsPageTests(TestCase):
    def test_accounts_page_requires_superadmin_and_lists_accounts(self):
        acct = TradingAccount.objects.create(name="nitin", environment="DEMO", market="futures", leverage=5)
        BotConfig.objects.create(account=acct, name="nitin - BTCUSDT EMA Bot", symbol="BTCUSDT", is_active=True)

        anon_resp = self.client.get(reverse("trading:accounts"))
        self.assertEqual(anon_resp.status_code, 302)
        self.assertIn("/login/", anon_resp["Location"])

        User = get_user_model()
        admin = User.objects.create_superuser("superadmin_acct", "acct@test.local", "pass1234")
        self.client.force_login(admin)

        resp = self.client.get(reverse("trading:accounts"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Managed Trading Accounts")
        self.assertContains(resp, "nitin")
        self.assertContains(resp, "1 / 1 Bots Active")
        self.assertContains(resp, reverse("trading:account_detail", args=[acct.id]))

    def test_account_detail_page_shows_only_that_accounts_bots(self):
        acct1 = TradingAccount.objects.create(name="nitin", environment="DEMO", market="futures", leverage=5)
        acct2 = TradingAccount.objects.create(name="amol", environment="DEMO", market="futures", leverage=5)
        b1 = BotConfig.objects.create(account=acct1, name="nitin - BTCUSDT EMA Bot", symbol="BTCUSDT", is_active=True)
        b2 = BotConfig.objects.create(account=acct2, name="amol - ETHUSDT EMA Bot", symbol="ETHUSDT", is_active=False)
        Trade.objects.create(
            bot_config=b1, symbol="BTCUSDT", status="OPEN", side="LONG",
            entry_time=pd.Timestamp("2026-11-27 10:00:00", tz="UTC"),
            entry_price=Decimal("60000.00"), quantity=Decimal("0.01"), cost=Decimal("600.00"), notes="nitin-trade",
        )
        Trade.objects.create(
            bot_config=b2, symbol="ETHUSDT", status="OPEN", side="SHORT",
            entry_time=pd.Timestamp("2026-11-27 10:00:00", tz="UTC"),
            entry_price=Decimal("2000.00"), quantity=Decimal("0.5"), cost=Decimal("1000.00"), notes="amol-trade",
        )

        User = get_user_model()
        admin = User.objects.create_superuser("superadmin_detail", "detail@test.local", "pass1234")
        self.client.force_login(admin)

        resp = self.client.get(reverse("trading:account_detail", args=[acct1.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="BTCUSDT"')
        self.assertNotContains(resp, 'value="ETHUSDT"')
        self.assertContains(resp, "nitin-trade")
        self.assertNotContains(resp, "amol-trade")

    def test_bot_update_and_execution_uses_fixed_order_qty(self):
        acct = TradingAccount.objects.create(name="qtyacct", environment="PAPER")
        bot = BotConfig.objects.create(account=acct, name="qtybot", symbol="TSLAUSDT", order_amount_usdt=Decimal("50.00"))

        User = get_user_model()
        admin = User.objects.create_superuser("superadmin_qty", "qty@test.local", "pass1234")
        self.client.force_login(admin)

        resp = self.client.post(
            reverse("trading:update_bot", args=[bot.id]),
            data=json.dumps({"symbol": "TSLAUSDT", "interval": "15m", "fast_ema_period": 20, "slow_ema_period": 50, "order_qty": "2.5", "mode": "PAPER", "mirror_to_demo": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        bot.refresh_from_db()
        self.assertEqual(bot.order_qty, Decimal("2.500000"))

        res = ExecutionService.process_signal(bot, {
            "signal": "BUY",
            "timestamp": pd.Timestamp("2026-11-27 12:00:00", tz="UTC"),
            "price": 400.0,
            "fast_ema": 405.0,
            "slow_ema": 395.0,
        })
        self.assertTrue(res["executed"])
        trade = Trade.objects.get(bot_config=bot, status="OPEN")
        self.assertEqual(trade.quantity, Decimal("2.500000"))

    @patch("trading.views.BinanceService")
    def test_chart_bot_id_scopes_dashboard_and_ema_settings(self, mock_bs_cls):
        acct = TradingAccount.objects.create(name="amol", environment="DEMO", market="futures")
        b1 = BotConfig.objects.create(account=acct, name="b1", symbol="BTCUSDT", fast_ema_period=20, slow_ema_period=50, is_active=True)
        b2 = BotConfig.objects.create(account=acct, name="b2", symbol="TSLAUSDT", fast_ema_period=9, slow_ema_period=21, is_active=True)
        Trade.objects.create(
            bot_config=b1, symbol="BTCUSDT", status="OPEN", side="LONG",
            entry_time=pd.Timestamp("2026-11-27 10:00:00", tz="UTC"),
            entry_price=Decimal("60000.00"), quantity=Decimal("0.01"), cost=Decimal("600.00"), notes="btc-only",
        )
        Trade.objects.create(
            bot_config=b2, symbol="TSLAUSDT", status="OPEN", side="LONG",
            entry_time=pd.Timestamp("2026-11-27 10:00:00", tz="UTC"),
            entry_price=Decimal("400.00"), quantity=Decimal("1"), cost=Decimal("400.00"), notes="tsla-only",
        )

        idx = pd.date_range("2026-11-25", periods=30, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "open": [400.0] * 30, "high": [402.0] * 30, "low": [398.0] * 30,
            "close": [400.0 + i for i in range(30)], "volume": [10.0] * 30,
        }, index=idx)
        inst = mock_bs_cls.return_value
        mock_bs_cls.for_bot.return_value = inst
        mock_bs_cls.from_account.return_value = inst
        inst.is_authenticated = False
        inst.is_demo = True
        inst.demo = True
        inst.testnet = False
        inst.market = "futures"
        inst.get_ticker_price.return_value = 429.0
        inst.get_account_balances.return_value = {"usdt": 5000.0, "asset": None}
        inst.fetch_klines.return_value = df

        User = get_user_model()
        admin = User.objects.create_superuser("superadmin_chart", "chart@test.local", "pass1234")
        self.client.force_login(admin)

        dash_resp = self.client.get(reverse("trading:dashboard") + f"?account={acct.id}&bot_id={b2.id}")
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "tsla-only")
        self.assertNotContains(dash_resp, "btc-only")
        self.assertContains(dash_resp, "9 / 21")

        api_resp = self.client.get(reverse("trading:api_status") + f"?account={acct.id}&bot_id={b2.id}&scope=bot")
        self.assertEqual(api_resp.status_code, 200)
        payload = api_resp.json()
        self.assertEqual(payload["primary_bot_id"], b2.id)
        self.assertEqual(payload["chart"]["fast_period"], 9)
        self.assertEqual(payload["chart"]["slow_period"], 21)
        self.assertEqual(len(payload["recent_trades"]), 1)
        self.assertEqual(payload["recent_trades"][0]["symbol"], "TSLAUSDT")

