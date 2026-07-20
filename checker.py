# -*- coding: utf-8 -*-
"""
Мониторинг дешёвых билетов Калининград (KGD) -> Шарм-эль-Шейх (SSH).
Прямые рейсы, окно дат 27.07-10.08.2026, порог цены THRESHOLD_RUB.
Данные: Travelpayouts (кэш цен Aviasales). Уведомления: Telegram.

Переменные окружения:
  TP_TOKEN     - токен Travelpayouts (обязательно)
  TG_BOT_TOKEN - токен Telegram-бота (без него алерты только в консоль)
  TG_CHAT_ID   - chat_id получателя в Telegram
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ORIGIN = "KGD"
DESTINATION = "SSH"
DATE_FROM = date(2026, 7, 20)
DATE_TO = date(2026, 8, 10)
THRESHOLD_RUB = 20_000
# Летом Египет живёт по UTC+3 (Калининград — UTC+2); нужно для местного времени прилёта
EGYPT_TZ = timezone(timedelta(hours=3))
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def fetch_month(tp_token: str, month: str) -> list:
    params = urllib.parse.urlencode({
        "origin": ORIGIN,
        "destination": DESTINATION,
        "departure_at": month,          # формат YYYY-MM: все даты месяца одним запросом
        "one_way": "true",
        "direct": "true",
        "currency": "rub",
        "limit": 100,
        "token": tp_token,
    })
    with urllib.request.urlopen(f"{API_URL}?{params}", timeout=30) as resp:
        payload = json.load(resp)
    if not payload.get("success"):
        raise RuntimeError(f"API вернул ошибку: {payload}")
    return payload.get("data", [])


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def flight_times(f: dict) -> tuple:
    """Вылет (местное время Калининграда) и прилёт (местное время Египта)."""
    dep = datetime.fromisoformat(f["departure_at"])
    arr = (dep + timedelta(minutes=f["duration"])).astimezone(EGYPT_TZ)
    return dep, arr


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage", data=data
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def main() -> None:
    tp_token = os.environ.get("TP_TOKEN")
    if not tp_token:
        sys.exit("Не задан TP_TOKEN")
    bot_token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")

    flights = fetch_month(tp_token, "2026-07") + fetch_month(tp_token, "2026-08")

    in_window = []
    for f in flights:
        dep = f.get("departure_at", "")[:10]
        try:
            dep_date = date.fromisoformat(dep)
        except ValueError:
            continue
        if DATE_FROM <= dep_date <= DATE_TO:
            in_window.append(f)

    in_window.sort(key=lambda f: f["departure_at"])
    print(f"Рейсов в окне {DATE_FROM}—{DATE_TO}: {len(in_window)}")
    for f in in_window:
        dep, arr = flight_times(f)
        print(f"  {dep:%d.%m %H:%M} -> {arr:%d.%m %H:%M}  "
              f"{f['airline']}{f['flight_number']}  {f['price']} руб.")

    cheap = [f for f in in_window if f["price"] < THRESHOLD_RUB]

    # Алерт шлём только если по этой дате ещё не слали, либо цена стала ниже
    state = load_state()
    new_alerts = []
    for f in cheap:
        key = f["departure_at"][:10]
        prev = state.get(key)
        if prev is None or f["price"] < prev:
            new_alerts.append(f)
            state[key] = f["price"]

    # Даты, где цена поднялась обратно выше порога, убираем из состояния,
    # чтобы поймать следующее падение
    cheap_dates = {f["departure_at"][:10] for f in cheap}
    for key in list(state):
        if key not in cheap_dates:
            del state[key]

    if not new_alerts:
        print("Билетов дешевле порога нет (или алерт уже отправлен ранее).")
        save_state(state)
        return

    lines = [f"✈️ <b>Найден билет дешевле {THRESHOLD_RUB:,} ₽!</b>".replace(",", " ")]
    for f in new_alerts:
        dep, arr = flight_times(f)
        url = "https://www.aviasales.ru" + f["link"]
        hours, minutes = divmod(f["duration"], 60)
        lines.append(
            f"\n📅 <b>{dep:%d.%m.%Y}</b> — рейс {f['airline']} {f['flight_number']}, "
            f"прямой, Калининград → Шарм-эль-Шейх\n"
            f"🛫 Вылет: {dep:%H:%M} (местное) — 🛬 Прилёт: {arr:%d.%m} {arr:%H:%M} (местное)\n"
            f"⏱ В пути: {hours} ч {minutes:02d} мин\n"
            f"💰 Цена: <b>{f['price']:,} ₽</b>".replace(",", " ") +
            f'\n<a href="{url}">Купить на Aviasales</a>'
        )
    message = "\n".join(lines)
    print(message)

    if bot_token and chat_id:
        send_telegram(bot_token, chat_id, message)
        print("Уведомление отправлено в Telegram.")
    else:
        print("TG_BOT_TOKEN/TG_CHAT_ID не заданы — уведомление не отправлено.")

    save_state(state)


if __name__ == "__main__":
    main()
