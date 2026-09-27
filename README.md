# Study Flow

A student assignment and revision planner built with **Python and SQLite**. Keep modules, deadlines, and completion status in one local database, then see what is overdue and what is coming up.

The first release is a command-line application for Windows, macOS, and Linux. It needs **Python 3.10 or newer** and no third-party packages, account, or network connection.

## What it does

- Add, list, and rename modules.
- Create assignments and revision sessions with a module, title, and due date.
- Edit, complete, reopen, or delete tasks.
- Filter by module, completion status, and due date.
- Show overdue work and the next seven days on a dashboard.
- Keep records when the program closes, using a local SQLite database.
- Protect modules from deletion while they still contain tasks.

## Quick start

Clone or download this repository, open a terminal in its folder, and run:

```sh
git clone https://github.com/ZuluBarbie/study-flow.git
cd study-flow
python study_flow.py module-add "Mathematics"
python study_flow.py module-add "Electronics"
python study_flow.py modules
```

The program prints IDs. In a fresh database, the first two modules are `1` and `2`. Use the IDs shown in your own output:

```sh
python study_flow.py add --module 1 --title "Complete problem set 3" --due 2026-10-05
python study_flow.py add --module 2 --title "Revise voltage dividers" --due 2026-10-03 --kind revision
python study_flow.py
python study_flow.py list
```

Replace the example dates with your real deadlines. On Windows, `py` can replace `python`; on macOS/Linux, you may need `python3`.

## Everyday commands

| Action | Command (after `python study_flow.py`) |
| --- | --- |
| Dashboard | `dashboard` or no command |
| All open tasks | `list` |
| Due today | `list --view today` |
| Next seven days | `list --view week` |
| Overdue work | `list --view overdue` |
| One module | `list --module 2` |
| Completed tasks | `list --status done` |
| Include open and completed | `list --status all` |
| Change a deadline | `edit 1 --due 2026-10-07` |
| Rename or move a task | `edit 1 --title "Updated problem set" --module 2` |
| Change task type | `edit 1 --kind revision` |
| Mark complete / reopen | `complete 1` / `reopen 1` |
| Delete a task permanently | `delete 1` |
| Rename a module | `module-rename 2 "Electronic Engineering"` |
| Delete an empty module | `module-delete 2` |
| Help for a command | `add --help` |

Combine list filters, for example `list --view week --module 2`.

## Example dashboard

Illustrative output for 27 September 2026:

```text
STUDY FLOW | 2026-09-27

OVERDUE
#1 [OVERDUE] 2026-09-26 | Mathematics | assignment
    Complete problem set 3

NEXT 7 DAYS (including today)
#2 [TODAY] 2026-09-27 | Electronics | revision
    Revise voltage dividers

Use 'list' for all open tasks, or '--help' for commands.
```

## Dates and validation

- Use a real date in exact `YYYY-MM-DD` format. Invalid dates such as `2026-02-30` are rejected.
- The planner uses your computer's local date. Due dates have no time of day or timezone.
- **Week** means today through six days from today, including both endpoints. It is a rolling seven-day window, not a Monday–Sunday calendar week.
- **Overdue** means an unfinished task due before today. A task due today is not yet overdue. Completed tasks never appear in the overdue view, even with `--status all`.
- Past dates are allowed so you can enter existing overdue work.
- Module names are limited to 80 characters; task titles to 120. Blank text and control characters are rejected. Module names are unique ignoring ASCII letter case; non-ASCII case folding is not applied.

## Your data and backups

The default database is `data/study_flow.db` beside `study_flow.py`, even when you launch the script from another folder. It is created automatically. There is no upload, telemetry, cloud sync, or notification service. This is ordinary local storage, not an encrypted vault.

Database files and the `data/` folder are ignored by Git. **Do not commit your personal schedule.** Moving or deleting the project folder also moves or deletes its default database.

To use a different location, put `--db` **before** the command:

```sh
python study_flow.py --db "my-planner.db" modules
```

To back up, close all Study Flow processes and copy the database file to a safe location. Restore by copying that backup back while the planner is closed, or point `--db` to the backup copy. Task deletion is permanent unless you restore a backup. A module containing any tasks, including completed ones, cannot be deleted until those tasks are moved or deleted.

## Tests

```sh
python -m unittest discover -v
```

Tests use temporary databases and never touch your saved planner. They cover real SQLite persistence, foreign keys, date boundaries, leap years, filtering, complete/reopen, atomic edits, deletion protection, invalid input, and command-line behavior across separate processes. All 21 tests passed locally on Windows with Python 3.14. The prepared workflow in `docs/ci.yml` targets **Windows and Linux with Python 3.10 and 3.14**. It is not active yet: the current GitHub authorization cannot upload workflow files. Once authorized, move it to `.github/workflows/ci.yml` to enable automated checks. Cross-platform CI results are not yet verified.

## Project layout

- `study_flow.py`: database schema, validation, planner operations, and command-line interface.
- `test_study_flow.py`: database and end-to-end CLI tests.
- `docs/ci.yml`: prepared automated checks, awaiting workflow authorization.
- `data/`: your local records, created on first use and excluded from Git.

The schema has `modules` and `tasks` tables, linked by a foreign key. SQL values are parameterized. Edits validate before writing, and each write is committed in a transaction.

## Scope

Version 1 focuses on a reliable local planner. A graphical interface, recurring revision schedules, calendar export, and reminders are possible later improvements. They are not included in this release.
