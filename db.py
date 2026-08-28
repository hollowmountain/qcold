"""Хранилище викторины: вопросы, история показов и счёт по чатам.

Источник правды для вопросов -- questions.csv: его удобно править руками
и смотреть диффом. При каждом старте csv заливается в sqlite. Sqlite нужен
не ради вопросов, а ради двух вещей, которые в питоне вышли бы неудобно:
выбрать случайные вопросы, которые в этом чате давно не задавали, и хранить
накопленный счёт между перезапусками.
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
CSV_PATH = HERE / "questions.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id       INTEGER PRIMARY KEY,
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
    answers  INTEGER NOT NULL DEFAULT 0,   -- правильных ответов (= баллов)
    total_ms INTEGER NOT NULL DEFAULT 0,   -- суммарное время этих ответов
    best_ms  INTEGER,
    PRIMARY KEY (chat_id, user_id)
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


CONN = None


def init():
    """Открыть базу и залить в неё questions.csv."""
    global CONN
    CONN = connect()
    return import_csv(CSV_PATH)


def import_csv(path):
    """Заливает csv в таблицу вопросов. Ключ -- текст вопроса: если вопрос
    поправили в файле, обновится строка, а не появится дубль. Возвращает,
    сколько вопросов теперь в базе."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"нет файла с вопросами: {path}")
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for i, row in enumerate(csv.DictReader(f, delimiter=";"), start=2):
            if not row.get("question") or not row.get("correct"):
                continue
            wrong = [row.get(k, "").strip() for k in ("wrong1", "wrong2", "wrong3")]
            wrong = [w for w in wrong if w]
            if len(wrong) < 1:
                print(f"{path.name}:{i} -- нет неверных вариантов, вопрос пропущен")
                continue
            rows.append((row.get("topic", "разное").strip(),
                         row["question"].strip(),
                         row["correct"].strip(),
                         json.dumps(wrong, ensure_ascii=False)))
    with CONN:
        CONN.executemany(
            """INSERT INTO questions (topic, question, correct, wrong)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(question) DO UPDATE SET
                   topic = excluded.topic,
                   correct = excluded.correct,
                   wrong = excluded.wrong""", rows)
    return count()


def count():
    return CONN.execute("SELECT COUNT(*) FROM questions").fetchone()[0]


def pick(chat_id, n):
    """n вопросов для чата: сначала те, которых тут ещё не было, потом самые
    давние. Внутри одного дня порядок случайный, иначе после первого круга
    вопросы шли бы всегда в одной и той же последовательности."""
    cur = CONN.execute(
        """SELECT q.id, q.topic, q.question, q.correct, q.wrong
             FROM questions q
             LEFT JOIN asked a ON a.question_id = q.id AND a.chat_id = ?
            ORDER BY COALESCE(a.at, 0) / 86400, RANDOM()
            LIMIT ?""", (chat_id, n))
    return [{"id": r["id"], "topic": r["topic"], "question": r["question"],
             "correct": r["correct"], "wrong": json.loads(r["wrong"])}
            for r in cur]


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
