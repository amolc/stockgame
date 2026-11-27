from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from trading.models import TradingAccount, BotConfig, Trade, SignalLog
from trading.services.binance_service import BinanceService


class TradingAccountAdminForm(forms.ModelForm):
    new_api_secret = forms.CharField(
        required=False,
        label="API Secret (update)",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Stored AES-GCM encrypted. Leave blank to keep existing secret.",
    )

    class Meta:
        model = TradingAccount
        fields = (
            'name',
            'slug',
            'environment',
            'market',
            'leverage',
            'api_key',
            'new_api_secret',
            'is_active',
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw_secret = self.cleaned_data.get("new_api_secret")
        if raw_secret:
            instance.set_api_secret(raw_secret)
        if commit:
            instance.save()
        return instance


class BotConfigInline(admin.TabularInline):
    model = BotConfig
    extra = 0
    fields = (
        'symbol', 'interval', 'fast_ema_period', 'slow_ema_period',
        'order_amount_usdt', 'current_cash', 'mode', 'mirror_to_demo', 'is_active',
    )


@admin.register(TradingAccount)
class TradingAccountAdmin(admin.ModelAdmin):
    form = TradingAccountAdminForm
    inlines = [BotConfigInline]
    list_display = (
        'name', 'environment', 'market', 'leverage',
        'masked_api_key_col', 'bots_count_col', 'is_active',
        'last_health_status', 'last_checked_at',
    )
    list_filter = ('environment', 'market', 'is_active', 'last_health_status')
    search_fields = ('name', 'slug', 'api_key')
    readonly_fields = ('last_health_status', 'last_health_message', 'last_checked_at', 'created_at', 'updated_at')
    actions = ('action_test_connection', 'action_seed_default_symbols', 'action_activate', 'action_pause')

    @admin.display(description="API Key")
    def masked_api_key_col(self, obj):
        return obj.masked_api_key

    @admin.display(description="Bots")
    def bots_count_col(self, obj):
        active = obj.bots.filter(is_active=True).count()
        total = obj.bots.count()
        return f"{active}/{total}"

    @admin.action(description="Test Binance API connection")
    def action_test_connection(self, request, queryset):
        for account in queryset:
            ok, msg, _ = BinanceService.from_account(account).test_connection()
            account.last_health_status = "OK" if ok else "ERROR"
            account.last_health_message = msg
            account.last_checked_at = timezone.now()
            account.save(update_fields=['last_health_status', 'last_health_message', 'last_checked_at'])
            level = messages.SUCCESS if ok else messages.ERROR
            self.message_user(request, f"[{account.name}] {msg}", level=level)

    @admin.action(description="Setup 6 default symbols (BTC, SOXL, TQQQ, ETH, NVDA, ANT)")
    def action_seed_default_symbols(self, request, queryset):
        total_created = 0
        for account in queryset:
            created = account.ensure_default_bots()
            total_created += len(created)
        self.message_user(
            request,
            f"Created {total_created} missing symbol bot(s) across {queryset.count()} account(s).",
            level=messages.SUCCESS,
        )

    @admin.action(description="Activate selected accounts")
    def action_activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Activated {updated} account(s).", level=messages.SUCCESS)

    @admin.action(description="Pause selected accounts")
    def action_pause(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Paused {updated} account(s).", level=messages.WARNING)


@admin.register(BotConfig)
class BotConfigAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'account', 'symbol', 'interval', 'fast_ema_period', 'slow_ema_period',
        'order_amount_usdt', 'current_cash', 'mode', 'mirror_to_demo', 'is_active', 'last_run_at'
    )
    list_filter = ('account', 'is_active', 'mode', 'mirror_to_demo', 'interval', 'symbol')
    search_fields = ('name', 'symbol', 'account__name')
    readonly_fields = ('created_at', 'updated_at', 'last_run_at')
    list_select_related = ('account',)


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'account_col', 'symbol', 'side', 'status', 'entry_time', 'entry_price',
        'exit_time', 'exit_price', 'quantity', 'pnl', 'pnl_pct', 'is_win'
    )
    list_filter = ('bot_config__account', 'status', 'side', 'symbol', 'is_win')
    search_fields = ('symbol', 'notes', 'bot_config__account__name')
    readonly_fields = ('created_at',)
    list_select_related = ('bot_config', 'bot_config__account')

    @admin.display(description="Account")
    def account_col(self, obj):
        return obj.bot_config.account.name if obj.bot_config and obj.bot_config.account_id else "-"


@admin.register(SignalLog)
class SignalLogAdmin(admin.ModelAdmin):
    list_display = (
        'timestamp', 'account_col', 'symbol', 'signal_type', 'price',
        'fast_ema', 'slow_ema', 'executed'
    )
    list_filter = ('bot_config__account', 'signal_type', 'executed', 'symbol')
    search_fields = ('symbol', 'message', 'bot_config__account__name')
    readonly_fields = ('created_at',)
    list_select_related = ('bot_config', 'bot_config__account')

    @admin.display(description="Account")
    def account_col(self, obj):
        return obj.bot_config.account.name if obj.bot_config and obj.bot_config.account_id else "-"
