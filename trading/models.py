from decimal import Decimal
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from trading.crypto import decrypt_secret, encrypt_secret

DEFAULT_BOT_SYMBOLS = (
    "BTCUSDT",
    "SOXLUSDT",
    "QQQUSDT",
    "ETHUSDT",
    "NVDAUSDT",
    "TSLAUSDT",
)


class TradingAccount(models.Model):
    """
    Represents an isolated Binance trading account (credentials, market, environment).
    Managed by Super Admin; each account owns its own set of BotConfig instances.
    """
    ENVIRONMENT_CHOICES = (
        ('PAPER', 'Paper Only (No Exchange Orders)'),
        ('DEMO', 'Binance Demo (demo.binance.com)'),
        ('TESTNET', 'Binance Testnet'),
        ('LIVE', 'Binance Live (Mainnet)'),
    )

    MARKET_CHOICES = (
        ('futures', 'USDⓈ-M Futures'),
        ('spot', 'Spot'),
    )

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    api_key = models.CharField(max_length=255, blank=True, default="")
    api_secret_encrypted = models.TextField(blank=True, default="")

    environment = models.CharField(max_length=16, choices=ENVIRONMENT_CHOICES, default='DEMO')
    market = models.CharField(max_length=16, choices=MARKET_CHOICES, default='futures')
    leverage = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True, help_text="Master toggle for all bots in this account")

    last_health_status = models.CharField(max_length=32, blank=True, default="")
    last_health_message = models.TextField(blank=True, default="")
    last_checked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "account"
            candidate = base
            idx = 2
            while TradingAccount.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{idx}"
                idx += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    def set_api_secret(self, raw_secret: str) -> None:
        self.api_secret_encrypted = encrypt_secret((raw_secret or "").strip())

    def get_api_secret(self) -> str:
        return decrypt_secret(self.api_secret_encrypted)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.get_api_secret())

    @property
    def masked_api_key(self) -> str:
        if not self.api_key:
            return "Not configured"
        if len(self.api_key) <= 10:
            return self.api_key[:2] + "..." + self.api_key[-2:]
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"

    def ensure_default_bots(self, symbols=DEFAULT_BOT_SYMBOLS) -> list["BotConfig"]:
        """Create missing BotConfig rows for all standard symbols using BTCUSDT defaults."""
        created = []
        existing = {
            sym.upper()
            for sym in self.bots.values_list("symbol", flat=True)
        }
        mirror = self.environment in ("DEMO", "TESTNET")
        for raw_sym in symbols:
            sym = raw_sym.strip().upper()
            if sym in existing:
                continue
            bot = BotConfig.objects.create(
                account=self,
                name=f"{self.name} - {sym} EMA Bot",
                symbol=sym,
                interval="15m",
                lookback="3 days ago UTC",
                fast_ema_period=20,
                slow_ema_period=50,
                initial_capital=Decimal("10000.00"),
                current_cash=Decimal("10000.00"),
                order_amount_usdt=Decimal("50.00"),
                fee_pct=Decimal("0.0010"),
                mode="DEMO" if mirror else "PAPER",
                mirror_to_demo=mirror,
                is_active=True,
            )
            created.append(bot)
            existing.add(sym)
        return created

    def __str__(self):
        status = "Active" if self.is_active else "Paused"
        return f"{self.name} [{self.environment} / {self.market}] ({status})"

    class Meta:
        ordering = ['id']
        verbose_name = "Trading Account"
        verbose_name_plural = "Trading Accounts"


class BotConfig(models.Model):
    """
    Configuration and runtime state for an automated trading bot instance.
    Can be edited directly via Django Admin or the Super Admin dashboard.
    """
    MODE_CHOICES = (
        ('PAPER', 'Paper Trading (Database Only)'),
        ('DEMO', 'Demo Account (Binance Demo)'),
        ('LIVE', 'Live Execution (Binance API)'),
    )

    INTERVAL_CHOICES = (
        ('1m', '1 Minute'),
        ('5m', '5 Minutes'),
        ('15m', '15 Minutes'),
        ('1h', '1 Hour'),
        ('4h', '4 Hours'),
        ('1d', '1 Day'),
    )

    account = models.ForeignKey(
        TradingAccount,
        on_delete=models.CASCADE,
        related_name='bots',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100, default="BTCUSDT EMA Crossover Bot")
    symbol = models.CharField(max_length=20, default="BTCUSDT")
    interval = models.CharField(max_length=5, choices=INTERVAL_CHOICES, default="15m")
    lookback = models.CharField(max_length=50, default="3 days ago UTC")

    fast_ema_period = models.PositiveIntegerField(default=20, help_text="Fast EMA span (e.g. 20)")
    slow_ema_period = models.PositiveIntegerField(default=50, help_text="Slow EMA span (e.g. 50)")

    initial_capital = models.DecimalField(max_digits=16, decimal_places=2, default=10000.00)
    current_cash = models.DecimalField(max_digits=16, decimal_places=2, default=10000.00)
    order_amount_usdt = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        default=50.00,
        help_text="Fixed USDT order size per automated BUY (minimum $10)"
    )
    order_qty = models.DecimalField(
        max_digits=16,
        decimal_places=6,
        null=True,
        blank=True,
        default=None,
        help_text="Fixed base-asset quantity per order (overrides order_amount_usdt when set)"
    )
    fee_pct = models.DecimalField(max_digits=6, decimal_places=4, default=0.0010, help_text="Trading fee (0.0010 = 0.1%)")

    is_active = models.BooleanField(default=True, help_text="Toggle bot execution on or off")
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='PAPER')
    mirror_to_demo = models.BooleanField(
        default=True,
        help_text="In PAPER mode, mirror signals to the Binance Demo/Testnet account (requires demo/testnet API keys)"
    )

    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = "Active" if self.is_active else "Paused"
        acct = f"{self.account.name} | " if self.account_id else ""
        return f"{acct}{self.name} [{self.symbol} {self.interval}] ({status})"

    class Meta:
        verbose_name = "Bot Configuration"
        verbose_name_plural = "Bot Configurations"
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'symbol'],
                condition=Q(account__isnull=False),
                name='uniq_account_symbol',
            )
        ]


class Trade(models.Model):
    """
    Represents an open or closed position executed by the bot.
    """
    STATUS_CHOICES = (
        ('OPEN', 'Open (Holding)'),
        ('CLOSED', 'Closed'),
    )

    SIDE_CHOICES = (
        ('LONG', 'Long'),
        ('SHORT', 'Short'),
    )

    bot_config = models.ForeignKey(BotConfig, on_delete=models.CASCADE, related_name='trades')
    symbol = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    side = models.CharField(max_length=5, choices=SIDE_CHOICES, default='LONG')

    entry_time = models.DateTimeField(default=timezone.now)
    entry_price = models.DecimalField(max_digits=16, decimal_places=4)
    quantity = models.DecimalField(max_digits=16, decimal_places=8)
    cost = models.DecimalField(max_digits=16, decimal_places=4)

    exit_time = models.DateTimeField(null=True, blank=True)
    exit_price = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    net_revenue = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    pnl = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    pnl_pct = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_win = models.BooleanField(null=True, blank=True)

    notes = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        pnl_str = f" | PnL: ${self.pnl:.2f}" if self.pnl is not None else ""
        return f"{self.symbol} {self.side} {self.status} (Entry: ${self.entry_price:.2f}{pnl_str})"

    class Meta:
        ordering = ['-entry_time']
        verbose_name = "Trade"
        verbose_name_plural = "Trades"


class SignalLog(models.Model):
    """
    Logs every technical indicator signal evaluated by the background task.
    """
    SIGNAL_CHOICES = (
        ('BUY', 'BUY (Golden Cross)'),
        ('SELL', 'SELL (Death Cross)'),
        ('NONE', 'NONE (Hold)'),
    )

    bot_config = models.ForeignKey(BotConfig, on_delete=models.CASCADE, related_name='signals')
    timestamp = models.DateTimeField()
    symbol = models.CharField(max_length=20)
    signal_type = models.CharField(max_length=10, choices=SIGNAL_CHOICES)
    price = models.DecimalField(max_digits=16, decimal_places=4)
    fast_ema = models.DecimalField(max_digits=16, decimal_places=4)
    slow_ema = models.DecimalField(max_digits=16, decimal_places=4)
    executed = models.BooleanField(default=False)
    message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.timestamp.strftime('%Y-%m-%d %H:%M')}] {self.symbol} {self.signal_type} @ ${self.price:.2f}"

    class Meta:
        ordering = ['-timestamp']
        verbose_name = "Signal Log"
        verbose_name_plural = "Signal Logs"
