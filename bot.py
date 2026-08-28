"""qcold -- викторина для групповых чатов телеграма.

Ведущий кидает в группу /quiz 10 -- бот задаёт десять случайных вопросов
из questions.csv, каждый с четырьмя вариантами. Балл получает тот, кто
первым нажал правильную кнопку. Ошибся -- на этом вопросе выбываешь:
иначе можно было бы перебрать все четыре варианта и всё равно оказаться
первым. В конце раунда -- таблица: у кого сколько баллов и кто отвечал
быстрее.

Всё в одном процессе и без потоков: таймеры вопросов крутит сам цикл
опроса, подбирая длину long polling под ближайший дедлайн.
"""
import html
import math
import os
import random
import sys
import time
from pathlib import Path

import requests

import db

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

HERE = Path(__file__).parent


def _token():
    """На сервере токен приходит переменной окружения, локально лежит файлом.
    В репозиторий файл не попадает никогда."""
    env = os.environ.get("TG_TOKEN")
    if env:
        return env.strip()
    local = HERE / "token.txt"
    if local.exists():
        return local.read_text(encoding="utf-8").strip()
    sys.exit("Нет токена. Задай переменную TG_TOKEN или положи его "
             "в файл token.txt рядом с bot.py")


TOKEN = _token()
API = f"https://api.telegram.org/bot{TOKEN}"

# Сколько секунд висит вопрос, если никто не ответил.
QUESTION_SECONDS = int(os.environ.get("QUIZ_SECONDS", 15))
# Пауза между вопросами -- чтобы успели прочитать, кто взял балл.
PAUSE_SECONDS = int(os.environ.get("QUIZ_PAUSE", 3))
# Сколько вариантов показывать. Если у вопроса неверных меньше, покажем
# столько, сколько есть, -- вопрос из-за этого не пропадёт.
OPTIONS = int(os.environ.get("QUIZ_OPTIONS", 8))
# Сообщение с вопросом не правится, пока кнопки живые: правка заставляет
# клиент перерисовать клавиатуру, и нажатие, попавшее в этот момент,
# пропадает -- человеку приходится тыкать второй раз. Поэтому ни живого
# отсчёта, ни живого счётчика под вопросом нет: единственная правка
# приходит уже вместе с ответом, когда нажимать больше нечего.
DEFAULT_QUESTIONS = 10
MAX_QUESTIONS = 50


def _points():
    """Баллы за место: первый угадавший берёт 5, второй 3, третий 2, все
    остальные по 1. Разрыв между первым и вторым намеренно крупный -- будь
    он в один балл, торопиться было бы незачем."""
    raw = os.environ.get("QUIZ_POINTS", "5,3,2,1")
    try:
        vals = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        vals = []
    if not vals or min(vals) < 0:
        sys.exit(f"QUIZ_POINTS не разобрать: {raw!r}. Нужен список "
                 f"неотрицательных чисел через запятую, например 5,3,2,1")
    return vals


POINTS = _points()
POINTS_TEXT = (" / ".join(map(str, POINTS[:-1])) + f", дальше по {POINTS[-1]}"
               if len(POINTS) > 1 else f"по {POINTS[0]} за верный ответ")

LETTERS = "АБВГДЕЖЗИК"
MEDALS = ["🥇", "🥈", "🥉"]

BOT_ID = None
BOT_NAME = ""

# Идущие раунды: chat_id -> состояние. В памяти, а не в базе: раунд живёт
# минуты, и переживать перезапуск ему незачем.
games = {}

esc = html.escape


# --- телеграм --------------------------------------------------------------
def api(method, **params):
    # пустые параметры выбрасываем: телеграм отвергает reply_markup=null
    # с 400 Bad Request, и сообщение молча не доходит
    params = {k: v for k, v in params.items() if v is not None}
    for attempt in range(3):
        try:
            r = requests.post(f"{API}/{method}", json=params, timeout=60).json()
            desc = r.get("description", "")
            # "not modified" -- не ошибка, а повторное нажатие той же кнопки
            if not r.get("ok") and "not modified" not in desc:
                print(f"api {method}: {desc}")
            return r
        except requests.RequestException as e:
            if attempt == 2:
                print("api fail:", method, e)
                return {}
            time.sleep(2)


def btn(text, data):
    return {"text": text, "callback_data": data}


def kb(rows):
    return {"inline_keyboard": rows}


def send(chat_id, text, markup=None, thread=None):
    # в форумных супергруппах ответ надо класть в ту же тему, откуда пришла
    # команда, иначе бот отвечает в General мимо всех
    return api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
               reply_markup=markup, message_thread_id=thread)


def edit(chat_id, msg_id, text, markup=None):
    # без reply_markup телеграм убирает клавиатуру -- ровно то, что нужно
    # закрытому вопросу
    return api("editMessageText", chat_id=chat_id, message_id=msg_id,
               text=text, parse_mode="HTML", reply_markup=markup)


def answer(cq, text=None, alert=False):
    api("answerCallbackQuery", callback_query_id=cq["id"], text=text,
        show_alert=True if alert else None)


def is_admin(chat_id, user_id):
    if chat_id == user_id:            # личка: сам себе админ
        return True
    r = api("getChatMember", chat_id=chat_id, user_id=user_id)
    if not r.get("ok"):
        return False
    return r["result"].get("status") in ("creator", "administrator")


# --- мелочи ----------------------------------------------------------------
def display_name(user):
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
    return name.strip() or user.get("username") or f"id{user.get('id')}"


def plural(n, one="вопрос", few="вопроса", many="вопросов"):
    tail = abs(n) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def secs(ms):
    """До миллисекунды: время ответа бот и так меряет именно в них."""
    return f"{ms / 1000:.3f} с".replace(".", ",")


def avg(times):
    return sum(times) / len(times) if times else float("inf")


def award(place):
    """Сколько баллов за это место. Кто не попал в список, получает столько
    же, сколько последний в нём."""
    return POINTS[min(place, len(POINTS)) - 1]


# --- ход раунда ------------------------------------------------------------
def start_round(chat_id, user, total, thread=None):
    items = db.pick(chat_id, total)
    if not items:
        send(chat_id, "В базе нет ни одного вопроса. Проверь questions.csv.",
             thread=thread)
        return
    if len(items) < total:
        send(chat_id, f"В базе всего {len(items)} {plural(len(items))} — "
                      f"столько и сыграем.", thread=thread)
        total = len(items)
    games[chat_id] = {
        "total": total, "asked": 0, "queue": items, "starter": user.get("id"),
        "seq": 0, "q": None, "next_at": time.time() + 2, "players": {},
        "thread": thread,
    }
    send(chat_id,
         f"🎯 <b>Викторина: {total} {plural(total)}</b>\n\n"
         f"Отвечают все, но кто раньше — тому больше: {POINTS_TEXT}.\n"
         f"Ошибся — этот вопрос для тебя закрыт, так что не тыкай наугад.\n"
         f"На каждый вопрос {QUESTION_SECONDS} секунд.\n\n"
         f"Начали.", thread=thread)


def send_question(chat_id, g):
    item = g["queue"].pop(0)
    wrong = item["wrong"]
    if len(wrong) > OPTIONS - 1:
        wrong = random.sample(wrong, OPTIONS - 1)
    options = [item["correct"]] + wrong
    random.shuffle(options)
    g["asked"] += 1
    g["seq"] += 1
    db.mark_asked(chat_id, [item["id"]])

    text = item["question"].strip()
    if not text.endswith(("?", "!", ".", ":")):
        text += "?"
    q = {
        "seq": g["seq"], "text": text, "topic": item["topic"],
        "options": options, "correct": options.index(item["correct"]),
        # кто уже нажал -- и угадавшие, и выбывшие: второй попытки нет ни у кого
        "answered": set(), "msg_id": None,
        # угадавшие по порядку: от него и считаются места
        "winners": [],
        # уточним после отправки
        "started": time.time(),
        "deadline": time.time() + QUESTION_SECONDS,
    }
    r = send(chat_id, body(g, q), keyboard(q), g["thread"])
    if not r.get("ok"):
        # не отправилось -- не подвешиваем раунд, пробуем следующий вопрос
        g["next_at"] = time.time() + PAUSE_SECONDS
        return
    q["msg_id"] = r["result"]["message_id"]
    # время считаем от момента, когда сообщение реально ушло, иначе в счёт
    # скорости попадёт задержка сети
    q["started"] = time.time()
    q["deadline"] = q["started"] + QUESTION_SECONDS
    g["q"] = q


def head(g, q):
    return (f"<b>Вопрос {g['asked']} из {g['total']}</b>  ·  "
            f"<i>{esc(q['topic'])}</i>")


def body(g, q):
    return (f"{head(g, q)}\n\n{esc(q['text'])}\n\n"
            f"⏱ {QUESTION_SECONDS} с  ·  {POINTS_TEXT}")


def keyboard(q):
    labels = [f"{LETTERS[i]}. {opt}" for i, opt in enumerate(q["options"])]
    buttons = [btn(t, f"a:{q['seq']}:{i}") for i, t in enumerate(labels)]
    # длинные варианты телеграм ужимает до многоточия, поэтому в два столбца
    # ставим только короткие -- иначе восемь кнопок читать невозможно
    per_row = 2 if max(len(t) for t in labels) <= 22 else 1
    return kb([buttons[i:i + per_row] for i in range(0, len(buttons), per_row)])


def close_question(chat_id, g):
    """Убирает кнопки, показывает верный вариант и кто какое место занял."""
    q = g["q"]
    right = f"{LETTERS[q['correct']]}. {esc(q['options'][q['correct']])}"
    lines = [head(g, q), "", esc(q["text"]), "", f"Ответ: <b>{right}</b>", ""]
    if q["winners"]:
        for w in q["winners"]:
            mark = (MEDALS[w["place"] - 1] if w["place"] <= len(MEDALS)
                    else f"{w['place']}.")
            lines.append(f"{mark} <b>{esc(w['name'])}</b> +{w['gain']}"
                         f"  ·  {secs(w['ms'])}")
        missed = len(q["answered"]) - len(q["winners"])
        if missed:
            lines.append(f"мимо: {missed}")
    elif q["answered"]:
        n = len(q["answered"])
        lines.append(f"🤷 Никто не угадал  ·  {n} "
                     f"{plural(n, 'ответ', 'ответа', 'ответов')} мимо")
    else:
        lines.append("🤷 Никто не ответил")
    if q["msg_id"]:
        edit(chat_id, q["msg_id"], "\n".join(lines))
    g["q"] = None
    g["next_at"] = time.time() + PAUSE_SECONDS


def results_table(g):
    rows = sorted(g["players"].values(), key=lambda p: (-p["points"], avg(p["times"])))
    lines = [f"🏁 <b>Итоги</b> · {g['asked']} {plural(g['asked'])}", ""]
    for i, p in enumerate(rows):
        mark = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
        lines.append(f"{mark} <b>{esc(p['name'])}</b> — {p['points']} "
                     f"{plural(p['points'], 'балл', 'балла', 'баллов')}"
                     f"  ·  {len(p['times'])} из {g['asked']}"
                     f"  ·  в среднем {secs(avg(p['times']))}")
    fastest = min(rows, key=lambda p: min(p["times"]))
    lines += ["", f"⚡ Самый быстрый ответ: <b>{esc(fastest['name'])}</b>, "
                  f"{secs(min(fastest['times']))}"]
    if len(rows) > 1:
        lines.append(f"👑 Победитель: <b>{esc(rows[0]['name'])}</b>")
    return "\n".join(lines)


def finish_round(chat_id, g):
    games.pop(chat_id, None)
    if not g["players"]:
        send(chat_id, "🏁 Раунд окончен, но никто так и не ответил.",
             thread=g["thread"])
        return
    db.save_round(chat_id, g["players"])
    send(chat_id, results_table(g), thread=g["thread"])


def tick():
    """Двигает раунды по времени: закрывает просроченные вопросы и задаёт
    следующие. Вызывается из главного цикла перед каждым getUpdates."""
    now = time.time()
    for chat_id, g in list(games.items()):
        if g["q"]:
            q = g["q"]
            if now >= q["deadline"]:
                close_question(chat_id, g)
        elif now >= g["next_at"]:
            if g["asked"] >= g["total"] or not g["queue"]:
                finish_round(chat_id, g)
            else:
                send_question(chat_id, g)


def poll_timeout():
    """Долго ждать обновления можно только пока никого не поджимает таймер."""
    if not games:
        return 30
    stamps = []
    for g in games.values():
        q = g["q"]
        if not q:
            stamps.append(g["next_at"])
            continue
        stamps.append(q["deadline"])
    return max(1, min(30, math.ceil(min(stamps) - time.time())))


# --- обработка апдейтов ----------------------------------------------------
HELP = (
    "🎯 <b>qcold</b> — викторина для групп.\n\n"
    "<b>/quiz N</b> — раунд из N вопросов (по умолчанию "
    f"{DEFAULT_QUESTIONS}, максимум {MAX_QUESTIONS})\n"
    "<b>/stop</b> — прервать раунд\n"
    "<b>/top</b> — таблица за всё время в этом чате\n"
    "<b>/reset</b> — обнулить таблицу чата (только админ)\n\n"
    f"У вопроса {OPTIONS} вариантов и {QUESTION_SECONDS} секунд. Отвечают "
    f"все, но баллы зависят от того, кто раньше: {POINTS_TEXT}. Ответил "
    "неверно — на этом вопросе выбываешь, перебирать варианты бессмысленно."
    "\n\n"
    "Пока идёт вопрос, бот его сообщение не трогает: любая правка "
    "перерисовывает кнопки, и нажатие в этот момент пропадает. Поэтому кто "
    "сколько взял, видно только когда вопрос закрылся.\n\n"
    "В конце раунда — у кого сколько баллов и кто отвечал быстрее всех."
)


def cmd_quiz(chat_id, user, args, thread=None):
    if chat_id in games:
        send(chat_id, "Раунд уже идёт. /stop — если надо прервать.", thread=thread)
        return
    total = DEFAULT_QUESTIONS
    if args:
        if not args[0].lstrip("+").isdigit():
            send(chat_id, "Сколько вопросов? Например: <code>/quiz 10</code>",
                 thread=thread)
            return
        total = max(1, min(MAX_QUESTIONS, int(args[0])))
    start_round(chat_id, user, total, thread)


def cmd_stop(chat_id, user, thread=None):
    g = games.get(chat_id)
    if not g:
        send(chat_id, "Сейчас ничего не идёт.", thread=thread)
        return
    if user.get("id") != g["starter"] and not is_admin(chat_id, user.get("id")):
        send(chat_id, "Прервать раунд может тот, кто его начал, или админ чата.",
             thread=thread)
        return
    if g["q"] and g["q"]["msg_id"]:
        close_question(chat_id, g)
    send(chat_id, "⏹ Раунд прерван.", thread=g["thread"])
    finish_round(chat_id, g)


def cmd_top(chat_id, thread=None):
    rows = db.top(chat_id)
    if not rows:
        send(chat_id, "Здесь ещё никто не набрал ни балла. /quiz 10 — и начнём.",
             thread=thread)
        return
    lines = ["🏆 <b>Таблица чата за всё время</b>", ""]
    for i, r in enumerate(rows):
        mark = MEDALS[i] if i < len(MEDALS) else f"{i + 1}."
        speed = (f"  ·  в среднем {secs(r['total_ms'] / r['answers'])}"
                 if r["answers"] else "")
        lines.append(f"{mark} <b>{esc(r['name'])}</b> — {r['points']} "
                     f"{plural(r['points'], 'балл', 'балла', 'баллов')}{speed}")
    best = min((r for r in rows if r["best_ms"] is not None),
               key=lambda r: r["best_ms"], default=None)
    if best:
        lines += ["", f"⚡ Рекорд скорости: <b>{esc(best['name'])}</b>, "
                      f"{secs(best['best_ms'])}"]
    send(chat_id, "\n".join(lines), thread=thread)


def handle_message(m):
    chat_id = m["chat"]["id"]
    # message_thread_id приходит и вне форумов, но валидной темой он является
    # только при is_topic_message -- иначе телеграм ответит
    # "message thread not found" и сообщение не дойдёт
    thread = m.get("message_thread_id") if m.get("is_topic_message") else None
    if any(u.get("id") == BOT_ID for u in m.get("new_chat_members", [])):
        send(chat_id, HELP, thread=thread)
        return
    text = (m.get("text") or "").strip()
    if not text.startswith("/"):
        return
    parts = text.split()
    # в группах команду часто пишут как /quiz@qcoldbot -- хвост отрезаем
    cmd = parts[0][1:].split("@")[0].lower()
    args = parts[1:]
    user = m.get("from") or {}

    if cmd in ("start", "help"):
        send(chat_id, HELP, thread=thread)
    elif cmd == "quiz":
        cmd_quiz(chat_id, user, args, thread)
    elif cmd == "stop":
        cmd_stop(chat_id, user, thread)
    elif cmd == "top":
        cmd_top(chat_id, thread)
    elif cmd == "reset":
        if is_admin(chat_id, user.get("id")):
            db.reset(chat_id)
            send(chat_id, "Таблица чата обнулена.", thread=thread)
        else:
            send(chat_id, "Обнулить таблицу может только админ чата.",
                 thread=thread)


def handle_callback(cq):
    data = cq.get("data") or ""
    chat = (cq.get("message") or {}).get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or not data.startswith("a:"):
        answer(cq)
        return
    try:
        _, seq, idx = data.split(":")
        seq, idx = int(seq), int(idx)
    except ValueError:
        answer(cq)
        return

    g = games.get(chat_id)
    # seq растёт с каждым вопросом: нажатие на кнопку старого сообщения
    # сюда не пролезет
    if not g or not g["q"] or g["q"]["seq"] != seq:
        answer(cq, "Этот вопрос уже закрыт")
        return

    q, user = g["q"], cq["from"]
    uid = user.get("id")
    if uid in q["answered"]:
        answer(cq, "Ты уже ответил на этот вопрос")
        return
    ms = int((time.time() - q["started"]) * 1000)
    q["answered"].add(uid)
    if idx != q["correct"]:
        answer(cq, "Мимо. Этот вопрос для тебя закрыт.", alert=True)
        # Вопрос при этом НЕ закрываем: остальные ещё не нажимали. Закрывать
        # его "когда ответили все" нельзя -- бот не знает, кто в чате играет,
        # а кто просто читает. Любая оценка состава ошибается в меньшую
        # сторону и отбирает ход у тех, кто не успел.
        return

    place = len(q["winners"]) + 1
    gain = award(place)
    name = display_name(user)
    q["winners"].append({"name": name, "ms": ms, "place": place, "gain": gain})
    p = g["players"].setdefault(uid, {"name": name, "points": 0, "times": []})
    p["name"] = name                    # мог сменить имя между раундами
    p["points"] += gain
    p["times"].append(ms)
    # вопрос не закрываем: у остальных ещё есть время занять своё место
    answer(cq, f"Верно! {place}-е место, +{gain} "
               f"{plural(gain, 'балл', 'балла', 'баллов')}  ·  {secs(ms)}")


def drop_pending():
    """После перезапуска в очереди у телеграма висят команды и нажатия,
    накопившиеся, пока бот лежал, -- в том числе от раунда, которого в памяти
    уже нет. Проигрывать их заново незачем: забираем только последний апдейт
    и продолжаем с него."""
    r = api("getUpdates", offset=-1, timeout=0)
    if r.get("ok") and r["result"]:
        skipped = r["result"][-1]["update_id"]
        print("пропущены накопившиеся апдейты по", skipped, "включительно")
        return skipped + 1
    return None


def main():
    global BOT_ID, BOT_NAME
    total = db.init()
    me = api("getMe")
    if not me.get("ok"):
        print("токен не принят:", me)
        return
    BOT_ID = me["result"]["id"]
    BOT_NAME = me["result"].get("username", "")
    api("setMyCommands", commands=[
        {"command": "quiz", "description": "начать викторину: /quiz 10"},
        {"command": "stop", "description": "прервать раунд"},
        {"command": "top", "description": "таблица чата за всё время"},
        {"command": "help", "description": "как это работает"},
    ])
    print(f"бот запущен: @{BOT_NAME}, вопросов в базе: {total}")

    offset = drop_pending()
    while True:
        tick()
        upd = api("getUpdates", offset=offset, timeout=poll_timeout())
        if not upd.get("ok"):
            time.sleep(3)
            continue
        for u in upd["result"]:
            offset = u["update_id"] + 1
            try:
                if "message" in u:
                    handle_message(u["message"])
                elif "callback_query" in u:
                    handle_callback(u["callback_query"])
            except Exception as e:
                print("ошибка обработки:", type(e).__name__, e)


if __name__ == "__main__":
    main()
