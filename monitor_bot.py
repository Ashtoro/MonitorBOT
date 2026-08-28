#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS Monitor Bot — мониторинг одного или нескольких VPS через Telegram.
CPU / RAM / диск / сеть / I/O / аптайм / алерты / кнопки / отчёты.
Локализация: русский и английский (/lang).
Мульти-сервер: локальный сервер + удалённые по SSH (лёгкий python3-агент).
"""
import asyncio
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
    "servers": [],  # [{"name","host","port","username","password","key_file"}]
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
# сеть: накопление трафика за день (локальный сервер)
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
        "help_text": "Команды:\n/status [сервер] — статус\n/cpu /ram /disk /uptime /net /io /top [сервер] — метрики\n"
                     "/servers — сводка всех серверов\n/report — дневной отчёт\n/alerts — пороги\n"
                     "/lang — язык\n/menu — меню кнопок\n/pause /resume — пауза/возобновление алертов",
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
        "io_title": "🗄 <b>Диск I/O</b> (за 1с)",
        "cpu_icon": "⚙️",
        "lang_title": "🌐 Выберите язык бота:",
        "lang_saved": "✅ Язык сохранён: {name}",
        "btn_servers": "🖧 Серверы",
        "servers_title": "🖧 <b>Серверы</b>",
        "srv_down": "🚫 недоступен",
        "no_server": "❌ Сервер не найден. Доступны: {names}",
        "alert_down": "🚫 <b>Сервер недоступен</b> — {name}\n<code>{err}</code>",
        "alert_up": "✅ <b>Сервер снова на связи</b> — {name}",
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
        "start_ok": "👋 <b>VPS Monitor</b> is active. Use the buttons or commands.",
        "quick_hint": "⬇️ Quick buttons below — just tap, no need to type commands.",
        "start_noauth": "👋 This is <b>VPS Monitor</b>.\nAccess is restricted. Enter the owner password:\n"
                        "<code>/auth PASSWORD</code>\n\nYour chat_id: <code>{chat}</code>",
        "already_auth": "You are already authorized ✅",
        "auth_need_pass": "Provide the password: /auth <password>",
        "auth_success": "✅ Authorization successful! You are the owner.",
        "auth_wrong": "❌ Wrong password.",
        "help_title": "🎛 <b>Menu</b> — use the buttons below.",
        "help_text": "Commands:\n/status [server] — status\n/cpu /ram /disk /uptime /net /io /top [server] — metrics\n"
                     "/servers — all servers overview\n/report — daily report\n/alerts — thresholds\n"
                     "/lang — language\n/menu — buttons menu\n/pause /resume — pause/resume alerts",
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
        "io_title": "🗄 <b>Disk I/O</b> (per 1s)",
        "cpu_icon": "⚙️",
        "lang_title": "🌐 Choose bot language:",
        "lang_saved": "✅ Language set: {name}",
        "btn_servers": "🖧 Servers",
        "servers_title": "🖧 <b>Servers</b>",
        "srv_down": "🚫 unreachable",
        "no_server": "❌ Server not found. Available: {names}",
        "alert_down": "🚫 <b>Server unreachable</b> — {name}\n<code>{err}</code>",
        "alert_up": "✅ <b>Server is back online</b> — {name}",
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


# ─────────────────────────────── форматирование ───────────────────────────────

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


def fmt_uptime(lang, sec=None):
    t = uptime_seconds() if sec is None else int(sec)
    d, rem = divmod(int(t), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return _fmt(lang, "uptime", d=d, h=h, m=m, s=s)


def status_bar(percent, width=14):
    filled = int(round(width * percent / 100))
    icon = "🔴" if percent >= 60 else ("🟠" if percent >= 25 else "🟢")
    return f"{icon} {'█' * filled}{'░' * (width - filled)} {percent:.0f}%"


# ─────────────────────────────── серверы ───────────────────────────────

def _servers_all():
    """[{name, local, host, port, username, password, key_file}] — локальный первым."""
    out = [{"name": socket.gethostname(), "local": True}]
    for s in (_config.get("servers") or []):
        out.append({
            "name": s.get("name") or s.get("host", "?"),
            "local": False,
            "host": s.get("host"),
            "port": int(s.get("port", 22)),
            "username": s.get("username", "root"),
            "password": s.get("password"),
            "key_file": s.get("key_file"),
        })
    return out


def find_server(name):
    n = (name or "").strip().lower()
    for s in _servers_all():
        if s["name"].lower() == n:
            return s
    if n in ("local", "локальный"):
        return _servers_all()[0]
    return None


def server_ip(srv):
    return get_public_ip() if srv.get("local") else srv.get("host", "?")


# ─────────────────────────────── сбор метрик ───────────────────────────────

def _net_snap_local():
    per = psutil.net_io_counters(pernic=True)
    rx = sum(v.bytes_recv for k, v in per.items() if k != "lo")
    tx = sum(v.bytes_sent for k, v in per.items() if k != "lo")
    return rx, tx


def _top_processes(limit=5):
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            procs.append((p.info.get("memory_percent") or 0, p))
        except Exception:
            pass
    procs.sort(reverse=True, key=lambda x: x[0])
    out = []
    for _, p in procs[:limit]:
        try:
            out.append([p.info.get("name") or "?", p.info["pid"], p.memory_info().rss])
        except Exception:
            pass
    return out


def collect_local():
    """Метрики сервера, где запущен бот (psutil). Единый формат с удалёнными."""
    vm = psutil.virtual_memory()
    ds = psutil.disk_usage("/")
    t1 = psutil.cpu_times()
    n1 = _net_snap_local()
    d1 = psutil.disk_io_counters()
    time.sleep(1)
    t2 = psutil.cpu_times()
    n2 = _net_snap_local()
    d2 = psutil.disk_io_counters()

    idle1 = getattr(t1, "idle", 0) + getattr(t1, "iowait", 0)
    idle2 = getattr(t2, "idle", 0) + getattr(t2, "iowait", 0)
    dtotal = sum(t2) - sum(t1)
    cpu = max(0.0, 100.0 * (1 - (idle2 - idle1) / dtotal)) if dtotal > 0 else 0.0
    dt = 1.0
    if d1 and d2:
        r_sp = max(0, d2.read_bytes - d1.read_bytes) / dt
        w_sp = max(0, d2.write_bytes - d1.write_bytes) / dt
    else:
        r_sp = w_sp = 0
    est, lst = count_connections()
    return {
        "cpu": round(cpu, 1),
        "ram_used": vm.used, "ram_total": vm.total, "ram_percent": vm.percent,
        "disk_used": ds.used, "disk_total": ds.total, "disk_percent": ds.percent,
        "load1": psutil.getloadavg()[0], "load5": psutil.getloadavg()[1], "load15": psutil.getloadavg()[2],
        "uptime": uptime_seconds(), "boot_ts": psutil.boot_time(),
        "net_rx_speed": n2[0] - n1[0], "net_tx_speed": n2[1] - n1[1],
        "net_rx_total": n2[0], "net_tx_total": n2[1],
        "io_read": r_sp, "io_write": w_sp,
        "conn_est": est, "conn_list": lst,
        "top": _top_processes(),
    }


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


# Лёгкий агент: исполняется на удалённом сервере штатным python3 (только stdlib),
# печатает JSON с метриками. Ничего не устанавливает и не сохраняет.
REMOTE_AGENT = r'''
import json, os, time
def rd(p):
    with open(p) as f: return f.read()
def cpu_snap():
    parts = [int(x) for x in rd('/proc/stat').splitlines()[0].split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    return idle, sum(parts)
def net_snap():
    rx = tx = 0
    for line in rd('/proc/net/dev').splitlines()[2:]:
        if ':' not in line: continue
        dev, data = line.split(':', 1)
        if dev.strip() == 'lo': continue
        f = data.split()
        rx += int(f[0]); tx += int(f[8])
    return rx, tx
def disk_snap():
    rs = ws = 0
    for line in rd('/proc/diskstats').splitlines():
        p = line.split()
        if len(p) < 14 or p[2].startswith(('loop', 'ram', 'dm-')): continue
        rs += int(p[5]) * 512; ws += int(p[9]) * 512
    return rs, ws
def conns():
    est = lis = 0
    for path in ('/proc/net/tcp', '/proc/net/tcp6'):
        try: lines = rd(path).splitlines()[1:]
        except Exception: continue
        for line in lines:
            p = line.split()
            if len(p) < 4: continue
            s = int(p[3], 16)
            if s == 0x01: est += 1
            elif s == 0x0A: lis += 1
    return est, lis
def top(n=5):
    ps = []
    page = os.sysconf('SC_PAGE_SIZE')
    for pid in os.listdir('/proc'):
        if not pid.isdigit(): continue
        try:
            comm = rd('/proc/%s/comm' % pid).strip()
            rss = int(rd('/proc/%s/statm' % pid).split()[1]) * page
            ps.append([comm, int(pid), rss])
        except Exception: pass
    ps.sort(key=lambda x: -x[2])
    return ps[:n]
c1 = cpu_snap(); n1 = net_snap(); d1 = disk_snap()
time.sleep(1)
c2 = cpu_snap(); n2 = net_snap(); d2 = disk_snap()
idle1, tot1 = c1; idle2, tot2 = c2
cpu = max(0.0, 100.0 * (1 - (idle2 - idle1) / max(1, tot2 - tot1)))
mi = {}
for line in rd('/proc/meminfo').splitlines():
    k, v = line.split(':', 1)
    mi[k.strip()] = int(v.strip().split()[0]) * 1024
total = mi['MemTotal']
avail = mi.get('MemAvailable', mi.get('MemFree', 0))
st = os.statvfs('/')
dtotal = st.f_blocks * st.f_frsize
dused = dtotal - st.f_bavail * st.f_frsize
la = rd('/proc/loadavg').split()[:3]
up = float(rd('/proc/uptime').split()[0])
dt = 1.0
out = {
 "cpu": round(cpu, 1),
 "ram_used": total - avail, "ram_total": total,
 "ram_percent": round(100.0 * (total - avail) / max(1, total), 1),
 "disk_used": dused, "disk_total": dtotal,
 "disk_percent": round(100.0 * dused / max(1, dtotal), 1),
 "load1": float(la[0]), "load5": float(la[1]), "load15": float(la[2]),
 "uptime": int(up), "boot_ts": time.time() - up,
 "net_rx_speed": n2[0] - n1[0], "net_tx_speed": n2[1] - n1[1],
 "net_rx_total": n2[0], "net_tx_total": n2[1],
 "io_read": max(0, d2[0] - d1[0]) / dt, "io_write": max(0, d2[1] - d1[1]) / dt,
 "conn_est": 0, "conn_list": 0, "top": []
}
est, lis = conns()
out["conn_est"], out["conn_list"] = est, lis
out["top"] = top()
print(json.dumps(out))
'''


def _ssh_collect(srv):
    """Подключиться по SSH и собрать метрики удалённого сервера."""
    import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        srv["host"], port=srv.get("port", 22), username=srv.get("username", "root"),
        password=srv.get("password"), key_filename=srv.get("key_file"),
        timeout=12, banner_timeout=15, auth_timeout=15,
    )
    try:
        cmd = "python3 - <<'PYEOF'\n" + REMOTE_AGENT + "\nPYEOF"
        stdin, stdout, stderr = cli.exec_command(cmd, timeout=25)
        out = stdout.read().decode()
        rc = stdout.channel.recv_exit_status()
        if rc != 0 or not out.strip():
            raise RuntimeError("agent rc=%s %s" % (rc, stderr.read().decode()[:120]))
        return json.loads(out.strip().splitlines()[-1])
    finally:
        cli.close()


async def _collect(srv):
    """Собрать метрики сервера (не блокируя event loop). None — если недоступен."""
    loop = asyncio.get_running_loop()
    if srv.get("local"):
        return await loop.run_in_executor(None, collect_local)

    def _job():
        try:
            return _ssh_collect(srv)
        except Exception as e:
            _state.setdefault("lasterr", {})[srv["name"]] = str(e)[:150]
            return None

    return await loop.run_in_executor(None, _job)


# ─────────────────────────────── трафик и состояние ───────────────────────────────

async def _net_tick(context: ContextTypes.DEFAULT_TYPE = None):
    """Накопление дневного трафика локального сервера (каждые 30с)."""
    global _NW
    today = date.today().isoformat()
    if _NW["date"] != today:
        _NW = {"date": today, "rx": 0, "tx": 0, "prev": None, "prev_t": None}
    c = psutil.net_io_counters()
    now = time.time()
    if _NW["prev"] is not None and _NW["prev_t"]:
        _NW["rx"] += max(0, c.bytes_recv - _NW["prev"].bytes_recv)
        _NW["tx"] += max(0, c.bytes_sent - _NW["prev"].bytes_sent)
    _NW["prev"] = c
    _NW["prev_t"] = now
    _state["net"] = {"date": _NW["date"], "rx": _NW["rx"], "tx": _NW["tx"]}
    save_state(_state)


def update_remote_traffic(srv, m):
    """Накопление дневного трафика удалённого сервера по кумулятивным счётчикам."""
    name = srv["name"]
    traffic = _state.setdefault("traffic", {})
    prev = _state.setdefault("netprev", {})
    today = date.today().isoformat()
    day = traffic.get(name, {})
    if day.get("date") != today:
        day = {"date": today, "rx": 0, "tx": 0}
    p = prev.get(name) or {}
    boot = m.get("boot_ts")
    if p.get("boot") == boot:
        day["rx"] += max(0, m["net_rx_total"] - p.get("rx", m["net_rx_total"]))
        day["tx"] += max(0, m["net_tx_total"] - p.get("tx", m["net_tx_total"]))
    prev[name] = {"boot": boot, "rx": m["net_rx_total"], "tx": m["net_tx_total"]}
    traffic[name] = day
    save_state(_state)


def traffic_for(srv):
    if srv.get("local"):
        today = date.today().isoformat()
        st = _state.get("net", {})
        if st.get("date") == today:
            return st.get("rx", 0), st.get("tx", 0)
        return (0, 0) if _NW["date"] != today else (_NW["rx"], _NW["tx"])
    st = _state.get("traffic", {}).get(srv["name"], {})
    if st.get("date") == date.today().isoformat():
        return st.get("rx", 0), st.get("tx", 0)
    return 0, 0


# ─────────────────────────────── клавиатуры ───────────────────────────────

def main_keyboard(lang="ru"):
    k = InlineKeyboardButton
    L = _MESSAGES[lang]
    return InlineKeyboardMarkup([
        [k(L["btn_status"], callback_data="status")],
        [k(L["btn_servers"], callback_data="servers")],
        [k(L["btn_cpu"], callback_data="cpu"), k(L["btn_ram"], callback_data="ram")],
        [k(L["btn_disk"], callback_data="disk"), k(L["btn_uptime"], callback_data="uptime")],
        [k(L["btn_net"], callback_data="net"), k(L["btn_io"], callback_data="io"), k(L["btn_conn"], callback_data="conn")],
        [k(L["btn_top"], callback_data="top"), k(L["btn_report"], callback_data="report"), k(L["btn_alerts"], callback_data="alerts")],
        [k(L["btn_lang"], callback_data="lang")],
    ])


def quick_keyboard(lang="ru"):
    k = KeyboardButton
    L = _MESSAGES[lang]
    return ReplyKeyboardMarkup(
        [
            [k(L["btn_status"])],
            [k(L["btn_servers"])],
            [k(L["btn_cpu"]), k(L["btn_ram"]), k(L["btn_disk"])],
            [k(L["btn_net"]), k(L["btn_io"]), k(L["btn_uptime"])],
            [k(L["btn_top"]), k(L["btn_report"]), k(L["btn_alerts"])],
            [k(L["btn_lang"])],
        ],
        resize_keyboard=True,
    )


def servers_keyboard(lang="ru"):
    """Inline-кнопки: по одной на каждый сервер."""
    k = InlineKeyboardButton
    rows = [[k(f"🖥 {s['name']}", callback_data=f"srv:{s['name']}")] for s in _servers_all()]
    return InlineKeyboardMarkup(rows)


def lang_keyboard():
    k = InlineKeyboardButton
    return InlineKeyboardMarkup([
        [k(_LANG_NAME["ru"], callback_data="setlang:ru"), k(_LANG_NAME["en"], callback_data="setlang:en")],
    ])


_QUICK_ACTIONS = ("status", "servers", "cpu", "ram", "disk", "uptime", "net", "io", "top", "report", "alerts")


def resolve_quick_action(lang, text):
    for lg in LANGUAGES:
        L = _MESSAGES[lg]
        for a in _QUICK_ACTIONS:
            if text == L.get("btn_" + a):
                return a
    return None


# ─────────────────────────────── алерты ───────────────────────────────

async def _broadcast(builder):
    """Отправить всем владельцам; builder(lang) -> текст."""
    for chat in list(_authorized):
        try:
            await _bot.send_message(chat, builder(get_lang(chat)), parse_mode=ParseMode.HTML)
        except Exception:
            pass


def _alert_text(lang, key, m, thr):
    L = _MESSAGES[lang]
    if key == "cpu":
        return L["alert_cpu"].format(v=m["cpu"], lim=thr["cpu"])
    if key == "ram":
        return L["alert_ram"].format(used=fmt_bytes(m["ram_used"]), total=fmt_bytes(m["ram_total"]),
                                     pct=m["ram_percent"], lim=thr["ram"])
    if key == "disk":
        return L["alert_disk"].format(v=m["disk_percent"], lim=thr["disk"])
    if key == "load":
        return L["alert_load"].format(v=m["load1"], lim=thr["load1"])
    return "?"


async def check_and_alert(context: ContextTypes.DEFAULT_TYPE):
    if not _config.get("alerts_enabled", True) or not _authorized:
        return

    thr = _config["thresholds"]
    now = time.time()
    cooldown = thr.get("cooldown", 300)
    breach = _state.setdefault("breach", {})
    down = _state.setdefault("down", {})

    checks = [
        ("cpu", "cpu", thr["cpu"], "{:.1f}%"),
        ("ram", "ram_percent", thr["ram"], "{:.1f}%"),
        ("disk", "disk_percent", thr["disk"], "{:.1f}%"),
        ("load", "load1", thr["load1"], "{:.2f}"),
    ]

    for srv in _servers_all():
        name = srv["name"]
        m = await _collect(srv)

        if m is None:
            if not srv.get("local") and not down.get(name):
                if now - _last_alert.get(f"{name}:down", 0) >= cooldown:
                    _last_alert[f"{name}:down"] = now
                    down[name] = True
                    err = _state.get("lasterr", {}).get(name, "")
                    await _broadcast(lambda lang, n=name, e=err: _fmt(lang, "alert_down", name=n, err=e))
            continue

        if down.get(name):
            down[name] = False
            await _broadcast(lambda lang, n=name: _fmt(lang, "alert_up", name=n))

        if not srv.get("local"):
            update_remote_traffic(srv, m)
            await _check_remote_reboot(srv, m)

        for key, mkey, limit, fmt in checks:
            value = m.get(mkey, 0)
            bkey = f"{name}:{key}"
            over = value >= limit
            was = bool(breach.get(bkey, False))
            if over and not was:
                if now - _last_alert.get(bkey, 0) >= cooldown:
                    _last_alert[bkey] = now
                    breach[bkey] = True
                    await _broadcast(lambda lang, n=name, k=key, mm=m:
                                     f"🖥 <b>{n}</b>\n{_alert_text(lang, k, mm, thr)}")
            elif not over and was:
                breach[bkey] = False
                if now - _last_alert.get(bkey, 0) >= cooldown:
                    _last_alert[bkey] = now
                    await _broadcast(lambda lang, n=name, k=key.upper(), v=fmt.format(value):
                                     f"🖥 <b>{n}</b>\n{_fmt(lang, 'recovery', key=k, v=v)}")

        # удалённый reboot
        if not srv.get("local"):
            boots = _state.setdefault("boot", {})
            bt = time.time() - m.get("uptime", 0)
            old = boots.get(name)
            if old and abs(old - bt) > 120:
                boot_str = datetime.fromtimestamp(bt).strftime('%Y-%m-%d %H:%M:%S')
                await _broadcast(lambda lang, n=name, b=boot_str:
                                 f"🖥 <b>{n}</b>\n{_fmt(lang, 'reboot', boot=b)}")
            boots[name] = bt

    save_state(_state)


async def _check_remote_reboot(srv, m):
    pass  # reboot для удалённых проверяется внутри check_and_alert


async def send_reboot_alert():
    """Локальный сервер: уведомление о перезагрузке (при старте бота)."""
    boot = psutil.boot_time()
    last = _state.get("last_boot")
    if last and abs(last - boot) > 10:
        boot_str = datetime.fromtimestamp(boot).strftime('%Y-%m-%d %H:%M:%S')
        await _broadcast(lambda lang: _fmt(lang, "reboot", boot=boot_str))
    _state["last_boot"] = boot
    save_state(_state)


async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    rx_tx = {}

    def _report(lang):
        L = _MESSAGES[lang]
        parts = [_fmt(lang, "report_title", date=datetime.now().strftime('%Y-%m-%d')), ""]
        for srv in _servers_all():
            m = _REPORT_CACHE.get(srv["name"])
            trx, ttx = traffic_for(srv)
            head = f"🖥 <b>{srv['name']}</b> — <code>{server_ip(srv)}</code>"
            if m is None:
                parts.append(head)
                parts.append("   " + _fmt(lang, "srv_down"))
            else:
                parts.append(head)
                parts.append("   " + _fmt(lang, "report_uptime", uptime=fmt_uptime(lang, m.get("uptime"))))
                parts.append(f"   {L['cpu_icon']} CPU: {m['cpu']:.1f}%")
                parts.append(f"   🧠 RAM: {m['ram_percent']:.1f}% ({fmt_bytes(m['ram_used'])} / {fmt_bytes(m['ram_total'])})")
                parts.append(f"   🗂 {L['disk_label']}: {m['disk_percent']:.1f}% ({fmt_bytes(m['disk_used'])} / {fmt_bytes(m['disk_total'])})")
                parts.append("   " + _fmt(lang, "report_traffic", rx=fmt_bytes(trx), tx=fmt_bytes(ttx)))
            parts.append("")
        parts.append(_fmt(lang, "report_created", time=datetime.now().strftime('%H:%M:%S')))
        return "\n".join(parts)

    # предварительный сбор метрик всех серверов (в фоне не блокируя)
    loop = asyncio.get_running_loop()
    _REPORT_CACHE.clear()
    for srv in _servers_all():
        _REPORT_CACHE[srv["name"]] = await _collect(srv)

    await _broadcast(_report)


_REPORT_CACHE = {}


# ─────────────────────────────── текст метрик ───────────────────────────────

def build_status(lang, m, srv):
    L = _MESSAGES[lang]
    icon_load = "✅" if m["load1"] < _config["thresholds"]["load1"] else "⚠️"
    trx, ttx = traffic_for(srv)
    return f"""🖥 <b>VPS Monitor — {srv['name']}</b>
🌐 IP: <code>{server_ip(srv)}</code>
{_fmt(lang, 'status_uptime', uptime=fmt_uptime(lang, m.get('uptime')))}
{_fmt(lang, 'status_load', icon=icon_load, l1=m['load1'], l5=m['load5'], l15=m['load15'])}
{_fmt(lang, 'status_traffic', rx=fmt_bytes(trx), tx=fmt_bytes(ttx))}

{L['cpu_icon']} <b>CPU</b>
   {_fmt(lang, 'status_usage')}: {m['cpu']:.1f}%
   {status_bar(m['cpu'])}

{_fmt(lang, 'start_ram')}
   {fmt_bytes(m['ram_used'])} / {fmt_bytes(m['ram_total'])} ({m['ram_percent']:.1f}%)
   {status_bar(m['ram_percent'])}

🗂 <b>{_fmt(lang, 'disk_label')} (/)</b>
   {fmt_bytes(m['disk_used'])} / {fmt_bytes(m['disk_total'])} ({m['disk_percent']:.1f}%)
   {status_bar(m['disk_percent'])}"""


def build_cpu(lang, m):
    return (f"{_MESSAGES[lang]['cpu_icon']} <b>CPU</b>: {m['cpu']:.1f}%\n{status_bar(m['cpu'])}\n"
            f"Load: {m['load1']:.2f} / {m['load5']:.2f} / {m['load15']:.2f}")


def build_ram(lang, m):
    return (f"🧠 <b>RAM</b>\n{fmt_bytes(m['ram_used'])} / {fmt_bytes(m['ram_total'])} "
            f"({m['ram_percent']:.1f}%)\n{status_bar(m['ram_percent'])}")


def build_disk(lang, m):
    return (f"🗂 <b>{_fmt(lang, 'disk_label')} (/)</b>\n{fmt_bytes(m['disk_used'])} / {fmt_bytes(m['disk_total'])} "
            f"({m['disk_percent']:.1f}%)\n{status_bar(m['disk_percent'])}")


def build_uptime(lang, m):
    boot = datetime.fromtimestamp(m.get("boot_ts", time.time() - m.get("uptime", 0))).strftime('%Y-%m-%d %H:%M:%S')
    return (f"⏱ {_fmt(lang, 'status_uptime', uptime=fmt_uptime(lang, m.get('uptime')))}\n"
            f"{_fmt(lang, 'boot_label')}: {boot}")


def build_net(lang, m, srv):
    trx, ttx = traffic_for(srv)
    return (f"🌐 <b>{_fmt(lang, 'net_title')}</b>\n"
            f"{_fmt(lang, 'net_now', rx=fmt_speed(m['net_rx_speed']), tx=fmt_speed(m['net_tx_speed']))}\n"
            f"{_fmt(lang, 'net_today', rx=fmt_bytes(trx), tx=fmt_bytes(ttx))}")


def build_io(lang, m):
    return (f"{_fmt(lang, 'io_title')}\n"
            f"{_fmt(lang, 'io_read', v=fmt_speed(m['io_read']))}\n{_fmt(lang, 'io_write', v=fmt_speed(m['io_write']))}\n\n"
            f"🔌 <b>{_fmt(lang, 'conn_title')}</b>\n"
            f"{_fmt(lang, 'conn_active', n=m['conn_est'])}\n{_fmt(lang, 'conn_listening', n=m['conn_list'])}")


def build_top(lang, m):
    lines = [_fmt(lang, "top_title")]
    for name, pid, rss in m.get("top", []):
        lines.append(f"  • {name} (PID {pid}) — {fmt_bytes(rss)}")
    return "\n".join(lines)


def build_servers_text(lang):
    """Сводка всех серверов одной строкой на сервер."""
    L = _MESSAGES[lang]
    lines = [_fmt(lang, "servers_title"), ""]
    for srv in _servers_all():
        m = _REPORT_CACHE.get(srv["name"])
        if m is None:
            lines.append(f"🖥 <b>{srv['name']}</b> — {_fmt(lang, 'srv_down')}")
        else:
            icon = "🔴" if m["cpu"] >= 60 or m["ram_percent"] >= 60 or m["disk_percent"] >= 60 else "🟢"
            lines.append(
                f"{icon} <b>{srv['name']}</b> — CPU {m['cpu']:.0f}% · RAM {m['ram_percent']:.0f}% · "
                f"{L['disk_label']} {m['disk_percent']:.0f}%"
            )
    return "\n".join(lines)


# ─────────────────────────────── сбор всех серверов ───────────────────────────────

async def _collect_all():
    """Собрать метрики всех серверов в _REPORT_CACHE."""
    _REPORT_CACHE.clear()
    for srv in _servers_all():
        _REPORT_CACHE[srv["name"]] = await _collect(srv)
    return _REPORT_CACHE


# ─────────────────────────────── команды ───────────────────────────────

def is_auth(update):
    return update.effective_chat.id in _authorized


def _ln(update):
    return get_lang(update.effective_chat.id)


def _pick_server(context):
    """Сервер из аргумента команды; None в аргументе -> локальный.
    Возвращает (srv, err): err — если имя задано, но не найдено."""
    if context.args:
        srv = find_server(context.args[0])
        if srv is None:
            names = ", ".join(s["name"] for s in _servers_all())
            return None, names
        return srv, None
    return _servers_all()[0], None


async def _collect_or_down(lang, srv):
    m = await _collect(srv)
    if m is None:
        err = _state.get("lasterr", {}).get(srv["name"], "")
        return None, f"🖥 <b>{srv['name']}</b> — {_fmt(lang, 'srv_down')}\n<code>{err}</code>"
    return m, None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _ln(update)
    chat = update.effective_chat.id
    text = _fmt(lang, "start_ok") if chat in _authorized else _fmt(lang, "start_noauth", chat=chat)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))
    await update.message.reply_text(_fmt(lang, "quick_hint"), reply_markup=quick_keyboard(lang))


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


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _ln(update)
    await update.message.reply_text(_fmt(lang, "lang_title"), reply_markup=lang_keyboard())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_status(lang, m, srv)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    await _collect_all()
    await update.message.reply_text(build_servers_text(lang), parse_mode=ParseMode.HTML,
                                    reply_markup=servers_keyboard(lang))


async def cmd_cpu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_cpu(lang, m)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_ram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_ram(lang, m)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_disk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_disk(lang, m)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_uptime(lang, m)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_net(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_net(lang, m, srv)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_io(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_io(lang, m)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    srv, err = _pick_server(context)
    if err:
        return await update.message.reply_text(_fmt(lang, "no_server", names=err))
    m, down = await _collect_or_down(lang, srv)
    text = down if m is None else build_top(lang, m)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    await _collect_all()

    L = _MESSAGES[lang]
    parts = [_fmt(lang, "report_title", date=datetime.now().strftime('%Y-%m-%d')), ""]
    for srv in _servers_all():
        m = _REPORT_CACHE.get(srv["name"])
        trx, ttx = traffic_for(srv)
        head = f"🖥 <b>{srv['name']}</b> — <code>{server_ip(srv)}</code>"
        parts.append(head)
        if m is None:
            parts.append("   " + _fmt(lang, "srv_down"))
        else:
            parts.append("   " + _fmt(lang, "report_uptime", uptime=fmt_uptime(lang, m.get("uptime"))))
            parts.append(f"   {L['cpu_icon']} CPU: {m['cpu']:.1f}%")
            parts.append(f"   🧠 RAM: {m['ram_percent']:.1f}% ({fmt_bytes(m['ram_used'])} / {fmt_bytes(m['ram_total'])})")
            parts.append(f"   🗂 {L['disk_label']}: {m['disk_percent']:.1f}% ({fmt_bytes(m['disk_used'])} / {fmt_bytes(m['disk_total'])})")
            parts.append("   " + _fmt(lang, "report_traffic", rx=fmt_bytes(trx), tx=fmt_bytes(ttx)))
        parts.append("")
    parts.append(_fmt(lang, "report_created", time=datetime.now().strftime('%H:%M:%S')))
    await update.message.reply_text("\n".join(parts), parse_mode=ParseMode.HTML,
                                    reply_markup=main_keyboard(lang))


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


# ─────────────────────────────── inline-кнопки ───────────────────────────────

async def _metric_reply(lang, data):
    """Текст для inline-кнопок (локальный сервер либо служебные экраны)."""
    if data == "servers":
        await _collect_all()
        return build_servers_text(lang)
    if data == "report":
        await _collect_all()
        L = _MESSAGES[lang]
        parts = [_fmt(lang, "report_title", date=datetime.now().strftime('%Y-%m-%d')), ""]
        for srv in _servers_all():
            m = _REPORT_CACHE.get(srv["name"])
            trx, ttx = traffic_for(srv)
            parts.append(f"🖥 <b>{srv['name']}</b>")
            if m is None:
                parts.append("   " + _fmt(lang, "srv_down"))
            else:
                parts.append(f"   {L['cpu_icon']} CPU: {m['cpu']:.1f}%")
                parts.append(f"   🧠 RAM: {m['ram_percent']:.1f}% ({fmt_bytes(m['ram_used'])} / {fmt_bytes(m['ram_total'])})")
                parts.append(f"   🗂 {L['disk_label']}: {m['disk_percent']:.1f}%")
                parts.append("   " + _fmt(lang, "report_traffic", rx=fmt_bytes(trx), tx=fmt_bytes(ttx)))
        return "\n".join(parts)

    srv = _servers_all()[0]  # остальные кнопки — локальный сервер
    m = await _collect(srv)
    if m is None:
        return f"🖥 <b>{srv['name']}</b> — {_fmt(lang, 'srv_down')}"
    if data == "status":
        return build_status(lang, m, srv)
    if data == "cpu":
        return build_cpu(lang, m)
    if data == "ram":
        return build_ram(lang, m)
    if data == "disk":
        return build_disk(lang, m)
    if data == "uptime":
        return build_uptime(lang, m)
    if data == "net":
        return build_net(lang, m, srv)
    if data == "io":
        return build_io(lang, m)
    if data == "conn":
        return (f"🔌 <b>{_fmt(lang, 'conn_title')}</b>\n"
                f"{_fmt(lang, 'conn_active', n=m['conn_est'])}\n{_fmt(lang, 'conn_listening', n=m['conn_list'])}")
    if data == "top":
        return build_top(lang, m)
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

    if data.startswith("srv:"):
        await q.answer()
        srv = find_server(data.split(":", 1)[1])
        if srv is None:
            await q.edit_message_text(_fmt(lang, "unknown"))
            return
        m, down = await _collect_or_down(lang, srv)
        text = down if m is None else build_status(lang, m, srv)
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))
        except Exception:
            await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))
        return

    await q.answer()
    text = await _metric_reply(lang, data)
    kb = servers_keyboard(lang) if data == "servers" else main_keyboard(lang)
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ─────────────────────────────── быстрые кнопки ───────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка тапов по быстрым reply-кнопкам и любого текста не-команды."""
    if not is_auth(update):
        return await cmd_start(update, context)
    lang = _ln(update)
    text = update.message.text

    action = resolve_quick_action(lang, text)
    if action is None:
        await update.message.reply_text(_fmt(lang, "unknown"))
        return
    if action == "servers":
        await _collect_all()
        await update.message.reply_text(build_servers_text(lang), parse_mode=ParseMode.HTML,
                                        reply_markup=servers_keyboard(lang))
        return
    if text == _MESSAGES[lang]["btn_lang"]:
        await update.message.reply_text(_fmt(lang, "lang_title"), reply_markup=lang_keyboard())
        return
    resp = await _metric_reply(lang, action)
    await update.message.reply_text(resp, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(lang))


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
        "status": cmd_status, "servers": cmd_servers, "cpu": cmd_cpu, "ram": cmd_ram,
        "disk": cmd_disk, "uptime": cmd_uptime, "net": cmd_net, "io": cmd_io,
        "report": cmd_report, "alerts": cmd_alerts, "pause": cmd_pause,
        "resume": cmd_resume, "top": cmd_top, "lang": cmd_lang,
    }.items():
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    async def _after_start(_app):
        await send_reboot_alert()

    app.post_init = _after_start

    print(f"[INFO] Бот запущен. Серверов: {len(_servers_all())}. Интервал проверки {thr['alert_interval']}с")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
