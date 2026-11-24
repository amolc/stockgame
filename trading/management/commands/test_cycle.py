from django.core.management.base import BaseCommand
from trading.tasks import run_all_active_bots
from trading.models import BotConfig, Trade, SignalLog


class Command(BaseCommand):
    help = "Run a single one-shot trading cycle to verify Binance connection, EMAs, and DB writes"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Testing Binance EMA Trading Task Cycle..."))

        results = run_all_active_bots()
        for r in results:
            self.stdout.write(self.style.SUCCESS(f"Result: {r}"))

        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("DATABASE SNAPSHOT:")
        self.stdout.write(f"Total Bot Configurations: {BotConfig.objects.count()}")
        self.stdout.write(f"Total Trades Logged:     {Trade.objects.count()}")
        self.stdout.write(f"Total Signals Logged:    {SignalLog.objects.count()}")
        self.stdout.write("=" * 50)
