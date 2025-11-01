import sqlite3, os

DB_FILE = os.path.join(os.path.dirname(__file__), "users.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT,
            expires_at TEXT,
            last_image TEXT
        )
    """)
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized ✅", DB_FILE)
