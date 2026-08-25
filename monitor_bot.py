#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS Monitor Bot — мониторинг VPS через Telegram.
CPU / RAM / диск / сеть / I/O / аптайм / алерты / кнопки / отчёты.
"""
import json
import os
import socket
import time
from datetime import date, datetime, time as dtime

import psutil
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

DEFAULT_CONFIG = {
    "authorized_users": [],
    "owner_token": "",
    "alerts_enabled": True,
    "daily_report": {"enabled": True, "hour": 22, "minute": 0},
    "thresholds": {
        "cpu": 70,
        "ram": 80,
        "disk": 85,
        "load1": 2.0,
        "alert_interval": 45,
        "cooldown": 300,
    },
}

_config = None
_authorized = set()
_bot = None
_last_alert = {}  # key -> timestamp последнего алерта
# сеть: накопление трафика за день + мгновенная скорость
_NW = {"date": None, "rx": 0, "tx": 0, "prev": None, "prev_t": None}


# ─────────────────────────────── конфиг и стейт ───────────────────────────────

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                user = json.load(f)
            cfg.update(user)
            for key in ("thresholds", "daily_report"):
                merged = dict(DEFAULT_CONFIG[key])
                merged.update(user.get(key, {}))
                cfg[key] = merged
        except Exception:
            pass
    return cfg


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_config, f, ensure_ascii=False, indent=2)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


_state = load_state()


# ─────────────────────────────── метрики ───────────────────────────────

def fmt_bytes(n):
    n = max(0.0, float(n))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024


def fmt_speed(n):
    n = max(0.0, float(n))
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if n < 1024 or unit == "GB/s":
            return f"{n:.1f} {unit}"
        n /= 1024


def get_public_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "?"


def get_uptime():
    t = time.time() - psutil.boot_time()
    d, rem = divmod(int(t), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return f"{d}д {h}ч {m}м {s}с"


def status_bar(percent, width=14):
    filled = int(round(width * percent / 100))
    icon = "🔴" if percent >= 60 else ("🟠" if percent >= 25 else "🟢")
    return f"{icon} {'█' * filled}{'░' * (width - filled)} {percent:.0f}%"


def net_counters():
    return psutil.net_io_counters()


async def _net_tick(context: ContextTypes.DEFAULT_TYPE = None):
    """Накопление дневного трафика + оценка мгновенной скорости (за интервал вызова)."""
    global _NW
    today = date.today().isoformat()
    if _NW["date"] != today:
        _NW = {"date": today, "rx": 0, "tx": 0, "prev": None, "prev_t": None}
    c = net_counters()
    now = time.time()
    rx_sp = tx_sp = 0
    if _NW["prev"] is not None and _NW["prev_t"]:
        dt = now - _NW["prev_t"]
        if dt > 0:
            rx_sp = max(0, c.bytes_recv - _NW["prev"].bytes_recv) / dt
            tx_sp = max(0, c.bytes_sent - _NW["prev"].bytes_sent) / dt
        _NW["rx"] += max(0, c.bytes_recv - _NW["prev"].bytes_recv)
        _NW["tx"] += max(0, c.bytes_sent - _NW["prev"].bytes_sent)
    _NW["prev"] = c
    _NW["prev_t"] = now
    _state["net"] = {"date": _NW["date"], "rx": _NW["rx"], "tx": _NW["tx"]}
    save_state(_state)


def net_speed_sample():
    """Точный замер скорости за 1 секунду."""
    c1 = net_counters()
    time.sleep(1)
    c2 = net_counters()
    return max(0, c2.bytes_recv - c1.bytes_recv), max(0, c2.bytes_sent - c1.bytes_sent)


def disk_io_sample():
    d1 = psutil.disk_io_counters()
    time.sleep(1)
    d2 = psutil.disk_io_counters()
    if not d1 or not d2:
        return 0, 0
    return max(0, d2.read_bytes - d1.read_bytes), max(0, d2.write_bytes - d1.write_bytes)


def count_connections():
    established = listening = 0
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                f.readline()
                for line in f:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    st = int(parts[3], 16)
                    if st == 0x01:
                        established += 1
                    elif st == 0x0A:
                        listening += 1
        except Exception:
            pass
    return established, listening


def traffic_today():
    today = date.today().isoformat()
    st = _state.get("net", {})
    if st.get("date") == today:
        return st.get("rx", 0), st.get("tx", 0)
    rx, tx = _NW["rx"], _NW["tx"]
    if _NW["date"] == today:
        return rx, tx
    return 0, 0


def format_status():
    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    load1, load5, load15 = psutil.getloadavg()
    cpu = psutil.cpu_percent(interval=1)
    rx, tx = traffic_today()
    load_icon = "✅" if load1 < _config["thresholds"]["load1"] else "⚠️"
    return f"""🖥 <b>VPS Monitor — {socket.gethostname()}</b>
🌐 IP: <code>{get_public_ip()}</code>
⏱ Аптайм: {get_uptime()}
⚙️ Загрузка: {load_icon} {load1:.2f} / {load5:.2f} / {load15:.2f}
📶 Трафик сегодня: ↓ {fmt_bytes(rx)} / ↑ {fmt_bytes(tx)}

🖇 <b>CPU</b>
   Использование: {cpu:.1f}%
   {status_bar(cpu)}

🧠 <b>RAM</b>
   {fmt_bytes(vm.used)} / {fmt_bytes(vm.total)} ({vm.percent:.1f}%)
   {status_bar(vm.percent)}

🗂 <b>Диск (/)</b>
   {fmt_bytes(ds.used)} / {fmt_bytes(ds.total)} ({ds.percent:.1f}%)
   {status_bar(ds.percent)}"""


def main_keyboard():
    k = InlineKeyboardButton
    return InlineKeyboardMarkup([
        [k("🖥 Статус", callback_data="status")],
        [k("🖇 CPU", callback_data="cpu"), k("🧠 RAM", callback_data="ram")],
        [k("🗂 Диск", callback_data="disk"), k("⏱ Аптайм", callback_data="uptime")],
        [k("🌐 Сеть", callback_data="net"), k("🗄 Диск I/O", callback_data="io"), k("🔌 Соед", callback_data="conn")],
        [k("🏆 Топ", callback_data="top"), k("📊 Отчёт", callback_data="report"), k("🔔 Алерты", callback_data="alerts")],
    ])


# ─────────────────────────────── алерты ───────────────────────────────

async def check_and_alert(context: ContextTypes.DEFAULT_TYPE):
    if not _config.get("alerts_enabled", True) or not _authorized:
        return

    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    load1, _, _ = psutil.getloadavg()
    cpu = psutil.cpu_percent(interval=1)
    thr = _config["thresholds"]
    now = time.time()
    cooldown = thr.get("cooldown", 300)

    breach = _state.setdefault("breach", {})
    # (текст алерта, ключ, значение, порог)
    checks = [
        (f"💥 <b>CPU перегруз</b> — {cpu:.1f}% (порог {thr['cpu']}%)", "cpu", cpu, thr["cpu"], "{:.1f}%"),
        (f"🧨 <b>RAM перегруз</b> — {fmt_bytes(vm.used)} / {fmt_bytes(vm.total)} ({vm.percent:.1f}%, порог {thr['ram']}%)",
         "ram", vm.percent, thr["ram"], "{:.1f}%"),
        (f"💾 <b>Диск почти полон</b> — {ds.percent:.1f}% (порог {thr['disk']}%)", "disk", ds.percent, thr["disk"], "{:.1f}%"),
        (f"⚡ <b>Высокая нагрузка</b> — load1 {load1:.2f} (порог {thr['load1']})", "load", load1, thr["load1"], "{:.2f}"),
    ]

    for alert_text, key, value, limit, _fmt in checks:
        over = value >= limit
        was = bool(breach.get(key, False))
        if over and not was:
            if now - _last_alert.get(key, 0) >= cooldown:
                _last_alert[key] = now
                breach[key] = True
                await _broadcast(alert_text)
        elif not over and was:
            # recovery
            breach[key] = False
            if now - _last_alert.get(key, 0) >= cooldown:
                _last_alert[key] = now
                await _broadcast(f"✅ <b>Нормализовано</b> ({key.upper()}). Текущее значение: {_fmt.format(value)}")

    save_state(_state)


async def _broadcast(text):
    for chat in _authorized:
        try:
            await _bot.send_message(chat, text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def send_reboot_alert():
    boot = psutil.boot_time()
    last = _state.get("last_boot")
    if last and abs(last - boot) > 10:
        await _broadcast(
            f"🔁 <b>Сервер перезагрузился!</b>\n"
            f"Новое время запуска: {datetime.fromtimestamp(boot).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Аптайм был сброшен."
        )
    _state["last_boot"] = boot
    save_state(_state)


async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    rx, tx = traffic_today()
    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=1)
    text = (
        f"📊 <b>Дневной отчёт</b> ({datetime.now().strftime('%Y-%m-%d')})\n"
        f"🏠 {socket.gethostname()} — <code>{get_public_ip()}</code>\n"
        f"⏱ Аптайм: {get_uptime()}\n\n"
        f"🖇 CPU: {cpu:.1f}%\n"
        f"🧠 RAM: {vm.percent:.1f}% ({fmt_bytes(vm.used)} / {fmt_bytes(vm.total)})\n"
        f"🗂 Диск: {ds.percent:.1f}% ({fmt_bytes(ds.used)} / {fmt_bytes(ds.total)})\n"
        f"📶 Трафик за день: ↓ {fmt_bytes(rx)} / ↑ {fmt_bytes(tx)}\n\n"
        f"— создано {datetime.now().strftime('%H:%M:%S')}"
    )
    await _broadcast(text)


# ─────────────────────────────── команды ───────────────────────────────

def is_auth(update):
    return update.effective_chat.id in _authorized


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat in _authorized:
        await update.message.reply_text(
            "👋 <b>VPS Monitor</b> активен. Выберите действие кнопками или используйте команды.",
            parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "👋 Это <b>VPS Monitor</b>.\nДоступ ограничен. Введите пароль владельца:\n"
            f"<code>/auth ПАРОЛЬ</code>\n\nВаш chat_id: <code>{chat}</code>", parse_mode=ParseMode.HTML
        )


async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    if chat in _authorized:
        await update.message.reply_text("Вы уже авторизованы ✅")
        return
    if not context.args:
        await update.message.reply_text("Укажите пароль: /auth <пароль>")
        return
    if context.args[0] == _config.get("owner_token"):
        _authorized.add(chat)
        _config["authorized_users"] = sorted(_authorized)
        save_config()
        await update.message.reply_text("✅ Авторизация успешна! Вы — владелец.", reply_markup=main_keyboard())
    else:
        await update.message.reply_text("❌ Неверный пароль.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    await update.message.reply_text(
        "🎛 <b>Меню</b> — кнопками ниже.\n"
        "Команды:\n/status /cpu /ram /disk /uptime — метрики\n"
        "/net — сеть\n/io — диск I/O & соединения\n/report — текущий дневной отчёт\n"
        "/top — топ процессов\n/alerts — пороги\n/menu — меню кнопок\n"
        "/pause /resume — пауза/возобновление алертов",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    await update.message.reply_text(format_status(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def cmd_cpu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    cpu = psutil.cpu_percent(interval=1)
    load1, load5, load15 = psutil.getloadavg()
    await update.message.reply_text(
        f"⚙️ <b>CPU</b>: {cpu:.1f}%\n{status_bar(cpu)}\n"
        f"Load: {load1:.2f} / {load5:.2f} / {load15:.2f}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_ram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    vm = psutil.virtual_memory()
    await update.message.reply_text(
        f"🧠 <b>RAM</b>\n{fmt_bytes(vm.used)} / {fmt_bytes(vm.total)} ({vm.percent:.1f}%)\n{status_bar(vm.percent)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_disk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    ds = psutil.disk_usage("/")
    await update.message.reply_text(
        f"🗂 <b>Диск (/)</b>\n{fmt_bytes(ds.used)} / {fmt_bytes(ds.total)} ({ds.percent:.1f}%)\n{status_bar(ds.percent)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    await update.message.reply_text(
        f"⏱ Аптайм: {get_uptime()}\nЗапуск: {datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')}",
        reply_markup=main_keyboard(),
    )


async def cmd_net(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    rx, tx = net_speed_sample()
    drx, dtx = traffic_today()
    await update.message.reply_text(
        f"🌐 <b>Сеть</b>\n"
        f"Скорость сейчас: ↓ {fmt_speed(rx)} / ↑ {fmt_speed(tx)}\n"
        f"Трафик сегодня: ↓ {fmt_bytes(drx)} / ↑ {fmt_bytes(dtx)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_io(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    rr, wr = disk_io_sample()
    est, lst = count_connections()
    await update.message.reply_text(
        f"🗄 <b>Диск I/O</b> (за 1с)\n"
        f"Чтение: {fmt_speed(rr)}\nЗапись: {fmt_speed(wr)}\n\n"
        f"🔌 <b>Соединения</b>\nАктивных (ESTABLISHED): {est}\nСлушающих портов: {lst}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    rx, tx = traffic_today()
    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=1)
    await update.message.reply_text(
        f"📊 <b>Дневной отчёт</b> ({datetime.now().strftime('%Y-%m-%d')})\n"
        f"🖇 CPU: {cpu:.1f}%\n"
        f"🧠 RAM: {vm.percent:.1f}% ({fmt_bytes(vm.used)} / {fmt_bytes(vm.total)})\n"
        f"🗂 Диск: {ds.percent:.1f}% ({fmt_bytes(ds.used)} / {fmt_bytes(ds.total)})\n"
        f"📶 Трафик: ↓ {fmt_bytes(rx)} / ↑ {fmt_bytes(tx)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    thr = _config["thresholds"]
    state = "включены" if _config.get("alerts_enabled", True) else "приостановлены"
    dr = _config["daily_report"]
    if dr.get("enabled"):
        rep_time = f"в {dr['hour']:02d}:{dr['minute']:02d}"
    else:
        rep_time = "выкл"
    await update.message.reply_text(
        f"🔔 <b>Алерты</b>: {state}\n"
        f"CPU ≥ {thr['cpu']}%\nRAM ≥ {thr['ram']}%\nДиск ≥ {thr['disk']}%\nLoad1 ≥ {thr['load1']}\n"
        f"Проверка: кажд {thr['alert_interval']}с, пауза {thr['cooldown']}с\n"
        f"📊 Дневной отчёт: {rep_time}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(),
    )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    _config["alerts_enabled"] = False
    save_config()
    await update.message.reply_text("⏸ Алерты приостановлены.", reply_markup=main_keyboard())


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    _config["alerts_enabled"] = True
    save_config()
    await update.message.reply_text("▶️ Алерты возобновлены.", reply_markup=main_keyboard())


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            procs.append((p.info.get("memory_percent") or 0, p))
        except Exception:
            pass
    procs.sort(reverse=True, key=lambda x: x[0])
    lines = ["🏆 <b>Топ процессов по памяти:</b>"]
    for _, p in procs[:5]:
        try:
            mem = fmt_bytes(p.memory_info().rss)
        except Exception:
            mem = "?"
        lines.append(f"  • {p.info.get('name')} (PID {p.info['pid']}) — {mem}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


# ─────────────────────────────── кнопки ───────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat = q.from_user.id
    if chat not in _authorized:
        await q.answer("Нет доступа", show_alert=True)
        return
    await q.answer()
    data = q.data
    kb = main_keyboard()

    if data == "status":
        text = format_status()
    elif data == "cpu":
        cpu = psutil.cpu_percent(interval=1)
        load1, load5, load15 = psutil.getloadavg()
        text = f"⚙️ <b>CPU</b>: {cpu:.1f}%\n{status_bar(cpu)}\nLoad: {load1:.2f} / {load5:.2f} / {load15:.2f}"
    elif data == "ram":
        vm = psutil.virtual_memory()
        text = f"🧠 <b>RAM</b>\n{fmt_bytes(vm.used)} / {fmt_bytes(vm.total)} ({vm.percent:.1f}%)\n{status_bar(vm.percent)}"
    elif data == "disk":
        ds = psutil.disk_usage("/")
        text = f"🗂 <b>Диск (/)</b>\n{fmt_bytes(ds.used)} / {fmt_bytes(ds.total)} ({ds.percent:.1f}%)\n{status_bar(ds.percent)}"
    elif data == "uptime":
        text = f"⏱ Аптайм: {get_uptime()}\nЗапуск: {datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')}"
    elif data == "net":
        rx, tx = net_speed_sample()
        drx, dtx = traffic_today()
        text = (f"🌐 <b>Сеть</b>\nСкорость сейчас: ↓ {fmt_speed(rx)} / ↑ {fmt_speed(tx)}\n"
                f"Трафик сегодня: ↓ {fmt_bytes(drx)} / ↑ {fmt_bytes(dtx)}")
    elif data == "io":
        rr, wr = disk_io_sample()
        est, lst = count_connections()
        text = (f"🗄 <b>Диск I/O</b> (за 1с)\nЧтение: {fmt_speed(rr)}\nЗапись: {fmt_speed(wr)}\n\n"
                f"🔌 <b>Соединения</b>\nАктивных (ESTABLISHED): {est}\nСлушающих портов: {lst}")
    elif data == "top":
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_percent"]):
            try:
                procs.append((p.info.get("memory_percent") or 0, p))
            except Exception:
                pass
        procs.sort(reverse=True, key=lambda x: x[0])
        lines = ["🏆 <b>Топ процессов:</b>"]
        for _, p in procs[:5]:
            try:
                mem = fmt_bytes(p.memory_info().rss)
            except Exception:
                mem = "?"
            lines.append(f"  • {p.info.get('name')} (PID {p.info['pid']}) — {mem}")
        text = "\n".join(lines)
    elif data == "report":
        rx, tx = traffic_today()
        vm = psutil.virtual_memory()
        ds = psutil.disk_usage("/")
        cpu = psutil.cpu_percent(interval=1)
        text = (f"📊 <b>Дневной отчёт</b> ({datetime.now().strftime('%Y-%m-%d')})\n"
                f"🖇 CPU: {cpu:.1f}%\n🧠 RAM: {vm.percent:.1f}% ({fmt_bytes(vm.used)} / {fmt_bytes(vm.total)})\n"
                f"🗂 Диск: {ds.percent:.1f}% ({fmt_bytes(ds.used)} / {fmt_bytes(ds.total)})\n"
                f"📶 Трафик: ↓ {fmt_bytes(rx)} / ↑ {fmt_bytes(tx)}")
    elif data == "alerts":
        thr = _config["thresholds"]
        state = "включены" if _config.get("alerts_enabled", True) else "приостановлены"
        text = (f"🔔 <b>Алерты</b>: {state}\nCPU ≥ {thr['cpu']}%\nRAM ≥ {thr['ram']}%\n"
                f"Диск ≥ {thr['disk']}%\nLoad1 ≥ {thr['load1']}")
    else:
        text = "Неизвестная команда."

    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ─────────────────────────────── запуск ───────────────────────────────

def main():
    global _config, _authorized, _bot
    _config = load_config()
    _authorized = set(_config.get("authorized_users", []))

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан")

    app = Application.builder().token(token).build()
    _bot = app.bot

    thr = _config["thresholds"]
    app.job_queue.run_repeating(check_and_alert, interval=thr["alert_interval"], first=thr["alert_interval"])
    app.job_queue.run_repeating(_net_tick, interval=30, first=10)

    dr = _config["daily_report"]
    if dr.get("enabled"):
        app.job_queue.run_daily(
            send_daily_report,
            time=dtime(hour=dr.get("hour", 22), minute=dr.get("minute", 0)),
        )

    for name, fn in {
        "start": cmd_start, "help": cmd_help, "menu": cmd_help, "auth": cmd_auth,
        "status": cmd_status, "cpu": cmd_cpu, "ram": cmd_ram, "disk": cmd_disk,
        "uptime": cmd_uptime, "net": cmd_net, "io": cmd_io, "report": cmd_report,
        "alerts": cmd_alerts, "pause": cmd_pause, "resume": cmd_resume, "top": cmd_top,
    }.items():
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(on_callback))

    async def _after_start(_app):
        await send_reboot_alert()

    app.post_init = _after_start

    print(f"[INFO] Бот запущен. Интервал проверки {thr['alert_interval']}с")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()