import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from trading.models import BotConfig, TradingAccount
from trading.services.binance_service import BinanceService


class Command(BaseCommand):
    help = "Seed Super Admin user, TradingAccounts, and default 6 symbol bots per account."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument("--password", default="admin123")
        parser.add_argument("--nitin-key", default=os.getenv("NITIN_API_KEY", ""))
        parser.add_argument("--nitin-secret", default=os.getenv("NITIN_API_SECRET", ""))
        parser.add_argument("--amol-key", default=os.getenv("AMOL_API_KEY", os.getenv("BINANCE_API_KEY", "")))
        parser.add_argument("--amol-secret", default=os.getenv("AMOL_API_SECRET", os.getenv("BINANCE_API_SECRET", "")))
        parser.add_argument("--skip-test", action="store_true", help="Skip live Binance connection test")

    def handle(self, *args, **options):
        User = get_user_model()
        if not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser(
                username=options["username"],
                email="admin@stockgame.local",
                password=options["password"],
            )
            self.stdout.write(self.style.SUCCESS(f"Created superuser '{options['username']}'"))
        else:
            self.stdout.write("Superuser already exists; skipping creation.")

        accounts_spec = [
            ("nitin", options["nitin_key"], options["nitin_secret"]),
            ("amol", options["amol_key"], options["amol_secret"]),
        ]

        accounts_by_name = {}
        for name, api_key, api_secret in accounts_spec:
            acct, created = TradingAccount.objects.get_or_create(
                name=name,
                defaults={
                    "environment": "DEMO",
                    "market": "futures",
                    "leverage": 5,
                    "is_active": True,
                },
            )
            updated_fields = []
            if api_key and acct.api_key != api_key:
                acct.api_key = api_key
                updated_fields.append("api_key")
            if api_secret:
                acct.set_api_secret(api_secret)
                updated_fields.append("api_secret_encrypted")
            if updated_fields:
                acct.save(update_fields=updated_fields + ["updated_at"])
            accounts_by_name[name] = acct
            verb = "Created" if created else "Updated"
            self.stdout.write(self.style.SUCCESS(f"{verb} account '{name}' ({acct.environment}/{acct.market})"))

        # Attach any orphan BotConfig (like initial BTCUSDT bot #1) to amol if amol has no BTCUSDT yet
        amol_acct = accounts_by_name["amol"]
        orphan_bots = BotConfig.objects.filter(account__isnull=True)
        for bot in orphan_bots:
            if not BotConfig.objects.filter(account=amol_acct, symbol=bot.symbol).exists():
                bot.account = amol_acct
                bot.name = f"{amol_acct.name} - {bot.symbol} EMA Bot"
                bot.save(update_fields=["account", "name", "updated_at"])
                self.stdout.write(f"Linked existing bot #{bot.id} ({bot.symbol}) -> '{amol_acct.name}'")

        for name, acct in accounts_by_name.items():
            created_bots = acct.ensure_default_bots()
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{name}] Total bots: {acct.bots.count()} (newly created: {[b.symbol for b in created_bots]})"
                )
            )

            if not options["skip_test"]:
                ok, msg, _ = BinanceService.from_account(acct).test_connection()
                acct.last_health_status = "OK" if ok else "ERROR"
                acct.last_health_message = msg
                acct.last_checked_at = timezone.now()
                acct.save(update_fields=["last_health_status", "last_health_message", "last_checked_at"])
                style = self.style.SUCCESS if ok else self.style.ERROR
                self.stdout.write(style(f"[{name}] API Test ({acct.last_health_status}): {msg}"))
