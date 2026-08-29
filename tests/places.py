"""Баллы за место: 5 / 3 / 2, дальше по 1."""
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

SENT, EDITS, POPUPS = [], [], []
MID = [500]


class R:
    def __init__(self, p):
        self.p = p

    def json(self):
        return self.p


def post(url, json=None, timeout=None, **kw):
    m = url.rsplit("/", 1)[-1]
    p = json or {}
    if m == "sendMessage":
        MID[0] += 1
        SENT.append(p)
        return R({"ok": True, "result": {"message_id": MID[0]}})
    if m == "editMessageText":
        EDITS.append(p)
        return R({"ok": True, "result": {}})
    if m == "answerCallbackQuery":
        POPUPS.append(p.get("text"))
        return R({"ok": True})
    return R({"ok": True, "result": {}})


requests.post = post

import bot
import db

db.init()
bot.BOT_ID = 42
CHAT = -555
PEOPLE = [{"id": i, "first_name": n} for i, n in
          enumerate(["Андрей", "Маша", "Петя", "Оля", "Игорь", "Лена"], start=1)]


def tap(user, kind):
    q = bot.games[CHAT]["q"]
    idx = q["correct"] if kind == "right" else (q["correct"] + 1) % len(q["options"])
    bot.handle_callback({"id": "cb", "from": user, "message": {"chat": {"id": CHAT}},
                         "data": f"a:{q['seq']}:{idx}"})


def next_q():
    bot.games[CHAT]["next_at"] = 0
    bot.tick()


def timeout():
    bot.games[CHAT]["q"]["deadline"] = 0
    bot.tick()


print("лестница баллов:", bot.POINTS, "|", bot.POINTS_TEXT)
assert bot.POINTS == [5, 3, 2, 1]
assert [bot.award(i) for i in range(1, 8)] == [5, 3, 2, 1, 1, 1, 1]

bot.handle_message({"chat": {"id": CHAT}, "from": PEOPLE[0], "text": "/quiz 3 общая"})
print("\n--- объявление раунда ---")
print(SENT[-1]["text"])

# вопрос 1: пятеро угадывают по очереди, шестой мажет
next_q()
POPUPS.clear()
for u in PEOPLE[:5]:
    tap(u, "right")
    assert bot.games[CHAT]["q"] is not None, f"вопрос закрылся на {u['first_name']}"
tap(PEOPLE[5], "wrong")

print("\n--- что увидел каждый в всплывашке ---")
for who, text in zip(PEOPLE, POPUPS):
    print(f"  {who['first_name']:<7} {text}")

got = {u["id"]: bot.games[CHAT]["players"][u["id"]]["points"] for u in PEOPLE[:5]}
print("\nбаллы по местам:", list(got.values()))
assert list(got.values()) == [5, 3, 2, 1, 1], "лестница поехала"
assert 6 not in bot.games[CHAT]["players"], "промахнувшийся получил балл"

timeout()
print("\n--- вопрос закрыт по таймеру ---")
print(EDITS[-1]["text"])
assert "мимо: 1" in EDITS[-1]["text"]

# вопрос 2: порядок другой -- баллы должны идти по новому порядку
next_q()
tap(PEOPLE[2], "right")     # Петя первый
tap(PEOPLE[0], "right")     # Андрей второй
timeout()
assert bot.games[CHAT]["players"][3]["points"] == 2 + 5, "Петя не взял первое место"
assert bot.games[CHAT]["players"][1]["points"] == 5 + 3, "Андрей не взял второе"
print("\nпосле второго вопроса: Петя", bot.games[CHAT]["players"][3]["points"],
      "| Андрей", bot.games[CHAT]["players"][1]["points"])

# вопрос 3: никто
next_q()
timeout()
next_q()

print("\n--- итоги раунда ---")
print(SENT[-1]["text"])

SENT.clear()
bot.handle_message({"chat": {"id": CHAT}, "from": PEOPLE[0], "text": "/top"})
print("\n--- /top ---")
print(SENT[-1]["text"])

print("\nвсё сходится")
