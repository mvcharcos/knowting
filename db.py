import sqlite3
import hashlib
import json
import os
from pathlib import Path

DB_DIR = Path(__file__).parent / "data"
DB_PATH = DB_DIR / "users.db"
TESTS_DIR = Path(__file__).parent / "tests"


def get_connection():
    DB_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS test_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_file TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS question_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_file TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            correct BOOLEAN NOT NULL,
            session_id INTEGER,
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (session_id) REFERENCES test_sessions(id)
        );
        CREATE TABLE IF NOT EXISTS favorite_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_file TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, test_file)
        );
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            author TEXT DEFAULT '',
            source_file TEXT,
            is_public BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            question_num INTEGER NOT NULL,
            tag TEXT NOT NULL,
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            answer_index INTEGER NOT NULL,
            explanation TEXT DEFAULT '',
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
        );
    """)
    # Migrations for older DB versions
    cursor = conn.execute("PRAGMA table_info(question_history)")
    columns = [row[1] for row in cursor.fetchall()]
    if "session_id" not in columns:
        conn.execute("ALTER TABLE question_history ADD COLUMN session_id INTEGER REFERENCES test_sessions(id)")
        conn.commit()

    cursor = conn.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    if "display_name" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        conn.commit()
    if "avatar" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN avatar BLOB")
        conn.commit()

    # Add test_id columns to existing tables for migration
    for table in ["test_sessions", "question_history", "favorite_tests"]:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cursor.fetchall()]
        if "test_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN test_id INTEGER REFERENCES tests(id)")
            conn.commit()

    conn.close()

    # Import JSON tests and backfill references
    auto_import_json_tests()


# --- JSON import ---

def auto_import_json_tests():
    """Import JSON test files from disk into the DB if not already imported."""
    if not TESTS_DIR.exists():
        return
    conn = get_connection()
    for file in TESTS_DIR.glob("*.json"):
        # Check if already imported by source_file
        row = conn.execute(
            "SELECT id FROM tests WHERE source_file = ?", (file.stem,)
        ).fetchone()
        if row:
            test_id = row[0]
        else:
            test_id = _import_json_file(conn, file)

        # Backfill test_id in old tables where test_file matches this stem
        if test_id:
            for table in ["test_sessions", "question_history", "favorite_tests"]:
                conn.execute(
                    f"UPDATE {table} SET test_id = ? WHERE test_file = ? AND test_id IS NULL",
                    (test_id, file.stem),
                )
            conn.commit()
    conn.close()


def _import_json_file(conn, file_path):
    """Import a single JSON test file into the DB. Returns test_id."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        title = file_path.stem.replace("_", " ").title()
        description = ""
        author = ""
        questions_data = data
    else:
        title = data.get("title") or file_path.stem.replace("_", " ").title()
        description = data.get("description", "")
        author = data.get("author", "")
        questions_data = data.get("questions", [])

    cursor = conn.execute(
        "INSERT INTO tests (owner_id, title, description, author, source_file, is_public) VALUES (?, ?, ?, ?, ?, 1)",
        (None, title, description, author, file_path.stem),
    )
    test_id = cursor.lastrowid

    for q in questions_data:
        conn.execute(
            "INSERT INTO questions (test_id, question_num, tag, question, options, answer_index, explanation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (test_id, q["id"], q["tag"], q["question"], json.dumps(q["options"], ensure_ascii=False), q["answer_index"], q.get("explanation", "")),
        )
    conn.commit()
    return test_id


# --- Test CRUD ---

def create_test(owner_id, title, description="", author=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO tests (owner_id, title, description, author, is_public) VALUES (?, ?, ?, ?, 1)",
        (owner_id, title, description, author),
    )
    test_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return test_id


def update_test(test_id, title, description="", author=""):
    conn = get_connection()
    conn.execute(
        "UPDATE tests SET title = ?, description = ?, author = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (title, description, author, test_id),
    )
    conn.commit()
    conn.close()


def delete_test(test_id):
    conn = get_connection()
    conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


def get_test(test_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, owner_id, title, description, author, is_public, created_at, updated_at FROM tests WHERE id = ?",
        (test_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0], "owner_id": row[1], "title": row[2],
        "description": row[3], "author": row[4], "is_public": row[5],
        "created_at": row[6], "updated_at": row[7],
    }


def get_all_tests(user_id=None):
    """Return all public tests, plus the user's own private tests."""
    conn = get_connection()
    if user_id:
        rows = conn.execute(
            """SELECT id, owner_id, title, description, author, is_public,
                      (SELECT COUNT(*) FROM questions WHERE questions.test_id = tests.id) as q_count
               FROM tests
               WHERE is_public = 1 OR owner_id = ?
               ORDER BY title""",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, owner_id, title, description, author, is_public,
                      (SELECT COUNT(*) FROM questions WHERE questions.test_id = tests.id) as q_count
               FROM tests
               WHERE is_public = 1
               ORDER BY title""",
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "author": r[4], "is_public": r[5], "question_count": r[6]}
        for r in rows
    ]


# --- Question CRUD ---

def get_test_questions(test_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, question_num, tag, question, options, answer_index, explanation
           FROM questions WHERE test_id = ?
           ORDER BY question_num""",
        (test_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0]}
        for r in rows
    ]


def get_test_questions_by_ids(test_id, question_nums):
    """Get specific questions by their question_num within a test."""
    if not question_nums:
        return []
    conn = get_connection()
    placeholders = ",".join("?" for _ in question_nums)
    rows = conn.execute(
        f"""SELECT id, question_num, tag, question, options, answer_index, explanation
            FROM questions WHERE test_id = ? AND question_num IN ({placeholders})
            ORDER BY question_num""",
        (test_id, *question_nums),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0]}
        for r in rows
    ]


def add_question(test_id, question_num, tag, question, options, answer_index, explanation=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO questions (test_id, question_num, tag, question, options, answer_index, explanation) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (test_id, question_num, tag, question, json.dumps(options, ensure_ascii=False), answer_index, explanation),
    )
    q_id = cursor.lastrowid
    conn.execute("UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()
    return q_id


def get_next_question_num(test_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(MAX(question_num), 0) + 1 FROM questions WHERE test_id = ?",
        (test_id,),
    ).fetchone()
    conn.close()
    return row[0]


def update_question(db_id, tag, question, options, answer_index, explanation=""):
    conn = get_connection()
    conn.execute(
        "UPDATE questions SET tag = ?, question = ?, options = ?, answer_index = ?, explanation = ? WHERE id = ?",
        (tag, question, json.dumps(options, ensure_ascii=False), answer_index, explanation, db_id),
    )
    # Update test timestamp
    conn.execute(
        "UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = (SELECT test_id FROM questions WHERE id = ?)",
        (db_id,),
    )
    conn.commit()
    conn.close()


def delete_question(db_id):
    conn = get_connection()
    conn.execute("DELETE FROM questions WHERE id = ?", (db_id,))
    conn.commit()
    conn.close()


def get_test_tags(test_id):
    """Get unique tags for a test."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT tag FROM questions WHERE test_id = ? ORDER BY tag",
        (test_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# --- User auth ---

def _hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt


def create_user(username, password):
    hashed, salt = _hash_password(password)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            (username, hashed, salt),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def authenticate(username, password):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, password_hash, salt FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    user_id, stored_hash, salt = row
    hashed, _ = _hash_password(password, salt)
    if hashed == stored_hash:
        return user_id
    return None


def get_or_create_google_user(email, name):
    """Get or create a user account for Google OAuth login."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (email,),
    ).fetchone()
    if row:
        conn.close()
        return row[0]
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            (email, "oauth_google", "oauth"),
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return user_id
    except sqlite3.IntegrityError:
        conn.close()
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (email,),
        ).fetchone()
        conn.close()
        return row[0] if row else None


# --- Sessions and history ---

def create_session(user_id, test_id, score, total):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO test_sessions (user_id, test_file, test_id, score, total) VALUES (?, ?, ?, ?, ?)",
        (user_id, str(test_id), test_id, score, total),
    )
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return session_id


def update_session_score(session_id, score, total):
    conn = get_connection()
    conn.execute(
        "UPDATE test_sessions SET score = ?, total = ? WHERE id = ?",
        (score, total, session_id),
    )
    conn.commit()
    conn.close()


def record_answer(user_id, test_id, question_id, correct, session_id=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO question_history (user_id, test_file, test_id, question_id, correct, session_id) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, str(test_id), test_id, question_id, correct, session_id),
    )
    conn.commit()
    conn.close()


def get_question_stats(user_id, test_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT question_id,
                  SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct_count,
                  SUM(CASE WHEN NOT correct THEN 1 ELSE 0 END) as wrong_count
           FROM question_history
           WHERE user_id = ? AND test_id = ?
           GROUP BY question_id""",
        (user_id, test_id),
    ).fetchall()
    conn.close()
    return {row[0]: {"correct": row[1], "wrong": row[2]} for row in rows}


def get_user_sessions(user_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT ts.id, ts.test_id, ts.score, ts.total, ts.started_at,
                  COALESCE(t.title, ts.test_file) as title
           FROM test_sessions ts
           LEFT JOIN tests t ON ts.test_id = t.id
           WHERE ts.user_id = ?
           ORDER BY ts.started_at DESC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "test_id": r[1], "score": r[2], "total": r[3], "date": r[4], "title": r[5]} for r in rows]


def get_session_wrong_answers(session_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT question_id, test_id
           FROM question_history
           WHERE session_id = ? AND NOT correct""",
        (session_id,),
    ).fetchall()
    conn.close()
    return [{"question_id": r[0], "test_id": r[1]} for r in rows]


def get_all_wrong_question_ids(user_id, test_id=None):
    """Get question_ids where user has more wrong than correct answers."""
    conn = get_connection()
    if test_id:
        rows = conn.execute(
            """SELECT question_id, test_id,
                      SUM(CASE WHEN correct THEN 1 ELSE 0 END) as c,
                      SUM(CASE WHEN NOT correct THEN 1 ELSE 0 END) as w
               FROM question_history
               WHERE user_id = ? AND test_id = ?
               GROUP BY question_id, test_id
               HAVING w > c""",
            (user_id, test_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT question_id, test_id,
                      SUM(CASE WHEN correct THEN 1 ELSE 0 END) as c,
                      SUM(CASE WHEN NOT correct THEN 1 ELSE 0 END) as w
               FROM question_history
               WHERE user_id = ?
               GROUP BY question_id, test_id
               HAVING w > c""",
            (user_id,),
        ).fetchall()
    conn.close()
    return [{"question_id": r[0], "test_id": r[1], "correct": r[2], "wrong": r[3]} for r in rows]


# --- Profile ---

def get_user_profile(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT display_name, avatar FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return {"display_name": None, "avatar": None}
    return {"display_name": row[0], "avatar": row[1]}


def update_user_profile(user_id, display_name=None, avatar_bytes=None):
    conn = get_connection()
    if avatar_bytes is not None:
        conn.execute(
            "UPDATE users SET display_name = ?, avatar = ? WHERE id = ?",
            (display_name, avatar_bytes, user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?",
            (display_name, user_id),
        )
    conn.commit()
    conn.close()


# --- Favorites ---

def toggle_favorite(user_id, test_id):
    """Toggle a test as favorite. Returns True if now favorited, False if removed."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM favorite_tests WHERE user_id = ? AND test_id = ?",
        (user_id, test_id),
    ).fetchone()
    if row:
        conn.execute("DELETE FROM favorite_tests WHERE id = ?", (row[0],))
        conn.commit()
        conn.close()
        return False
    else:
        conn.execute(
            "INSERT INTO favorite_tests (user_id, test_file, test_id) VALUES (?, ?, ?)",
            (user_id, str(test_id), test_id),
        )
        conn.commit()
        conn.close()
        return True


def get_favorite_tests(user_id):
    """Return set of test_ids that are favorited by the user."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT test_id FROM favorite_tests WHERE user_id = ? AND test_id IS NOT NULL",
        (user_id,),
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}
