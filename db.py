"""Хранилище викторины: вопросы, история показов и счёт по чатам.

Источник правды для вопросов -- папка questions/: один csv на тему, имя
файла и есть её название. Такие файлы удобно править руками и смотреть
диффом, а добавить тему -- значит просто положить рядом ещё один файл.

Sqlite нужен не ради самих вопросов, а ради двух вещей, которые в питоне
вышли бы неудобно: выбрать случайные вопросы, которые в этом чате давно не
задавали, и хранить накопленный счёт между перезапусками.
"""
import csv
import json
import os
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).parent
# На сервере файловая система часто временная -- пусть путь к базе можно
# будет увести на диск переменной окружения, не трогая код.
DB_PATH = Path(os.environ.get("DATA_DIR", HERE)) / "quiz.db"
QUESTIONS_DIR = HERE / "questions"

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id       INTEGER PRIMARY KEY,
    pack     TEXT NOT NULL DEFAULT 'общая',   -- тема, она же имя файла
    topic    TEXT NOT NULL,
    question TEXT NOT NULL UNIQUE,
    correct  TEXT NOT NULL,
    wrong    TEXT NOT NULL              -- json-список неверных вариантов
);

-- когда вопрос последний раз задавали в этом чате
CREATE TABLE IF NOT EXISTS asked (
    chat_id     INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    at          INTEGER NOT NULL,
    PRIMARY KEY (chat_id, question_id)
);

-- счёт за всё время, отдельно в каждом чате
CREATE TABLE IF NOT EXISTS scores (
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    points   INTEGER NOT NULL DEFAULT 0,
    rounds   INTEGER NOT NULL DEFAULT 0,
    answers  INTEGER NOT NULL DEFAULT 0,   -- верных ответов
    total_ms INTEGER NOT NULL DEFAULT 0,   -- суммарное время этих ответов
    best_ms  INTEGER,
    PRIMARY KEY (chat_id, user_id)
);
"""

CONN = None


def connect():
    # на Railway каталог тома появляется сам, но если DATA_DIR задали
    # раньше, чем подключили том, пусть база всё равно откроется
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # база могла остаться с прошлой версии, когда тем ещё не было
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
    if "pack" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN pack TEXT "
                     "NOT NULL DEFAULT 'общая'")
        conn.commit()
    return conn


def init():
    """Открыть базу и залить в неё все файлы из questions/."""
    global CONN
    CONN = connect()
    return import_all()


def _read(path, pack):
    """Разбирает один файл темы. Возвращает строки для вставки."""
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        # колонок с неверными вариантами может быть сколько угодно:
        # wrong1, wrong2, ... -- сколько дописали в файл, столько и берём
        wrong_cols = sorted(
            (c for c in (reader.fieldnames or []) if c and c.startswith("wrong")),
            key=lambda c: int(c[5:]) if c[5:].isdigit() else 0)
        for i, row in enumerate(reader, start=2):
            if not row.get("question") or not row.get("correct"):
                continue
            correct = row["correct"].strip()
            wrong, seen = [], {correct}
            for k in wrong_cols:
                w = (row.get(k) or "").strip()
                if not w:
                    continue
                # два одинаковых варианта в одном вопросе -- всегда опечатка,
                # а совпавший с правильным сделал бы вопрос нечестным
                if w in seen:
                    print(f"{path.name}:{i} -- вариант «{w}» повторяется, пропущен")
                    continue
                seen.add(w)
                wrong.append(w)
            if not wrong:
                print(f"{path.name}:{i} -- нет неверных вариантов, вопрос пропущен")
                continue
            rows.append((pack, row.get("topic", pack).strip() or pack,
                         row["question"].strip(), correct,
                         json.dumps(wrong, ensure_ascii=False)))
    return rows


def import_all():
    """Заливает все темы в базу. Ключ -- текст вопроса: поправленный вопрос
    обновляет строку, а не плодит дубль. Возвращает, сколько вопросов в базе."""
    files = sorted(QUESTIONS_DIR.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"нет ни одного файла с вопросами в {QUESTIONS_DIR}")
    rows = []
    for path in files:
        rows.extend(_read(path, path.stem))
    with CONN:
        CONN.executemany(
            """INSERT INTO questions (pack, topic, question, correct, wrong)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(question) DO UPDATE SET
                   pack = excluded.pack,
                   topic = excluded.topic,
                   correct = excluded.correct,
                   wrong = excluded.wrong""", rows)
        # Вопрос, пропавший из файлов, надо убрать и из базы: иначе
        # выброшенный или переименованный живёт в ней вечно и продолжает
        # выпадать людям. Через временную таблицу, а не через IN (?, ?, ...):
        # списком параметров можно упереться в лимит sqlite.
        if rows:
            CONN.execute("CREATE TEMP TABLE IF NOT EXISTS keep "
                         "(question TEXT PRIMARY KEY)")
            CONN.execute("DELETE FROM keep")
            CONN.executemany("INSERT OR IGNORE INTO keep VALUES (?)",
                             [(r[2],) for r in rows])
            CONN.execute("""DELETE FROM asked WHERE question_id IN (
                                SELECT id FROM questions
                                 WHERE question NOT IN (SELECT question FROM keep))""")
            gone = CONN.execute("""DELETE FROM questions
                                    WHERE question NOT IN
                                          (SELECT question FROM keep)""").rowcount
            if gone:
                print(f"убрано вопросов, которых больше нет в файлах: {gone}")
    return count()


def count():
    return CONN.execute("SELECT COUNT(*) FROM questions").fetchone()[0]


def packs():
    """Темы и сколько в каждой вопросов, по убыванию размера."""
    cur = CONN.execute("""SELECT pack, COUNT(*) AS n FROM questions
                           GROUP BY pack ORDER BY n DESC, pack""")
    return [(r["pack"], r["n"]) for r in cur]


def pick(chat_id, n, chosen=None):
    """n вопросов для чата: сначала те, которых тут ещё не было, потом самые
    давние. Внутри одного дня порядок случайный, иначе после первого круга
    вопросы шли бы всегда в одной и той же последовательности.

    chosen -- список тем; пустой или None означает «все темы»."""
    where, params = "", [chat_id]
    if chosen:
        where = " WHERE q.pack IN (%s)" % ",".join("?" * len(chosen))
        params += list(chosen)
    params.append(n)
    cur = CONN.execute(
        f"""SELECT q.id, q.pack, q.topic, q.question, q.correct, q.wrong
              FROM questions q
              LEFT JOIN asked a ON a.question_id = q.id AND a.chat_id = ?
              {where}
             ORDER BY COALESCE(a.at, 0) / 86400, RANDOM()
             LIMIT ?""", params)
    return [{"id": r["id"], "pack": r["pack"], "topic": r["topic"],
             "question": r["question"], "correct": r["correct"],
             "wrong": json.loads(r["wrong"])} for r in cur]


def mark_asked(chat_id, question_ids):
    now = int(time.time())
    with CONN:
        CONN.executemany(
            """INSERT INTO asked (chat_id, question_id, at) VALUES (?, ?, ?)
               ON CONFLICT(chat_id, question_id) DO UPDATE SET at = excluded.at""",
            [(chat_id, qid, now) for qid in question_ids])


def save_round(chat_id, players):
    """players: {user_id: {"name", "points", "times"}} -- итоги одного раунда."""
    with CONN:
        for uid, p in players.items():
            times = p["times"]
            CONN.execute(
                """INSERT INTO scores (chat_id, user_id, name, points, rounds,
                                       answers, total_ms, best_ms)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                   ON CONFLICT(chat_id, user_id) DO UPDATE SET
                       name     = excluded.name,
                       points   = scores.points + excluded.points,
                       rounds   = scores.rounds + 1,
                       answers  = scores.answers + excluded.answers,
                       total_ms = scores.total_ms + excluded.total_ms,
                       best_ms  = MIN(COALESCE(scores.best_ms, excluded.best_ms),
                                      COALESCE(excluded.best_ms, scores.best_ms))""",
                (chat_id, uid, p["name"], p["points"], len(times),
                 sum(times), min(times) if times else None))


def top(chat_id, limit=15):
    cur = CONN.execute(
        """SELECT name, points, rounds, answers, total_ms, best_ms
             FROM scores
            WHERE chat_id = ? AND points > 0
            ORDER BY points DESC,
                     CASE WHEN answers > 0 THEN total_ms * 1.0 / answers END ASC
            LIMIT ?""", (chat_id, limit))
    return [dict(r) for r in cur]


def reset(chat_id):
    with CONN:
        CONN.execute("DELETE FROM scores WHERE chat_id = ?", (chat_id,))
