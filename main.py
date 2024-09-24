from abc import ABC
import argparse
import datetime
import logging
import sqlite3
import subprocess
import signal
import sys
from time import sleep

SQLITE_FILE = "timetracker.db"
APPS_TABLE_NAME = "apps"
TIME_TRACKING_TABLE_NAME = "time_tracking"
TRACKED_APPS = ["code", "firefox", "pycharm", "konsole", "spotify", "nvim", "foot"]
DEFAULT_SLEEP_TIME = 60

argparser = argparse.ArgumentParser()
argparser.add_argument("--sleep-time", type=int, default=DEFAULT_SLEEP_TIME)
argparser.add_argument("--debug", action="store_true")
argparser.add_argument("--clear-db", action="store_true")
argparser.add_argument("--report", action="store_true")
argparser.add_argument("--hour-report", action="store_true")
argparser.add_argument("--hour-report-for", type=str)
args = argparser.parse_args()

logging.basicConfig(
    level=logging.DEBUG if args.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s (main.py:%(lineno)s)"
)

def log_debug_app(app: str, message: str):
    logging.debug(f"[{app}] {message}")

def log_info_app(app: str, message: str):
    logging.info(f"[{app}] {message}")

class SQLiteTable(ABC):
    _table_name = ""
    _columns_def = {}

    def create_table_if_not_exists(self, cur: sqlite3.Cursor):
        columns_def = ', '.join([f"{k} {v}" for k, v in self._columns_def.items()])
        cur.execute(f"CREATE TABLE IF NOT EXISTS {self._table_name}({columns_def})")
        cur.connection.commit()
    
    def drop_table_if_exists(self, cur: sqlite3.Cursor):
        cur.execute(f"DROP TABLE IF EXISTS {self._table_name}")
        cur.connection.commit()

    def table_exists(self, cur: sqlite3.Cursor) -> bool:
        cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{self._table_name}'")
        return cur.fetchone() is not None

    def insert(self, cur: sqlite3.Cursor, **kwargs):
        values = ', '.join(['?' for _ in kwargs.values()])
        cur.execute(f"INSERT INTO {self._table_name}({', '.join(kwargs.keys())}) VALUES({values})", tuple(kwargs.values()))
        cur.connection.commit()


class AppsTable(SQLiteTable):
    _table_name = APPS_TABLE_NAME
    _columns_def = {
        "name": "TEXT UNIQUE"
    }

    def get_app_name_id_mapping(self, cur: sqlite3.Cursor) -> dict[str, int]:
        return {row[1]: row[0] for row in cur.execute(f"SELECT ROWID, name FROM {self._table_name}").fetchall()}

class TimeTrackingTable(SQLiteTable):
    _table_name = TIME_TRACKING_TABLE_NAME
    _columns_def = {
        "app_id": "INTEGER",
        "start_time": "TEXT",
        "end_time": "TEXT",
        "seconds": "INTEGER"
    }

    def join_apps_table(self, cur: sqlite3.Cursor):
        return cur.execute(f"SELECT {self._table_name}.*, {APPS_TABLE_NAME}.name FROM {self._table_name} JOIN {APPS_TABLE_NAME} ON {self._table_name}.app_id = {APPS_TABLE_NAME}.ROWID")
    
    def get_app_time_sum(self, cur: sqlite3.Cursor):
        return cur.execute(f"SELECT {APPS_TABLE_NAME}.name, SUM(seconds) FROM {self._table_name} JOIN {APPS_TABLE_NAME} ON {self._table_name}.app_id = {APPS_TABLE_NAME}.ROWID GROUP BY {APPS_TABLE_NAME}.name")

apps_table = AppsTable()
time_tracking_table = TimeTrackingTable()
_cursor_to_close: sqlite3.Cursor | None = None
_app_started_time: dict[str, datetime.datetime] = {}

def pid_of(name: str) -> int | None:
    res = subprocess.run(f"pidof -s {name}", shell=True, stdout=subprocess.PIPE)
    if res.returncode != 0:
        return None
    return int(res.stdout.decode().strip())

def app_running(name: str) -> bool:
    return pid_of(name) is not None

def encode_time(time: datetime.datetime) -> str:
    return time.isoformat()

def decode_time(time: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(time)

def populate_apps_table_if_needed(cur: sqlite3.Cursor):
    for app in TRACKED_APPS:
        # If app is not in the table, insert it
        if not cur.execute(f"SELECT * FROM {APPS_TABLE_NAME} WHERE name=?", (app,)).fetchone():
            apps_table.insert(cur, name=app)
    cur.connection.commit()

def populate_time_tracking_table_if_needed(cur: sqlite3.Cursor):
    if not time_tracking_table.table_exists(cur):
        time_tracking_table.create_table_if_not_exists(cur)
        cur.connection.commit()

def _exit(signum, frame):
    logging.debug("\n\nReceived signal %s", signum)
    if _cursor_to_close is not None:
        app_name_id_map = apps_table.get_app_name_id_mapping(_cursor_to_close)
        for app, start in _app_started_time.items():
            now = datetime.datetime.now()
            secs = (now - start).seconds
            time_tracking_table.insert(
                cur,
                app_id=app_name_id_map[app],
                start_time=encode_time(start),
                end_time=encode_time(now),
                seconds=secs
            )
            log_debug_app(app, "App was running when signal was received")
            log_info_app(app, f"Inserted time tracking record lasting from {now} ({secs // 60}m {secs % 60}s)")
            cur.connection.commit()
            log_debug_app(app, "Committed transaction")

        print("\nClosing connection...", end="")
        _cursor_to_close.connection.close()
        print("OK")
    sys.exit(0)

def handle_exit_signals() -> None:
    signal.signal(signal.SIGINT, _exit)
    signal.signal(signal.SIGTERM, _exit)
    signal.signal(signal.SIGQUIT, _exit)


conn = sqlite3.connect(SQLITE_FILE)
cur = conn.cursor()
_cursor_to_close = cur
handle_exit_signals()

if args.clear_db:
    logging.info("Clearing database")
    apps_table.drop_table_if_exists(cur)
    time_tracking_table.drop_table_if_exists(cur)
    sys.exit(0)

if args.report:
    print("\nTotal time spent per app:")
    for row in time_tracking_table.get_app_time_sum(cur):
        app_name, total_s = row
        h = total_s // 3600
        m = (total_s % 3600) // 60
        s = total_s % 60
        # Print table
        print("{:<20} {:02d}:{:02d}:{:02d}".format(app_name, h, m, s))
    sys.exit(0)
elif args.hour_report:
    for row in time_tracking_table.get_app_time_sum(cur):
        app_name, total_s = row
        h = total_s / 3600
        # Print table
        print("{:<20} {:.1f}h".format(app_name, h))
    sys.exit(0)
elif args.hour_report_for:
    for row in time_tracking_table.get_app_time_sum(cur):
        app_name, total_s = row
        if app_name == args.hour_report_for:
            app_name, total_s = row
            h = total_s / 3600
            # Print table
            print("{:.1f}".format(h))
    sys.exit(0)

apps_table.create_table_if_not_exists(cur)
time_tracking_table.create_table_if_not_exists(cur)
populate_apps_table_if_needed(cur)
populate_time_tracking_table_if_needed(cur)

app_name_id_mapping = apps_table.get_app_name_id_mapping(cur)


logging.info("Starting main loop")
while True:
    for app in TRACKED_APPS:
        now = datetime.datetime.now()
        log_debug_app(app, "Checking state of the app")
        if app_running(app):
            log_debug_app(app, " App is running")
            match _app_started_time.get(app):
                # Non-present cycle ago but runs now
                case None:
                    log_debug_app(app, "  App has just started running")
                    _app_started_time[app] = now
                # Present cycle ago and keeps running
                case start:
                    log_debug_app(app, f"  App has been running since {start}")
                    pass
        else:
            log_debug_app(app, " App is not running")
            match _app_started_time.get(app):
                # Non-present cycle ago and still not running
                case None:
                    log_debug_app(app, "  App is not running and was not running before")
                    pass
                # Present cycle ago but stopped now
                case start:
                    log_debug_app(app, "  App has stopped running")
                    log_debug_app(app, f"  App was running for {now - start}")
                    secs = (now - start).seconds
                    time_tracking_table.insert(
                        cur,
                        app_id=app_name_id_mapping[app],
                        start_time=encode_time(start),
                        end_time=encode_time(now),
                        seconds=(now - start).seconds
                    )
                    log_debug_app(app, f"  Inserted time tracking record for {now - start} ({secs}s)")
                    cur.connection.commit()
                    log_debug_app(app, "  Committed transaction")
                    del _app_started_time[app]
                    log_debug_app(app, "  Deleted app from tracking")

    logging.debug("Sleeping for %s seconds", args.sleep_time)
    sleep(args.sleep_time)
