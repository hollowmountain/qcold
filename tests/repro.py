"""Воспроизводим жалобу: вопрос закрывается сразу после ответа одного
человека, а остальные не успевают нажать."""
import os
import pathlib
import sys
import time

os.environ["TG_TOKEN"] = "0:TEST"
os.environ["DATA_DIR"] = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"..")
dbf = os.path.join(os.environ["DATA_DIR"], "quiz.db")
if os.path.exists(dbf):
    os.remove(dbf)

import requests

MID = [400]


class R:
    def __init__(self, p):
        self.p = p

    def json(self):
        return self.p


def post(url, json=None, timeout=None, **kw):
    if url.endswith("/sendMessage"):
        MID[0] += 1
        return R({"ok": True, "result": {"message_id": MID[0]}})
    return R({"ok": True, "result": {}})


requests.post = post

import bot
import db

db.init()
bot.BOT_ID = 42
CHAT = -999
A = {"id": 1, "first_name": "Андрей"}
B = {"id": 2, "first_name": "Маша"}


def tap(user, kind):
    q = bot.games[CHAT]["q"]
    idx = q["correct"] if kind == "right" else (q["correct"] + 1) % len(q["options"])
    bot.handle_callback({"id": "cb", "from": user, "message": {"chat": {"id": CHAT}},
                         "data": f"a:{q['seq']}:{idx}"})


def next_q():
    bot.games[CHAT]["next_at"] = 0
    bot.tick()


def alive():
    return bot.games[CHAT]["q"] is not None


def left():
    q = bot.games[CHAT]["q"]
    return round(q["deadline"] - time.time(), 1) if q else None


bot.handle_message({"chat": {"id": CHAT}, "from": A, "text": "/quiz 4"})

next_q()
print("вопрос 1: Андрей отвечает мимо первым")
tap(A, "wrong")
print("   вопрос ещё открыт:", alive(), "| осталось секунд:", left())

# вопрос 1 доживает до таймаута
bot.games[CHAT]["q"]["deadline"] = 0
bot.tick()

next_q()
print("вопрос 2: Андрей снова отвечает мимо первым")
tap(A, "wrong")
print("   вопрос ещё открыт:", alive(), "| осталось секунд:", left())
if not alive():
    print("   >>> ВОСПРОИЗВЕЛОСЬ: Маша не успела нажать, вопрос уже закрыт")
else:
    print("   Маша ещё может ответить -- пробуем:")
    tap(B, "right")
    print("   у Маши баллов:", bot.games[CHAT]["players"].get(2, {}).get("points", 0))
