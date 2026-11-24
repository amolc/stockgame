from django.db import models
from django.utils import timezone


class BotConfig(models.Model):
    """
    Configuration and runtime state for an automated trading bot instance.
    Can be edited directly via Django Admin.
    """
    MODE_CHOICES = (
        ('PAPER', 'Paper Trading (Simulated)'),
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

    name = models.CharField(max_length=100, default="BTCUSDT EMA Crossover Bot")
    symbol = models.CharField(max_length=20, default="BTCUSDT")
    interval = models.CharField(max_length=5, choices=INTERVAL_CHOICES, default="15m")
    lookback = models.CharField(max_length=50, default="3 days ago UTC")
    
    fast_ema_period = models.PositiveIntegerField(default=20, help_text="Fast EMA span (e.g. 20)")
    slow_ema_period = models.PositiveIntegerField(default=50, help_text="Slow EMA span (e.g. 50)")
    
    initial_capital = models.DecimalField(max_digits=16, decimal_places=2, default=10000.00)
    current_cash = models.DecimalField(max_digits=16, decimal_places=2, default=10000.00)
    fee_pct = models.DecimalField(max_digits=6, decimal_places=4, default=0.0010, help_text="Trading fee (0.0010 = 0.1%)")
    
    is_active = models.BooleanField(default=True, help_text="Toggle bot execution on or off")
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='PAPER')
    
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        status = "Active" if self.is_active else "Paused"
        return f"{self.name} [{self.symbol} {self.interval}] ({status})"

    class Meta:
        verbose_name = "Bot Configuration"
        verbose_name_plural = "Bot Configurations"


class Trade(models.Model):
    """
    Represents an open or closed position executed by the bot.
    """
    STATUS_CHOICES = (
        ('OPEN', 'Open (Holding)'),
        ('CLOSED', 'Closed'),
    )

    bot_config = models.ForeignKey(BotConfig, on_delete=models.CASCADE, related_name='trades')
    symbol = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    
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
        return f"{self.symbol} {self.status} (Entry: ${self.entry_price:.2f}{pnl_str})"

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
