#!/usr/bin/env python3
import sqlite3
import sys
import os

# to apply a backup of the database, run this script. Works only if no other .db file exists
# Run "python db_restore.py <backup_file_or_dir>" 
# if a directory is given, latest backup file inside will be used

DB_FILE = "meshapp.db"

def pick_backup_file(arg):
    # If user passes a directory, pick latest backup inside it
    if os.path.isdir(arg):
        files = [
            os.path.join(arg, f)
            for f in os.listdir(arg)
            if f.startswith("meshapp_backup_") and f.endswith(".db")
        ]
        if not files:
            raise SystemExit("No backup files found in directory.")

        return sorted(files)[-1]

    return arg

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 db_restore.py <backup_file_or_dir>")
        sys.exit(1)

    backup_source = pick_backup_file(sys.argv[1])

    if not os.path.exists(backup_source):
        raise SystemExit(f"Backup not found: {backup_source}")

    if os.path.exists(DB_FILE):
        raise SystemExit(
            f"Refusing to restore: {DB_FILE} already exists. "
            "Delete or move it first."
        )

    print(f"Restoring from: {backup_source}")

    # Ensure clean restore file creation via SQLite API
    src = sqlite3.connect(backup_source)
    dst = sqlite3.connect(DB_FILE)

    try:
        src.backup(dst)
        print(f"Restore completed: {DB_FILE}")
    finally:
        dst.close()
        src.close()

if __name__ == "__main__":
    main()