"""Перегоняем квизы из соседних чатов в формат questions/*.csv.

Два формата: у футбола варианты в одну строку кириллическими буквами и
отдельная строка «Ответ:», у аниме и лукмаксинга — по варианту на строку
латиницей, правильный помечен галочкой.
"""
import csv
import io
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(r"C:\Users\User\Desktop\CLAUDE\qcold\questions")
DOWNLOADS = Path(r"C:\Users\User\Downloads")

JOBS = [
    ("futbol_kviz_50_voprosov.md", "футбол", "инлайн", "после 2005"),
    ("anime-quiz-50.md", "аниме", "построчно", "сюжет"),
    ("lookmaxxing_quiz.txt", "лукмаксинг", "построчно", "тикток"),
]

CYR = "абвгдежз"
LAT = "abcdefgh"


def clean(s):
    s = s.replace("**", "").replace("✅", "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .;")


def parse_inline(text):
    """Формат футбола: вопрос жирным, варианты в строку, ниже «Ответ: б) …»."""
    out = []
    blocks = re.split(r"\n\s*\n", text)
    for b in blocks:
        lines = [l.strip() for l in b.strip().splitlines() if l.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"\*\*\d+\.\s*(.+?)\*\*$", lines[0])
        if not m:
            continue
        question = clean(m.group(1))
        parts = re.split(r"(?:^|\s)([%s])\)\s*" % CYR, lines[1])
        opts = {}
        for i in range(1, len(parts) - 1, 2):
            opts[parts[i]] = clean(parts[i + 1])
        ans = re.search(r"Ответ:\s*([%s])\)" % CYR, lines[2])
        if not ans or len(opts) < 4:
            continue
        letter = ans.group(1)
        if letter not in opts:
            continue
        correct = opts.pop(letter)
        out.append((question, correct, list(opts.values())))
    return out


def parse_lines(text):
    """Формат аниме и лукмаксинга: по варианту на строку, галочка у верного."""
    out = []
    question, opts, correct = None, [], None
    for raw in text.splitlines() + ["999. конец"]:
        line = raw.strip()
        mq = re.match(r"^\*{0,2}(\d+)\.\s+(.+?)\*{0,2}$", line)
        mo = re.match(r"^([%s])\)\s*(.+)$" % LAT, line)
        if mq and not mo:
            if question and correct and len(opts) >= 4:
                out.append((question, correct, opts))
            question, opts, correct = clean(mq.group(2)), [], None
            continue
        if mo and question:
            text_opt = clean(mo.group(2))
            if "✅" in raw:
                correct = text_opt
            else:
                opts.append(text_opt)
    return out


existing = set()
for f in REPO.glob("*.csv"):
    for r in csv.DictReader(f.open(encoding="utf-8-sig"), delimiter=";"):
        existing.add(r["question"].strip().lower().rstrip("?"))

for fname, pack, mode, topic in JOBS:
    path = DOWNLOADS / fname
    text = path.read_text(encoding="utf-8", errors="replace")
    items = parse_inline(text) if mode == "инлайн" else parse_lines(text)
    rows, skipped_dup, skipped_bad = [], 0, 0
    for question, correct, wrong in items:
        key = question.strip().lower().rstrip("?")
        if key in existing:
            skipped_dup += 1
            continue
        wrong = [w for w in dict.fromkeys(wrong) if w and w != correct][:7]
        if not correct or len(wrong) < 3:
            skipped_bad += 1
            continue
        existing.add(key)
        rows.append([topic, question, correct] + wrong)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    for r in rows:
        w.writerow(r)
    with (REPO / f"{pack}.csv").open("a", encoding="utf-8", newline="") as out:
        out.write(buf.getvalue())
    print(f"{fname:<32} -> {pack:<12} разобрано {len(items):>3}, "
          f"добавлено {len(rows):>3}, дублей {skipped_dup:>3}, битых {skipped_bad}")
