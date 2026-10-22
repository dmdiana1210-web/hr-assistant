#!/usr/bin/env python3
"""Точечная запись во вкладку «БХЧ» личного дела (.xlsx).

  python3 zapis_bhch.py delo.xlsx novoe.xlsx --data ДД.ММ.ГГГГ "Вопрос=Значение" "Вопрос=" ...

Обычно запускается не вручную, а из lichnye_dela.py.

«Вопрос=Значение» — найти строку, где в столбце B этот вопрос, записать значение в C,
                    а в D той же строки «обновлено от ДД.ММ.ГГГГ».
«Вопрос=»         — очистить C в этой строке (связанные строки), D не трогаем.

Строка ищется по тексту вопроса в B, а не по номеру. Если вопрос встречается несколько раз
или не найден, ничего не записывается. Меняются только нужные ячейки: картинки, оформление
и остальные вкладки переносятся в новый файл байт в байт.
"""
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
VKLADKA = "БХЧ"


def norm(s):
    return " ".join((s or "").replace("ё", "е").replace("Ё", "Е").lower().split())


def put_vkladki(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    celi = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", NS)}
    for s in wb.findall("m:sheets/m:sheet", NS):
        if s.get("name").strip().upper() == VKLADKA:
            cel = celi[s.get(f"{{{NS['r']}}}id")].lstrip("/")
            return cel if cel.startswith("xl/") else "xl/" + cel
    raise ValueError(f"В файле нет вкладки «{VKLADKA}»")


def obshchie_stroki(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in sst.findall("m:si", NS)]


def voprosy(list_xml, stroki):
    """Номер строки -> текст вопроса из столбца B."""
    rez = {}
    for c in ET.fromstring(list_xml).iter(f"{{{NS['m']}}}c"):
        m = re.fullmatch(r"B(\d+)", c.get("r", ""))
        if not m:
            continue
        if c.get("t") == "s":
            tekst = stroki[int(c.find("m:v", NS).text)]
        elif c.get("t") == "inlineStr":
            tekst = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
        else:
            v = c.find("m:v", NS)
            tekst = v.text if v is not None else ""
        rez[int(m.group(1))] = tekst
    return rez


def nayti_stroku(vopr, vopros, posle=None, okno=6):
    """posle — номер строки главного вопроса: тогда ищем только в ближайших okno строках под ним."""
    if posle is not None:
        vopr = {r: t for r, t in vopr.items() if posle < r <= posle + okno}
    n = norm(vopros)
    tochno = [r for r, t in vopr.items() if norm(t) == n]
    if len(tochno) == 1:
        return tochno[0]
    if not tochno:
        nachalo = [r for r, t in vopr.items() if norm(t).startswith(n)]
        if len(nachalo) == 1:
            return nachalo[0]
        raise ValueError(f"вопрос «{vopros}» " + ("не найден" if not nachalo else f"найден {len(nachalo)} раз"))
    raise ValueError(f"вопрос «{vopros}» найден {len(tochno)} раз")


def nomer_stolbca(adres):
    n = 0
    for ch in re.match(r"[A-Z]+", adres).group():
        n = n * 26 + ord(ch) - 64
    return n


def zamenit_yacheyku(xml, adres, znachenie):
    """znachenie=None — очистить ячейку. Стиль ячейки (атрибут s) сохраняется."""
    nomer = re.search(r"\d+", adres).group()
    novaya = lambda s: (f'<c r="{adres}"{s}/>' if znachenie is None else
                        f'<c r="{adres}"{s} t="inlineStr"><is><t xml:space="preserve">{escape(znachenie)}</t></is></c>')
    shablon = re.compile(rf'<c r="{adres}"(?P<atr>[^>]*?)(?:/>|>(?P<telo>.*?)</c>)', re.S)
    m = shablon.search(xml)
    if m:
        if m.group("telo") and "<f" in m.group("telo"):
            raise ValueError(f"в ячейке {adres} формула, не трогаю")
        s = re.search(r'\ss="\d+"', m.group("atr"))
        return xml[:m.start()] + novaya(s.group() if s else "") + xml[m.end():]
    if znachenie is None:
        return xml
    # Ячейки нет: вставляем в строку по порядку столбцов, с оформлением столбца по умолчанию
    s = ""
    for col in re.finditer(r"<col\s[^>]*>", xml):
        a = dict(re.findall(r'(\w+)="([^"]*)"', col.group()))
        if "style" in a and int(a.get("min", 0)) <= nomer_stolbca(adres) <= int(a.get("max", 0)):
            s = f' s="{a["style"]}"'
    stroka = re.search(rf'<row r="{nomer}"[^>]*?(?:/>|>(.*?)</row>)', xml, re.S)
    if not stroka:
        raise ValueError(f"нет строки {nomer}")
    if stroka.group(0).endswith("/>"):
        otkr = stroka.group(0)[:-2] + ">"
        return xml[:stroka.start()] + otkr + novaya(s) + "</row>" + xml[stroka.end():]
    telo_start = stroka.start(1)
    for c in re.finditer(r'<c r="([A-Z]+)\d+"', stroka.group(1)):
        if nomer_stolbca(c.group(1)) > nomer_stolbca(adres):
            poz = telo_start + c.start()
            return xml[:poz] + novaya(s) + xml[poz:]
    poz = stroka.end(1)
    return xml[:poz] + novaya(s) + xml[poz:]


def zapisat(iz, v, pravki, data):
    """pravki: список (вопрос, значение или None[, главный вопрос, под которым искать]).
    Возвращает список «что сделано»."""
    with zipfile.ZipFile(iz) as z:
        put = put_vkladki(z)
        xml = z.read(put).decode("utf-8")
        vopr = voprosy(xml, obshchie_stroki(z))
        sdelano = []
        for pravka in pravki:
            vopros, znach = pravka[0], pravka[1]
            glavnyy = pravka[2] if len(pravka) > 2 and pravka[2] else None
            r = nayti_stroku(vopr, vopros, nayti_stroku(vopr, glavnyy) if glavnyy else None)
            xml = zamenit_yacheyku(xml, f"C{r}", znach)
            if znach is not None:
                xml = zamenit_yacheyku(xml, f"D{r}", f"обновлено от {data}")
            sdelano.append(f"строка {r}: «{vopr[r].strip()}» → " + (f"«{znach}»" if znach is not None else "очищено"))
        with zipfile.ZipFile(v, "w") as nov:
            for info in z.infolist():
                dannye = xml.encode("utf-8") if info.filename == put else z.read(info.filename)
                nov.writestr(info, dannye)
    return sdelano


if __name__ == "__main__":
    argi = sys.argv[1:]
    if len(argi) < 4 or "--data" not in argi:
        sys.exit(__doc__)
    i = argi.index("--data")
    data = argi[i + 1]
    ostalnye = argi[:i] + argi[i + 2:]
    iz, v, pary = ostalnye[0], ostalnye[1], ostalnye[2:]
    pravki = []
    for p in pary:
        vopros, _, znach = p.partition("=")
        pravki.append((vopros.strip(), znach.strip() or None))
    try:
        for stroka in zapisat(iz, v, pravki, data):
            print(stroka)
    except ValueError as e:
        sys.exit(f"Ничего не записано: {e}")
    print(f"Готово: {v}")
