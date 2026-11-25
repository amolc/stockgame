from django.contrib import admin
from trading.models import BotConfig, Trade, SignalLog


@admin.register(BotConfig)
class BotConfigAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'symbol', 'interval', 'fast_ema_period', 'slow_ema_period',
        'order_amount_usdt', 'current_cash', 'mode', 'is_active', 'last_run_at'
    )
    list_filter = ('is_active', 'mode', 'interval', 'symbol')
    search_fields = ('name', 'symbol')
    readonly_fields = ('created_at', 'updated_at', 'last_run_at')


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'symbol', 'status', 'entry_time', 'entry_price',
        'exit_time', 'exit_price', 'quantity', 'pnl', 'pnl_pct', 'is_win'
    )
    list_filter = ('status', 'symbol', 'is_win')
    search_fields = ('symbol', 'notes')
    readonly_fields = ('created_at',)


@admin.register(SignalLog)
class SignalLogAdmin(admin.ModelAdmin):
    list_display = (
        'timestamp', 'symbol', 'signal_type', 'price',
        'fast_ema', 'slow_ema', 'executed'
    )
    list_filter = ('signal_type', 'executed', 'symbol')
    search_fields = ('symbol', 'message')
    readonly_fields = ('created_at',)
