import json
import sqlite3


# Connect to SQLite database
def connect_db():
    return sqlite3.connect("doc_sage.sqlite")


def init_db():
    """Create the tables if they do not exist.
    'metadata' stores, for AI messages, the JSON trace of how the answer was produced
    (router decision, MCP tool calls, RAG sources, errors)."""
    conn = connect_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chat_id) REFERENCES chat(id)
        );
        """
    )
    conn.commit()
    conn.close()


# CRUD Operations for 'chat' table
def create_chat(title):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat (title) VALUES (?)", (title,))
    chat_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return chat_id


def list_chats():
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chat ORDER BY created_at DESC")
    chats = cursor.fetchall()
    conn.close()
    return chats


def read_chat(chat_id):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM chat WHERE id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    return result


def delete_chat(chat_id):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    cursor.execute("DELETE FROM chat WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()


# CRUD Operations for 'messages' table
def create_message(chat_id, sender, content, metadata=None):
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (chat_id, sender, content, metadata) VALUES (?, ?, ?, ?)",
        (chat_id, sender, content, json.dumps(metadata, ensure_ascii=False) if metadata else None),
    )
    conn.commit()
    conn.close()


def get_messages(chat_id):
    """Returns a list of (sender, content, metadata dict or None), oldest first."""
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT sender, content, metadata FROM messages WHERE chat_id = ? ORDER BY id ASC",
        (chat_id,),
    )
    messages = [
        (sender, content, json.loads(metadata) if metadata else None)
        for sender, content, metadata in cursor.fetchall()
    ]
    conn.close()
    return messages
