import os
import sys
import html
import time
import json
import random
import string
import threading
from collections import deque

import requests
import telepot
import paho.mqtt.client as mqtt
from telepot.namedtuple import (
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove,
)

PRESETS = [("Close", 0), ("25%", 25), ("50%", 50), ("75%", 75), ("Open", 100)]
PANEL_REFRESH_SEC = 10   # how often an open panel refreshes itself
ALERT_HISTORY = 5        # alerts kept and shown per zone
DIVIDER = "➖➖➖➖➖➖➖➖➖➖➖"


def esc(value):
    return html.escape(str(value))


def pretty(reason):
    return esc(str(reason).replace("->", "→"))


def short(reason, limit=42):
    text = str(reason).replace("->", "→")
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return esc(text)


def state_icon(position):
    if position is None:
        return "⚪"
    if position <= 0:
        return "\U0001f534"       # red: closed
    if position >= 100:
        return "\U0001f7e2"       # green: fully open
    return "\U0001f7e1"           # yellow: partially open


def source_icon(source):
    return "\U0001f916" if source == "auto" else "\U0001f464"  # robot / person


def position_text(position):
    if position is None:
        return "unknown"
    if position <= 0:
        return "closed"
    if position >= 100:
        return "fully open"
    return f"{position}% open"


def window_bar(position):
    filled = round((position or 0) / 10)
    return "▓" * filled + "░" * (10 - filled)


def fmt(value):
    return value if value is not None else "--"


def trend_arrow(now, later):
    if later > now + 0.1:
        return "↗"
    if later < now - 0.1:
        return "↘"
    return "→"


def senml_value(payload):
    """Extract the first measurement value from a SenML document (RFC 8428)."""
    records = payload.get("e")
    if isinstance(records, list) and records:
        return records[0].get("v")
    return None


class TelegramBot:
    def __init__(self, settings_file='settings.json'):
        self.load_settings(settings_file)
        self.broker = None
        self.port = None
        self.transport = None
        self.users_db = []

        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
        self.service_id = f"telegram_bot_{random_suffix}"

        self.sessions = {}          # chat_id -> user record
        self.panel = {}             # chat_id -> {"msg": id, "view": (...)}
        self.sensors = {}           # zone -> {sensor: value}
        self.win_pos = {}           # zone -> position
        self.alerts = {}            # zone -> deque[{t, pos, src, reason}]
        self.forecast = {}          # zone -> {"v": value, "for": epoch, "at": received}

        self.service_info = {
            "serviceID": self.service_id,
            "serviceName": "Telegram Control Bot",
            "availableServices": ["MQTT"],
            "servicesDetails": [
                {"serviceType": "MQTT",
                 "topic": ["home/+/sensor/+", "home/+/actuator/window", "home/+/forecast/temp"]}
            ]
        }

        self.mqtt_client = None
        self.bot = telepot.Bot(self.bot_token)

    def load_settings(self, filepath):
        try:
            with open(filepath, 'r') as f:
                settings = json.load(f)
                self.registry_url = settings.get("registry_url", "http://registry:8080")
                # The token is a secret: it comes from .env, never from a committed file.
                self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or settings.get("bot_token")
        except FileNotFoundError:
            print("Telegram bot settings.json not found.")
            sys.exit(1)

    # --- registry -----------------------------------------------------------

    def discover_and_register(self):
        print(f"Contacting registry at {self.registry_url}...")
        while True:
            try:
                resp = requests.get(f"{self.registry_url}/config", timeout=5)
                if resp.status_code == 200:
                    config = resp.json()
                    self.broker = config["broker"]["IP"]
                    self.port = config["broker"]["port"]
                    self.transport = config["broker"]["transport"]
                    self.users_db = config["usersList"]
                    print("Registry config received (broker and users loaded).")
                    break
            except Exception:
                time.sleep(3)

        try:
            resp = requests.post(f"{self.registry_url}/servicesList", json=self.service_info)
            if resp.status_code == 200:
                print("Telegram bot registered in catalog.")
        except Exception as e:
            print(f"Registration failed: {e}")

    def heartbeat_worker(self):
        while True:
            time.sleep(50)
            try:
                requests.put(f"{self.registry_url}/servicesList", json={"id": self.service_id}, timeout=5)
            except Exception:
                pass

    def login(self, username, password):
        # The catalog checks the password; the bot never sees the user list's passwords.
        try:
            resp = requests.post(f"{self.registry_url}/login",
                                 json={"userName": username, "password": password}, timeout=5)
            return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None

    def save_chat_id(self, user, chat_id):
        # Link (or unlink, with None) the Telegram chat to the user in the
        # catalog, so the session survives a bot restart.
        try:
            requests.put(f"{self.registry_url}/usersList",
                         json={"id": user["userID"], "chatID": chat_id}, timeout=5)
        except Exception:
            pass

    def restore_sessions(self):
        for user in self.users_db:
            if user.get("chatID"):
                self.sessions[user["chatID"]] = user
        if self.sessions:
            print(f"Restored {len(self.sessions)} Telegram session(s) from the catalog.")

    # --- mqtt ---------------------------------------------------------------

    def start_mqtt(self):
        self.mqtt_client = mqtt.Client(client_id=f"MQTT_{self.service_id}", transport=self.transport)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        self.mqtt_client.connect(self.broker, self.port, 60)
        self.mqtt_client.loop_start()

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Bot connected to MQTT broker.")
            self.mqtt_client.subscribe("home/+/sensor/+")
            self.mqtt_client.subscribe("home/+/actuator/window")
            self.mqtt_client.subscribe("home/+/forecast/temp")
        else:
            print(f"MQTT connection failed: {rc}")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            if not isinstance(payload, dict):
                return
            parts = msg.topic.split('/')
            if len(parts) < 4:
                return
            zone, kind = parts[1], parts[2]

            if kind == 'sensor':
                self.sensors.setdefault(zone, {})[parts[3]] = senml_value(payload)
            elif kind == 'forecast':
                value = senml_value(payload)
                if isinstance(value, (int, float)):
                    self.forecast[zone] = {"v": value, "for": payload["e"][0].get("t", 0), "at": time.time()}
            elif kind == 'actuator':
                position = payload.get("position")
                self.win_pos[zone] = position
                if msg.retain:
                    return  # last known position from before we started, not a new event
                self.alerts.setdefault(zone, deque(maxlen=ALERT_HISTORY)).append({
                    "t": time.strftime("%H:%M"),
                    "pos": position,
                    "src": payload.get("source", "auto"),
                    "reason": payload.get("reason", ""),
                })
                # Reflect the change in every open panel; no extra messages.
                self.refresh_panels_for(zone)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"MQTT parse error: {e}")

    def publish_position(self, zone, position, user_name):
        payload = json.dumps({
            "position": int(position),
            "reason": f"Telegram ({user_name})",
            "source": "manual",
        })
        self.mqtt_client.publish(f"home/{zone}/actuator/window", payload, retain=True)

    # --- rendering ----------------------------------------------------------

    def zone_key(self, room):
        return room.replace(" ", "").lower()

    def latest_alert(self, tz):
        history = self.alerts.get(tz)
        return history[-1] if history else None

    def fresh_forecast(self, tz):
        fc = self.forecast.get(tz)
        if not fc or time.time() - fc["at"] > 60:
            return None
        return fc

    def render_home(self, user):
        lines = [f"\U0001f3e0 <b>Smart Window</b> · {esc(user['userName'])}", ""]
        rooms = user.get('allowed_zones', [])
        for i, room in enumerate(rooms):
            tz = self.zone_key(room)
            pos = self.win_pos.get(tz)
            data = self.sensors.get(tz, {})
            lines.append(f"{state_icon(pos)} <b>{esc(room)}</b> — {position_text(pos)}")
            if data:
                fc = self.fresh_forecast(tz)
                ahead = ""
                if fc and data.get("temp") is not None:
                    ahead = f" {trend_arrow(data['temp'], fc['v'])} {fc['v']:.1f}°"
                lines.append(f"    \U0001f321 {fmt(data.get('temp'))}°C{ahead} · "
                             f"\U0001f4a7 {fmt(data.get('hum'))}% · "
                             f"\U0001f4a1 {fmt(data.get('light'))} lx")
            else:
                lines.append("    <i>waiting for data…</i>")
            alert = self.latest_alert(tz)
            if alert:
                lines.append(f"    \U0001f514 {alert['t']} {source_icon(alert['src'])} "
                             f"{position_text(alert['pos'])} · {short(alert['reason'])}")
            if i < len(rooms) - 1:
                lines.append(DIVIDER)
        lines += ["", f"<i>Updated {time.strftime('%H:%M:%S')}</i>"]

        rows = [[InlineKeyboardButton(text=r, callback_data=f"zone:{self.zone_key(r)}")
                 for r in rooms[i:i + 2]] for i in range(0, len(rooms), 2)]
        rows.append([
            InlineKeyboardButton(text="\U0001f504 Refresh", callback_data="home"),
            InlineKeyboardButton(text="\U0001f6aa Log out", callback_data="logout"),
        ])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def render_zone(self, room):
        tz = self.zone_key(room)
        pos = self.win_pos.get(tz)
        data = self.sensors.get(tz, {})
        alert = self.latest_alert(tz)

        lines = [f"{state_icon(pos)} <b>{esc(room)}</b>", ""]
        lines.append(f"\U0001fa9f <code>{window_bar(pos)}</code> {position_text(pos)}")
        lines.append("")
        if data:
            lines.append(f"\U0001f321 Temperature   <b>{fmt(data.get('temp'))}</b> °C")
            fc = self.fresh_forecast(tz)
            if fc and data.get("temp") is not None:
                minutes = max(1, round((fc["for"] - time.time()) / 60))
                lines.append(f"\U0001f4c8 Forecast      <b>{fc['v']:.1f}</b> °C "
                             f"{trend_arrow(data['temp'], fc['v'])} in ~{minutes} min")
            lines.append(f"\U0001f4a7 Humidity      <b>{fmt(data.get('hum'))}</b> %")
            lines.append(f"\U0001f4a1 Light         <b>{fmt(data.get('light'))}</b> lux")
        else:
            lines.append("<i>waiting for sensor data…</i>")
        if alert:
            lines += ["", f"{source_icon(alert['src'])} <b>Last action</b> · {alert['t']}",
                      f"<i>{pretty(alert['reason'])}</i>"]
        lines += ["", f"<i>Updated {time.strftime('%H:%M:%S')}</i>"]

        rows = []
        if room != "Outdoor":
            rows.append([InlineKeyboardButton(text=label, callback_data=f"set:{tz}:{value}")
                         for label, value in PRESETS])
        rows.append([
            InlineKeyboardButton(text="\U0001f4dc History", callback_data=f"hist:{tz}"),
            InlineKeyboardButton(text="\U0001f504 Refresh", callback_data=f"zone:{tz}"),
        ])
        rows.append([InlineKeyboardButton(text="⬅ Zones", callback_data="home")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def render_history(self, room):
        tz = self.zone_key(room)
        history = list(self.alerts.get(tz, []))
        lines = [f"\U0001f4dc <b>{esc(room)}</b> · last {ALERT_HISTORY} alerts", ""]
        if history:
            for alert in reversed(history):
                lines.append(f"\U0001f550 {alert['t']} · {source_icon(alert['src'])} "
                             f"→ <b>{position_text(alert['pos'])}</b>")
                if alert['reason']:
                    lines.append(f"    <i>{pretty(alert['reason'])}</i>")
        else:
            lines.append("<i>No alerts yet.</i>")

        rows = [[
            InlineKeyboardButton(text=f"⬅ {esc(room)}", callback_data=f"zone:{tz}"),
            InlineKeyboardButton(text="\U0001f504 Refresh", callback_data=f"hist:{tz}"),
        ]]
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    def render_view(self, chat_id):
        user = self.sessions.get(chat_id)
        if not user:
            return None
        view = self.panel.get(chat_id, {}).get("view", ("home",))
        if view[0] in ("zone", "hist"):
            room = next((r for r in user['allowed_zones'] if self.zone_key(r) == view[1]), None)
            if room:
                return self.render_zone(room) if view[0] == "zone" else self.render_history(room)
        return self.render_home(user)

    # --- single-panel plumbing ----------------------------------------------

    def send_panel(self, chat_id, view=("home",)):
        self.panel[chat_id] = {"msg": None, "view": view}
        text, keyboard = self.render_view(chat_id)
        sent = self.bot.sendMessage(chat_id, text, parse_mode='HTML', reply_markup=keyboard)
        self.panel[chat_id]["msg"] = sent['message_id']

    def update_panel(self, chat_id, view=None):
        info = self.panel.get(chat_id)
        if not info or not info.get("msg"):
            return
        if view is not None:
            info["view"] = view
        rendered = self.render_view(chat_id)
        if not rendered:
            return
        text, keyboard = rendered
        try:
            self.bot.editMessageText((chat_id, info["msg"]), text, parse_mode='HTML', reply_markup=keyboard)
        except Exception:
            pass  # unchanged content or edit window expired

    def refresh_panels_for(self, zone):
        for chat_id, user in list(self.sessions.items()):
            if any(self.zone_key(r) == zone for r in user.get('allowed_zones', [])):
                self.update_panel(chat_id)

    def panel_refresher(self):
        # Keep every open panel showing live values without any new messages.
        while True:
            time.sleep(PANEL_REFRESH_SEC)
            for chat_id in list(self.panel.keys()):
                self.update_panel(chat_id)

    # --- handlers -----------------------------------------------------------

    def handle_message(self, msg):
        chat_id = msg['chat']['id']
        text = msg.get('text', '')

        if text.startswith('/login'):
            parts = text.split()
            user = self.login(parts[1], parts[2]) if len(parts) == 3 else None
            if user:
                previous = self.sessions.get(chat_id)
                if previous and previous["userID"] != user["userID"]:
                    self.save_chat_id(previous, None)  # this chat now belongs to someone else
                self.sessions[chat_id] = user
                self.save_chat_id(user, chat_id)
                self.bot.sendMessage(chat_id, f"✅ Signed in as <b>{esc(user['userName'])}</b>",
                                     parse_mode='HTML', reply_markup=ReplyKeyboardRemove())
                self.send_panel(chat_id)
            else:
                self.bot.sendMessage(chat_id, "❌ Invalid credentials. Try: <code>/login user pass</code>",
                                     parse_mode='HTML')
            return

        if chat_id not in self.sessions:
            self.bot.sendMessage(chat_id, "\U0001f510 <b>Smart Window</b>\nSign in with "
                                          "<code>/login username password</code>",
                                 parse_mode='HTML', reply_markup=ReplyKeyboardRemove())
            return

        self.send_panel(chat_id)

    def handle_callback(self, query):
        query_id = query['id']
        chat_id = query['message']['chat']['id']
        message_id = query['message']['message_id']
        data = query.get('data', '')

        user = self.sessions.get(chat_id)
        if not user:
            self.bot.answerCallbackQuery(query_id, text="Session expired. Send /login again.")
            return

        # Always drive the message the tapped button lives on.
        self.panel[chat_id] = {"msg": message_id, "view": self.panel.get(chat_id, {}).get("view", ("home",))}
        allowed = user.get('allowed_zones', [])

        if data == "home":
            self.update_panel(chat_id, view=("home",))
            self.bot.answerCallbackQuery(query_id)

        elif data == "logout":
            self.save_chat_id(user, None)
            self.sessions.pop(chat_id, None)
            self.panel.pop(chat_id, None)
            try:
                self.bot.editMessageText((chat_id, message_id),
                                         "\U0001f44b Signed out. Send /login to return.", parse_mode='HTML')
            except Exception:
                pass
            self.bot.answerCallbackQuery(query_id, text="Signed out")

        elif data.startswith("zone:") or data.startswith("hist:"):
            kind, tz = data.split(":", 1)
            if not any(self.zone_key(r) == tz for r in allowed):
                self.bot.answerCallbackQuery(query_id, text="Not authorized")
                return
            self.update_panel(chat_id, view=(("zone" if kind == "zone" else "hist"), tz))
            self.bot.answerCallbackQuery(query_id)

        elif data.startswith("set:"):
            _, tz, value = data.split(":")
            room = next((r for r in allowed if self.zone_key(r) == tz), None)
            if not room or room == "Outdoor":
                self.bot.answerCallbackQuery(query_id, text="Not authorized")
                return
            position = int(value)
            self.publish_position(tz, position, user.get('userName'))
            self.bot.answerCallbackQuery(query_id, text=f"{room}: {position_text(position)}")
            # The panel updates when the command echoes back over MQTT.

    def custom_polling_loop(self):
        # Manual long-poll instead of telepot's MessageLoop, which can crash
        # when a user blocks or unblocks the bot.
        offset = None
        while True:
            try:
                for update in self.bot.getUpdates(offset=offset):
                    offset = update['update_id'] + 1
                    if 'message' in update:
                        self.handle_message(update['message'])
                    elif 'callback_query' in update:
                        self.handle_callback(update['callback_query'])
            except Exception:
                pass
            time.sleep(1)

    def start(self):
        if not self.bot_token:
            print("TELEGRAM_BOT_TOKEN is not set (see .env.example), the bot is disabled.")
            return
        # We poll with getUpdates, which Telegram refuses while a webhook is set.
        try:
            self.bot.deleteWebhook()
        except Exception as e:
            print(f"Could not reset the webhook: {e}")

        self.discover_and_register()
        self.restore_sessions()
        threading.Thread(target=self.heartbeat_worker, daemon=True).start()
        self.start_mqtt()
        threading.Thread(target=self.panel_refresher, daemon=True).start()
        threading.Thread(target=self.custom_polling_loop, daemon=True).start()
        print("Telegram bot is listening...")

        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()


if __name__ == "__main__":
    TelegramBot().start()
