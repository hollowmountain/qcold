"""Выбор тем: кнопками с мультивыбором и прямо в команде."""
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
MID = [800]


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

print("всего вопросов:", db.init())
bot.BOT_ID = 42
CHAT = -321
U = {"id": 1, "first_name": "Андрей"}


def msg(text):
    bot.handle_message({"chat": {"id": CHAT}, "from": U, "text": text})


def press(data):
    bot.handle_callback({"id": "cb", "from": U, "message": {"chat": {"id": CHAT}},
                         "data": data})


def buttons(markup):
    return [b for row in markup["inline_keyboard"] for b in row]


# --- /themes ---------------------------------------------------------------
msg("/themes")
print("\n--- /themes ---")
print(SENT[-1]["text"])

# --- выбор кнопками --------------------------------------------------------
SENT.clear()
msg("/quiz 4")
assert CHAT in bot.pending, "выбор тем не открылся"
assert CHAT not in bot.games, "раунд стартовал без выбора"
print("\n--- клавиатура выбора ---")
print(SENT[-1]["text"])
for row in SENT[-1]["reply_markup"]["inline_keyboard"]:
    print("  " + " | ".join(b["text"] for b in row))

names = [n for n, _ in bot.pending[CHAT]["packs"]]
i_anime = names.index("аниме")
i_cs = names.index("кс")

press(f"p:{i_anime}")
press(f"p:{i_cs}")
assert bot.pending[CHAT]["picked"] == {i_anime, i_cs}
marked = [b["text"] for b in buttons(EDITS[-1]["reply_markup"]) if b["text"].startswith("✅")]
print("\nотмечено:", marked)
assert len(marked) == 2

# повторное нажатие снимает отметку
press(f"p:{i_cs}")
assert bot.pending[CHAT]["picked"] == {i_anime}
press(f"p:{i_cs}")

print("текст с двумя темами:", EDITS[-1]["text"].splitlines()[-1])

press("p:go")
assert CHAT not in bot.pending, "выбор не закрылся"
assert CHAT in bot.games, "раунд не стартовал"
assert set(bot.games[CHAT]["queue"][0].keys()) >= {"pack", "topic"}
packs_in_round = {q["pack"] for q in bot.games[CHAT]["queue"]}
print("\nтемы вопросов в раунде:", packs_in_round)
assert packs_in_round <= {"аниме", "кс"}, "просочилась чужая тема"

print("\n--- объявление раунда (поверх выбора) ---")
print(EDITS[-1]["text"])

bot.games[CHAT]["next_at"] = 0
bot.tick()
print("\n--- первый вопрос ---")
print(SENT[-1]["text"])

# --- «Все темы» ------------------------------------------------------------
bot.games.pop(CHAT)
SENT.clear()
msg("/quiz 5")
press("p:all")
assert len(bot.pending[CHAT]["picked"]) == len(bot.pending[CHAT]["packs"])
press("p:all")
assert not bot.pending[CHAT]["picked"], "повторное «Все темы» не сбросило выбор"
press("p:go")
assert bot.games[CHAT]["total"] == 5
print("\nбез отметок взяты все темы:",
      {q["pack"] for q in bot.games[CHAT]["queue"]})

# --- темы прямо в команде --------------------------------------------------
bot.games.pop(CHAT)
msg("/quiz 6 маркарян")
assert CHAT in bot.games and CHAT not in bot.pending, "кнопки лишние при явной теме"
assert {q["pack"] for q in bot.games[CHAT]["queue"]} == {"маркарян"}
print("\n/quiz 6 маркарян ->", bot.games[CHAT]["total"], "вопросов, тема одна")

bot.games.pop(CHAT)
msg("/quiz 8 мемы лукмакс")          # вторая тема названа началом слова
assert {q["pack"] for q in bot.games[CHAT]["queue"]} <= {"мемы", "лукмаксинг"}
print("/quiz 8 мемы лукмакс -> темы:",
      {q["pack"] for q in bot.games[CHAT]["queue"]})

bot.games.pop(CHAT)
SENT.clear()
msg("/quiz 5 барабан")
assert CHAT not in bot.games and CHAT not in bot.pending
print("\nнеизвестная тема:", SENT[-1]["text"])

SENT.clear()
msg("/quiz много")
print("не число и не тема:", SENT[-1]["text"])

# --- выбор протухает -------------------------------------------------------
msg("/quiz 3")
bot.pending[CHAT]["at"] = 0
bot.tick()
assert CHAT not in bot.pending, "протухший выбор не убрался"
print("\nпротухший выбор:", EDITS[-1]["text"])

print("\nвсё сходится")
