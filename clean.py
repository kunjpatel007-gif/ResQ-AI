import sqlite3
from db import DB_PATH

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM incidents WHERE emergency_type = 'PARSE_ERROR'")
    conn.commit()
    print(f"Deleted {cursor.rowcount} broken rows from DB.")
finally:
    conn.close()
