/**
 * PM2 Process Manager Configuration for StockGame
 * Controls 24/7 background worker and Django web server.
 *
 * Usage:
 *   pm2 start ecosystem.config.js   (Start all background apps)
 *   pm2 status                      (Check status & CPU/RAM)
 *   pm2 logs                        (View live stream of trade execution)
 *   pm2 stop all                    (Stop processes)
 */

module.exports = {
  apps: [
    {
      name: "stockgame-worker",
      script: "manage.py",
      args: "run_bot --interval 30",
      // Uses the Python virtual environment interpreter
      interpreter: process.platform === "win32" ? "./venv/Scripts/python.exe" : "./venv/bin/python",
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
      name: "stockgame-web",
      script: "manage.py",
      args: "runserver 0.0.0.0:8080",
      interpreter: process.platform === "win32" ? "./venv/Scripts/python.exe" : "./venv/bin/python",
      autorestart: true,
      watch: false,
      max_memory_restart: "300M",
      env: {
        PYTHONUNBUFFERED: "1",
        DJANGO_SETTINGS_MODULE: "stockgame.settings"
      }
    }
  ]
};
