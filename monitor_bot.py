#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS Monitor Bot — мониторинг VPS через Telegram.
CPU / RAM / диск / сеть / I/O / аптайм / алерты / кнопки / отчёты.
Локализация: русский и английский (выбирается командой /lang и кнопкой 🌐).
"""
import json
import os
import socket
import time
from datetime import date, datetime, time as dtime

import psutil
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
    MessageHandler,
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

LANGUAGES = ("ru", "en")

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


# ─────────────────────────────── i18n ───────────────────────────────

def get_lang(chat_id):
    """Язык пользователя; по умолчанию 'ru'."""
    return _state.get("langs", {}).get(str(chat_id), "ru")


def set_lang(chat_id, lang):
    _state.setdefault("langs", {})[str(chat_id)] = lang
    save_state(_state)


def _fmt(lang, key, **kw):
    return _MESSAGES[lang][key].format(**kw)


_LANG_NAME = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

# Все пользовательские строки. Оба словаря имеют одинаковый набор ключей.
_MESSAGES = {
    "ru": {
        "uptime": "{d}д {h}ч {m}м {s}с",
        "status_uptime": "⏱ Аптайм: {uptime}",
        "status_load": "⚙️ Загрузка: {icon} {l1:.2f} / {l5:.2f} / {l15:.2f}",
        "status_traffic": "📶 Трафик сегодня: ↓ {rx} / ↑ {tx}",
        "status_usage": "Использование",
        "start_ram": "🧠 <b>RAM</b>",
        "start_ok": "👋 <b>VPS Monitor</b> активен. Выберите действие кнопками или используйте команды.",
        "quick_hint": "⬇️ Быстрые кнопки внизу — просто тапните, не нужно писать команды.",
        "start_noauth": "👋 Это <b>VPS Monitor</b>.\nДоступ ограничен. Введите пароль владельца:\n"
                        "<code>/auth ПАРОЛЬ</code>\n\nВаш chat_id: <code>{chat}</code>",
        "already_auth": "Вы уже авторизованы ✅",
        "auth_need_pass": "Укажите пароль: /auth <пароль>",
        "auth_success": "✅ Авторизация успешна! Вы — владелец.",
        "auth_wrong": "❌ Неверный пароль.",
        "help_title": "🎛 <b>Меню</b> — кнопками ниже.",
        "help_text": "Команды:\n/status /cpu /ram /disk /uptime — метрики\n"
                     "/net — сеть\n/io — диск I/O & соединения\n/report — дневной отчёт\n"
                     "/top — топ процессов\n/alerts — пороги\n/lang — язык\n/menu — меню кнопок\n"
                     "/pause /resume — пауза/возобновление алертов",
        "net_now": "Скорость сейчас: ↓ {rx} / ↑ {tx}",
        "net_today": "Трафик сегодня: ↓ {rx} / ↑ {tx}",
        "io_read": "Чтение: {v}",
        "io_write": "Запись: {v}",
        "conn_active": "Активных (ESTABLISHED): {n}",
        "conn_listening": "Слушающих портов: {n}",
        "report_title": "📊 <b>Дневной отчёт</b> ({date})",
        "report_up": "🏠 {hostname} — <code>{ip}</code>",
        "report_uptime": "⏱ Аптайм: {uptime}",
        "report_traffic": "📶 Трафик: ↓ {rx} / ↑ {tx}",
        "report_created": "— создано {time}",
        "alerts_title": "🔔 <b>Алерты</b>: {state}",
        "alerts_interval": "Проверка: кажд {n}с, пауза {c}с",
        "alerts_report": "📊 Дневной отчёт: {when}",
        "enabled": "включены",
        "paused": "приостановлены",
        "report_at": "в {t}",
        "report_off": "выкл",
        "alerts_on": "⏸ Алерты приостановлены.",
        "alerts_resume": "▶️ Алерты возобновлены.",
        "top_title": "🏆 <b>Топ процессов по памяти:</b>",
        "no_access": "Нет доступа",
        "unknown": "Неизвестная команда.",
        "boot_label": "Запуск",
        "net_title": "Сеть",
        "conn_title": "Соединения",
        "disk_label": "Диск",
        "cpu_icon": "⚙️",
        "lang_title": "🌐 Выберите язык бота:",
        "lang_saved": "✅ Язык сохранён: {name}",
        "alert_cpu": "💥 <b>CPU перегруз</b> — {v:.1f}% (порог {lim}%)",
        "alert_ram": "🧨 <b>RAM перегруз</b> — {used} / {total} ({pct:.1f}%, порог {lim}%)",
        "alert_disk": "💾 <b>Диск почти полон</b> — {v:.1f}% (порог {lim}%)",
        "alert_load": "⚡ <b>Высокая нагрузка</b> — load1 {v:.2f} (порог {lim:.2f})",
        "recovery": "✅ <b>Нормализовано</b> ({key}). Текущее значение: {v}",
        "reboot": "🔁 <b>Сервер перезагрузился!</b>\nНовое время запуска: {boot}\nАптайм был сброшен.",
        "btn_status": "🖥 Статус",
        "btn_cpu": "🗠 CPU",
        "btn_ram": "🧠 RAM",
        "btn_disk": "🗂 Диск",
        "btn_uptime": "⏱ Аптайм",
        "btn_net": "🌐 Сеть",
        "btn_io": "🗄 Диск I/O",
        "btn_conn": "🔌 Соед",
        "btn_top": "🏆 Топ",
        "btn_report": "📊 Отчёт",
        "btn_alerts": "🔔 Алерты",
        "btn_lang": "🌐 Язык",
    },
    "en": {
        "uptime": "{d}d {h}h {m}m {s}s",
        "status_uptime": "⏱ Uptime: {uptime}",
        "status_load": "⚙️ Load: {icon} {l1:.2f} / {l5:.2f} / {l15:.2f}",
        "status_traffic": "📶 Traffic today: ↓ {rx} / ↑ {tx}",
        "status_usage": "Usage",
        "start_ram": "🧠 <b>RAM</b>",
        "start_noauth": "👋 This is <b>VPS Monitor</b>.\nAccess is restricted. Enter the owner password:\n"
                        "<code>/auth PASSWORD</code>\n\nYour chat_id: <code>{chat}</code>",
        "start_ok": "👋 <b>VPS Monitor</b> is active. Use the buttons or commands.",
        "quick_hint": "⬇️ Quick buttons below — just tap, no need to type commands.",
        "already_auth": "You are already authorized ✅",
        "auth_need_pass": "Provide the password: /auth <password>",
        "auth_success": "✅ Authorization successful! You are the owner.",
        "auth_wrong": "❌ Wrong password.",
        "help_title": "🎛 <b>Menu</b> — use the buttons below.",
        "help_text": "Commands:\n/status /cpu /ram /disk /uptime — metrics\n"
                     "/net — network\n/io — disk I/O & connections\n/report — daily report\n"
                     "/top — top processes\n/lang — language\n/menu — buttons menu\n"
                     "/pause /resume — pause/resume alerts",
        "net_now": "Speed now: ↓ {rx} / ↑ {tx}",
        "net_today": "Traffic today: ↓ {rx} / ↑ {tx}",
        "io_read": "Read: {v}",
        "io_write": "Write: {v}",
        "conn_active": "Active (ESTABLISHED): {n}",
        "conn_listening": "Listening ports: {n}",
        "report_title": "📊 <b>Daily report</b> ({date})",
        "report_up": "🏠 {hostname} — <code>{ip}</code>",
        "report_uptime": "⏱ Uptime: {uptime}",
        "report_traffic": "📶 Traffic: ↓ {rx} / ↑ {tx}",
        "report_created": "— generated {time}",
        "alerts_title": "🔔 <b>Alerts</b>: {state}",
        "alerts_interval": "Check: every {n}s, pause {c}s",
        "alerts_report": "📊 Daily report: {when}",
        "enabled": "enabled",
        "paused": "paused",
        "report_at": "at {t}",
        "report_off": "off",
        "alerts_on": "⏸ Alerts paused.",
        "alerts_resume": "▶️ Alerts resumed.",
        "top_title": "🏆 <b>Top processes by memory:</b>",
        "no_access": "No access",
        "unknown": "Unknown command.",
        "boot_label": "Boot",
        "net_title": "Network",
        "conn_title": "Connections",
        "disk_label": "Disk",
        "cpu_icon": "⚙️",
        "lang_title": "🌐 Choose bot language:",
        "lang_saved": "✅ Language set: {name}",
        "alert_cpu": "💥 <b>CPU overload</b> — {v:.1f}% (threshold {lim}%)",
        "alert_ram": "🧨 <b>RAM overload</b> — {used} / {total} ({pct:.1f}%, threshold {lim}%)",
        "alert_disk": "💾 <b>Disk almost full</b> — {v:.1f}% (threshold {lim}%)",
        "alert_load": "⚡ <b>High load</b> — load1 {v:.2f} (threshold {lim:.2f})",
        "recovery": "✅ <b>Recovered</b> ({key}). Current value: {v}",
        "reboot": "🔁 <b>Server rebooted!</b>\nNew boot time: {boot}\nUptime was reset.",
        "btn_status": "🖥 Status",
        "btn_cpu": "🗠 CPU",
        "btn_ram": "🧠 RAM",
        "btn_disk": "🗂 Disk",
        "btn_uptime": "⏱ Uptime",
        "btn_net": "🌐 Net",
        "btn_io": "🗄 Disk I/O",
        "btn_conn": "🔌 Conn",
        "btn_top": "🏆 Top",
        "btn_report": "📊 Report",
        "btn_alerts": "🔔 Alerts",
        "btn_lang": "🌐 Lang",
    },
}

# У единого набора ключей есть расхождения в названиях — выравниваем зеркальные ключи,
# чтобы в обоих языках разыменовывание было идентичным.


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


def uptime_seconds():
    return int(time.time() - psutil.boot_time())


def fmt_uptime(lang):
    t = uptime_seconds()
    d, rem = divmod(int(t), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return _fmt(lang, "uptime", d=d, h=h, m=m, s=s)


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
    if _NW["prev"] is not None and _NW["prev_t"]:
        dt = now - _NW["prev_t"]
        if dt > 0:
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
    if _NW["date"] == today:
        return _NW["rx"], _NW["tx"]
    return 0, 0


# ─────────────────────────────── текст и клавиатуры ───────────────────────────────

def build_status(lang="ru"):
    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    load1, load5, load15 = psutil.getloadavg()
    cpu = psutil.cpu_percent(interval=1)
    rx, tx = traffic_today()
    icon_load = "✅" if load1 < _config["thresholds"]["load1"] else "⚠️"
    return f"""🖥 <b>VPS Monitor — {socket.gethostname()}</b>
🌐 IP: <code>{get_public_ip()}</code>
{_fmt(lang, 'status_uptime', uptime=fmt_uptime(lang))}
{_fmt(lang, 'status_load', icon=icon_load, l1=load1, l5=load5, l15=load15)}
{_fmt(lang, 'status_traffic', rx=fmt_bytes(rx), tx=fmt_bytes(tx))}

{_MESSAGES[lang]['cpu_icon']} <b>CPU</b>
   {_fmt(lang, 'status_usage')}: {cpu:.1f}%
   {status_bar(cpu)}

{_fmt(lang, 'start_ram')}
   {fmt_bytes(vm.used)} / {fmt_bytes(vm.total)} ({vm.percent:.1f}%)
   {status_bar(vm.percent)}

🗂 <b>{_fmt(lang, 'disk_label')} (/)</b>
   {fmt_bytes(ds.used)} / {fmt_bytes(ds.total)} ({ds.percent:.1f}%)
   {status_bar(ds.percent)}"""


def main_keyboard(lang="ru"):
    k = InlineKeyboardButton
    L = _MESSAGES[lang]
    return InlineKeyboardMarkup([
        [k(L["btn_status"], callback_data="status")],
        [k(L["btn_cpu"], callback_data="cpu"), k(L["btn_ram"], callback_data="ram")],
        [k(L["btn_disk"], callback_data="disk"), k(L["btn_uptime"], callback_data="uptime")],
        [k(L["btn_net"], callback_data="net"), k(L["btn_io"], callback_data="io"), k(L["btn_conn"], callback_data="conn")],
        [k(L["btn_top"], callback_data="top"), k(L["btn_report"], callback_data="report"), k(L["btn_alerts"], callback_data="alerts")],
        [k(L["btn_lang"], callback_data="lang")],
    ])


def lang_keyboard():
    k = InlineKeyboardButton
    return InlineKeyboardMarkup([
        [k(_LANG_NAME["ru"], callback_data="setlang:ru"), k(_LANG_NAME["en"], callback_data="setlang:en")],
    ])


def quick_keyboard(lang="ru"):
    """Быстрые reply-кнопки внизу поля ввода."""
    k = KeyboardButton
    L = _MESSAGES[lang]
    return ReplyKeyboardMarkup(
        [
            [k(L["btn_status"])],
            [k(L["btn_cpu"]), k(L["btn_ram"]), k(L["btn_disk"])],
            [k(L["btn_net"]), k(L["btn_io"]), k(L["btn_uptime"])],
            [k(L["btn_top"]), k(L["btn_report"]), k(L["btn_alerts"])],
            [k(L["btn_lang"])],
        ],
        resize_keyboard=True,
    )


_QUICK_ACTIONS = ("status", "cpu", "ram", "disk", "uptime", "net", "io", "top", "report", "alerts")


def resolve_quick_action(lang, text):
    """Сопоставить текст кнопки (в любом из языков) с действием."""
    for lg in LANGUAGES:
        L = _MESSAGES[lg]
        for a in _QUICK_ACTIONS:
            if text == L.get("btn_" + a):
                return a
    return None


# ─────────────────────────────── рассылки и алерты ───────────────────────────────

async def _broadcast(builder):
    """Отправить всем владельцам; builder(lang) -> текст."""
    for chat in list(_authorized):
        try:
            await _bot.send_message(chat, builder(get_lang(chat)), parse_mode=ParseMode.HTML)
        except Exception:
            pass


def _alert_text(lang, key, value, thr):
    L = _MESSAGES[lang]
    if key == "cpu":
        return L["alert_cpu"].format(v=value, lim=thr["cpu"])
    if key == "ram":
        return L["alert_ram"].format(used=fmt_bytes(psutil.virtual_memory().used),
                                     total=fmt_bytes(psutil.virtual_memory().total),
                                     pct=value, lim=thr["ram"])
    if key == "disk":
        return L["alert_disk"].format(v=value, lim=thr["disk"])
    if key == "load":
        return L["alert_load"].format(value=value, lim=thr["load1"])
    return "?"


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
    checks = [
        ("cpu", cpu, thr["cpu"], "{:.1f}%"),
        ("ram", vm.percent, thr["ram"], "{:.1f}%"),
        ("disk", ds.percent, thr["disk"], "{:.1f}%"),
        ("load", load1, thr["load1"], "{:.2f}"),
    ]

    for key, value, limit, fmt in checks:
        over = value >= limit
        was = bool(breach.get(key, False))
        if over and not was:
            if now - _last_alert.get(key, 0) >= cooldown:
                _last_alert[key] = now
                breach[key] = True
                await _broadcast(lambda lang, k=key, v=value, f=fmt: _alert_text(lang, k, v, f))
        elif not over and was:
            breach[key] = False
            if now - _last_alert.get(key, 0) >= cooldown:
                _last_alert[key] = now
                await _broadcast(lambda lang, k=key.upper(), v=fmt.format(value):
                                 _fmt(lang, "recovery", key=k, v=v))

    save_state(_state)


async def send_reboot_alert():
    boot = psutil.boot_time()
    last = _state.get("last_boot")
    if last and abs(last - boot) > 10:
        boot_str = datetime.fromtimestamp(boot).strftime('%Y-%m-%d %H:%M:%S')
        await _broadcast(lambda lang: _fmt(lang, "reboot", boot=boot_str))
    _state["last_boot"] = boot
    save_state(_state)


async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    rx, tx = traffic_today()
    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=1)

    def _report(lang):
        L = _MESSAGES[lang]
        return "\n".join([
            _fmt(lang, "report_title", date=datetime.now().strftime('%Y-%m-%d')),
            _fmt(lang, "report_up", hostname=socket.gethostname(), ip=get_public_ip()),
            _fmt(lang, "report_uptime", uptime=fmt_uptime(lang)),
            "",
            f"{L['cpu_icon']} CPU: {cpu:.1f}%",
            f"🧠 RAM: {vm.percent:.1f}% ({fmt_bytes(vm.used)} / {fmt_bytes(vm.total)})",
            f"🗂 {_fmt(lang, 'disk_label')}: {ds.percent:.1f}% ({fmt_bytes(ds.used)} / {fmt_bytes(ds.total)})",
            _fmt(lang, "report_traffic", rx=fmt_bytes(rx), tx=fmt_bytes(tx)),
            "",
            _fmt(lang, "report_created", time=datetime.now().strftime('%H:%M:%S')),
        ])

    await _broadcast(_report)


# ─────────────────────────────── команды ───────────────────────────────

def is_auth(update):
    return update.effective_chat.id in _authorized


def _ln(update):
    return get_lang(update.effective_chat.id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _ln(update)
    chat = update.effective_chat.id
    text = _fmt(lang, "start_ok") if chat in _authorized else _fmt(lang, "start_noauth", chat=chat)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))
    # закрепляем быстрые кнопки внизу поля ввода
    await update.message.reply_text(
        _fmt(lang, "quick_hint") if chat in _authorized else _fmt(lang, "quick_hint"),
        reply_markup=quick_keyboard(lang),
    )


async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _ln(update)
    chat = update.effective_chat.id
    if chat in _authorized:
        await update.message.reply_text(_fmt(lang, "already_auth"))
        return
    if not context.args:
        await update.message.reply_text(_fmt(lang, "auth_need_pass"))
        return
    if context.args[0] == _config.get("owner_token"):
        _authorized.add(chat)
        _config["authorized_users"] = sorted(_authorized)
        save_config()
        await update.message.reply_text(_fmt(lang, "auth_success"), reply_markup=main_keyboard(lang))
    else:
        await update.message.reply_text(_fmt(lang, "auth_wrong"))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    text = f"{_fmt(lang, 'help_title')}\n{_fmt(lang, 'help_text')}"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    await update.message.reply_text(build_status(lang), parse_mode=ParseMode.HTML,
                                    reply_markup=main_keyboard(lang))


async def cmd_cpu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    cpu = psutil.cpu_percent(interval=1)
    load1, load5, load15 = psutil.getloadavg()
    text = (f"{_MESSAGES[lang]['cpu_icon']} <b>CPU</b>: {cpu:.1f}%\n{status_bar(cpu)}\n"
            f"Load: {load1:.2f} / {load5:.2f} / {load15:.2f}")
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_ram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    vm = psutil.virtual_memory()
    await update.message.reply_text(
        f"🧠 <b>RAM</b>\n{fmt_bytes(vm.used)} / {fmt_bytes(vm.total)} ({vm.percent:.1f}%)\n{status_bar(vm.percent)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_disk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    ds = psutil.disk_usage("/")
    await update.message.reply_text(
        f"🗂 <b>{_fmt(lang, 'disk_label')} (/)</b>\n{fmt_bytes(ds.used)} / {fmt_bytes(ds.total)} ({ds.percent:.1f}%)\n{status_bar(ds.percent)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    boot = datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')
    await update.message.reply_text(
        f"⏱ {_fmt(lang, 'status_uptime', uptime=fmt_uptime(lang))}\n"
        f"{_fmt(lang, 'boot_label')}: {boot}",
        reply_markup=main_keyboard(lang))


async def cmd_net(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    rx, tx = net_speed_sample()
    drx, dtx = traffic_today()
    await update.message.reply_text(
        f"🌐 <b>{_fmt(lang, 'net_title')}</b>\n"
        f"{_fmt(lang, 'net_now', rx=fmt_speed(rx), tx=fmt_speed(tx))}\n"
        f"{_fmt(lang, 'net_today', rx=fmt_bytes(drx), tx=fmt_bytes(dtx))}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_io(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    rr, wr = disk_io_sample()
    est, tah = count_connections()
    await update.message.reply_text(
        f"🗄 <b>Disk I/O</b> (per 1s)\n"
        f"{_fmt(lang, 'io_read', v=fmt_speed(rr))}\n{_fmt(lang, 'io_write', v=fmt_speed(wr))}\n\n"
        f"🔌 <b>{_fmt(lang, 'conn_title')}</b>\n"
        f"{_fmt(lang, 'conn_active', n=est)}\n{_fmt(lang, 'conn_listening', n=tah)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    rx, tx = traffic_today()
    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    cpu = psutil.cpu_percent(interval=1)
    await update.message.reply_text(
        f"{_fmt(lang, 'report_title', date=datetime.now().strftime('%Y-%m-%d'))}\n"
        f"{_MESSAGES[lang]['cpu_icon']} CPU: {cpu:.1f}%\n"
        f"🧠 RAM: {vm.percent:.1f}% ({fmt_bytes(vm.used)} / {fmt_bytes(vm.total)})\n"
        f"🗂 {_fmt(lang, 'disk_label')}: {ds.percent:.1f}% ({fmt_bytes(ds.used)} / {fmt_bytes(ds.total)})\n"
        f"{_fmt(lang, 'report_traffic', rx=fmt_bytes(rx), tx=fmt_bytes(tx))}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    thr = _config["thresholds"]
    state = _fmt(lang, "enabled") if _config.get("alerts_enabled", True) else _fmt(lang, "paused")
    dr = _config["daily_report"]
    rep_time = _fmt(lang, "report_at", t=f"{dr['hour']:02d}:{dr['minute']:02d}") if dr.get("enabled") else _fmt(lang, "report_off")
    await update.message.reply_text(
        f"{_fmt(lang, 'alerts_title', state=state)}\n"
        f"CPU ≥ {thr['cpu']}%\nRAM ≥ {thr['ram']}%\n{_fmt(lang, 'disk_label')} ≥ {thr['disk']}%\nLoad1 ≥ {thr['load1']}\n"
        f"{_fmt(lang, 'alerts_interval', n=thr['alert_interval'], c=thr['cooldown'])}\n"
        f"{_fmt(lang, 'alerts_report', when=rep_time)}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    _config["alerts_enabled"] = False
    save_config()
    await update.message.reply_text(_fmt(lang, "alerts_on"), reply_markup=main_keyboard(lang))


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    _config["alerts_enabled"] = True
    save_config()
    await update.message.reply_text(_fmt(lang, "alerts_resume"), reply_markup=main_keyboard(lang))


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            procs.append((p.info.get("memory_percent") or 0, p))
        except Exception:
            pass
    procs.sort(reverse=True, key=lambda x: x[0])
    lines = [_fmt(lang, "top_title")]
    for _, p in procs[:5]:
        try:
            mem = fmt_bytes(p.memory_info().rss)
        except Exception:
            mem = "?"
        lines.append(f"  • {p.info.get('name')} (PID {p.info['pid']}) — {mem}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _ln(update)
    await update.message.reply_text(_fmt(lang, "lang_title"), reply_markup=lang_keyboard())


# ─────────────────────────────── кнопки ───────────────────────────────

async def _metric_reply(lang, data):
    """Текст для inline-кнопок по callback_data."""
    L = _MESSAGES[lang]
    if data == "status":
        return build_status(lang)
    if data == "cpu":
        cpu = psutil.cpu_percent(interval=1)
        load1, load5, load15 = psutil.getloadavg()
        return (f"{L['cpu_icon']} <b>CPU</b>: {cpu:.1f}%\n{status_bar(cpu)}\n"
                f"Load: {load1:.2f} / {load5:.2f} / {load15:.2f}")
    if data == "ram":
        vm = psutil.virtual_memory()
        return f"🧠 <b>RAM</b>\n{fmt_bytes(vm.used)} / {fmt_bytes(vm.total)} ({vm.percent:.1f}%)\n{status_bar(vm.percent)}"
    if data == "disk":
        ds = psutil.disk_usage("/")
        return (f"🗂 <b>{_fmt(lang, 'disk_label')} (/)</b>\n{fmt_bytes(ds.used)} / {fmt_bytes(ds.total)} "
                f"({ds.percent:.1f}%)\n{status_bar(ds.percent)}")
    if data == "uptime":
        boot = datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d %H:%M:%S')
        return (f"⏱ {_fmt(lang, 'status_uptime', uptime=fmt_uptime(lang))}\n"
                f"{_fmt(lang, 'boot_label')}: {boot}")
    if data == "net":
        rx, tx = net_speed_sample()
        drx, dtx = traffic_today()
        return (f"🌐 <b>{_fmt(lang, 'net_title')}</b>\n"
                f"{_fmt(lang, 'net_now', rx=fmt_speed(rx), tx=fmt_speed(tx))}\n"
                f"{_fmt(lang, 'net_today', rx=fmt_bytes(drx), tx=fmt_bytes(dtx))}")
    if data == "io":
        rr, wr = disk_io_sample()
        est, ta = count_connections()
        return (f"🗄 <b>Disk I/O</b> (per 1s)\n"
                f"{_fmt(lang, 'io_read', v=fmt_speed(rr))}\n{_fmt(lang, 'io_write', v=fmt_speed(wr))}\n\n"
                f"🔌 <b>{_fmt(lang, 'conn_title')}</b>\n"
                f"{_fmt(lang, 'conn_active', n=est)}\n{_fmt(lang, 'conn_listening', n=ta)}")
    if data == "top":
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_percent"]):
            try:
                procs.append((p.info.get("memory_percent") or 0, p))
            except Exception:
                pass
        procs.sort(reverse=True, key=lambda x: x[0])
        lines = [_fmt(lang, "top_title")]
        for _, p in procs[:5]:
            try:
                mem = fmt_bytes(p.memory_info().rss)
            except Exception:
                mem = "?"
            lines.append(f"  • {p.info.get('name')} (PID {p.info['pid']}) — {mem}")
        return "\n".join(lines)
    if data == "report":
        rx, tx = traffic_today()
        vm = psutil.virtual_memory()
        ds = psutil.disk_usage("/")
        cpu = psutil.cpu_percent(interval=1)
        return (f"{_fmt(lang, 'report_title', date=datetime.now().strftime('%Y-%m-%d'))}\n"
                f"{L['cpu_icon']} CPU: {cpu:.1f}%\n"
                f"🧠 RAM: {vm.percent:.1f}% ({fmt_bytes(vm.used)} / {fmt_bytes(vm.total)})\n"
                f"🗂 {_fmt(lang, 'disk_label')}: {ds.percent:.1f}% ({fmt_bytes(ds.used)} / {fmt_bytes(ds.total)})\n"
                f"{_fmt(lang, 'report_traffic', rx=fmt_bytes(rx), tx=fmt_bytes(tx))}")
    if data == "alerts":
        thr = _config["thresholds"]
        state = _fmt(lang, "enabled") if _config.get("alerts_enabled", True) else _fmt(lang, "paused")
        return (f"{_fmt(lang, 'alerts_title', state=state)}\n"
                f"CPU ≥ {thr['cpu']}%\nRAM ≥ {thr['ram']}%\n{_fmt(lang, 'disk_label')} ≥ {thr['disk']}%\nLoad1 ≥ {thr['load1']}")
    return _fmt(lang, "unknown")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat = q.from_user.id
    lang = get_lang(chat)
    data = q.data

    if data.startswith("setlang:"):
        new_lang = data.split(":", 1)[1]
        if new_lang in LANGUAGES:
            set_lang(chat, new_lang)
            lang = new_lang
        await q.answer()
        await q.edit_message_text(
            _fmt(lang, "start_ok"), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))
        await _bot.send_message(chat, _fmt(lang, "lang_saved", name=_LANG_NAME[lang]))
        return

    if chat not in _authorized:
        await q.answer(_fmt(lang, "no_access"), show_alert=True)
        return

    if data == "lang":
        await q.answer()
        await q.edit_message_text(_fmt(lang, "lang_title"), reply_markup=lang_keyboard())
        return

    await q.answer()
    text = await _metric_reply(lang, data)
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка тапов по быстрым reply-кнопкам и любого текста не-команды."""
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    text = update.message.text

    action = resolve_quick_action(lang, text)
    if action:
        resp = await _metric_reply(lang, action)
        await update.message.reply_text(resp, parse_mode=ParseMode.HTML,
                                        reply_markup=main_keyboard(lang))
        return
    if text == _MESSAGES[lang]["btn_lang"]:
        await update.message.reply_text(_fmt(lang, "lang_title"), reply_markup=lang_keyboard())
        return
    await update.message.reply_text(_fmt(lang, "unknown"))


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
        "lang": cmd_lang,
    }.items():
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    async def _after_start(_app):
        await send_reboot_alert()

    app.post_init = _after_start

    print(f"[INFO] Бот запущен. Интервал проверки {thr['alert_interval']}с")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()