#!/usr/bin/env python3
"""Поздравления бывших сотрудников с днём рождения в Telegram.

Запуск:
  python3 birthday.py              обычная работа (так запускает автозапуск)
  python3 birthday.py --login      один раз войти в Telegram (номер + код)
  python3 birthday.py --preview    показать, кого и каким текстом поздравим, без отправки
  python3 birthday.py --test       отправить пример поздравления себе в «Избранное»
  python3 birthday.py --date 2026-10-02 --preview   проверить любую дату
"""
import argparse
import asyncio
import datetime as dt
import json
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
SENT = HERE / "sent.json"
LOG = HERE / "log.txt"
SESSION = HERE / "telegram"

# Частые имена: по ним программа понимает, где в ФИО имя
# («Анна Иванова» и «Петров Иван» → «Анна», «Иван»).
FIRST_NAMES = set("""
александра алена алёна алина алла анастасия ангелина анна антонина арина валентина валерия
варвара вера вероника виктория галина дарья диана ева евгения екатерина елена елизавета жанна
зинаида зоя инна ирина карина кира кристина ксения лариса лидия лилия любовь людмила маргарита
марина мария милана надежда наталья наталия нина оксана олеся ольга полина раиса регина римма
светлана снежана софия софья таисия тамара татьяна ульяна юлия яна эльвира альбина гульнара
александр алексей анатолий андрей антон артем артём борис вадим валентин валерий василий виктор
виталий владимир владислав вячеслав геннадий георгий григорий даниил денис дмитрий евгений егор
иван игорь илья кирилл константин леонид максим михаил никита николай олег павел петр пётр
роман руслан сергей станислав степан тимофей тимур федор фёдор эдуард юрий ярослав
""".split())


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M}  {msg}"
    print(line)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(text):
    """Уведомление в правом верхнем углу экрана Mac."""
    text = text.replace('"', "'")
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "Поздравления 🎂"'],
                   check=False)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_phone(raw):
    """89000000000 / +7 900 000-00-00 / 9000000000 → 79000000000. Ошибочный номер → None."""
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "7" + digits
    return None


def first_name(person):
    if person.get("call"):
        return person["call"]
    fio = re.sub(r"\(.*?\)", " ", person["fio"])
    for word in fio.split():
        if word.lower() in FIRST_NAMES:
            return word.capitalize()
    return ""


def target_dates(today, weekend_mode):
    """Какие дни рождения поздравляем сегодня: [(дата, 'today'|'early'|'late')]."""
    targets = [(today, "today")]
    if weekend_mode == "friday" and today.weekday() == 4:
        targets += [(today + dt.timedelta(days=d), "early") for d in (1, 2)]
    if weekend_mode == "monday" and today.weekday() == 0:
        targets += [(today - dt.timedelta(days=d), "late") for d in (2, 1)]
    return targets


def month_days(day):
    """Для даты — строки "ММ-ДД", включая 29 февраля в невисокосный год (поздравим 28-го)."""
    result = [day.strftime("%m-%d")]
    is_leap = day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0)
    if day.month == 2 and day.day == 28 and not is_leap:
        result.append("02-29")
    return result


def fetch_people(cfg, dates):
    query = urllib.parse.urlencode({"token": cfg["sheet_token"], "dates": ",".join(dates)})
    with urllib.request.urlopen(cfg["sheet_url"] + "?" + query, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError("Таблица ответила: " + data["error"])
    return data["people"]


def make_text(cfg, name, kind):
    greeting = cfg["greeting"][kind]
    if name:
        greeting = greeting.format(name=name)
    else:
        greeting = greeting.replace("{name}, ", "")
        greeting = greeting[:1].upper() + greeting[1:]
    body = random.choice(cfg["wishes"])
    parts = [greeting, body, cfg.get("signature", "")]
    return "\n\n".join(p for p in parts if p)


def plan(cfg, today):
    """Список поздравлений на сегодня: [(person, phone, kind, text)]."""
    targets = target_dates(today, cfg.get("weekend", "friday"))
    kind_by_md = {}
    for day, kind in targets:
        for md in month_days(day):
            kind_by_md[md] = kind
    people = fetch_people(cfg, list(kind_by_md))
    result = []
    for p in people:
        kind = kind_by_md[p["birthday"]]
        text = make_text(cfg, first_name(p), kind)
        result.append((p, normalize_phone(p["phone"]), kind, text))
    return result


async def find_user(client, phone, fio):
    from telethon import errors, functions, types
    try:
        return await client.get_entity("+" + phone)  # уже есть в контактах
    except (ValueError, TypeError, errors.RPCError):
        pass
    res = await client(functions.contacts.ImportContactsRequest([
        types.InputPhoneContact(client_id=random.randrange(1 << 62),
                                phone="+" + phone, first_name=fio, last_name="")
    ]))
    return res.users[0] if res.users else None


async def telegram_client(cfg):
    from telethon import TelegramClient
    client = TelegramClient(str(SESSION), int(cfg["api_id"]), cfg["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        notify("Нужно войти в Telegram: запустите вход по инструкции")
        log("ОШИБКА: не выполнен вход в Telegram (нужен запуск с --login)")
        sys.exit(1)
    return client


async def run(cfg, today):
    sent = load_json(SENT, {})
    key = lambda person, phone: f"{today.year}:{phone or person['fio']}"
    todo = [item for item in plan(cfg, today) if key(item[0], item[1]) not in sent]
    if not todo:
        log("Сегодня поздравлять некого (или все уже поздравлены)")
        return

    client = await telegram_client(cfg)
    ok, failed = [], []
    try:
        for person, phone, kind, text in todo:
            fio = person["fio"]
            problem = None
            if not phone:
                problem = f"неправильный номер «{person['phone']}» (строка {person['row']})"
            else:
                try:
                    user = await find_user(client, phone, fio)
                    if user:
                        await client.send_message(user, text)
                    else:
                        problem = "не найден в Telegram по номеру"
                except Exception as e:  # одна ошибка не должна мешать остальным
                    problem = str(e)
            # Запоминаем в любом случае, чтобы не писать человеку и не слать отчёт повторно
            stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M} {fio}"
            if problem:
                failed.append(f"{fio}: {problem}")
                log(f"НЕ ОТПРАВЛЕНО: {fio}: {problem}")
                sent[key(person, phone)] = "НЕ ОТПРАВЛЕНО " + stamp
            else:
                ok.append(fio)
                log(f"Поздравили: {fio}")
                sent[key(person, phone)] = stamp
            save_json(SENT, sent)
            await asyncio.sleep(5)

        report = [f"🎂 Поздравления {today:%d.%m.%Y}"]
        if ok:
            report.append("✅ Поздравили:\n" + "\n".join("• " + n for n in ok))
        if failed:
            report.append("⚠️ Не получилось — поздравьте, пожалуйста, вручную:\n"
                          + "\n".join("• " + f for f in failed))
        await client.send_message("me", "\n\n".join(report))  # отчёт в «Избранное»
    finally:
        await client.disconnect()

    notify(f"Поздравили: {len(ok)}" + (f", не получилось: {len(failed)}" if failed else ""))


async def login(cfg):
    from telethon import TelegramClient
    client = TelegramClient(str(SESSION), int(cfg["api_id"]), cfg["api_hash"])
    await client.start()  # спросит номер телефона и код из Telegram
    me = await client.get_me()
    print(f"\n✅ Вход выполнен: {me.first_name}. Можно закрыть это окно.")
    await client.disconnect()


async def test(cfg):
    client = await telegram_client(cfg)
    try:
        text = make_text(cfg, "Елена", "today")
        await client.send_message("me", "🧪 Так будет выглядеть поздравление:\n\n" + text)
        print("✅ Пример отправлен вам в «Избранное» в Telegram")
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--date", help="ГГГГ-ММ-ДД, для проверки")
    args = ap.parse_args()

    cfg = load_json(CONFIG, None)
    if cfg is None:
        sys.exit("Не найден файл config.json рядом с программой")
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    if args.login:
        return asyncio.run(login(cfg))
    if args.test:
        return asyncio.run(test(cfg))
    if args.preview:
        items = plan(cfg, today)
        print(f"{today:%d.%m.%Y}: поздравлений — {len(items)}\n")
        for person, phone, kind, text in items:
            print(f"── {person['fio']}  ({person['birthday']}, тел. {phone or 'ОШИБКА В НОМЕРЕ'})")
            print(text, "\n")
        return

    # Автозапуск: только по будням и не раньше указанного времени
    if today.weekday() >= 5:
        return
    not_before = dt.time.fromisoformat(cfg.get("not_before", "08:00"))
    if dt.datetime.now().time() < not_before:
        return
    try:
        asyncio.run(run(cfg, today))
    except Exception as e:
        log(f"ОШИБКА: {e}")
        sent = load_json(SENT, {})
        if sent.get("last_error_notice") != str(today):  # не больше одного уведомления в день
            sent["last_error_notice"] = str(today)
            save_json(SENT, sent)
            notify("Не удалось проверить дни рождения, попробую ещё раз позже")
        sys.exit(1)


if __name__ == "__main__":
    main()
