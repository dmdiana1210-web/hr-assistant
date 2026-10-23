#!/usr/bin/env python3
"""Запись изменений опросника в личные дела на Я.Диске (вкладка «БХЧ»).

  python3 lichnye_dela.py plan [--zanovo]  — по otchet/izmeneniya.csv составить otchet/plan_bhch.csv.
                                             Ничего не записывает. --zanovo: заново обойти Я.Диск.
  python3 lichnye_dela.py zapis            — записать план в личные дела, после подтверждения «да».

Перед записью каждого файла его копия кладётся на Я.Диск в «Резервные копии опросника/<дата>».
Что уже записано, отмечено в otchet/zhurnal_bhch.csv: повторно не пишем.
Ключ Я.Диска (OAuth-токен) берётся из Связки ключей Mac (запись «HR Яндекс Диск API»).
"""
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

PAPKA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PAPKA)
from zapis_bhch import zapisat  # noqa: E402

NASTROYKI = json.load(open(os.path.join(PAPKA, "nastroyki.json"), encoding="utf-8"))
OTCHET = os.path.join(PAPKA, "otchet")
IZMENENIYA = os.path.join(OTCHET, "izmeneniya.csv")
PLAN = os.path.join(OTCHET, "plan_bhch.csv")
INDEKS = os.path.join(OTCHET, "indeks_lichnyh_del.json")
ZHURNAL = os.path.join(OTCHET, "zhurnal_bhch.csv")
API = "https://cloud-api.yandex.net/v1/disk"

# Вопросы вкладки «БХЧ» (столбец B). Связанные строки ищутся только под своим главным вопросом.
SEM, SVADBA, SUPRUG, KOMPANIYA = "Семейное положение", "Если в браке", "Если замужем", "В какой компании"
DETI, KOLVO, VOZRAST = "Есть ли дети", "Количество детей", "Их возраст"
ZHIL, IPOTEKA, IPOTEKA_KTO = "Жилье собственное", "Если собственное", "Ипотека на кого"
AVTO, AVTO_KRED = "Есть ли машина и какая марка", "Если да, то выкупленная"
KRED, SUMMA = "Есть ли потребительские кредиты", "Если ДА, то какая сумма"
SPORT, SPORT_KAK, SPORT_CHASTO, SPORT_SKOLKO = ("Занимаетесь ли спортом", "Если ДА, то каким спортом",
                                                "Как часто занимаетесь спортом", "Сколько времени уже этим")


def norm(s):
    return " ".join((s or "").replace("ё", "е").replace("Ё", "Е").lower().split())


# --- Я.Диск (REST API: WebDAV закрыт для бесплатного тарифа с октября 2026)

def avtorizaciya():
    r = subprocess.run(["security", "find-generic-password", "-s", NASTROYKI["keychain_yadisk"], "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"Нет ключа Я.Диска в Связке ключей (запись «{NASTROYKI['keychain_yadisk']}»). "
                 "Как его получить — «Как запускать.md», раздел 8.")
    return "OAuth " + r.stdout.strip()


AUTH = None


def zapros(metod, url, dannye=None, s_klyuchom=True, ok=(200, 201, 202)):
    """Запрос с четырьмя попытками: Яндекс иногда обрывает связь."""
    global AUTH
    zag = {}
    if s_klyuchom:
        AUTH = AUTH or avtorizaciya()
        zag["Authorization"] = AUTH
    for popytka in range(4):
        try:
            z = urllib.request.Request(url, data=dannye, method=metod, headers=zag)
            with urllib.request.urlopen(z, timeout=120) as o:
                return o.status, o.read()
        except urllib.error.HTTPError as e:
            if e.code in ok:
                return e.code, e.read()
            if e.code == 401:
                sys.exit("Я.Диск не принял ключ (истёк или отозван). Получите новый — «Как запускать.md», раздел 8.")
            oshibka = f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
        except Exception as e:
            oshibka = e
        time.sleep(3 * (popytka + 1))
    raise RuntimeError(f"Я.Диск: {metod} {url[:100]}: {oshibka}")


def api(metod, chto, ok=(200, 201, 202), **parametry):
    return zapros(metod, API + chto + "?" + urllib.parse.urlencode(parametry), ok=ok)


def skachat(put):
    _, telo = api("GET", "/resources/download", path="disk:" + put)
    return zapros("GET", json.loads(telo)["href"], s_klyuchom=False)[1]


def zagruzit(put, dannye):
    _, telo = api("GET", "/resources/upload", path="disk:" + put, overwrite="true")
    zapros("PUT", json.loads(telo)["href"], dannye=dannye, s_klyuchom=False)


def papka(put):
    api("PUT", "/resources", ok=(201, 409), path="disk:" + put)  # 409 — папка уже есть


def kopiya(otkuda, kuda):
    # Копию не перезаписываем: при повторном запуске она уже лежит (409)
    api("POST", "/resources/copy", ok=(201, 202, 409),
        **{"from": "disk:" + otkuda, "path": "disk:" + kuda, "overwrite": "false"})


def spisok(put):
    """Содержимое папки: пути от корня диска, у папок в конце «/»."""
    puti, sdvig = [], 0
    while True:
        _, telo = api("GET", "/resources", path="disk:" + put, limit=1000, offset=sdvig,
                      fields="_embedded.items.path,_embedded.items.type,_embedded.total")
        vlozh = json.loads(telo)["_embedded"]
        for x in vlozh["items"]:
            puti.append(x["path"].removeprefix("disk:") + ("/" if x["type"] == "dir" else ""))
        sdvig += len(vlozh["items"])
        if not vlozh["items"] or sdvig >= vlozh["total"]:
            return puti


def obojti(koren, isklyuchit, glubina=4):
    """Все .xlsx в папке личных дел (кроме исключённых папок)."""
    faily, uroven = [], [koren]
    for g in range(glubina):
        sled = []
        for p in uroven:
            for x in spisok(p):
                imya = x.rstrip("/").rsplit("/", 1)[-1]
                if x.endswith("/"):
                    if imya.strip() not in isklyuchit:
                        sled.append(x)
                elif x.lower().endswith(".xlsx"):
                    faily.append(x)
        print(f"  уровень {g + 1}: папок {len(sled)}, файлов Excel {len(faily)}")
        uroven = sled
    return faily


def indeks(zanovo=False):
    if os.path.exists(INDEKS) and not zanovo:
        return json.load(open(INDEKS, encoding="utf-8"))
    print("Обхожу папку личных дел на Я.Диске (может занять несколько минут)...")
    faily = obojti(NASTROYKI["papka_lichnyh_del"], set(NASTROYKI["papka_lichnyh_del_isklyuchit"]))
    os.makedirs(OTCHET, exist_ok=True)
    json.dump(faily, open(INDEKS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return faily


def razobrat_fio(tekst):
    """«Халилова(Алексеева) Татьяна Валентиновна» -> ({халилова, алексеева}, [татьяна, валентиновна])."""
    t = norm(tekst)
    m = re.match(r"^(\S+?)\s*\(\s*([^)\s]+)\s*\)\s*(.*)$", t)
    if m:
        return {m.group(1), m.group(2)}, m.group(3).split()
    slova = t.split()
    return ({slova[0]} if slova else set()), slova[1:]


def nayti_fayl(fio, faily):
    fam, ostalnoe = razobrat_fio(fio)
    podhodyat = []
    for f in faily:
        stem = f.rsplit("/", 1)[-1][:-5]
        fam_f, ost_f = razobrat_fio(stem)
        k = min(len(ost_f), len(ostalnoe))
        if fam & fam_f and k >= 1 and ost_f[:k] == ostalnoe[:k]:
            podhodyat.append(f)
    return podhodyat


# --- План: изменения общей таблицы -> правки «БХЧ»

def pravki_dlya(pole, bylo, stalo):
    """Список (вопрос, значение или "" = очистить, главный вопрос)."""
    p, s, b = norm(pole), norm(stalo), norm(bylo)
    if p.startswith("семейное"):
        rez = [(SEM, stalo, "")]
        if s in ("не замужем/не женат", "в разводе"):
            rez += [(SVADBA, "", SEM), (SUPRUG, "", SEM), (KOMPANIYA, "", SEM)]
        return rez
    if p.startswith("наличие детей"):
        return [(DETI, "да", "")] if s == "да" else []
    if p.startswith("кол-во"):
        m = re.match(r"^\s*(\d+)\s*-\s*(.*?)\s*(лет)?\s*$", stalo)
        return [(KOLVO, m.group(1), DETI), (VOZRAST, m.group(2), DETI)] if m else [(KOLVO, stalo, DETI)]
    if p.startswith("жилье"):
        if s == "ипотека":
            return [(ZHIL, "собственное", ""), (IPOTEKA, "да", ZHIL)]
        glavnoe = {"в собственности": "собственное", "аренда": "в найме", "живет у родителей": "живу у родителей"}
        return [(ZHIL, glavnoe.get(s, stalo), ""), (IPOTEKA, "", ZHIL), (IPOTEKA_KTO, "", ZHIL)]
    if p.startswith("наличие авто"):
        if s == "нет":
            return [(AVTO, "нет", ""), (AVTO_KRED, "", AVTO)]
        if s == "да, в кредите":
            return ([] if b.startswith("да") else [(AVTO, "да", "")]) + [(AVTO_KRED, "в кредите", AVTO)]
        if s == "да":
            # Был в кредите -> выкуплен: главную строку (там марка) не трогаем
            return [(AVTO_KRED, "", AVTO)] if b == "да, в кредите" else [(AVTO, "да", "")]
    if p.startswith("наличие потреб"):
        return [(KRED, "нет", ""), (SUMMA, "", KRED)] if s == "нет" else [(KRED, "да", "")]
    if p.startswith("занятие спортом"):
        if s == "нет":
            return [(SPORT, "нет", ""), (SPORT_KAK, "", SPORT), (SPORT_CHASTO, "", SPORT), (SPORT_SKOLKO, "", SPORT)]
        return [(SPORT, "да", "")]
    return []


def plan(zanovo):
    if not os.path.exists(IZMENENIYA):
        sys.exit("Нет otchet/izmeneniya.csv. Сначала запустите сверку (sverka.py).")
    with open(IZMENENIYA, encoding="utf-8-sig") as f:
        izm = [r for r in csv.DictReader(f) if r["Стало"].strip()]
    if not izm:
        sys.exit("Изменений к записи нет, в личные дела писать нечего.")
    faily = indeks(zanovo)
    stroki, ne_naydeny = [], []
    for fio in sorted({r["Сотрудник"] for r in izm}):
        nayd = nayti_fayl(fio, faily)
        if len(nayd) != 1:
            ne_naydeny.append((fio, "не найдено" if not nayd else f"несколько файлов: {'; '.join(nayd)}"))
            continue
        for r in izm:
            if r["Сотрудник"] == fio:
                for vopros, znach, glavnyy in pravki_dlya(r["Поле"], r["Было"], r["Стало"]):
                    stroki.append([fio, nayd[0], r["Поле"], vopros, glavnyy, znach])
    with open(PLAN, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Сотрудник", "Файл на Я.Диске", "Поле таблицы", "Вопрос в БХЧ", "Искать под вопросом",
                    "Записать (пусто = очистить)"])
        w.writerows(stroki)
        for fio, prichina in ne_naydeny:
            w.writerow([fio, f"ЛИЧНОЕ ДЕЛО {prichina.upper()}", "", "", "", ""])
    print(f"План: {len({s[1] for s in stroki})} личных дел, правок {len(stroki)}. "
          f"Не найдены на Я.Диске: {len(ne_naydeny)}.")
    for fio, prichina in ne_naydeny:
        print(f"  — {fio}: {prichina}")
    print(f"Проверьте и при необходимости поправьте слова в {PLAN}, затем: python3 lichnye_dela.py zapis")


# --- Запись

def zapisano_ranee():
    if not os.path.exists(ZHURNAL):
        return set()
    with open(ZHURNAL, encoding="utf-8") as f:
        return {r["Файл"] for r in csv.DictReader(f) if r["Статус"] == "записано"}


def v_zhurnal(fayl, status):
    novyy = not os.path.exists(ZHURNAL)
    with open(ZHURNAL, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if novyy:
            w.writerow(["Файл", "Время", "Статус"])
        w.writerow([fayl, datetime.now().strftime("%d.%m.%Y %H:%M"), status])


def zapis():
    if not os.path.exists(PLAN):
        sys.exit("Нет otchet/plan_bhch.csv. Сначала: python3 lichnye_dela.py plan")
    po_failam = {}
    with open(PLAN, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["Файл на Я.Диске"].startswith("/"):
                po_failam.setdefault(r["Файл на Я.Диске"], []).append(
                    (r["Вопрос в БХЧ"], r["Записать (пусто = очистить)"].strip() or None, r["Искать под вопросом"]))
    uzhe = zapisano_ranee()
    ochered = {f: p for f, p in po_failam.items() if f not in uzhe}
    print(f"Записать в личные дела: {len(ochered)}. Уже записано раньше: {len(po_failam) - len(ochered)}.")
    if not ochered:
        sys.exit("Записывать нечего.")
    if input("Записывать? Перед каждым файлом будет резервная копия. Напишите да: ").strip().lower() != "да":
        sys.exit("Ничего не записано.")
    data = datetime.now().strftime("%d.%m.%Y")
    rezerv = NASTROYKI["papka_rezervnyh_kopiy"].rstrip("/") + "/"
    papka(rezerv)
    rezerv += datetime.now().strftime("%Y-%m-%d") + "/"
    papka(rezerv)
    oshibki = 0
    with tempfile.TemporaryDirectory() as tmp:
        for fayl, pravki in ochered.items():
            imya = fayl.rsplit("/", 1)[-1]
            try:
                iz, v = os.path.join(tmp, "iz.xlsx"), os.path.join(tmp, "v.xlsx")
                open(iz, "wb").write(skachat(fayl))
                sdelano = zapisat(iz, v, pravki, data)  # ошибка в вопросах — до любой записи на Я.Диск
                kopiya(fayl, rezerv + imya)
                novyy = open(v, "rb").read()
                zagruzit(fayl, novyy)
                if skachat(fayl) != novyy:
                    raise RuntimeError("после загрузки файл на Я.Диске не совпал с записанным")
                v_zhurnal(fayl, "записано")
                print(f"✓ {imya}: " + "; ".join(sdelano))
            except Exception as e:
                oshibki += 1
                v_zhurnal(fayl, f"ошибка: {e}")
                print(f"✗ {imya}: {e}")
    print(f"Готово. Ошибок: {oshibki}. Журнал: {ZHURNAL}. Резервные копии: {rezerv}")


if __name__ == "__main__":
    rezhim = sys.argv[1] if len(sys.argv) > 1 else ""
    if rezhim == "plan":
        plan("--zanovo" in sys.argv)
    elif rezhim == "zapis":
        zapis()
    else:
        sys.exit(__doc__)
