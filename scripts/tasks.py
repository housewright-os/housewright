"""Household task board: the shared to-do list for the house.

Backed by data/tasks.json (gitignored, like all household data). Used three
ways: from this CLI, from the Telegram bot ("add task fix the gate"), and by
notify.py, which puts the top open tasks in the morning brief.

Tasks are numbered with stable ids. Deferring a task hides it until the date
it was pushed to; it is not a judgment, just a snooze.

Both the bot daemon and this CLI can write the file, so every mutation takes
an exclusive flock and every save is an atomic temp-file replace. A file that
fails to parse is preserved as tasks.json.corrupt-<stamp>, never overwritten.

Usage:
    python3 scripts/tasks.py add "fix the gate latch" [--due 2026-08-20]
    python3 scripts/tasks.py list            # open tasks
    python3 scripts/tasks.py list --all      # everything, including done
    python3 scripts/tasks.py done 3
    python3 scripts/tasks.py defer 3 2       # push task 3 out 2 days
    python3 scripts/tasks.py brief           # the lines the morning brief uses
"""

import fcntl
import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(ROOT, "data", "tasks.json")
LOCKFILE = TASKS + ".lock"

EMPTY = {"seq": 0, "tasks": []}


@contextmanager
def _locked():
    """Exclusive lock for the read-modify-write cycle (bot daemon vs CLI)."""
    os.makedirs(os.path.dirname(TASKS), exist_ok=True)
    with open(LOCKFILE, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _load():
    if not os.path.exists(TASKS):
        return json.loads(json.dumps(EMPTY))
    try:
        with open(TASKS) as f:
            return json.load(f)
    except Exception:
        # Never overwrite a corrupted file with defaults; keep it for a look.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            os.replace(TASKS, f"{TASKS}.corrupt-{stamp}")
            print(f"tasks.json unreadable, preserved as tasks.json.corrupt-{stamp}",
                  file=sys.stderr)
        except Exception:
            pass
        return json.loads(json.dumps(EMPTY))


def _save(db):
    os.makedirs(os.path.dirname(TASKS), exist_ok=True)
    tmp = TASKS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=1)
    os.replace(tmp, TASKS)


def add(text, due=None, by=None):
    """Add a task, return its id."""
    with _locked():
        db = _load()
        db["seq"] += 1
        db["tasks"].append({
            "id": db["seq"],
            "text": text.strip(),
            "added": date.today().isoformat(),
            "due": due,
            "snoozed_until": None,
            "done_at": None,
            "by": by or None,
        })
        _save(db)
        return db["seq"]


def complete(tid):
    """Mark a task done. Returns the task dict or None if not found/already done."""
    with _locked():
        db = _load()
        for t in db["tasks"]:
            if t["id"] == tid and not t["done_at"]:
                t["done_at"] = datetime.now().isoformat(timespec="seconds")
                _save(db)
                return t
        return None


def defer(tid, days=1):
    """Snooze a task for N days. Returns (task, until_date) or (None, None)."""
    with _locked():
        db = _load()
        until = (date.today() + timedelta(days=max(1, days))).isoformat()
        for t in db["tasks"]:
            if t["id"] == tid and not t["done_at"]:
                t["snoozed_until"] = until
                _save(db)
                return t, until
        return None, None


def open_tasks(today=None):
    """Open, unsnoozed tasks, most urgent first: overdue, then by due date,
    then oldest added. Tasks with no due date sort after dated ones.
    Read-only, so no lock: saves are atomic replaces."""
    today = today or date.today().isoformat()
    out = [
        t for t in _load()["tasks"]
        if not t["done_at"] and (not t.get("snoozed_until") or t["snoozed_until"] <= today)
    ]
    out.sort(key=lambda t: (t.get("due") or "9999-12-31", t["added"], t["id"]))
    return out


def fmt(t, today=None):
    today = today or date.today().isoformat()
    line = f"#{t['id']} {t['text']}"
    if t.get("due"):
        if t["due"] < today:
            line += f" (was due {t['due'][5:]})"
        elif t["due"] == today:
            line += " (due today)"
        else:
            line += f" (due {t['due'][5:]})"
    return line


def brief_lines(n=3):
    """The task section of the morning brief: top N open tasks plus a count."""
    ts = open_tasks()
    if not ts:
        return []
    lines = [fmt(t) for t in ts[:n]]
    extra = len(ts) - n
    if extra > 0:
        lines.append(f"(+{extra} more, say 'tasks' for the list)")
    return lines


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip())
        return 2
    cmd = args[0]

    if cmd == "add" and len(args) >= 2:
        due = None
        if "--due" in args:
            i = args.index("--due")
            due = args[i + 1]
            del args[i:i + 2]
        tid = add(" ".join(args[1:]), due=due)
        print(f"added #{tid}")
        return 0

    if cmd == "list":
        show_all = "--all" in args
        ts = _load()["tasks"] if show_all else open_tasks()
        if not ts:
            print("nothing open" if not show_all else "no tasks yet")
            return 0
        for t in ts:
            mark = "x" if t["done_at"] else " "
            print(f"[{mark}] {fmt(t)}")
        return 0

    if cmd == "done" and len(args) >= 2:
        t = complete(int(args[1].lstrip("#")))
        print(f"done: {t['text']}" if t else "no open task with that id")
        return 0 if t else 1

    if cmd == "defer" and len(args) >= 2:
        days = int(args[2]) if len(args) >= 3 else 1
        t, until = defer(int(args[1].lstrip("#")), days)
        print(f"deferred to {until}: {t['text']}" if t else "no open task with that id")
        return 0 if t else 1

    if cmd == "brief":
        for line in brief_lines():
            print(line)
        return 0

    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
