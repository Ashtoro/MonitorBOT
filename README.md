# 📡 MonitorBOT — VPS Monitor Telegram Bot

> Bot for monitoring your VPS/VPS server straight from Telegram:
> CPU, RAM, disk, network, disk I/O, uptime, alerts, daily reports.

**MonitorBOT** — это Telegram-бот, который следит за вашим сервером и шлёт метрики и уведомления прямо в ваш мессенджер. Он работает на самом сервере, читает показатели системы с помощью `psutil` и выводит их в Telegram по командам, кнопкам и автоматическим алертам.

**MonitorBOT** is a Telegram bot that watches your server and sends metrics & notifications straight to your chat. It runs **on the server itself**, reads system stats via `psutil`, and pushes them to Telegram through commands, inline buttons, and automatic alerts.

---

## 🌍 README
- [Русский](#-русский-документация) — полная документация на русском
- [English](#-english-documentation) — full documentation in English

---

# 🇷🇺 Русский — документация

## Содержание
1. [Что умеет бот](#что-умеет-бот)
2. [Как это работает (архитектура)](#как-это-работает-архитектура)
3. [Начало работы за 5 минут](#начало-работы-за-5-минут)
4. [Подробная установка шаг за шагом](#подробная-установка-шаг-за-шагом)
5. [Запуск как systemd-сервис (автозапуск)](#запуск-как-systemd-сервис-автозапуск)
6. [Конфигурация (config.json)](#конфигурация-configjson)
7. [Команды и кнопки](#команды-и-кнопки)
8. [Как работает защита доступа](#как-работает-защита-доступа)
9. [Автоматические алерты](#автоматические-алерты)
10. [Решение проблем (FAQ)](#решение-проблем-faq)

---

## Что умеет бот

- 📊 **Полный статус** одним экраном: CPU, RAM, диск (/) , аптайм, load average, IP сервера.
- 🖇 **CPU** — процент загрузки + график-полоска.
- 🧠 **RAM** — использовано / всего / процент.
- 🗂 **Диск** — заполнение корневого раздела.
- ⏱ **Аптайм** — сколько сервер работает без перезагрузки + время последнего запуска.
- 🌐 **Сеть** — мгновенная скорость приёма/передачи (замер 1 сек) и суммарный трафик за день.
- 🗄 **Диск I/O** — скорость чтения/записи.
- 🔌 **Соединения** — число активных TCP-соединений и слушающих портов.
- 🔔 **Алерты** — автоматические уведомления, когда CPU / RAM / диск / load превышают порог.
- ✅ **Recovery-уведомления** — сообщение, когда всё вернулось в норму.
- 🔁 **Уведомление о перезагрузке** — бот сразу пишет, если сервер перезапустился.
- 📅 **Дневной отчёт** — сводка в заданное время (по умолчанию 22:00).
- 🏆 **Топ процессов** по памяти.
- 🎛 **Инлайн-кнопки** — всё управление в один тап.
- 🔐 **Авторизация владельца** по паролю — чужой не увидит метрики.

---

## Как это работает (архитектура)

```
┌──────────────────────────┐        HTTPS polling        ┌─────────────────────┐
│  Telegram (ваш чат)      │ ◄────────────────────────►│  Telegram Bot API    │
│  команды /status /cpu    │                            └──────────┬──────────┘
└──────────────────────────┘                                       ▲
                                                                   │ long polling (getUpdates)
                    ┌──────────────────────────────────────────────┘
                    │
        ┌───────────▼────────────────────────────┐
        │   monitor_bot.py  (запущен НА сервере)  │
        │  python-telegram-bot  (PTB v22+)        │
        │  · обработка команд и кнопок            │
        │  · фоновые задачи (jobs):               │
        │      - проверка алертов каждые N сек    │
        │      - подсчёт трафика каждые 30 сек    │
        │      - дневной отчёт по расписанию      │
        └───────────▲────────────────┬────────────┘
                    │ psutil         │ чтение/запись
                    ▼                ▼
        ┌─────────────────────┐  config.json (настройки)
        │  Система (CPU, RAM, │  state.json  (трафик за день,
        │  диск, сеть, диски) │              колёса алертов)
        └─────────────────────┘
```

Ключевые моменты:
- **Бот живёт на самом сервере** — ему не нужен внешний хостинг. Он ходит к Telegram через long polling (сам запрашивает команды), поэтому NAT/фаервол не помеха.
- Все системные показатели берутся из библиотеки `psutil` (нативное и кроссплатформенное решение).
- Есть **фоновый планировщик** (APScheduler через `job_queue` PTB), который запускает проверку алертов и накопление трафика.
- Настройки хранятся в `config.json`, состояние (суточный трафик, факт алертов) — в `state.json`. Оба файла создаются автоматически и не коммитятся в git.

---

## Начало работы за 5 минут

Нужно всего 3 шага — подходит даже для полного новичка.

### Шаг 1. Создайте своего бота в Telegram
1. Откройте **@BotFather** в Telegram.
2. Отправьте `/newbot`.
3. Придумайте имя и username (username должен заканчиваться на `bot`, например `mymonitor_bot`).
4. BotFather выдаст **токен** — строку вида `123456:ABC-DEF...`. **Сохраните его, он понадобится.**

### Шаг 2. Подготовьте сервер
Вам нужен Linux-сервер (Ubuntu/Debian/CentOS) с Python 3.8+ и доступом по SSH (root или sudo). Проверьте:

```bash
python3 --version    # должно быть 3.8 или новее
```

Если нет — установите:
```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y python3 python3-venv git
```

### Шаг 3. Скачайте проект и запустите

```bash
# 1. Клонируем репозиторий
git clone https://github.com/Ashtoro/MonitorBOT.git
cd MonitorBOT

# 2. Создаём виртуальное окружение (изоляция зависимостей)
python3 -m venv venv

# 3. Ставим зависимости
./venv/bin/pip install -r requirements.txt

# 4. Задаём токен бота (ВАШ, из BotFather)
export TELEGRAM_BOT_TOKEN="ВАШ_ТОКЕН_ОТ_BOTFATHER"

# 5. Запускаем
./venv/bin/python monitor_bot.py
```

Увидите сообщение `[INFO] Бот запущен. Интервал проверки 45с` — всё работает!

Бот живет в чате с *вашим* ботом в Telegram. Откройте его и нажмите **Start**.

---

## Подробная установка шаг за шагом

### Скачивание
```bash
git clone https://github.com/Ashtoro/MonitorBOT.git
cd MonitorBOT
```

### Виртуальное окружение (зачем это нужно)
Venv изолирует зависимости проекта от системных пакетов — так вы не сломаете Python на сервере и не словите конфликты версий.

```bash
python3 -m venv venv
```

### Установка зависимостей
```bash
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

Что ставится (см. `requirements.txt`):
- `python-telegram-bot[job-queue]` — библиотека Telegram-бота (версия **22.x**, обязательна версия 21+).
- `psutil` — чтение системных метрик (CPU, RAM, диск, сеть).

> ⚠️ **Важно:** для работы фоновых задач нужен именно вариант `[job-queue]` (он тянет APScheduler). Обычный `pip install python-telegram-bot` не даст планировщик.

### Первый запуск
```bash
export TELEGRAM_BOT_TOKEN="ВАШ_ТОКЕН"
./venv/bin/python monitor_bot.py
```

Проверьте логи: сообщение `[INFO] Бот запущен...` — успех.

### Авторизация владельца в боте
1. Откройте чат с ботом в Telegram, отправьте `/start`.
2. Бот покажет ваш **chat_id** и попросит пароль владельца.
3. Пароль владельца задаётся в **config.json** (`owner_token`). Подробнее — в разделе [Конфигурация](#конфигурация-configjson).
4. Отправьте в чат: `/auth ВАШ_ПАРОЛЬ` — после этого вы станете владельцем и получите доступ ко всем командам.

---

## Запуск как systemd-сервис (автозапуск)

Чтобы бот **сам запускался после перезагрузки сервера** и перезапускался при сбоях, используйте systemd. Это стандартный способ для Linux.

1. Создайте файл сервиса. В нём обязательно подставьте **свой токен**:

```bash
sudo tee /etc/systemd/system/monitor-bot.service > /dev/null <<'EOF'
[Unit]
Description=VPS Monitor Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/MonitorBOT
ExecStart=/root/MonitorBOT/venv/bin/python /root/MonitorBOT/monitor_bot.py
Environment=TELEGRAM_BOT_TOKEN=ВАШ_ТОКЕН_ОТ_BOTFATHER
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

> Пути `/root/MonitorBOT/...` замените на реальные, куда вы склонировали проект. Токен «зашивается» через `Environment=`, как и при ручном запуске.

2. Активируйте и запустите:

```bash
sudo systemctl daemon-reload
sudo systemctl enable monitor-bot   # автозапуск при загрузке
sudo systemctl start monitor-bot    # запустить сейчас
```

3. Проверьте состояние:

```bash
systemctl status monitor-bot        # должно быть active (running)
systemctl is-active monitor-bot     # выведет: active
```

Полезные команды:
```bash
journalctl -u monitor-bot -f        # следить за логами в реальном времени
journalctl -u monitor-bot -n 50     # последние 50 строк логов
systemctl restart monitor-bot       # перезапустить
systemctl stop monitor-bot          # остановить
```

Благодаря `Restart=always` systemd сам поднимет бота, если он упадёт.

---

## Конфигурация (config.json)

При запуске бот создаёт `config.json` рядом с скриптом. Все настройки в нём.

Пример:

```json
{
  "authorized_users": [],
  "owner_token": "ВАШ_СЕКРЕТНЫЙ_ПАРОЛЬ",
  "alerts_enabled": true,
  "daily_report": { "enabled": true, "hour": 22, "minute": 0 },
  "thresholds": {
    "cpu": 70,
    "ram": 80,
    "disk": 85,
    "load1": 2.0,
    "alert_interval": 45,
    "cooldown": 300
  }
}
```

### Описание полей

| Параметр | По умолчанию | Описание |
|---|---|---|
| `authorized_users` | `[]` | Список Telegram **chat_id**, кому разрешён доступ. Заполняется автоматически после `/auth`, но можно вписать вручную. |
| `owner_token` | `""` | **Пароль владельца.** Первый пользователь, отправивший `/auth <этот пароль>`, становится владельцем. Смените дефолт на свой! |
| `alerts_enabled` | `true` | Глобальный переключатель алертов. Можно менять командой `/pause` и `/resume`. |
| `daily_report.enabled` | `true` | Включить дневной отчёт. |
| `daily_report.hour` / `minute` | `22:00` | Время отправки ежедневного отчёта (локальный пояс сервера). |
| `thresholds.cpu` | `70` | Порог CPU в %. Алерт, когда выше. |
| `thresholds.ram` | `80` | Порог RAM в %. |
| `thresholds.disk` | `85` | Порог заполнения диска (/) в %. |
| `thresholds.load1` | `2.0` | Порог load average (1 мин). Высокие показатели — алерт. |
| `thresholds.alert_interval` | `45` | Период проверки состояния, сек. |
| `thresholds.cooldown` | `300` | Минимальная пауза между повторными алертами по одному каналу, сек. |

После редактирования config.json перезапустите бота (`systemctl restart monitor-bot` или Ctrl+C → запуск заново).

---

## Команды и кнопки

### Команды (вводятся в чат)

| Команда | Описание |
|---|---|
| `/start` | Главное меню и клавиатура с кнопками |
| `/menu`, `/help` | Показать меню и список команд |
| `/auth <пароль>` | Авторизоваться владельцем |
| `/status` | Полный статус сервера |
| `/cpu` | Загрузка CPU + load average |
| `/ram` | Использование памяти |
| `/disk` | Заполнение диска |
| `/uptime` | Аптайм и время последнего запуска |
| `/net` | Скорость сети + трафик за день |
| `/io` | Диск I/O + активные соединения |
| `/report` | Дневной отчёт (сейчас) |
| `/top` | Топ-5 процессов по памяти |
| `/alerts` | Текущие пороги алертов |
| `/pause` | Приостановить алерты |
| `/resume` | Возобновить алерты |

### Инлайн-кнопки

После `/start` (или под любым сообщением с метриками) доступны кнопки:

```
🖥 Статус
🖇 CPU | 🧠 RAM
🗂 Диск | ⏱ Аптайм
🌐 Сеть | 🗄 Диск I/O | 🔌 Соед
🏆 Топ | 📊 Отчёт | 🔔 Алерты
```

Тап по кнопке сразу показывает запрошенную метрику — команды вводить не нужно.

---

## Как работает защита доступа

- Пока вы **не** авторизованы, бот не показывает метрики — только приветствие с `chat_id` и просьбу ввести пароль `/auth`.
- Пароль задаётся в `config.json` (`owner_token`).
- После успешного `/auth` ваш **chat_id** добавляется в `authorized_users` и сохраняется в config — при перезапуске бота доступ сохраняется.
- Все, кто отправил `/auth` правильно, получают полный доступ. Поэтому пароль держите в секрете.

> 💡 **Совет:** сразу после установки смените `owner_token` на свой сложный пароль, иначе любой сможет авторизоваться.

---

## Автоматические алерты

Каждые `alert_interval` секунд (по умолчанию 45) бот проверяет состояние:

- **CPU** ≥ порога → `💥 CPU перегруз — 92.0%`
- **RAM** ≥ порога → `🧨 RAM перегруз — 1.2 / 1.0 GB (95.0%)`
- **Диск** ≥ порога → `💾 Диск почти полон — 91.0%`
- **Load** ≥ порога → `⚡ Высокая нагрузка — load1 3.50`

Когда значение падает ниже порога, бот шлёт восстановление:
- `✅ Нормализовано (CPU). Текущее значение: 40.0%`

Защита от спама: между алертами по одному каналу выдерживается пауза `cooldown` (300 сек), даже если значение всё ещё выше порога.

Если сервер **перезагрузился**, бот при старте шлёт:
- `🔁 Сервер перезагрузился! ...`

---

## Решение проблем (FAQ)

**Бот не отвечает?**
- Проверьте, что токен верный (не забыли `export TELEGRAM_BOT_TOKEN` при ручном запуске или токен в systemd-файле).
- Проверьте статус сервиса: `systemctl status monitor-bot`.
- Посмотрите логи: `journalctl -u monitor-bot -n 50`.
- Ошибка `Conflict: terminated by other getUpdates request` — значит, запущено **два** экземпляра бота. Остановите лишний: `pkill -f monitor_bot.py`, затем перезапустите сервис.

**Ошибка `No JobQueue set up` / заданий не выполняются?**
- Ставьте `python-telegram-bot[job-queue]` (см. `requirements.txt`), а не голый пакет.

**Как сменить пароль владельца?**
- Отредактируйте `owner_token` в config.json и перезапустите бота.

**Как добавить второго владельца?**
- Пусть он отправит боту `/auth <пароль>` (если знает пароль), либо впишите его `chat_id` в `authorized_users`.

**Бот выводит IP как `?`?**
- Это нормально для некоторых сетей. Используется внешний IP-адрес сетевого интерфейса.

**Могу ли я запускать на Windows?**
- Да, команды `/status /cpu /ram /disk` и алерты работают. Счётчик активных соединений ориентирован на Linux (`/proc`) — на Windows `/io` покажет 0 соединений, но остальное работает.

---

# 🇬🇧 English — Documentation

## Table of contents
1. [What the bot does](#what-the-bot-does)
2. [How it works (architecture)](#architecture)
3. [Quick start in 5 minutes](#quick-start-in-5-minutes)
4. [Full step-by-step install](#full-step-by-step-install)
5. [Run as a systemd service (auto-start)](#run-as-a-systemd-service-auto-start)
6. [Configuration (config.json)](#configuration-configjson)
7. [Commands & buttons](#commands--buttons)
8. [Access protection](#access-protection)
9. [Automatic alerts](#automatic-alerts)
10. [Troubleshooting (FAQ)](#troubleshooting-faq)

---

## What the bot does

- 📊 **Full status** on one screen: CPU, RAM, disk (/), uptime, load average, server IP.
- 🖇 **CPU** — load % + progress bar.
- 🧠 **RAM** — used / total / percent.
- 🗂 **Disk** — root partition usage.
- ⏱ **Uptime** — how long the server runs + last boot time.
- 🌐 **Network** — live send/recv speed (1s sample) and traffic today.
- 🗄 **Disk I/O** — read/write speed.
- 🔌 **Connections** — active TCP connections & listening ports.
- 🔔 **Alerts** — automatic messages when CPU / RAM / disk / load cross a threshold.
- ✅ **Recovery messages** — when everything is back to normal.
- 🔁 **Reboot notification** — instantly tells you if the server restarted.
- 📅 **Daily report** — scheduled summary (default 22:00).
- 🏆 **Top processes** by memory.
- 🎛 **Inline buttons** — one-tap control.
- 🔐 **Owner authorization** — strangers can't see your metrics.

---

## Architecture

```
┌──────────────────────────┐        HTTPS polling        ┌─────────────────────┐
│  Telegram (your chat)    │ ◄────────────────────────►│  Telegram Bot API    │
│  commands /status /cpu   │                            └──────────┬──────────┘
└──────────────────────────┘                                       ▲
                                                                   │ long polling (getUpdates)
                    ┌──────────────────────────────────────────────┘
                    │
        ┌───────────▼────────────────────────────┐
        │   monitor_bot.py  (runs ON the server)  │
        │  python-telegram-bot  (PTB v22+)        │
        │  · command & button handlers            │
        │  · background jobs:                     │
        │      - alert checks every N seconds     │
        │      - traffic accounting every 30s     │
        │      - daily report on schedule         │
        └───────────▲────────────────┬────────────┘
                    │ psutil         │ read/write
                    ▼                ▼
        ┌─────────────────────┐  config.json (settings)
        │  System (CPU, RAM,  │  state.json  (daily traffic,
        │  disk, network)     │              alert flags)
        └─────────────────────┘
```

Key points:
- **The bot lives on the server itself** — no external hosting needed. It talks to Telegram via long polling, so NAT / firewalls are not a problem.
- System metrics come from the **`psutil`** library (native, cross-platform).
- A **scheduler** (APScheduler via PTB `job_queue`) runs alert checks and traffic accounting in the background.
- Settings live in `config.json`, runtime state (daily traffic, alert flags) in `state.json`. Both are auto-created and **not** committed to git.

---

## Quick start in 5 minutes

Only 3 steps — fine even for total beginners.

### Step 1. Create your bot in Telegram
1. Open **@BotFather** in Telegram.
2. Send `/newbot`.
3. Choose a name and a username (username must end in `bot`, e.g. `mymonitor_bot`).
4. BotFather gives you a **token** like `123456:ABC-DEF...`. **Save it — you'll need it.**

### Step 2. Prepare the server
You need a Linux server (Ubuntu/Debian/CentOS) with Python 3.8+ and SSH access (root or sudo). Check:

```bash
python3 --version    # must be 3.8 or newer
```

If missing, install:
```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y python3 python3-venv git
```

### Step 3. Download & run

```bash
# 1. Clone the repository
git clone https://github.com/Ashtoro/MonitorBOT.git
cd MonitorBOT

# 2. Create a virtual env (isolates dependencies)
python3 -m venv venv

# 3. Install dependencies
./venv/bin/pip install -r requirements.txt

# 4. Set YOUR bot token (from BotFather)
export TELEGRAM_BOT_TOKEN="YOUR_TOKEN_FROM_BOTFATHER"

# 5. Run
./venv/bin/python monitor_bot.py
```

You'll see `[INFO] Бот запущен. Интервал проверки 45с` — it works!
Open your bot in Telegram and press **Start**.

---

## Full step-by-step install

### Download
```bash
git clone https://github.com/Ashtoro/MonitorBOT.git
cd MonitorBOT
```

### Virtual environment (why)
A venv isolates the project's dependencies from system packages — so you don't break the server's Python or hit version conflicts.

```bash
python3 -m venv venv
```

### Install dependencies
```bash
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

What gets installed (see `requirements.txt`):
- `python-telegram-bot[job-queue]` — Telegram bot library (**v22.x**, must be 21+).
- `psutil` — system metrics (CPU, RAM, disk, network).

> ⚠️ **Important:** you need the `[job-queue]` extra for the background scheduler (it pulls in APScheduler). A plain `pip install python-telegram-bot` will **not** give you the scheduler.

### First run
```bash
export TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
./venv/bin/python monitor_bot.py
```

Check the log: message `[INFO] Бот запущен...` means success.

### Authorize as owner
1. Open the chat with the bot, send `/start`.
2. It shows your **chat_id** and asks for the owner password.
3. The owner password is set in **config.json** (`owner_token`). See [Configuration](#configuration-configjson).
4. Send `/auth YOUR_PASSWORD` — you become the owner and unlock all commands.

---

## Run as a systemd service (auto-start)

To make the bot **start automatically after reboot** and restart itself on crash, use systemd — the standard way on Linux.

1. Create the service file. `Set YOUR token` inside:

```bash
sudo tee /etc/systemd/system/monitor-bot.service > /dev/null <<'EOF'
[Unit]
Description=VPS Monitor Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/MonitorBOT
ExecStart=/root/MonitorBOT/venv/bin/python /root/MonitorBOT/monitor_bot.py
Environment=TELEGRAM_BOT_TOKEN=YOUR_TOKEN_FROM_BOTFATHER
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

> Replace `/root/MonitorBOT/...` with the real path where you cloned the project. The token is passed via `Environment=`, same as with the manual run.

2. Activate & start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable monitor-bot   # start on boot
sudo systemctl start monitor-bot    # start now
```

3. Check status:

```bash
systemctl status monitor-bot        # should show active (running)
systemctl is-active monitor-bot     # prints: active
```

Useful commands:
```bash
journalctl -u monitor-bot -f        # follow logs live
journalctl -u monitor-bot -n 50     # last 50 log lines
systemctl restart monitor-bot       # restart
systemctl stop monitor-bot          # stop
```

Thanks to `Restart=always`, systemd will bring the bot back if it crashes.

---

## Configuration (config.json)

On first run the bot creates `config.json` next to the script. All settings live here.

Example:

```json
{
  "authorized_users": [],
  "owner_token": "YOUR_SECRET_PASSWORD",
  "alerts_enabled": true,
  "daily_report": { "enabled": true, "hour": 22, "minute": 0 },
  "thresholds": {
    "cpu": 70,
    "ram": 80,
    "disk": 85,
    "load1": 2.0,
    "alert_interval": 45,
    "cooldown": 300
  }
}
```

### Fields

| Parameter | Default | Description |
|---|---|---|
| `authorized_users` | `[]` | List of authorized Telegram **chat_id**s. Filled automatically after `/auth`, but you can add manually. |
| `owner_token` | `""` | **Owner password.** The first user who sends `/auth <this>` becomes the owner. **Change the default to your own!** |
| `alerts_enabled` | `true` | Global alerts switch. Toggle with `/pause` and `/resume`. |
| `daily_report.enabled` | `true` | Enable the daily report. |
| `daily_report.hour` / `minute` | `22:00` | Time to send the daily report (server local timezone). |
| `thresholds.cpu` | `70` | CPU threshold in %. Alert when above. |
| `thresholds.ram` | `80` | RAM threshold in %. |
| `thresholds.disk` | `85` | Disk (/) usage threshold in %. |
| `thresholds.load1` | `2.0` | Load average (1 min) threshold. |
| `thresholds.alert_interval` | `45` | How often to check the system, seconds. |
| `thresholds.cooldown` | `300` | Min pause between repeated alerts for the same channel, seconds. |

After editing config.json, restart the bot (`systemctl restart monitor-bot` or Ctrl+C → run again).

---

## Commands & buttons

### Commands

| Command | Description |
|---|---|
| `/start` | Main menu + inline keyboard |
| `/menu`, `/help` | Show menu & command list |
| `/auth <password>` | Authorize as owner |
| `/status` | Full server status |
| `/cpu` | CPU load + load average |
| `/ram` | Memory usage |
| `/disk` | Disk usage |
| `/uptime` | Uptime & last boot time |
| `/net` | Network speed + traffic today |
| `/io` | Disk I/O + active connections |
| `/report` | Daily report (now) |
| `/top` | Top-5 processes by memory |
| `/alerts` | Current alert thresholds |
| `/pause` | Pause alerts |
| `/resume` | Resume alerts |

### Inline buttons

After `/start` (or under any metric message):

```
🖥 Status
🖇 CPU | 🧠 RAM
🗂 Disk | ⏱ Uptime
🌐 Net | 🗄 Disk I/O | 🔌 Conn
🏆 Top | 📊 Report | 🔔 Alerts
```

Tap a button to instantly see that metric — no typing needed.

---

## Access protection

- Until you **authorize**, the bot shows no metrics — only a greeting with your `chat_id` and a request for `/auth <password>`.
- The password is set in `config.json` (`owner_token`).
- After a successful `/auth`, your `chat_id` is added to `authorized_users` and persisted — access survives bot restarts.
- Anyone who sends the correct `/auth` gets full access, so keep the password secret.

> 💡 **Tip:** change `owner_token` to a strong password right after setup, otherwise anyone can authorize.

---

## Automatic alerts

Every `alert_interval` seconds (default 45) the bot checks system health:

- **CPU** ≥ threshold → `💥 CPU перегруз — 92.0%`
- **RAM** ≥ threshold → `🧨 RAM перегруз — 1.2 / 1.0 GB (95.0%)`
- **Disk** ≥ threshold → `💾 Диск почти полон — 91.0%`
- **Load** ≥ threshold → `⚡ Высокая нагрузка — load1 3.50`

When the value drops below the threshold, it sends a recovery message:
- `✅ Нормализовано (CPU). Текущее значение: 40.0%`

Spam protection: a `cooldown` pause (300s) is kept between alerts for each channel, even if the value stays over.

If the server **rebooted**, on startup the bot sends:
- `🔁 Сервер перезагрузился! ...`

---

## Troubleshooting (FAQ)

**The bot doesn't reply?**
- Check the token is correct (didn't forget `export TELEGRAM_BOT_TOKEN`, or the token in the systemd file).
- Check the service: `systemctl status monitor-bot`.
- Look at logs: `journalctl -u monitor-bot -n 50`.
- Error `Conflict: terminated by other getUpdates request` means **two** bot instances are running. Stop the extra one: `pkill -f monitor_bot.py`, then restart the service.

**Error `No JobQueue set up` / jobs don't run?**
- Install `python-telegram-bot[job-queue]` (see `requirements.txt`), not the bare package.

**How do I change the owner password?**
- Edit `owner_token` in config.json and restart the bot.

**How do I add a second owner?**
- They can send `/auth <password>` (if they know it), or you add their `chat_id` to `authorized_users`.

**The bot shows IP as `?`?**
- Normal for some networks. It uses the external IP of the network interface.

**Can I run it on Windows?**
- Yes — `/status /cpu /ram /disk` and alerts work. The connection counter is Linux-oriented (`/proc`); on Windows `/io` will show 0 connections, but everything else works.

---

## 📄 License

The project is distributed under the [MIT](LICENSE) license — free to use, modify and distribute with attribution.

---

<p align="center">Made with ❤️ for anyone who wants to keep an eye on their servers · MonitorBOT · <a href="https://github.com/Ashtoro/MonitorBOT">GitHub</a></p>