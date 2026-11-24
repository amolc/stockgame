import time
import sys
from datetime import datetime
from django.core.management.base import BaseCommand
from trading.tasks import run_all_active_bots


class Command(BaseCommand):
    help = "Run the 24/7 background trading worker daemon (managed by PM2)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=30,
            help="Polling interval in seconds (default: 30)"
        )

    def handle(self, *args, **options):
        interval_sec = options['interval']
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("[START] Binance EMA Trading Bot Daemon Started"))
        self.stdout.write(self.style.SUCCESS(f"[POLL] Cycle Interval: Every {interval_sec} seconds"))
        self.stdout.write(self.style.SUCCESS(f"[SYSTEM] Python: {sys.executable}"))
        self.stdout.write(self.style.SUCCESS("=" * 60))

        cycle_count = 0
        try:
            while True:
                cycle_count += 1
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.stdout.write(f"\n[{now_str}] Running Cycle #{cycle_count}...")

                try:
                    results = run_all_active_bots()
                    for r in results:
                        if "error" in r:
                            self.stdout.write(self.style.ERROR(f"  [ERROR] [{r['bot']}]: {r['error']}"))
                        else:
                            sig_style = self.style.SUCCESS if r.get('executed') else self.style.WARNING
                            self.stdout.write(
                                sig_style(
                                    f"  [*] [{r['bot']}] {r['symbol']} ${r.get('latest_price', 0):,.2f} | "
                                    f"Signal: {r.get('signal')} | {r.get('message')}"
                                )
                            )
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  [FAIL] Unexpected cycle error: {e}"))

                # Sleep until next poll interval
                time.sleep(interval_sec)

        except KeyboardInterrupt:
            self.stdout.write(self.style.NOTICE("\n[STOP] Bot daemon stopped gracefully."))
