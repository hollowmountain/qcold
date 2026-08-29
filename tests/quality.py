"""Длинные варианты и механизм поиска ошибок в базе."""
import os
import pathlib
import sys

os.environ["TG_TOKEN"] = "0:TEST"
os.environ["DATA_DIR"] = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"..")
dbf = os.path.join(os.environ["DATA_DIR"], "quiz.db")
if os.path.exists(dbf):
    os.remove(dbf)

import requests

SENT, EDITS, POPUPS = [], [], []
MID = [900]


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
CHAT = -4242
A = {"id": 1, "first_name": "Андрей"}
M = {"id": 2, "first_name": "Маша"}


def buttons(markup):
    return [b for row in markup["inline_keyboard"] for b in row]


# --- как рисуются короткие и длинные варианты ------------------------------
short = {"id": 1, "pack": "тест", "topic": "тест", "question": "Столица Японии",
         "correct": "Токио", "wrong": ["Осака", "Киото", "Нагоя", "Кобе",
                                       "Саппоро", "Фукуока", "Сендай"]}
longq = {"id": 2, "pack": "тест", "topic": "тест",
         "question": "Что такое starvemaxxing",
         "correct": "Голодание ради худого лица, по сути замаскированная анорексия",
         "wrong": ["Диета с высоким содержанием белка", "Интервальное голодание",
                   "Отказ от сахара", "Вегетарианство", "Разгрузочный день",
                   "Отказ от алкоголя", "Подсчёт калорий"]}

g = {"total": 2, "asked": 0, "queue": [short, longq], "starter": 1, "seq": 0,
     "q": None, "next_at": 0, "players": {}, "thread": None}
bot.games[CHAT] = g

bot.send_question(CHAT, g)
print("--- короткие варианты ---")
print(SENT[-1]["text"])
btns = buttons(SENT[-1]["reply_markup"])
print("кнопки:", [b["text"] for b in btns][:3], "...")
assert len(btns) == 8
assert btns[0]["text"][0] in bot.LETTERS and len(btns[0]["text"]) > 3, \
    "у коротких вариантов текст должен быть на кнопке"
assert not g["q"]["long"]
g["q"]["deadline"] = 0
bot.tick()

bot.send_question(CHAT, g)
print("\n--- длинные варианты ---")
print(SENT[-1]["text"])
btns = buttons(SENT[-1]["reply_markup"])
print("кнопки:", [b["text"] for b in btns])
assert g["q"]["long"], "длинный вариант не распознан"
assert all(b["text"] in bot.LETTERS for b in btns), "на кнопках не только буквы"
assert "Голодание ради худого лица" in SENT[-1]["text"], "вариантов нет в тексте"
assert max(len(b["text"]) for b in btns) == 1

# отвечают: Маша мимо, Андрей верно
qq = g["q"]
wrong_idx = (qq["correct"] + 1) % 8
bot.handle_callback({"id": "c", "from": M, "message": {"chat": {"id": CHAT}},
                     "data": f"a:{qq['seq']}:{wrong_idx}"})
bot.handle_callback({"id": "c", "from": A, "message": {"chat": {"id": CHAT}},
                     "data": f"a:{qq['seq']}:{qq['correct']}"})
qq["deadline"] = 0
bot.tick()

# --- статистика ------------------------------------------------------------
row = db.CONN.execute("SELECT * FROM qstats WHERE question_id = 2").fetchone()
print("\nстатистика вопроса:", dict(row))
assert row["shown"] == 1 and row["taps"] == 2 and row["hits"] == 1

# --- кнопка жалобы ---------------------------------------------------------
report_btn = buttons(EDITS[-1]["reply_markup"])[0]
print("кнопка под закрытым вопросом:", report_btn["text"], "->", report_btn["callback_data"])
assert report_btn["callback_data"] == "r:2"

POPUPS.clear()
for who in (A, M, A):        # третий раз тот же человек -- не должен считаться
    bot.handle_callback({"id": "c", "from": who,
                         "message": {"chat": {"id": CHAT}},
                         "data": report_btn["callback_data"]})
print("ответы на жалобы:", POPUPS)
n = db.CONN.execute("SELECT reports FROM qstats WHERE question_id = 2").fetchone()[0]
print("жалоб засчитано:", n)
assert n == 2, "повторная жалоба того же человека засчиталась"

# --- /bad ------------------------------------------------------------------
SENT.clear()
bot.handle_message({"chat": {"id": CHAT}, "from": A, "text": "/bad"})
print("\n--- /bad ---")
print(SENT[-1]["text"])
real2 = db.CONN.execute("SELECT question FROM questions WHERE id=2").fetchone()[0]
assert real2 in SENT[-1]["text"], "вопрос с жалобами не попал в /bad"

# вопрос, в который никто не попадает, всплывает без всяких жалоб
db.CONN.execute("INSERT OR REPLACE INTO qstats (question_id, shown, taps, hits) "
                "VALUES (1, 5, 12, 0)")
db.CONN.commit()
rows = db.suspicious()
found = [r["question"] for r in rows]
print("\nподозрительные:", found)
real1 = db.CONN.execute("SELECT question FROM questions WHERE id=1").fetchone()[0]
assert real1 in found, "вопрос без единого попадания не всплыл"

print("\nвсё сходится")
