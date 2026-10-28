"""Отправка отчёта в Битрикс24: вступление + картинка + Excel, больше ничего.

Запуск: python send_report.py <data.json> <папка_вывода> <id отчёта> <ID в Битрикс24> ["заголовок"]
Вебхук берётся из Связки ключей macOS (запись «HR Битрикс24»), в файлах не хранится.
Заголовок нужен для отправки на проверку, например «[b][НА ПРОВЕРКУ — …][/b]».
"""
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

hook = subprocess.run(["security", "find-generic-password", "-s", "HR Битрикс24", "-w"],
                      capture_output=True, text=True).stdout.strip().rstrip("/")


def call(method, params):
    req = urllib.request.Request(f"{hook}/{method}.json", json.dumps(params).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        return json.load(e)


def err(r):
    return r.get("error_description") or r.get("error")


data_path, out, rep_id, uid = sys.argv[1:5]
title = sys.argv[5] if len(sys.argv) > 5 else ""
if not hook:
    sys.exit("ошибка: вебхук «HR Битрикс24» не найден в Связке ключей")
data = json.load(open(data_path))
rep = next(r for r in data["reports"] if r["id"] == rep_id)
text = json.load(open(f"{out}/{rep_id}.intro.json"))["intro"]

chat = (call("im.dialog.get", {"DIALOG_ID": uid}).get("result") or {}).get("id")
if not chat:
    sys.exit("ошибка: не найден чат с получателем")
folder = (call("im.disk.folder.get", {"CHAT_ID": chat}).get("result") or {}).get("ID")
if not folder:
    sys.exit("ошибка: не найдена папка файлов чата")

r = call("im.message.add", {"DIALOG_ID": uid, "MESSAGE": (title + "\n" if title else "") + text})
print("текст →", "ok" if r.get("result") else err(r))
for ext in ("png", "xlsx"):
    name = f"{rep['title']} — зарплаты {data['month']}.{ext}"
    content = base64.b64encode(open(f"{out}/{rep_id}.{ext}", "rb").read()).decode()
    u = call("disk.folder.uploadfile", {"id": folder, "data": {"NAME": name},
                                        "fileContent": [name, content], "generateUniqueName": True})
    file_id = (u.get("result") or {}).get("ID")
    if not file_id:
        print(ext, "→ ошибка загрузки:", err(u))
        continue
    k = call("im.disk.file.commit", {"CHAT_ID": chat, "FILE_ID": file_id})
    print(ext, "→", "ok" if k.get("result") else err(k))
    time.sleep(0.7)
