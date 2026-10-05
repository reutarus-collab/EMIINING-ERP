#!/usr/bin/env python3
"""Create a consistent SQLite online backup and retain the latest 14 days."""

import argparse
from contextlib import closing
import datetime as dt
import os
from pathlib import Path
import re
import sqlite3
import tempfile


BACKUP_NAME = re.compile(r"erp-(\d{4}-\d{2}-\d{2})\.db$")


def integrity_check(database: Path) -> None:
    with closing(sqlite3.connect(str(database), timeout=30)) as conn:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity check failed for {database.name}.")


def copy_database(source: Path, destination: Path) -> None:
    with closing(sqlite3.connect(str(source), timeout=30)) as src:
        with closing(sqlite3.connect(str(destination), timeout=30)) as dst:
            src.backup(dst)


def backup(database: Path, backup_dir: Path, verify_restore: bool = False) -> Path:
    database = database.expanduser().resolve()
    backup_dir = backup_dir.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {database}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    destination = backup_dir / f"erp-{today.isoformat()}.db"
    fd, temp_name = tempfile.mkstemp(prefix=".erp-backup-", suffix=".db.tmp", dir=backup_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        copy_database(database, temp_path)
        integrity_check(temp_path)
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, destination)
        os.chmod(destination, 0o600)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    if verify_restore:
        fd, restore_name = tempfile.mkstemp(prefix=".erp-restore-check-", suffix=".db", dir=backup_dir)
        os.close(fd)
        restore_path = Path(restore_name)
        try:
            copy_database(destination, restore_path)
            integrity_check(restore_path)
        finally:
            if restore_path.exists():
                restore_path.unlink()

    keep_from = today - dt.timedelta(days=13)
    for old in backup_dir.iterdir():
        match = BACKUP_NAME.fullmatch(old.name)
        if not match or not old.is_file():
            continue
        try:
            backup_date = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if backup_date < keep_from:
            old.unlink()
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="Path to emining_erp.db")
    parser.add_argument("--backup-dir", required=True, type=Path, help="Private directory outside the deployed repo")
    parser.add_argument("--verify-restore", action="store_true",
                        help="restore the backup to a temporary DB and run integrity_check")
    args = parser.parse_args()
    path = backup(args.database, args.backup_dir, args.verify_restore)
    print(f"SQLite backup created and integrity-checked: {path}")
    if args.verify_restore:
        print("Temporary restore test passed.")


if __name__ == "__main__":
    main()
