"""Study Flow: a local student assignment planner. Python 3.10+, standard library only."""

import argparse
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys

VERSION = "1.0.0"
DEFAULT_DB = Path(__file__).resolve().parent / "data" / "study_flow.db"


class PlannerError(ValueError):
    """An actionable error that can be shown without a traceback."""


def clean_text(value, label, maximum=120):
    value = value.strip()
    if not value or len(value) > maximum:
        raise PlannerError(f"{label} must contain 1-{maximum} characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PlannerError(f"{label} must be a single line without control characters.")
    return value


def parse_date(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise PlannerError("Use a real date in YYYY-MM-DD format (for example, 2026-10-05).") from None


@contextmanager
def database(path):
    """Open the local database; preserve data and reject unknown future schemas."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise PlannerError("This database uses a newer schema. Use a matching Study Flow version.")
        if version == 0:
            # Do not silently adopt an unrelated SQLite file passed via --db.
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                raise PlannerError("This is not an empty Study Flow database. Choose a different --db path.")
            with connection:
                connection.execute("""CREATE TABLE modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(trim(name)) > 0)
                )""")
                connection.execute("""CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_id INTEGER NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
                    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                    due TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('assignment', 'revision')),
                    done INTEGER NOT NULL DEFAULT 0 CHECK(done IN (0, 1))
                )""")
                connection.execute("CREATE INDEX tasks_due ON tasks(done, due)")
                connection.execute("PRAGMA user_version = 1")
        yield connection
    finally:
        connection.close()


def require_module(db, module_id):
    if not db.execute("SELECT 1 FROM modules WHERE id = ?", (module_id,)).fetchone():
        raise PlannerError(f"Module {module_id} does not exist. Run 'modules' to see module IDs.")


def require_task(db, task_id):
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise PlannerError(f"Task {task_id} does not exist. Run 'list --status all' to see task IDs.")
    return row


def add_module(db, name):
    name = clean_text(name, "Module name", 80)
    try:
        with db:
            return db.execute("INSERT INTO modules(name) VALUES (?)", (name,)).lastrowid
    except sqlite3.IntegrityError:
        raise PlannerError("That module already exists (names are case-insensitive for ASCII letters).") from None


def rename_module(db, module_id, name):
    require_module(db, module_id)
    name = clean_text(name, "Module name", 80)
    try:
        with db:
            db.execute("UPDATE modules SET name = ? WHERE id = ?", (name, module_id))
    except sqlite3.IntegrityError:
        raise PlannerError("That module name is already in use.") from None


def delete_module(db, module_id):
    require_module(db, module_id)
    try:
        with db:
            db.execute("DELETE FROM modules WHERE id = ?", (module_id,))
    except sqlite3.IntegrityError:
        raise PlannerError("This module still has tasks. Move or delete them before deleting the module.") from None


def add_task(db, module_id, title, due, kind="assignment"):
    require_module(db, module_id)
    title = clean_text(title, "Task title")
    due = parse_date(due).isoformat()
    if kind not in ("assignment", "revision"):
        raise PlannerError("Task kind must be assignment or revision.")
    with db:
        return db.execute("INSERT INTO tasks(module_id, title, due, kind) VALUES (?, ?, ?, ?)",
                          (module_id, title, due, kind)).lastrowid


def edit_task(db, task_id, *, module_id=None, title=None, due=None, kind=None):
    old = require_task(db, task_id)
    if all(value is None for value in (module_id, title, due, kind)):
        raise PlannerError("Choose at least one field to edit: --module, --title, --due, or --kind.")
    module_id = old["module_id"] if module_id is None else module_id
    require_module(db, module_id)
    title = old["title"] if title is None else clean_text(title, "Task title")
    due = old["due"] if due is None else parse_date(due).isoformat()
    kind = old["kind"] if kind is None else kind
    if kind not in ("assignment", "revision"):
        raise PlannerError("Task kind must be assignment or revision.")
    with db:
        db.execute("UPDATE tasks SET module_id = ?, title = ?, due = ?, kind = ? WHERE id = ?",
                   (module_id, title, due, kind, task_id))


def set_done(db, task_id, done):
    require_task(db, task_id)
    with db:
        db.execute("UPDATE tasks SET done = ? WHERE id = ?", (int(done), task_id))


def delete_task(db, task_id):
    require_task(db, task_id)
    with db:
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def list_tasks(db, *, view="all", status="open", module_id=None, today=None):
    today = date.today() if today is None else today
    conditions, values = [], []
    if status not in ("open", "done", "all") or view not in ("all", "today", "week", "overdue"):
        raise PlannerError("Unknown task view or status.")
    if status != "all":
        conditions.append("t.done = ?")
        values.append(int(status == "done"))
    if module_id is not None:
        require_module(db, module_id)
        conditions.append("t.module_id = ?")
        values.append(module_id)
    if view == "today":
        conditions.append("t.due = ?")
        values.append(today.isoformat())
    elif view == "overdue":
        conditions.extend(("t.due < ?", "t.done = 0"))
        values.append(today.isoformat())
    elif view == "week":
        conditions.append("t.due BETWEEN ? AND ?")
        values.extend((today.isoformat(), (today + timedelta(days=6)).isoformat()))
    query = "SELECT t.*, m.name AS module FROM tasks t JOIN modules m ON m.id = t.module_id"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY t.due, t.id"
    return db.execute(query, values).fetchall()


def print_tasks(rows, today):
    if not rows:
        print("No matching tasks. A little breathing room.")
        return
    for task in rows:
        state = "DONE" if task["done"] else "OVERDUE" if task["due"] < today.isoformat() else "TODAY" if task["due"] == today.isoformat() else "OPEN"
        print(f"#{task['id']} [{state}] {task['due']} | {task['module']} | {task['kind']}")
        print(f"    {task['title']}")


def positive_id(value):
    try:
        number = int(value)
        if number > 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("IDs must be positive whole numbers.")


def parser():
    root = argparse.ArgumentParser(description="Study Flow - your modules, deadlines, and revision in one local planner.",
                                   epilog="Start: module-add 'Mathematics', then add --module 1 --title 'Problem set' --due YYYY-MM-DD")
    root.add_argument("--version", action="version", version=f"Study Flow {VERSION}")
    root.add_argument("--db", type=Path, default=DEFAULT_DB, help="database file (default: data/study_flow.db beside this script); put before the command")
    sub = root.add_subparsers(dest="command")
    sub.add_parser("dashboard", help="show overdue tasks and the next seven days (default)")
    sub.add_parser("modules", help="list modules and their IDs")
    add = sub.add_parser("module-add", help="add a module")
    add.add_argument("name")
    rename = sub.add_parser("module-rename", help="rename a module without losing its tasks")
    rename.add_argument("id", type=positive_id)
    rename.add_argument("name")
    remove = sub.add_parser("module-delete", help="delete an empty module")
    remove.add_argument("id", type=positive_id)
    add = sub.add_parser("add", help="add an assignment or revision session")
    add.add_argument("--module", type=positive_id, required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--due", required=True, help="YYYY-MM-DD; past dates are allowed")
    add.add_argument("--kind", choices=("assignment", "revision"), default="assignment")
    edit = sub.add_parser("edit", help="update a task's fields")
    edit.add_argument("id", type=positive_id)
    edit.add_argument("--module", type=positive_id)
    edit.add_argument("--title")
    edit.add_argument("--due", help="YYYY-MM-DD")
    edit.add_argument("--kind", choices=("assignment", "revision"))
    for name, help_text in (("complete", "mark a task complete"), ("reopen", "mark a task open"), ("delete", "permanently delete a task")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("id", type=positive_id)
    listing = sub.add_parser("list", help="filter tasks; open tasks by default")
    listing.add_argument("--view", choices=("all", "today", "week", "overdue"), default="all")
    listing.add_argument("--status", choices=("open", "done", "all"), default="open")
    listing.add_argument("--module", type=positive_id)
    return root


def run(args, db):
    command = args.command or "dashboard"
    today = date.today()
    if command == "module-add":
        print(f"Added module #{add_module(db, args.name)}.")
    elif command == "modules":
        rows = db.execute("SELECT id, name FROM modules ORDER BY name COLLATE NOCASE").fetchall()
        for row in rows:
            print(f"#{row['id']}  {row['name']}")
        if not rows:
            print("No modules yet. Start with: python study_flow.py module-add 'Mathematics'")
    elif command == "module-rename":
        rename_module(db, args.id, args.name)
        print(f"Renamed module #{args.id}.")
    elif command == "module-delete":
        delete_module(db, args.id)
        print(f"Deleted module #{args.id}.")
    elif command == "add":
        print(f"Added task #{add_task(db, args.module, args.title, args.due, args.kind)}.")
    elif command == "edit":
        edit_task(db, args.id, module_id=args.module, title=args.title, due=args.due, kind=args.kind)
        print(f"Updated task #{args.id}.")
    elif command in ("complete", "reopen"):
        set_done(db, args.id, command == "complete")
        print(f"Task #{args.id} marked {'complete' if command == 'complete' else 'open'}.")
    elif command == "delete":
        delete_task(db, args.id)
        print(f"Deleted task #{args.id}.")
    elif command == "list":
        print_tasks(list_tasks(db, view=args.view, status=args.status, module_id=args.module, today=today), today)
    else:
        print(f"STUDY FLOW | {today.isoformat()}")
        print("\nOVERDUE")
        print_tasks(list_tasks(db, view="overdue", today=today), today)
        print("\nNEXT 7 DAYS (including today)")
        print_tasks(list_tasks(db, view="week", today=today), today)
        print("\nUse 'list' for all open tasks, or '--help' for commands.")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with database(args.db) as db:
            run(args, db)
        return 0
    except (PlannerError, OSError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
