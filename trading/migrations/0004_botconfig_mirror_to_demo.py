from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trading', '0003_trade_side'),
    ]

    operations = [
        migrations.AddField(
            model_name='botconfig',
            name='mirror_to_demo',
            field=models.BooleanField(
                default=True,
                help_text=(
                    "In PAPER mode, mirror signals to the Binance Demo/Testnet "
                    "account (requires demo/testnet API keys)"
                ),
            ),
        ),
    ]
