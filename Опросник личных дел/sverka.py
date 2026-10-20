#!/usr/bin/env python3
"""Сверка ответов опросника с общей таблицей. Ничего не меняет, только делает отчёт.

  python3 sverka.py otvety.csv obshaya.csv [--s ДД.ММ.ГГГГ]

otvety.csv  — лист «Ответы на форму (1)», скачанный как CSV
obshaya.csv — лист «ОБЩАЯ таблица по сотрдуникам», скачанный как CSV
--s         — с какой даты брать ответы (по умолчанию nachalo_volny из nastroyki.json)

Результат в папке otchet/: izmeneniya.csv, bez_stolbca.csv, ne_opoznany.csv.
"""
import csv
import json
import os
import re
import sys
from datetime import datetime

PAPKA = os.path.dirname(os.path.abspath(__file__))
NASTROYKI = json.load(open(os.path.join(PAPKA, "nastroyki.json"), encoding="utf-8"))

PROPUSK = {"", "-", "—", ".", "нет", "не", "нету", "0", "осталось неизменным за последние полгода"}
NET_OTVETA = PROPUSK | {"не получала", "не получал", "не было", "никакое", "ни какое", "никакого",
                        "нет хобби", "не проходила", "не обучалась"}
STANDART_ZHILYE = {"в собственности", "в ипотеке", "живу у родственников/родителей", "арендую"}
SLOVA_CHISLA = {"один": 1, "одна": 1, "двое": 2, "два": 2, "две": 2, "трое": 3, "три": 3, "четверо": 4}
DOP_VOPROSY = [("Какое дополнительное", "Дополнительное образование/обучение"),
               ("Есть ли у Вас хобби", "Хобби, увлечения"),
               ("Наличие иждивенцев", "Иждивенцы"),
               ("Какие регулярные расходы", "Регулярные расходы"),
               ("Есть ли новые события", "Новые события")]
# О чём сразу сообщать Диане: ищем в ответе «Новые события»
VAZHNOE = r"болез|болен|больн|онколог|диагноз|операци|умер|умерл|смерт|скончал|похорон|утрат|беремен|декрет|инвалид|кредит|займ"


def norm(s):
    return " ".join((s or "").replace("ё", "е").replace("Ё", "Е").lower().split())


def stolbec(zagolovki, nachalo):
    for i, z in enumerate(zagolovki):
        if z.strip().startswith(nachalo):
            return i
    sys.exit(f"Не нашёл столбец «{nachalo}». Возможно, изменили форму или таблицу.")


def stolbec_esli_est(zagolovki, nachalo):
    for i, z in enumerate(zagolovki):
        if z.strip().startswith(nachalo):
            return i
    return None


def bukva(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def chitat(put):
    with open(put, encoding="utf-8-sig") as f:
        return list(csv.reader(f))


# --- Перевод ответа формы в слова общей таблицы. Возвращает (значение, пометка) или None = пропуск.

def semya(otvet):
    o = norm(otvet)
    if o in PROPUSK:
        return None
    karta = {"замужем/женат": "Замужем/женат", "не замужем/не женат": "Не замужем/не женат",
             "в разводе": "В разводе"}
    if o in karta:
        return karta[o], ""
    return None, f"Уточнить: нестандартный ответ «{otvet.strip()}»"


def zhilye(otvet):
    o = norm(otvet)
    if o in PROPUSK:
        return None
    if "ипотек" in o:
        znach = "Ипотека"
    elif "родител" in o or "родствен" in o:
        znach = "Живет у родителей"
    elif "аренд" in o:
        znach = "Аренда"
    elif "собствен" in o or re.search(r"\bсво[еёяй]", o):
        znach = "В собственности"
    else:
        return None, f"Уточнить: нестандартный ответ «{otvet.strip()}»"
    return znach, "" if o in STANDART_ZHILYE else f"Проверить: ответ был «{otvet.strip()}»"


def da_net(otvet, sport=False):
    o = norm(otvet)
    if o in PROPUSK - {"нет"}:
        return None
    karta = {"да": "Да", "нет": "Нет", "да, в кредите": "Да, в кредите"}
    if o in karta:
        return karta[o], ""
    if sport and not re.search(r"\bне\b|временно|планир|хочу", o):
        return "Да", f"Проверить: ответ был «{otvet.strip()}»"
    return None, f"Уточнить: нестандартный ответ «{otvet.strip()}»"


def deti(otvet):
    """(количество, возрасты) или None, если детей нет или ответа нет."""
    o = norm(otvet)
    if o in PROPUSK or re.fullmatch(r"(детей )?нет( детей)?.*", o):
        return None
    chisla = [int(x) for x in re.findall(r"\d+", o)]
    pervoe = o.split()[0].strip(",.:(") if o.split() else ""
    if pervoe in SLOVA_CHISLA:
        return SLOVA_CHISLA[pervoe], chisla
    if re.match(r"^\d+\s*(-?е|ребен|дет|\(|:|,|\.|-|\s*$)", o) and chisla:
        return chisla[0], chisla[1:]
    return None


def kolvo_v_tablice(tekst):
    m = re.match(r"^\s*(\d+)\s*[-(.,]", tekst or "")
    return int(m.group(1)) if m else None


# --- Основная сверка

def main():
    argi = sys.argv[1:]
    if len(argi) < 2:
        sys.exit(__doc__)
    s_daty = NASTROYKI["nachalo_volny"]
    if "--s" in argi:
        s_daty = argi[argi.index("--s") + 1]
    s_daty = datetime.strptime(s_daty, "%d.%m.%Y")

    otv = chitat(argi[0])
    obsh = chitat(argi[1])
    zo, zt = otv[0], obsh[0]
    o_fio, o_vremya = stolbec(zo, "ФИО"), stolbec(zo, "Отметка времени")
    o_pole = {"sem": stolbec(zo, "Семейное положение"), "deti": stolbec(zo, "Наличие детей"),
              "kolvo": stolbec(zo, "Кол-во, возраст"), "zhil": stolbec(zo, "Жилье"),
              "avto": stolbec(zo, "Наличие авто"), "kred": stolbec(zo, "Наличие потреб"),
              "sport": stolbec(zo, "Занятие спортом")}
    o_dop = [(stolbec(zo, nach), nazv) for nach, nazv in DOP_VOPROSY]
    t = {"fio": stolbec(zt, "ФИО"), "sem": stolbec(zt, "Семейное положение"),
         "deti": stolbec(zt, "Наличие детей"), "kolvo": stolbec(zt, "Кол-во, возраст"),
         "zhil": stolbec(zt, "Жилье"), "avto": stolbec(zt, "Наличие авто"),
         "kred": stolbec(zt, "Наличие потреб"), "sport": stolbec(zt, "Занятие спортом")}
    t_dop_info = stolbec_esli_est(zt, "Дополнительная информация")
    o_sobytiya = stolbec(zo, "Есть ли новые события")

    # Сотрудники общей таблицы: строка в таблице (с 1) -> ФИО
    sotr = {}
    for i, r in enumerate(obsh[1:], start=2):
        fio = r[t["fio"]].strip() if len(r) > t["fio"] else ""
        if fio and not fio.startswith("Дата"):
            sotr[i] = fio

    def po_chasti(chast):
        slova = norm(chast).replace(".", " ").split()
        if not slova:
            return None
        tochno = [i for i, f in sotr.items() if len(slova) >= 2 and norm(f).split()[:len(slova)] == slova[:3]]
        if len(tochno) == 1:
            return tochno[0]
        famil = [i for i, f in sotr.items()
                 if re.search(r"(^|[^а-я])" + re.escape(slova[0]) + r"([^а-я]|$)", norm(f))]
        if len(famil) > 1 and len(slova) > 1:
            famil = [i for i in famil if len(norm(sotr[i]).split()) > 1
                     and norm(sotr[i]).split()[1][:1] == slova[1][:1]]
        return famil[0] if len(famil) == 1 else None

    def uznat(napisano):
        # «Фамилия Имя Отчество, отдел» или «Отдел, Фамилия»: пробуем каждую часть
        nayden = {po_chasti(ch) for ch in re.split(r"[,(]", napisano)} - {None}
        if len(nayden) != 1:
            return None, ""
        stroka = nayden.pop()
        if norm(napisano).startswith(norm(sotr[stroka])):
            return stroka, ""
        return stroka, f"Проверить: ФИО в ответе «{napisano.strip()}»"

    # Последний ответ каждого сотрудника с начала волны
    posledniy, ne_opoznany = {}, []
    for r in otv[1:]:
        if len(r) <= o_fio or not r[o_fio].strip():
            continue
        try:
            vremya = datetime.strptime(r[o_vremya].strip(), "%d.%m.%Y %H:%M:%S")
        except ValueError:
            continue
        if vremya < s_daty:
            continue
        stroka, pometka_fio = uznat(r[o_fio])
        if stroka is None:
            ne_opoznany.append([r[o_fio].strip(), r[o_vremya].strip(), "Не удалось сопоставить с общей таблицей"])
            continue
        if stroka not in posledniy or vremya >= posledniy[stroka][0]:
            posledniy[stroka] = (vremya, r, pometka_fio)

    izmeneniya, bez_stolbca, vazhnoe, bez_izm = [], [], [], 0
    for stroka in sorted(posledniy, key=lambda i: sotr[i]):
        _, r, pometka_fio = posledniy[stroka]
        tr = obsh[stroka - 1] + [""] * (len(zt) - len(obsh[stroka - 1]))
        val = lambda k: r[o_pole[k]] if len(r) > o_pole[k] else ""
        bylo_izm = len(izmeneniya)

        def dobavit(kl, rez):
            if rez is None:
                return
            novoe, pometka = rez
            pometka = "; ".join(x for x in (pometka_fio, pometka) if x)
            staroe = tr[t[kl]].strip()
            if novoe is None:
                izmeneniya.append([sotr[stroka], stroka, "", zt[t[kl]], staroe or "(пусто)", "", pometka])
            elif norm(novoe) != norm(staroe):
                izmeneniya.append([sotr[stroka], stroka, bukva(t[kl]), zt[t[kl]], staroe or "(пусто)", novoe, pometka])

        sem = semya(val("sem"))
        if sem and norm(tr[t["sem"]]) in {"свободный(ая)", "есть молодой человек,девушка"} and sem[0] == "Не замужем/не женат":
            sem = None  # в таблице уточнённая формулировка, не перезаписываем
        dobavit("sem", sem)
        dobavit("zhil", zhilye(val("zhil")))
        dobavit("avto", da_net(val("avto")))
        kred = da_net(val("kred"))
        dop_info = tr[t_dop_info] if t_dop_info is not None else ""
        if kred and kred[0] == "Нет" and re.search(r"кредит|займ", norm(dop_info)) \
                and not re.search(r"(нет|без)\s+кредит", norm(dop_info)):
            kred = (None, f"Уточнить: ответ «Нет», а в «Дополнительной информации»: «{dop_info.strip()}»")
        dobavit("kred", kred)
        if kred and kred[0] == "Да" and norm(tr[t["kred"]]) != "да":
            vazhnoe.append([sotr[stroka], "Новый потребительский кредит", val("kred").strip()])
        sobytie = r[o_sobytiya].strip() if len(r) > o_sobytiya else ""
        if norm(sobytie) not in NET_OTVETA and re.search(VAZHNOE, norm(sobytie)):
            vazhnoe.append([sotr[stroka], "Новые события", sobytie])
        dobavit("sport", da_net(val("sport"), sport=True))

        d = deti(f"{val('deti')} {val('kolvo')}")
        if d:
            kolvo, vozrast = d
            novoe = f"{kolvo}- " + " и ".join(map(str, vozrast)) + (" лет" if vozrast else "")
            if norm(tr[t["deti"]]) != "да":
                dobavit("deti", ("Да", ""))
                dobavit("kolvo", (novoe.strip(), "Проверить формулировку"))
            else:
                v_tabl = kolvo_v_tablice(tr[t["kolvo"]])
                if not tr[t["kolvo"]].strip():
                    dobavit("kolvo", (novoe.strip(), "Проверить формулировку"))
                elif v_tabl is not None and v_tabl != kolvo:
                    dobavit("kolvo", (novoe.strip(), "Изменилось число детей, проверить"))

        for i, nazv in o_dop:
            otvet = r[i].strip().rstrip(",").strip() if len(r) > i else ""
            if norm(otvet) not in NET_OTVETA:
                bez_stolbca.append([sotr[stroka], nazv, otvet])
        if len(izmeneniya) == bylo_izm:
            bez_izm += 1

    vyhod = os.path.join(PAPKA, "otchet")
    os.makedirs(vyhod, exist_ok=True)
    for imya, shapka, stroki in [
            ("izmeneniya.csv", ["Сотрудник", "Строка", "Столбец", "Поле", "Было", "Стало", "Пометка"], izmeneniya),
            ("bez_stolbca.csv", ["Сотрудник", "Вопрос", "Ответ"], bez_stolbca),
            ("ne_opoznany.csv", ["Как написано", "Время ответа", "Причина"], ne_opoznany),
            ("vazhnoe.csv", ["Сотрудник", "Откуда", "Ответ"], vazhnoe)]:
        with open(os.path.join(vyhod, imya), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(shapka)
            w.writerows(stroki)
    k_zapisi = sum(1 for x in izmeneniya if x[5])
    if vazhnoe:
        print(f"!!! ВАЖНОЕ: {len(vazhnoe)} (болезнь, утрата, новый кредит и т.п.), см. otchet/vazhnoe.csv")
    print(f"Ответили и опознаны: {len(posledniy)}. Без изменений: {bez_izm}. "
          f"Изменений к записи: {k_zapisi}. Уточнить: {len(izmeneniya) - k_zapisi}. "
          f"Ответов без столбца: {len(bez_stolbca)}. Не опознаны: {len(ne_opoznany)}.")
    print(f"Отчёт: {vyhod}")


if __name__ == "__main__":
    main()
