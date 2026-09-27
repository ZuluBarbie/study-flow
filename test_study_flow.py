"""Real SQLite and subprocess tests; all data lives in temporary directories."""

from datetime import date
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import study_flow as flow


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "planner.db"
        self.context = flow.database(self.path)
        self.db = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.module = flow.add_module(self.db, "Mathematics")

    def task(self, due="2026-09-27", title="Problem set", kind="assignment"):
        return flow.add_task(self.db, self.module, title, due, kind)

    def test_modules_are_trimmed_and_ascii_case_duplicates_rejected(self):
        with self.assertRaises(flow.PlannerError):
            flow.add_module(self.db, " mathematics ")
        number = flow.add_module(self.db, "  Circuits  ")
        self.assertEqual(self.db.execute("SELECT name FROM modules WHERE id=?", (number,)).fetchone()[0], "Circuits")

    def test_invalid_names_and_control_characters_are_rejected(self):
        for text in ("", "   ", "a" * 81, "Line\nbreak", "Escape\x1b[2J"):
            with self.subTest(text=text), self.assertRaises(flow.PlannerError):
                flow.add_module(self.db, text)
        for text in ("", "a" * 121, "tab\there"):
            with self.subTest(text=text), self.assertRaises(flow.PlannerError):
                self.task(title=text)

    def test_real_strict_dates_and_leap_years(self):
        self.assertEqual(flow.parse_date("2028-02-29"), date(2028, 2, 29))
        for value in ("2026-02-29", "2026-04-31", "2026-13-01", "27/09/2026", "20260927", "2026-9-27", "2026-W39-7", "", "2026-09-27T12:00:00"):
            with self.subTest(value=value), self.assertRaises(flow.PlannerError):
                flow.parse_date(value)

    def test_assignment_and_revision_persist_with_foreign_key(self):
        a = self.task()
        b = self.task(kind="revision")
        self.assertEqual(flow.require_task(self.db, a)["kind"], "assignment")
        self.assertEqual(flow.require_task(self.db, b)["kind"], "revision")
        with self.assertRaises(sqlite3.IntegrityError), self.db:
            self.db.execute("INSERT INTO tasks(module_id,title,due,kind) VALUES(999,'Invalid','2026-09-27','assignment')")

    def test_missing_module_never_creates_task(self):
        with self.assertRaises(flow.PlannerError):
            flow.add_task(self.db, 999, "Missing", "2026-09-27")
        self.assertEqual(flow.list_tasks(self.db), [])

    def test_week_boundaries_year_rollover_and_overdue(self):
        today = date(2026, 12, 29)
        older = self.task("2026-12-28")
        first = self.task("2026-12-29")
        last = self.task("2027-01-04")
        self.task("2027-01-05")
        self.assertEqual([r["id"] for r in flow.list_tasks(self.db, view="week", today=today)], [first, last])
        self.assertEqual([r["id"] for r in flow.list_tasks(self.db, view="today", today=today)], [first])
        self.assertEqual([r["id"] for r in flow.list_tasks(self.db, view="overdue", today=today)], [older])

    def test_completed_tasks_leave_open_and_overdue_views_and_can_reopen(self):
        number = self.task("2020-01-01")
        flow.set_done(self.db, number, True)
        self.assertEqual(flow.list_tasks(self.db), [])
        self.assertEqual(flow.list_tasks(self.db, view="overdue", status="all"), [])
        self.assertEqual(len(flow.list_tasks(self.db, status="done")), 1)
        flow.set_done(self.db, number, False)
        self.assertEqual(len(flow.list_tasks(self.db, view="overdue")), 1)

    def test_module_filter_and_due_date_sorting(self):
        later = self.task("2026-12-01")
        earlier = self.task("2026-10-01")
        other = flow.add_module(self.db, "Electronics")
        flow.add_task(self.db, other, "Lab", "2026-01-01")
        self.assertEqual([r["id"] for r in flow.list_tasks(self.db, module_id=self.module)], [earlier, later])

    def test_edit_moves_task_and_preserves_completion(self):
        number = self.task()
        flow.set_done(self.db, number, True)
        other = flow.add_module(self.db, "Electronics")
        flow.edit_task(self.db, number, module_id=other, title="Revise circuits", due="2026-10-01", kind="revision")
        row = flow.require_task(self.db, number)
        self.assertEqual((row["module_id"], row["title"], row["due"], row["kind"], row["done"]),
                         (other, "Revise circuits", "2026-10-01", "revision", 1))

    def test_invalid_edit_is_atomic(self):
        number = self.task()
        for changes in ({"title": "Changed", "due": "bad"}, {"module_id": 999}, {"kind": "exam"}, {}):
            with self.subTest(changes=changes), self.assertRaises(flow.PlannerError):
                flow.edit_task(self.db, number, **changes)
        self.assertEqual(flow.require_task(self.db, number)["title"], "Problem set")

    def test_module_delete_protects_open_and_completed_tasks(self):
        number = self.task()
        for done in (False, True):
            flow.set_done(self.db, number, done)
            with self.assertRaises(flow.PlannerError):
                flow.delete_module(self.db, self.module)
        flow.delete_task(self.db, number)
        flow.delete_module(self.db, self.module)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM modules").fetchone()[0], 0)

    def test_module_rename_keeps_tasks_and_duplicate_rename_rolls_back(self):
        self.task()
        other = flow.add_module(self.db, "Electronics")
        flow.rename_module(self.db, self.module, "Applied Mathematics")
        self.assertEqual(flow.list_tasks(self.db)[0]["module"], "Applied Mathematics")
        with self.assertRaises(flow.PlannerError):
            flow.rename_module(self.db, self.module, "Electronics")
        self.assertEqual(flow.list_tasks(self.db)[0]["module"], "Applied Mathematics")
        flow.require_module(self.db, other)

    def test_missing_ids_are_clear_errors(self):
        for action in (lambda: flow.delete_task(self.db, 999), lambda: flow.edit_task(self.db, 999, title="Test"),
                       lambda: flow.set_done(self.db, 999, True), lambda: flow.delete_module(self.db, 999)):
            with self.assertRaises(flow.PlannerError):
                action()

    def test_sql_like_text_is_stored_as_data(self):
        title = "Read 'SQL'); DROP TABLE modules; --"
        self.task(title=title)
        self.assertEqual(flow.list_tasks(self.db)[0]["title"], title)
        flow.require_module(self.db, self.module)


class DatabaseAndCliTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "nested" / "planner.db"

    def cli(self, *arguments, expected=0):
        result = subprocess.run([sys.executable, str(Path(flow.__file__).resolve()), "--db", str(self.path), *arguments],
                                cwd=self.folder.name, capture_output=True, text=True, encoding="utf-8",
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def test_help_and_version_do_not_create_database(self):
        self.assertIn("module-add", self.cli("--help").stdout)
        self.assertIn(flow.VERSION, self.cli("--version").stdout)
        self.assertFalse(self.path.exists())

    def test_full_cli_lifecycle_survives_separate_processes(self):
        self.assertIn("No modules", self.cli("modules").stdout)
        self.cli("module-add", "Mathematics")
        self.cli("add", "--module", "1", "--title", "Problem set", "--due", "2026-10-05")
        self.assertIn("Problem set", self.cli("list").stdout)
        self.cli("edit", "1", "--title", "Revision", "--kind", "revision")
        self.cli("complete", "1")
        self.assertIn("No matching tasks", self.cli("list").stdout)
        self.assertIn("[DONE]", self.cli("list", "--status", "all").stdout)
        self.cli("reopen", "1")
        self.assertIn("Revision", self.cli("list").stdout)
        self.cli("module-delete", "1", expected=1)
        self.cli("delete", "1")
        self.cli("module-delete", "1")
        self.assertIn("No modules", self.cli("modules").stdout)

    def test_dashboard_shows_overdue_and_today_but_not_completed(self):
        self.cli("module-add", "Circuits")
        self.cli("add", "--module", "1", "--title", "Overdue lab", "--due", "2000-01-01")
        self.cli("add", "--module", "1", "--title", "Today's revision", "--due", date.today().isoformat())
        output = self.cli().stdout
        self.assertIn("[OVERDUE]", output)
        self.assertIn("[TODAY]", output)
        self.cli("complete", "1")
        self.assertNotIn("Overdue lab", self.cli("dashboard").stdout)

    def test_cli_reports_bad_ids_dates_and_missing_fields(self):
        self.cli("module-add", "Circuits")
        result = self.cli("add", "--module", "1", "--title", "Lab", "--due", "2026-02-30", expected=1)
        self.assertIn("real date", result.stderr)
        self.cli("complete", "0", expected=2)
        self.cli("add", expected=2)

    def test_closed_database_can_be_reopened(self):
        with flow.database(self.path) as db:
            number = flow.add_module(db, "Saved module")
            flow.add_task(db, number, "Saved task", "2026-09-27")
        with flow.database(self.path) as db:
            self.assertEqual(flow.list_tasks(db)[0]["title"], "Saved task")

    def test_unrelated_database_is_not_adopted_or_modified(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
        with self.assertRaises(flow.PlannerError), flow.database(self.path):
            pass
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("unrelated",)])

    def test_future_schema_and_corrupt_file_report_clean_errors(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version = 2")
        self.assertIn("newer schema", self.cli("modules", expected=1).stderr)
        self.path.write_bytes(b"This is not a SQLite database" * 20)
        self.assertIn("Error:", self.cli("modules", expected=1).stderr)


if __name__ == "__main__":
    unittest.main()
