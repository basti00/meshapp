#!/usr/bin/env python3
import sqlite3
from datetime import datetime
import os

# to backup the database, run this script. Works while the app is running
# Run "python db_backup.py" 

DB_FILE = "meshapp.db"

def main():
    if not os.path.exists(DB_FILE):
        raise SystemExit(f"DB not found: {DB_FILE}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"meshapp_backup_{timestamp}.db"

    print(f"Creating backup: {backup_file}")

    src = sqlite3.connect(DB_FILE)
    dst = sqlite3.connect(backup_file)

    try:
        src.backup(dst)
        print("Backup completed successfully.")
    finally:
        dst.close()
        src.close()

if __name__ == "__main__":
    main()