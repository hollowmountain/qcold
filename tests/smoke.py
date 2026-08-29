"""Прогон раунда без телеграма: подменяем requests.post и играем сами с собой."""
import os
import pathlib
import sys

os.environ["TG_TOKEN"] = "0:TEST"
os.environ["DATA_DIR"] = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"..")

dbfile = os.path.join(os.environ["DATA_DIR"], "quiz.db")
if os.path.exists(dbfile):
    os.remove(dbfile)

import requests

SENT = []
MSG_ID = [100]


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def fake_post(url, json=None, timeout=None, **kw):
    method = url.rsplit("/", 1)[-1]
    params = json or {}
    if method == "getMe":
        return FakeResp({"ok": True, "result": {"id": 42, "username": "qcoldbot"}})
    if method == "sendMessage":
        MSG_ID[0] += 1
        SENT.append(("send", params.get("text"), params.get("reply_markup")))
        return FakeResp({"ok": True, "result": {"message_id": MSG_ID[0]}})
    if method == "editMessageText":
        SENT.append(("edit", params.get("text"), None))
        return FakeResp({"ok": True, "result": {}})
    if method == "answerCallbackQuery":
        SENT.append(("popup", params.get("text"), None))
        return FakeResp({"ok": True})
    return FakeResp({"ok": True, "result": {}})


requests.post = fake_post

import bot
import db

print("вопросов в базе:", db.init())
bot.BOT_ID = 42

CHAT = -100500
ANDREY = {"id": 1, "first_name": "Андрей"}
MASHA = {"id": 2, "first_name": "Маша", "last_name": "П."}


def msg(user, text):
    bot.handle_message({"chat": {"id": CHAT}, "from": user, "text": text})


def tap(user, kind):
    q = bot.games[CHAT]["q"]
    idx = q["correct"] if kind == "right" else (q["correct"] + 1) % len(q["options"])
    bot.handle_callback({"id": "cb", "from": user,
                         "message": {"chat": {"id": CHAT}},
                         "data": f"a:{q['seq']}:{idx}"})


def next_q():
    bot.games[CHAT]["next_at"] = 0
    bot.tick()


def timeout():
    bot.games[CHAT]["q"]["deadline"] = 0
    bot.tick()


# --- сценарий --------------------------------------------------------------
msg(ANDREY, "/quiz 3 общая")
assert CHAT in bot.games, "раунд не начался"

# вопрос 1: Маша тыкает мимо и выбывает, Андрей забирает первое место
next_q()
q1_seq = bot.games[CHAT]["q"]["seq"]
tap(MASHA, "wrong")
assert MASHA["id"] in bot.games[CHAT]["q"]["answered"], "ошибшийся не выбыл"
tap(MASHA, "right")
assert bot.games[CHAT]["q"] is not None, "выбывший смог ответить"
tap(ANDREY, "right")
assert bot.games[CHAT]["q"] is not None, "вопрос закрылся первым верным ответом"
assert bot.games[CHAT]["players"][1]["points"] == 5, "первое место не 5 баллов"

# запоздалое нажатие по уже закрытому вопросу не должно ничего сломать
timeout()
bot.handle_callback({"id": "cb", "from": MASHA, "message": {"chat": {"id": CHAT}},
                     "data": f"a:{q1_seq}:0"})

# вопрос 2: Маша первая, Андрей вторым
next_q()
tap(MASHA, "right")
tap(ANDREY, "right")
assert bot.games[CHAT]["players"][2]["points"] == 5
assert bot.games[CHAT]["players"][1]["points"] == 5 + 3
timeout()

# вопрос 3: истекает время
next_q()
timeout()
assert bot.games[CHAT]["q"] is None, "вопрос не закрылся по таймеру"

# раунд должен завершиться таблицей
next_q()
assert CHAT not in bot.games, "раунд не завершился"

print("\n--- итоги раунда ---")
print(SENT[-1][1])

SENT.clear()
msg(ANDREY, "/top")
print("\n--- /top ---")
print(SENT[-1][1])

msg(ANDREY, "/quiz 2 общая")
next_q()
msg(ANDREY, "/stop")
assert CHAT not in bot.games, "/stop не остановил раунд"
print("\n--- /stop ---")
print(SENT[-1][1])

SENT.clear()
msg(ANDREY, "/quiz много")
print("\n--- /quiz много ---")
print(SENT[-1][1])
msg(ANDREY, "/quiz@qcoldbot 999 общая")
print("вопросов в раунде при /quiz 999:", bot.games[CHAT]["total"])

print("\nвсё прошло")
