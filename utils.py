import sqlite3, os, datetime

DB_FILE = os.path.join(os.path.dirname(__file__), "users.db")

def upsert_user(chat_id, username):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users(chat_id, username) VALUES (?, ?)", (chat_id, username))
    conn.commit(); conn.close()

def set_last_image(chat_id, image_path):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_image=? WHERE chat_id=?", (image_path, chat_id))
    conn.commit(); conn.close()
