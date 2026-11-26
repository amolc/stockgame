/**
 * PM2 Process Manager Configuration for StockGame
 *
 * - `stockgame`     : web app via runstockgame.sh (migrations + Django on 127.0.0.1:15500)
 * - `stockgame-bot` : trading daemon via runbot.sh (manage.py run_bot)
 *
 * Usage:
 *   pm2 start ecosystem.config.js   (Start both apps)
 *   pm2 status                      (Check status & CPU/RAM)
 *   pm2 logs                        (View live logs)
 *   pm2 restart all                 (Restart after a deploy)
 *   pm2 restart stockgame-bot       (Restart only the bot)
 *   pm2 stop all                    (Stop processes)
 */

module.exports = {
  apps: [
    {
      name: "stockgame",
      script: "./runstockgame.sh",
      interpreter: "bash",
      cwd: __dirname,
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1",
        DJANGO_SETTINGS_MODULE: "stockgame.settings"
      }
    },
    {
      name: "stockgame-bot",
      script: "./runbot.sh",
      interpreter: "bash",
      cwd: __dirname,
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1",
        DJANGO_SETTINGS_MODULE: "stockgame.settings",
        BOT_INTERVAL: "60"
      }
    }
  ]
};
