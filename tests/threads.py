"""Проверяем, что в форумной супергруппе всё уходит в свою тему,
а в обычной группе message_thread_id не подставляется вовсе."""
import os
import pathlib
import sys

os.environ["TG_TOKEN"] = "0:TEST"
os.environ["DATA_DIR"] = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"..")

import requests

CALLS = []
MID = [200]


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
        CALLS.append((p.get("message_thread_id"), (p.get("text") or "")[:34]))
        return R({"ok": True, "result": {"message_id": MID[0]}})
    return R({"ok": True, "result": {}})


requests.post = post

import bot
import db

db.init()
bot.BOT_ID = 42
U = {"id": 7, "first_name": "Андрей"}


def run(chat_id, extra, label):
    CALLS.clear()
    m = {"chat": {"id": chat_id}, "from": U, "text": "/quiz 2 общая"}
    m.update(extra)
    bot.handle_message(m)
    bot.games[chat_id]["next_at"] = 0
    bot.tick()                                   # вопрос 1
    q = bot.games[chat_id]["q"]
    bot.handle_callback({"id": "c", "from": U, "message": {"chat": {"id": chat_id}},
                         "data": f"a:{q['seq']}:{q['correct']}"})
    bot.games[chat_id]["next_at"] = 0
    bot.tick()                                   # вопрос 2
    q = bot.games[chat_id]["q"]
    bot.handle_callback({"id": "c", "from": U, "message": {"chat": {"id": chat_id}},
                         "data": f"a:{q['seq']}:{q['correct']}"})
    bot.games[chat_id]["next_at"] = 0
    bot.tick()                                   # итоги
    print(f"\n--- {label} ---")
    for thread, text in CALLS:
        print(f"  message_thread_id={thread!r:>6}  {text}")
    return [t for t, _ in CALLS]


forum = run(-1001, {"message_thread_id": 55, "is_topic_message": True},
            "форумная супергруппа, тема 55")
assert all(t == 55 for t in forum), "не всё ушло в тему"

plain = run(-1002, {}, "обычная группа")
assert all(t is None for t in plain), "в обычной группе подставилась тема"

# message_thread_id без is_topic_message -- не тема, подставлять нельзя
fake = run(-1003, {"message_thread_id": 99}, "ответ в ветке, не форум")
assert all(t is None for t in fake), "подставили невалидную тему"

CALLS.clear()
bot.handle_message({"chat": {"id": -1001}, "from": U, "text": "/top",
                    "message_thread_id": 55, "is_topic_message": True})
print("\n--- /top в теме 55 ---")
print("  message_thread_id =", CALLS[-1][0])
assert CALLS[-1][0] == 55

print("\nвсё сходится")
