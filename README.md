# MonitorBOT — VPS Monitor Telegram Bot

Бот для мониторинга VPS-сервера через Telegram: CPU, RAM, диск, сеть, диск I/O, аптайм, алерты, ежедневный отчёт.

## Возможности

- 📊 **Статус**: CPU, RAM, диск (/), аптайм, load average, IP
- 🌐 **Сеть**: мгновенная скорость, суммарный трафик за день
- 🗄 **Диск I/O**: скорость чтения/записи за 1 сек
- 🔌 **Соединения**: активные TCP-соединения и слушающие порты
- 🔔 **Алерты**: превышение порогов CPU / RAM / диск / load, восстановление (recovery)
- 🔁 **Уведомление о перезагрузке** сервера
- 📅 **Дневной отчёт** в заданное время
- 🏆 **Топ процессов** по памяти
- 🎛 **Инлайн-кнопки** в Telegram
- 🔐 Авторизация владельца по паролю (`/auth ПАРОЛЬ`)

## Команды

```
/start    — меню
/menu     — меню кнопок
/status   — полный статус
/cpu      — использование CPU
/ram      — память
/disk     — диск
/uptime   — аптайм
/net      — сеть (скорость + трафик за день)
/io       — диск I/O + соединения
/report   — дневной отчёт
/top      — топ процессов по памяти
/alerts   — текущие пороги алертов
/pause    — приостановить алерты
/resume   — возобновить алерты
auth      — авторизация владельца
```

## Установка и запуск

```bash
# зависимости
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# переменная окружения с токеном бота
export TELEGRAM_BOT_TOKEN="ТОКЕН_ОТ_BOTFATHER"

# запуск
./venv/bin/python monitor_bot.py
```

## Конфигурация (config.json)

| Параметр | По умолчанию | Описание |
|---|---|---|
| `owner_token` | — | пароль для первой авторизации владельца |
| `thresholds.cpu` | 70 | порог CPU, % |
| `thresholds.ram` | 80 | порог RAM, % |
| `thresholds.disk` | 85 | порог диска, % |
| `thresholds.load1` | 2.0 | порог load average |
| `thresholds.alert_interval` | 45 | период проверки, сек |
| `thresholds.cooldown` | 300 | пауза между алертами, сек |
| `daily_report.enabled` | true | дневной отчёт |
| `daily_report.hour` / `minute` | 22:00 | время отчёта |

`config.json` и `state.json` создаются автоматически и **не** коммитятся в git.

## Запуск как systemd-сервис

```ini
[Unit]
Description=VPS Monitor Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/monitor_bot
ExecStart=/opt/monitor_bot/venv/bin/python /opt/monitor_bot/monitor_bot.py
Environment=TELEGRAM_BOT_TOKEN=ТОКЕН_ОТ_BOTFATHER
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now monitor-bot
```

## Структура

```
monitor_bot.py   — основной код бота
requirements.txt — зависимости
config.json      — конфигурация (создаётся автоматически, не в git)
state.json       — состояние (трафик за день, алерты; не в git)
```