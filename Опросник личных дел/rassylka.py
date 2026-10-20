#!/usr/bin/env python3
"""Рассылка опросника личных дел через Битрикс24.

  python3 rassylka.py proba   — одно письмо только Диане («Мои заметки»)
  python3 rassylka.py vse     — всем из sotrudniki.csv, после подтверждения «да»
  python3 rassylka.py napominanie otvety.csv — на 5-й день тем, кто не ответил, после «да»

Ключ (вебхук) Битрикс24 берётся из Связки ключей Mac (запись «HR Битрикс24»).
Кому уже отправлено, записано в zhurnal_rassylki.csv: повторно им не шлём.
"""
import csv
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime

PAPKA = os.path.dirname(os.path.abspath(__file__))
NASTROYKI = json.load(open(os.path.join(PAPKA, "nastroyki.json"), encoding="utf-8"))
ZHURNAL = os.path.join(PAPKA, "zhurnal_rassylki.csv")
SOTRUDNIKI = os.path.join(PAPKA, "sotrudniki.csv")
NE_NAYDENY = os.path.join(PAPKA, "net_v_bitrix24.csv")
NEPONYATNO = os.path.join(PAPKA, "napominanie_proverit.csv")


def klyuch():
    sluzhba = NASTROYKI["keychain_bitrix"]
    r = subprocess.run(["security", "find-generic-password", "-s", sluzhba, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"Нет ключа Битрикс24 в Связке ключей. Добавьте его один раз командой:\n"
                 f"  security add-generic-password -s \"{sluzhba}\" -a webhook -w\n"
                 f"(Mac попросит вставить адрес вебхука, на экране он не появится.)")
    return r.stdout.strip().rstrip("/") + "/"


def bitrix(vebhuk, metod, parametry):
    dannye = urllib.parse.urlencode(parametry, doseq=True).encode()
    with urllib.request.urlopen(vebhuk + metod + ".json", data=dannye, timeout=30) as otvet:
        rez = json.load(otvet)
    if "error" in rez:
        raise RuntimeError(f"{metod}: {rez.get('error')} {rez.get('error_description', '')}")
    return rez


def ssylka(fio):
    return (f"https://docs.google.com/forms/d/e/{NASTROYKI['forma_id']}/viewform?usp=pp_url"
            f"&entry.{NASTROYKI['forma_entry_fio']}={urllib.parse.quote(fio)}")


def pismo(fio, shablon="tekst_pisma"):
    chasti = fio.split()
    imya = chasti[1] if len(chasti) > 1 else fio
    return NASTROYKI[shablon].format(imya=imya, ssylka=ssylka(fio))


def norm(s):
    return " ".join(s.replace("ё", "е").replace("Ё", "Е").lower().split())


def slova(s):
    return re.findall(r"[а-яa-z]+", norm(s))


def iz_zhurnala(status):
    """ФИО -> ID Битрикс24 для строк журнала с этим статусом."""
    if not os.path.exists(ZHURNAL):
        return {}
    with open(ZHURNAL, encoding="utf-8") as f:
        return {r["ФИО"]: r["ID Битрикс24"] for r in csv.DictReader(f) if r["Статус"] == status}


def uzhe_otpravleno():
    return set(iz_zhurnala("отправлено"))


def zapisat(fio, uid, status):
    novyy = not os.path.exists(ZHURNAL)
    with open(ZHURNAL, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if novyy:
            w.writerow(["ФИО", "ID Битрикс24", "Время", "Статус"])
        w.writerow([fio, uid, datetime.now().strftime("%d.%m.%Y %H:%M"), status])


def vse_polzovateli(vebhuk):
    lyudi, start = [], 0
    while True:
        rez = bitrix(vebhuk, "user.get", {"ACTIVE": "true", "start": start})
        lyudi += rez["result"]
        if "next" not in rez:
            return lyudi
        start = rez["next"]


def proba(vebhuk):
    ya = bitrix(vebhuk, "user.current", {})["result"]
    fio = " ".join(x for x in (ya.get("LAST_NAME"), ya.get("NAME"), ya.get("SECOND_NAME")) if x)
    rez = bitrix(vebhuk, "im.message.add", {"DIALOG_ID": ya["ID"], "MESSAGE": pismo(fio)})
    print(f"Проба отправлена вам ({fio}) в «Мои заметки». ID сообщения: {rez['result']}")


def vse(vebhuk):
    if not os.path.exists(SOTRUDNIKI):
        sys.exit("Нет файла sotrudniki.csv (столбец «ФИО»). Как его сделать, написано в «Как запускать.md».")
    with open(SOTRUDNIKI, encoding="utf-8-sig") as f:
        spisok = [r["ФИО"].strip() for r in csv.DictReader(f) if r.get("ФИО", "").strip()]
    po_fio = {}
    for u in vse_polzovateli(vebhuk):
        klyuch_fio = norm(f"{u.get('LAST_NAME', '')} {u.get('NAME', '')}")
        po_fio.setdefault(klyuch_fio, []).append(u["ID"])
    otpravleno = uzhe_otpravleno()
    if otpravleno:
        print(f"Внимание: рассылка уже была, отправлено писем: {len(otpravleno)}. "
              f"Этим людям повторно не пишем.")
    plan, net = [], []
    for fio in spisok:
        if fio in otpravleno:
            continue
        chasti = norm(fio).split()
        ids = po_fio.get(" ".join(chasti[:2]), [])
        if len(ids) == 1:
            plan.append((fio, ids[0]))
        else:
            net.append((fio, "нет в Битрикс24" if not ids else "несколько с таким именем"))
    with open(NE_NAYDENY, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ФИО", "Причина"])
        w.writerows(net)
    print(f"Отправить: {len(plan)}. Уже отправлено раньше: {len(otpravleno)}. "
          f"Не найдены в Битрикс24: {len(net)} (список в net_v_bitrix24.csv).")
    if not plan:
        sys.exit("Отправлять некому.")
    if input("Отправлять? Напишите да: ").strip().lower() != "да":
        sys.exit("Ничего не отправлено.")
    for fio, uid in plan:
        try:
            bitrix(vebhuk, "im.message.add", {"DIALOG_ID": uid, "MESSAGE": pismo(fio)})
            zapisat(fio, uid, "отправлено")
        except Exception as e:
            zapisat(fio, uid, f"ошибка: {e}")
            print(f"Ошибка у {fio}: {e}")
    print("Готово. Итог в zhurnal_rassylki.csv.")


def otvetivshie(put):
    """ФИО из ответов с начала волны (лист «Ответы на форму (1)», скачанный как CSV)."""
    nachalo = datetime.strptime(NASTROYKI["nachalo_volny"], "%d.%m.%Y")
    with open(put, encoding="utf-8-sig") as f:
        stroki = list(csv.reader(f))
    shapka = stroki[0]
    try:
        i_fio = next(i for i, z in enumerate(shapka) if z.strip().startswith("ФИО"))
        i_vr = next(i for i, z in enumerate(shapka) if z.strip().startswith("Отметка времени"))
    except StopIteration:
        sys.exit("В файле ответов нет столбца «ФИО» или «Отметка времени».")
    fio = []
    for r in stroki[1:]:
        try:
            if datetime.strptime(r[i_vr].strip(), "%d.%m.%Y %H:%M:%S") >= nachalo and r[i_fio].strip():
                fio.append(r[i_fio].strip())
        except (ValueError, IndexError):
            continue
    return fio


def napominanie(vebhuk, put):
    otpravleno = iz_zhurnala("отправлено")
    if not otpravleno:
        sys.exit("Рассылки ещё не было (zhurnal_rassylki.csv пуст), напоминать некому.")
    uzhe = iz_zhurnala("напоминание")
    zhdem = {fio: uid for fio, uid in otpravleno.items() if fio not in uzhe}
    proverit = []
    for napisano in otvetivshie(put):
        s = set(slova(napisano))
        tochno = [fio for fio in zhdem if set(slova(fio)[:2]) <= s]
        if len(tochno) == 1:
            zhdem.pop(tochno[0])
            continue
        # Непонятно, кто ответил: всех с такой фамилией не трогаем, показываем Диане
        famil = [fio for fio in zhdem if slova(fio)[:1] and slova(fio)[0] in s]
        for fio in famil:
            zhdem.pop(fio)
        proverit.append([napisano, "; ".join(famil) or "никого из рассылки"])
    with open(NEPONYATNO, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Как написано в ответе", "Кому из-за этого не напоминаем"])
        w.writerows(proverit)
    print(f"Не ответили, отправить напоминание: {len(zhdem)}. Напоминание уже было: {len(uzhe)}. "
          f"Непонятных ответов: {len(proverit)} (список в napominanie_proverit.csv).")
    if not zhdem:
        sys.exit("Напоминать некому.")
    if input("Отправлять напоминание? Напишите да: ").strip().lower() != "да":
        sys.exit("Ничего не отправлено.")
    for fio, uid in zhdem.items():
        try:
            bitrix(vebhuk, "im.message.add", {"DIALOG_ID": uid, "MESSAGE": pismo(fio, "tekst_napominaniya")})
            zapisat(fio, uid, "напоминание")
        except Exception as e:
            zapisat(fio, uid, f"ошибка напоминания: {e}")
            print(f"Ошибка у {fio}: {e}")
    print("Готово. Итог в zhurnal_rassylki.csv.")


if __name__ == "__main__":
    rezhim = sys.argv[1] if len(sys.argv) > 1 else ""
    if rezhim == "proba":
        proba(klyuch())
    elif rezhim == "vse":
        vse(klyuch())
    elif rezhim == "napominanie" and len(sys.argv) > 2:
        napominanie(klyuch(), sys.argv[2])
    else:
        sys.exit(__doc__)
