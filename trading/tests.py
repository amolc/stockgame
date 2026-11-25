import json
from datetime import datetime, timezone as dt_tz
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
from django.test import Client as DjangoClient, TestCase
from django.urls import reverse

from trading.models import BotConfig, SignalLog, Trade
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
        self.assertGreater(self.bot.current_cash, Decimal("1000.00"))

    def test_live_mode_executes_binance_orders_with_mock(self):
        self.bot.mode = "LIVE"
        self.bot.save(update_fields=["mode"])

        mock_binance = MagicMock()
        mock_binance.is_authenticated = True
        mock_binance.round_quantity.side_effect = lambda sym, qty: Decimal("0.00100")
        mock_binance.place_market_order.side_effect = [
            {"orderId": 12345, "executedQty": "0.00100", "cummulativeQuoteQty": "50.00"},
            {"orderId": 12346, "executedQty": "0.00100", "cummulativeQuoteQty": "52.00"},
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
        self.assertEqual(trade.pnl, Decimal("2.0000"))
        self.assertIn("Binance #12346", trade.notes)


class DashboardViewsTests(TestCase):
    def setUp(self):
        self.client = DjangoClient()
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
        self.assertContains(resp, "Order Size (USDT)")

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
        inst.is_authenticated = True
        inst.testnet = True
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
        inst.is_authenticated = False
        inst.testnet = False
        inst.get_ticker_price.return_value = 50000.0
        inst.get_account_balances.return_value = {"usdt": None, "asset": None}

        client = DjangoClient()
        dash_resp = client.get(reverse("trading:dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, "1 / 2 Active Bot(s)")
        self.assertContains(dash_resp, "1 Active / 2 Total")

        status_resp = client.get(reverse("trading:api_status") + "?chart=0")
        self.assertEqual(status_resp.status_code, 200)
        kpis = status_resp.json()["kpis"]
        self.assertEqual(kpis["active_bots"], 1)
        self.assertEqual(kpis["total_bots"], 2)
