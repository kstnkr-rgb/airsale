# -*- coding: utf-8 -*-
"""
Мониторинг дешёвых билетов Калининград (KGD) <-> Санкт-Петербург (LED), туда-обратно.
Туда:    06-07 августа 2026
Обратно: 09-10 августа 2026
3 пассажира. Цель: суммарно на троих <= 30 000 руб (т.е. <= 10 000 руб/чел за круг).
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
from datetime import date, datetime, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ORIGIN = "KGD"
DESTINATION = "LED"
OUTBOUND_DATES = [date(2026, 8, 6), date(2026, 8, 7)]     # туда
RETURN_DATES = [date(2026, 8, 9), date(2026, 8, 10)]      # обратно
PASSENGERS = 3
TOTAL_THRESHOLD_RUB = 30_000                              # суммарно на всех пассажиров за круг
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state_spb.json")

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def fetch_month(tp_token: str, origin: str, destination: str, month: str) -> list:
    params = urllib.parse.urlencode({
        "origin": origin,
        "destination": destination,
        "departure_at": month,          # формат YYYY-MM: все даты месяца одним запросом
        "one_way": "true",
        "currency": "rub",
        "limit": 100,
        "token": tp_token,
    })
    with urllib.request.urlopen(f"{API_URL}?{params}", timeout=30) as resp:
        payload = json.load(resp)
    if not payload.get("success"):
        raise RuntimeError(f"API вернул ошибку: {payload}")
    return payload.get("data", [])


def cheapest_by_dates(flights: list, wanted: list) -> dict:
    """Для каждой нужной даты — самый дешёвый рейс (dict) или None."""
    result = {d: None for d in wanted}
    for f in flights:
        try:
            dep = date.fromisoformat(f.get("departure_at", "")[:10])
        except ValueError:
            continue
        if dep in result and (result[dep] is None or f["price"] < result[dep]["price"]):
            result[dep] = f
    return result


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def fmt_flight(f: dict) -> str:
    dep = datetime.fromisoformat(f["departure_at"])
    arr = dep + timedelta(minutes=f["duration"])
    tr = "прямой" if f["transfers"] == 0 else f"пересадок: {f['transfers']}"
    return (f"{dep:%d.%m %H:%M} → {arr:%H:%M}  "
            f"{f['airline']}{f['flight_number']}  "
            f"{f['price']:,} ₽/чел  ({tr})".replace(",", " "))


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


def search_link(out_f: dict, ret_f: dict) -> str:
    """Ссылка на Aviasales с готовым поиском туда-обратно на PASSENGERS человек."""
    out_d = datetime.fromisoformat(out_f["departure_at"])
    ret_d = datetime.fromisoformat(ret_f["departure_at"])
    code = (f"{ORIGIN}{out_d:%d%m}{DESTINATION}{ret_d:%d%m}{PASSENGERS}")
    return f"https://www.aviasales.ru/search/{code}"


def main() -> None:
    tp_token = os.environ.get("TP_TOKEN")
    if not tp_token:
        sys.exit("Не задан TP_TOKEN")
    bot_token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")

    out_flights = fetch_month(tp_token, ORIGIN, DESTINATION, "2026-08")
    ret_flights = fetch_month(tp_token, DESTINATION, ORIGIN, "2026-08")

    out_by_date = cheapest_by_dates(out_flights, OUTBOUND_DATES)
    ret_by_date = cheapest_by_dates(ret_flights, RETURN_DATES)

    print("=== Туда (KGD → LED) ===")
    for d in OUTBOUND_DATES:
        print(f"  {d}: " + (fmt_flight(out_by_date[d]) if out_by_date[d] else "нет рейсов"))
    print("=== Обратно (LED → KGD) ===")
    for d in RETURN_DATES:
        print(f"  {d}: " + (fmt_flight(ret_by_date[d]) if ret_by_date[d] else "нет рейсов"))

    # Лучший (самый дешёвый) вариант по каждому направлению
    out_options = [f for f in out_by_date.values() if f]
    ret_options = [f for f in ret_by_date.values() if f]
    if not out_options or not ret_options:
        print("Нет рейсов на одну из сторон — круг не собрать.")
        return

    best_out = min(out_options, key=lambda f: f["price"])
    best_ret = min(ret_options, key=lambda f: f["price"])
    per_person = best_out["price"] + best_ret["price"]
    total = per_person * PASSENGERS
    print(f"\nЛучший круг: {per_person:,} ₽/чел  ×{PASSENGERS}  = {total:,} ₽ (порог {TOTAL_THRESHOLD_RUB:,})"
          .replace(",", " "))

    if total > TOTAL_THRESHOLD_RUB:
        print("Дороже порога — уведомление не шлём.")
        return

    # Антиспам: шлём только если такого дешёвого варианта ещё не было
    state = load_state()
    if state.get("last_total") is not None and total >= state["last_total"]:
        print("Такой вариант (или дешевле) уже отправлен ранее.")
        return

    link = search_link(best_out, best_ret)
    message = (
        f"🎯 <b>Калининград ⇄ Санкт-Петербург укладывается в {TOTAL_THRESHOLD_RUB:,} ₽ на {PASSENGERS} чел!</b>\n\n"
        f"🛫 <b>Туда:</b> {fmt_flight(best_out)}\n"
        f"🛬 <b>Обратно:</b> {fmt_flight(best_ret)}\n\n"
        f"💰 {per_person:,} ₽/чел × {PASSENGERS} = <b>{total:,} ₽</b> за всех\n"
        f'<a href="{link}">Открыть поиск на Aviasales ({PASSENGERS} пасс.)</a>'
    ).replace(",", " ")
    print("\n" + message)

    if bot_token and chat_id:
        send_telegram(bot_token, chat_id, message)
        print("Уведомление отправлено в Telegram.")
    else:
        print("TG_BOT_TOKEN/TG_CHAT_ID не заданы — уведомление не отправлено.")

    state["last_total"] = total
    save_state(state)


if __name__ == "__main__":
    main()
