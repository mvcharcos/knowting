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
        CREATE TABLE IF NOT EXISTS test_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            material_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            url TEXT DEFAULT '',
            file_data BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS question_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            context TEXT DEFAULT '',
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES test_materials(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS program_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id INTEGER NOT NULL,
            test_id INTEGER NOT NULL,
            FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
            UNIQUE(program_id, test_id)
        );
        CREATE TABLE IF NOT EXISTS program_collaborators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id INTEGER NOT NULL,
            user_email TEXT NOT NULL,
            user_id INTEGER,
            role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined')),
            invited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (program_id) REFERENCES programs(id) ON DELETE CASCADE,
            UNIQUE(program_id, user_email)
        );
        CREATE TABLE IF NOT EXISTS test_collaborators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            user_email TEXT NOT NULL,
            user_id INTEGER,
            role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined')),
            invited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
            UNIQUE(test_id, user_email)
        );
        CREATE TABLE IF NOT EXISTS test_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
            UNIQUE(test_id, tag)
        );
        CREATE TABLE IF NOT EXISTS surveys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            survey_type TEXT NOT NULL CHECK(survey_type IN ('initial', 'periodic', 'feedback')),
            is_active BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            valid_from TIMESTAMP,
            valid_until TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS survey_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id INTEGER NOT NULL,
            question_num INTEGER NOT NULL,
            question_type TEXT NOT NULL CHECK(question_type IN ('multiple_choice', 'text', 'rating', 'checkbox')),
            question_text TEXT NOT NULL,
            options TEXT DEFAULT '[]',
            required BOOLEAN DEFAULT 1,
            FOREIGN KEY (survey_id) REFERENCES surveys(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS survey_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (survey_id) REFERENCES surveys(id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(survey_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS survey_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            response_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            answer_text TEXT DEFAULT '',
            answer_options TEXT DEFAULT '[]',
            FOREIGN KEY (response_id) REFERENCES survey_responses(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES survey_questions(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS user_survey_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            knowter_access_type TEXT CHECK(knowter_access_type IN ('survey', 'paid', 'granted')),
            initial_survey_completed BOOLEAN DEFAULT 0,
            pending_approval BOOLEAN DEFAULT 0,
            last_periodic_survey_id INTEGER,
            last_periodic_survey_date TIMESTAMP,
            survey_deadline TIMESTAMP,
            access_on_hold BOOLEAN DEFAULT 0,
            access_revoked BOOLEAN DEFAULT 0,
            revoked_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
    """)
    # Migration: add access_on_hold column if it doesn't exist
    cursor = conn.execute("PRAGMA table_info(user_survey_status)")
    columns = [row[1] for row in cursor.fetchall()]
    if "access_on_hold" not in columns:
        conn.execute("ALTER TABLE user_survey_status ADD COLUMN access_on_hold BOOLEAN DEFAULT 0")
        conn.commit()
    # Migration: rename monthly columns to periodic
    cursor = conn.execute("PRAGMA table_info(user_survey_status)")
    columns = [row[1] for row in cursor.fetchall()]
    if "last_monthly_survey_id" in columns:
        conn.execute("ALTER TABLE user_survey_status RENAME COLUMN last_monthly_survey_id TO last_periodic_survey_id")
        conn.execute("ALTER TABLE user_survey_status RENAME COLUMN last_monthly_survey_date TO last_periodic_survey_date")
        conn.commit()
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
    if "global_role" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN global_role TEXT DEFAULT 'knower'")
        conn.commit()

    # Migrate any existing 'student' global roles to 'knower' (student role removed from global roles)
    conn.execute("UPDATE users SET global_role = 'knower' WHERE global_role = 'student'")
    # Migrate old role names: 'free' -> 'knower', 'premium' -> 'knowter'
    conn.execute("UPDATE users SET global_role = 'knower' WHERE global_role = 'free'")
    conn.execute("UPDATE users SET global_role = 'knowter' WHERE global_role = 'premium'")
    conn.commit()

    # Add pause_times and questions_per_pause to test_materials
    cursor = conn.execute("PRAGMA table_info(test_materials)")
    mat_cols = [row[1] for row in cursor.fetchall()]
    if "pause_times" not in mat_cols:
        conn.execute("ALTER TABLE test_materials ADD COLUMN pause_times TEXT DEFAULT ''")
        conn.commit()
    if "questions_per_pause" not in mat_cols:
        conn.execute("ALTER TABLE test_materials ADD COLUMN questions_per_pause INTEGER DEFAULT 1")
        conn.commit()
    if "transcript" not in mat_cols:
        conn.execute("ALTER TABLE test_materials ADD COLUMN transcript TEXT DEFAULT ''")
        conn.commit()

    # Add test_id columns to existing tables for migration
    for table in ["test_sessions", "question_history", "favorite_tests"]:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cursor.fetchall()]
        if "test_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN test_id INTEGER REFERENCES tests(id)")
            conn.commit()

    # Add source column to questions
    cursor = conn.execute("PRAGMA table_info(questions)")
    cols = [row[1] for row in cursor.fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN source TEXT DEFAULT 'manual'")
        conn.commit()

    # Add language column to tests
    cursor = conn.execute("PRAGMA table_info(tests)")
    cols = [row[1] for row in cursor.fetchall()]
    if "language" not in cols:
        conn.execute("ALTER TABLE tests ADD COLUMN language TEXT DEFAULT ''")
        conn.commit()

    # Migrate test_collaborators to add 'student' role to CHECK constraint
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='test_collaborators'").fetchone()
    if row and "'student'" not in row[0]:
        conn.executescript("""
            CREATE TABLE test_collaborators_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id INTEGER NOT NULL,
                user_email TEXT NOT NULL,
                user_id INTEGER,
                role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
                invited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
                UNIQUE(test_id, user_email)
            );
            INSERT INTO test_collaborators_new SELECT * FROM test_collaborators;
            DROP TABLE test_collaborators;
            ALTER TABLE test_collaborators_new RENAME TO test_collaborators;
        """)

    # Add visibility column to tests (public / private / hidden)
    if "visibility" not in cols:
        conn.execute("ALTER TABLE tests ADD COLUMN visibility TEXT DEFAULT 'public'")
        conn.execute("UPDATE tests SET visibility = CASE WHEN is_public = 1 THEN 'public' ELSE 'hidden' END WHERE visibility IS NULL OR visibility = 'public'")
        conn.commit()

    # Add visibility column to programs
    cursor = conn.execute("PRAGMA table_info(programs)")
    prog_cols = [row[1] for row in cursor.fetchall()]
    if "visibility" not in prog_cols:
        conn.execute("ALTER TABLE programs ADD COLUMN visibility TEXT DEFAULT 'public'")
        conn.commit()

    # Populate test_tags from existing question tags
    conn.execute("""
        INSERT OR IGNORE INTO test_tags (test_id, tag)
        SELECT DISTINCT test_id, tag FROM questions WHERE tag != ''
    """)
    conn.commit()

    # Add status column to test_collaborators for invitation confirmation
    cursor = conn.execute("PRAGMA table_info(test_collaborators)")
    tc_cols = [row[1] for row in cursor.fetchall()]
    if "status" not in tc_cols:
        conn.execute("ALTER TABLE test_collaborators ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'")
        conn.commit()

    # Add status column to program_collaborators for invitation confirmation
    cursor = conn.execute("PRAGMA table_info(program_collaborators)")
    pc_cols = [row[1] for row in cursor.fetchall()]
    if "status" not in pc_cols:
        conn.execute("ALTER TABLE program_collaborators ADD COLUMN status TEXT NOT NULL DEFAULT 'accepted'")
        conn.commit()

    # Add program_visibility column to program_tests for per-test visibility override
    cursor = conn.execute("PRAGMA table_info(program_tests)")
    pt_cols = [row[1] for row in cursor.fetchall()]
    if "program_visibility" not in pt_cols:
        # Check if old elevated_role column exists (migration from role-based to visibility-based)
        if "elevated_role" in pt_cols:
            conn.execute("ALTER TABLE program_tests ADD COLUMN program_visibility TEXT")
            # Migrate: set program_visibility based on test's base visibility (default to test's visibility)
            conn.execute("""
                UPDATE program_tests SET program_visibility = (
                    SELECT COALESCE(t.visibility, 'public') FROM tests t WHERE t.id = program_tests.test_id
                )
            """)
            conn.commit()
        else:
            conn.execute("ALTER TABLE program_tests ADD COLUMN program_visibility TEXT DEFAULT 'public'")
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

    language = data.get("language", "") if isinstance(data, dict) else ""
    visibility = data.get("visibility", "public") if isinstance(data, dict) else "public"

    cursor = conn.execute(
        "INSERT INTO tests (owner_id, title, description, author, source_file, is_public, language, visibility) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (None, title, description, author, file_path.stem, language, visibility),
    )
    test_id = cursor.lastrowid

    # Import materials
    mat_id_map = {}
    materials_data = data.get("materials", []) if isinstance(data, dict) else []
    for mat in materials_data:
        old_id = mat.get("id")
        mat_cursor = conn.execute(
            "INSERT INTO test_materials (test_id, material_type, title, url, pause_times, transcript) VALUES (?, ?, ?, ?, ?, ?)",
            (test_id, mat.get("material_type", "url"), mat.get("title", ""), mat.get("url", ""),
             mat.get("pause_times", ""), mat.get("transcript", "")),
        )
        if old_id is not None:
            mat_id_map[old_id] = mat_cursor.lastrowid

    # Import collaborators (as pending invitations)
    collabs_data = data.get("collaborators", []) if isinstance(data, dict) else []
    for collab in collabs_data:
        email = collab.get("email", "").strip()
        role = collab.get("role", "guest")
        if email and role in ("student", "guest", "reviewer", "admin"):
            conn.execute(
                "INSERT OR IGNORE INTO test_collaborators (test_id, user_email, role, status) VALUES (?, ?, ?, 'pending')",
                (test_id, email, role),
            )

    for q in questions_data:
        q_cursor = conn.execute(
            "INSERT INTO questions (test_id, question_num, tag, question, options, answer_index, explanation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (test_id, q["id"], q["tag"], q["question"], json.dumps(q["options"], ensure_ascii=False), q["answer_index"], q.get("explanation", "")),
        )
        # Import material references
        for ref in q.get("material_refs", []):
            new_mid = mat_id_map.get(ref.get("material_id"))
            if new_mid:
                conn.execute(
                    "INSERT INTO question_materials (question_id, material_id, context) VALUES (?, ?, ?)",
                    (q_cursor.lastrowid, new_mid, ref.get("context", "")),
                )

    # Populate test_tags from imported questions
    conn.execute("""
        INSERT OR IGNORE INTO test_tags (test_id, tag)
        SELECT DISTINCT test_id, tag FROM questions WHERE test_id = ? AND tag != ''
    """, (test_id,))

    conn.commit()
    return test_id


def import_test_from_json(owner_id, json_content):
    """Import a test from JSON content. Returns (test_id, title) or raises ValueError."""
    conn = get_connection()

    if isinstance(json_content, list):
        title = "Imported Test"
        description = ""
        author = ""
        questions_data = json_content
        language = ""
        visibility = "public"
        materials_data = []
        collabs_data = []
    else:
        title = json_content.get("title", "Imported Test")
        description = json_content.get("description", "")
        author = json_content.get("author", "")
        questions_data = json_content.get("questions", [])
        language = json_content.get("language", "")
        visibility = json_content.get("visibility", "public")
        materials_data = json_content.get("materials", [])
        collabs_data = json_content.get("collaborators", [])

    if not questions_data:
        conn.close()
        raise ValueError("No questions found in JSON")

    cursor = conn.execute(
        "INSERT INTO tests (owner_id, title, description, author, is_public, language, visibility) VALUES (?, ?, ?, ?, 1, ?, ?)",
        (owner_id, title, description, author, language, visibility),
    )
    test_id = cursor.lastrowid

    # Import materials
    mat_id_map = {}
    for mat in materials_data:
        old_id = mat.get("id")
        mat_cursor = conn.execute(
            "INSERT INTO test_materials (test_id, material_type, title, url, pause_times, transcript) VALUES (?, ?, ?, ?, ?, ?)",
            (test_id, mat.get("material_type", "url"), mat.get("title", ""), mat.get("url", ""),
             mat.get("pause_times", ""), mat.get("transcript", "")),
        )
        if old_id is not None:
            mat_id_map[old_id] = mat_cursor.lastrowid

    # Import collaborators (as pending invitations)
    for collab in collabs_data:
        email = collab.get("email", "").strip()
        role = collab.get("role", "guest")
        if email and role in ("student", "guest", "reviewer", "admin"):
            conn.execute(
                "INSERT OR IGNORE INTO test_collaborators (test_id, user_email, role, status) VALUES (?, ?, ?, 'pending')",
                (test_id, email, role),
            )

    # Import questions
    for q in questions_data:
        q_cursor = conn.execute(
            "INSERT INTO questions (test_id, question_num, tag, question, options, answer_index, explanation) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (test_id, q.get("id", 0), q.get("tag", ""), q.get("question", ""),
             json.dumps(q.get("options", []), ensure_ascii=False), q.get("answer_index", 0), q.get("explanation", "")),
        )
        # Import material references
        for ref in q.get("material_refs", []):
            new_mid = mat_id_map.get(ref.get("material_id"))
            if new_mid:
                conn.execute(
                    "INSERT INTO question_materials (question_id, material_id, context) VALUES (?, ?, ?)",
                    (q_cursor.lastrowid, new_mid, ref.get("context", "")),
                )

    # Populate test_tags from imported questions
    conn.execute("""
        INSERT OR IGNORE INTO test_tags (test_id, tag)
        SELECT DISTINCT test_id, tag FROM questions WHERE test_id = ? AND tag != ''
    """, (test_id,))

    conn.commit()
    conn.close()
    return test_id, title


# --- Test CRUD ---

def create_test(owner_id, title, description="", author="", language=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO tests (owner_id, title, description, author, is_public, language) VALUES (?, ?, ?, ?, 1, ?)",
        (owner_id, title, description, author, language),
    )
    test_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return test_id


def update_test(test_id, title, description="", author="", language="", visibility="public"):
    conn = get_connection()
    conn.execute(
        "UPDATE tests SET title = ?, description = ?, author = ?, language = ?, visibility = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (title, description, author, language, visibility, test_id),
    )
    conn.commit()
    conn.close()


def delete_test(test_id):
    conn = get_connection()
    # Look up source_file for legacy cleanup
    row = conn.execute("SELECT source_file FROM tests WHERE id = ?", (test_id,)).fetchone()
    source_file = row[0] if row and row[0] else None

    # Delete question_history by test_id AND by session_id (for old records with NULL test_id)
    conn.execute("DELETE FROM question_history WHERE test_id = ?", (test_id,))
    conn.execute("DELETE FROM question_history WHERE session_id IN (SELECT id FROM test_sessions WHERE test_id = ?)", (test_id,))
    if source_file:
        conn.execute("DELETE FROM question_history WHERE test_file = ? AND test_id IS NULL", (source_file,))
    conn.execute("DELETE FROM test_sessions WHERE test_id = ?", (test_id,))
    if source_file:
        conn.execute("DELETE FROM test_sessions WHERE test_file = ? AND test_id IS NULL", (source_file,))
    conn.execute("DELETE FROM favorite_tests WHERE test_id = ?", (test_id,))
    if source_file:
        conn.execute("DELETE FROM favorite_tests WHERE test_file = ? AND test_id IS NULL", (source_file,))
    # Now delete the test (CASCADE will handle questions, materials, collaborators, program_tests)
    conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


def get_test(test_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, owner_id, title, description, author, is_public, created_at, updated_at, language, visibility FROM tests WHERE id = ?",
        (test_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": row[0], "owner_id": row[1], "title": row[2],
        "description": row[3], "author": row[4], "is_public": row[5],
        "created_at": row[6], "updated_at": row[7], "language": row[8] or "",
        "visibility": row[9] or "public",
    }


def get_all_tests(user_id=None):
    """Return public + private tests (visible to all), plus hidden tests the user has access to.

    Hidden tests are shown if the user:
    - Is the owner
    - Is a direct collaborator (accepted)
    - Is a member of a program that includes the test (accepted)
    """
    conn = get_connection()
    if user_id:
        rows = conn.execute(
            """SELECT DISTINCT t.id, t.owner_id, t.title, t.description, t.author, t.is_public,
                      (SELECT COUNT(*) FROM questions WHERE questions.test_id = t.id) as q_count,
                      t.language, t.visibility
               FROM tests t
               LEFT JOIN test_collaborators tc ON tc.test_id = t.id
                   AND (tc.user_id = ? OR tc.user_email = (SELECT username FROM users WHERE id = ?))
                   AND tc.status = 'accepted'
               LEFT JOIN program_tests pt ON pt.test_id = t.id
               LEFT JOIN program_collaborators pc ON pc.program_id = pt.program_id
                   AND (pc.user_id = ? OR pc.user_email = (SELECT username FROM users WHERE id = ?))
                   AND pc.status = 'accepted'
               WHERE t.visibility IN ('public', 'private', 'restricted')
                  OR t.owner_id = ?
                  OR tc.id IS NOT NULL
                  OR pc.id IS NOT NULL
               ORDER BY t.title""",
            (user_id, user_id, user_id, user_id, user_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, owner_id, title, description, author, is_public,
                      (SELECT COUNT(*) FROM questions WHERE questions.test_id = tests.id) as q_count,
                      language, visibility
               FROM tests
               WHERE visibility IN ('public', 'private', 'restricted')
               ORDER BY title""",
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "author": r[4], "is_public": r[5], "question_count": r[6], "language": r[7] or "",
         "visibility": r[8] or "public"}
        for r in rows
    ]


# --- Question CRUD ---

def get_test_questions(test_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, question_num, tag, question, options, answer_index, explanation, source
           FROM questions WHERE test_id = ?
           ORDER BY question_num""",
        (test_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0], "source": r[7] or "manual"}
        for r in rows
    ]


def get_test_questions_by_ids(test_id, question_nums):
    """Get specific questions by their question_num within a test."""
    if not question_nums:
        return []
    conn = get_connection()
    placeholders = ",".join("?" for _ in question_nums)
    rows = conn.execute(
        f"""SELECT id, question_num, tag, question, options, answer_index, explanation, source
            FROM questions WHERE test_id = ? AND question_num IN ({placeholders})
            ORDER BY question_num""",
        (test_id, *question_nums),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0], "source": r[7] or "manual"}
        for r in rows
    ]


def add_question(test_id, question_num, tag, question, options, answer_index, explanation="", source="manual"):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO questions (test_id, question_num, tag, question, options, answer_index, explanation, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (test_id, question_num, tag, question, json.dumps(options, ensure_ascii=False), answer_index, explanation, source),
    )
    q_id = cursor.lastrowid
    # Auto-ensure the tag exists in test_tags
    if tag and tag.strip():
        conn.execute("INSERT OR IGNORE INTO test_tags (test_id, tag) VALUES (?, ?)", (test_id, tag))
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
    # Auto-ensure the tag exists in test_tags
    if tag and tag.strip():
        test_row = conn.execute("SELECT test_id FROM questions WHERE id = ?", (db_id,)).fetchone()
        if test_row:
            conn.execute("INSERT OR IGNORE INTO test_tags (test_id, tag) VALUES (?, ?)", (test_row[0], tag))
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
    """Get tags for a test from the test_tags table."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT tag FROM test_tags WHERE test_id = ? ORDER BY tag",
        (test_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def add_test_tag(test_id, tag):
    """Add a tag to a test. Does nothing if the tag already exists."""
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO test_tags (test_id, tag) VALUES (?, ?)",
        (test_id, tag),
    )
    conn.commit()
    conn.close()


def rename_test_tag(test_id, old_tag, new_tag):
    conn = get_connection()
    # Check if new_tag already exists
    existing = conn.execute(
        "SELECT id FROM test_tags WHERE test_id = ? AND tag = ?",
        (test_id, new_tag),
    ).fetchone()
    if existing:
        # Merge: delete the old tag entry
        conn.execute("DELETE FROM test_tags WHERE test_id = ? AND tag = ?", (test_id, old_tag))
    else:
        # Rename the tag entry
        conn.execute("UPDATE test_tags SET tag = ? WHERE test_id = ? AND tag = ?", (new_tag, test_id, old_tag))
    # Update questions to use the new tag
    conn.execute(
        "UPDATE questions SET tag = ? WHERE test_id = ? AND tag = ?",
        (new_tag, test_id, old_tag),
    )
    conn.execute("UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


def delete_test_tag(test_id, tag, delete_questions=False):
    conn = get_connection()
    if delete_questions:
        conn.execute("DELETE FROM questions WHERE test_id = ? AND tag = ?", (test_id, tag))
    else:
        conn.execute("UPDATE questions SET tag = '' WHERE test_id = ? AND tag = ?", (test_id, tag))
    # Remove from test_tags table
    conn.execute("DELETE FROM test_tags WHERE test_id = ? AND tag = ?", (test_id, tag))
    conn.execute("UPDATE tests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (test_id,))
    conn.commit()
    conn.close()


# --- Materials ---

def get_test_materials(test_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, material_type, title, url, file_data, created_at, pause_times, questions_per_pause, transcript FROM test_materials WHERE test_id = ? ORDER BY created_at",
        (test_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "material_type": r[1], "title": r[2], "url": r[3], "file_data": r[4], "created_at": r[5],
         "pause_times": r[6] or "", "questions_per_pause": r[7] or 1, "transcript": r[8] or ""}
        for r in rows
    ]


def get_material_by_id(material_id):
    """Get a single material by its ID, including the test_id it belongs to."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, test_id, material_type, title, url, file_data, created_at, pause_times, questions_per_pause, transcript FROM test_materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    conn.close()
    if row:
        return {
            "id": row[0], "test_id": row[1], "material_type": row[2], "title": row[3],
            "url": row[4], "file_data": row[5], "created_at": row[6],
            "pause_times": row[7] or "", "questions_per_pause": row[8] or 1, "transcript": row[9] or ""
        }
    return None


def add_test_material(test_id, material_type, title, url="", file_data=None, pause_times="", questions_per_pause=1, transcript=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO test_materials (test_id, material_type, title, url, file_data, pause_times, questions_per_pause, transcript) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (test_id, material_type, title, url, file_data, pause_times, questions_per_pause, transcript),
    )
    mat_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return mat_id


def update_test_material(material_id, title, url="", pause_times="", questions_per_pause=1):
    conn = get_connection()
    conn.execute(
        "UPDATE test_materials SET title = ?, url = ?, pause_times = ?, questions_per_pause = ? WHERE id = ?",
        (title, url, pause_times, questions_per_pause, material_id),
    )
    conn.commit()
    conn.close()


def delete_test_material(material_id):
    conn = get_connection()
    conn.execute("DELETE FROM test_materials WHERE id = ?", (material_id,))
    conn.commit()
    conn.close()


def update_material_transcript(material_id, transcript):
    conn = get_connection()
    conn.execute("UPDATE test_materials SET transcript = ? WHERE id = ?", (transcript, material_id))
    conn.commit()
    conn.close()


def update_material_pause_times(material_id, pause_times):
    conn = get_connection()
    conn.execute("UPDATE test_materials SET pause_times = ? WHERE id = ?", (pause_times, material_id))
    conn.commit()
    conn.close()


# --- Question-Material Links ---

def get_question_material_links(question_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT material_id, context FROM question_materials WHERE question_id = ?",
        (question_id,),
    ).fetchall()
    conn.close()
    return [{"material_id": r[0], "context": r[1] or ""} for r in rows]


def get_question_material_links_bulk(question_ids):
    """Return {question_id: [{material_id, context}]} for multiple questions."""
    if not question_ids:
        return {}
    conn = get_connection()
    placeholders = ",".join("?" for _ in question_ids)
    rows = conn.execute(
        f"SELECT question_id, material_id, context FROM question_materials WHERE question_id IN ({placeholders})",
        tuple(question_ids),
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r[0], []).append({"material_id": r[1], "context": r[2] or ""})
    return result


def set_question_material_links(question_id, links):
    """Replace all material links for a question. links = [{material_id, context}]."""
    conn = get_connection()
    conn.execute("DELETE FROM question_materials WHERE question_id = ?", (question_id,))
    for link in links:
        conn.execute(
            "INSERT INTO question_materials (question_id, material_id, context) VALUES (?, ?, ?)",
            (question_id, link["material_id"], link.get("context", "")),
        )
    conn.commit()
    conn.close()


# --- Programs ---

def create_program(owner_id, title, description=""):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO programs (owner_id, title, description) VALUES (?, ?, ?)",
        (owner_id, title, description),
    )
    program_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return program_id


def update_program(program_id, title, description="", visibility="public"):
    conn = get_connection()
    conn.execute(
        "UPDATE programs SET title = ?, description = ?, visibility = ? WHERE id = ?",
        (title, description, visibility, program_id),
    )
    conn.commit()
    conn.close()


def delete_program(program_id):
    conn = get_connection()
    conn.execute("DELETE FROM programs WHERE id = ?", (program_id,))
    conn.commit()
    conn.close()


def get_program(program_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT id, owner_id, title, description, created_at, visibility FROM programs WHERE id = ?",
        (program_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"id": row[0], "owner_id": row[1], "title": row[2], "description": row[3], "created_at": row[4], "visibility": row[5] or "public"}


def get_all_programs(user_id):
    """Return public/private programs + hidden programs the user has access to.

    Hidden programs are shown if the user:
    - Is the owner
    - Is a direct collaborator (accepted)
    """
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT p.id, p.owner_id, p.title, p.description, p.created_at,
                  (SELECT COUNT(*) FROM program_tests WHERE program_id = p.id) as test_count,
                  p.visibility
           FROM programs p
           LEFT JOIN program_collaborators pc ON pc.program_id = p.id
               AND (pc.user_id = ? OR pc.user_email = (SELECT username FROM users WHERE id = ?))
               AND pc.status = 'accepted'
           WHERE p.visibility IN ('public', 'private', 'restricted')
              OR p.owner_id = ?
              OR pc.id IS NOT NULL
           ORDER BY p.title""",
        (user_id, user_id, user_id),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "created_at": r[4], "test_count": r[5], "visibility": r[6] or "public"}
        for r in rows
    ]


def add_test_to_program(program_id, test_id, program_visibility=None):
    """Add a test to a program with an optional visibility override for program members.

    program_visibility can be 'public', 'private', 'restricted', or 'hidden'.
    If None, defaults to the test's base visibility.
    'restricted' means users can see and take the test, but materials are hidden.
    """
    if program_visibility not in ("public", "private", "restricted", "hidden", None):
        program_visibility = None
    conn = get_connection()
    # If no visibility specified, use the test's base visibility
    if program_visibility is None:
        row = conn.execute("SELECT visibility FROM tests WHERE id = ?", (test_id,)).fetchone()
        program_visibility = row[0] if row and row[0] else "public"
    try:
        conn.execute(
            "INSERT INTO program_tests (program_id, test_id, program_visibility) VALUES (?, ?, ?)",
            (program_id, test_id, program_visibility),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Already exists, update the program_visibility
        conn.execute(
            "UPDATE program_tests SET program_visibility = ? WHERE program_id = ? AND test_id = ?",
            (program_visibility, program_id, test_id),
        )
        conn.commit()
    conn.close()


def remove_test_from_program(program_id, test_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM program_tests WHERE program_id = ? AND test_id = ?",
        (program_id, test_id),
    )
    conn.commit()
    conn.close()


def get_program_tests(program_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.id, t.title, t.description, t.author,
                  (SELECT COUNT(*) FROM questions WHERE test_id = t.id) as q_count,
                  pt.program_visibility,
                  t.visibility
           FROM tests t
           JOIN program_tests pt ON pt.test_id = t.id
           WHERE pt.program_id = ?
           ORDER BY t.title""",
        (program_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "title": r[1], "description": r[2], "author": r[3], "question_count": r[4],
         "program_visibility": r[5] or r[6] or "public",
         "test_visibility": r[6] or "public"}
        for r in rows
    ]


def update_program_test_visibility(program_id, test_id, program_visibility):
    """Update the visibility override for a test within a program.

    'restricted' means users can see and take the test, but materials are hidden.
    """
    if program_visibility not in ("public", "private", "restricted", "hidden"):
        program_visibility = "public"
    conn = get_connection()
    conn.execute(
        "UPDATE program_tests SET program_visibility = ? WHERE program_id = ? AND test_id = ?",
        (program_visibility, program_id, test_id),
    )
    conn.commit()
    conn.close()


def get_program_questions(program_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT q.id, q.question_num, q.tag, q.question, q.options, q.answer_index, q.explanation, q.source
           FROM questions q
           JOIN program_tests pt ON pt.test_id = q.test_id
           WHERE pt.program_id = ?
           ORDER BY q.test_id, q.question_num""",
        (program_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[1], "tag": r[2], "question": r[3],
         "options": json.loads(r[4]), "answer_index": r[5],
         "explanation": r[6], "db_id": r[0], "source": r[7] or "manual"}
        for r in rows
    ]


def get_program_tags(program_id):
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT tt.tag FROM test_tags tt
           JOIN program_tests pt ON pt.test_id = tt.test_id
           WHERE pt.program_id = ?
           ORDER BY tt.tag""",
        (program_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


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


def user_exists(email):
    """Check if a user with the given email already exists."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (email,),
    ).fetchone()
    conn.close()
    return row is not None


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
        (user_id, str(test_id) if test_id else "", test_id, score, total),
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
        (user_id, str(test_id) if test_id else "", test_id if test_id else None, question_id, correct, session_id),
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


def get_topic_statistics(user_id, test_id):
    """Get statistics for each topic in a test for a specific user.

    Returns a dict mapping topic -> {
        total: int,
        correct: int,
        incorrect: int,
        percent_correct: float,
        history: [{date, correct, incorrect, percent}]  # daily aggregates
    }
    """
    conn = get_connection()

    # Get overall stats per topic
    # Note: question_history.question_id stores question_num, not questions.id
    rows = conn.execute(
        """SELECT q.tag,
                  COUNT(*) as total,
                  SUM(CASE WHEN qh.correct THEN 1 ELSE 0 END) as correct,
                  SUM(CASE WHEN NOT qh.correct THEN 1 ELSE 0 END) as incorrect
           FROM question_history qh
           JOIN questions q ON qh.question_id = q.question_num AND qh.test_id = q.test_id
           WHERE qh.user_id = ? AND qh.test_id = ?
           GROUP BY q.tag
           ORDER BY q.tag""",
        (user_id, test_id),
    ).fetchall()

    stats = {}
    for row in rows:
        tag, total, correct, incorrect = row
        stats[tag] = {
            "total": total,
            "correct": correct,
            "incorrect": incorrect,
            "percent_correct": round(100 * correct / total, 1) if total > 0 else 0,
            "history": []
        }

    # Get daily history per topic for trend chart
    history_rows = conn.execute(
        """SELECT q.tag,
                  DATE(qh.answered_at) as answer_date,
                  SUM(CASE WHEN qh.correct THEN 1 ELSE 0 END) as correct,
                  SUM(CASE WHEN NOT qh.correct THEN 1 ELSE 0 END) as incorrect
           FROM question_history qh
           JOIN questions q ON qh.question_id = q.question_num AND qh.test_id = q.test_id
           WHERE qh.user_id = ? AND qh.test_id = ?
           GROUP BY q.tag, DATE(qh.answered_at)
           ORDER BY q.tag, answer_date""",
        (user_id, test_id),
    ).fetchall()

    for row in history_rows:
        tag, date, correct, incorrect = row
        if tag in stats:
            total = correct + incorrect
            stats[tag]["history"].append({
                "date": date,
                "correct": correct,
                "incorrect": incorrect,
                "percent": round(100 * correct / total, 1) if total > 0 else 0
            })

    conn.close()
    return stats


def get_tests_performance(user_id, test_ids=None):
    """Get overall performance for multiple tests for a specific user.

    Args:
        user_id: The user ID
        test_ids: Optional list of test IDs to filter. If None, returns all tests.

    Returns a dict mapping test_id -> {
        total: int,
        correct: int,
        percent_correct: float
    }
    """
    conn = get_connection()

    if test_ids:
        placeholders = ",".join("?" * len(test_ids))
        query = f"""
            SELECT test_id,
                   COUNT(*) as total,
                   SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct
            FROM question_history
            WHERE user_id = ? AND test_id IN ({placeholders})
            GROUP BY test_id
        """
        rows = conn.execute(query, [user_id] + list(test_ids)).fetchall()
    else:
        rows = conn.execute(
            """SELECT test_id,
                      COUNT(*) as total,
                      SUM(CASE WHEN correct THEN 1 ELSE 0 END) as correct
               FROM question_history
               WHERE user_id = ?
               GROUP BY test_id""",
            (user_id,),
        ).fetchall()

    conn.close()

    result = {}
    for row in rows:
        test_id, total, correct = row
        result[test_id] = {
            "total": total,
            "correct": correct,
            "percent_correct": round(100 * correct / total, 1) if total > 0 else 0
        }
    return result


def get_user_test_ids(user_id):
    """Get list of test IDs that a user has answered questions for."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT test_id FROM question_history WHERE user_id = ? AND test_id IS NOT NULL",
        (user_id,),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_user_session_count(user_id):
    """Get count of test sessions completed by a user."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM test_sessions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def get_user_program_ids(user_id):
    """Get list of program IDs where the user has answered questions for tests in those programs."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT pt.program_id
           FROM question_history qh
           JOIN program_tests pt ON pt.test_id = qh.test_id
           WHERE qh.user_id = ? AND qh.test_id IS NOT NULL""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_programs_performance(user_id, program_ids=None):
    """Get overall performance for multiple programs for a specific user.

    Aggregates performance across all tests in each program.

    Args:
        user_id: The user ID
        program_ids: Optional list of program IDs to filter. If None, returns all programs.

    Returns a dict mapping program_id -> {
        total: int,
        correct: int,
        percent_correct: float,
        tests_taken: int  (number of distinct tests in this program the user has answered)
    }
    """
    conn = get_connection()

    if program_ids:
        placeholders = ",".join("?" * len(program_ids))
        query = f"""
            SELECT pt.program_id,
                   COUNT(*) as total,
                   SUM(CASE WHEN qh.correct THEN 1 ELSE 0 END) as correct,
                   COUNT(DISTINCT qh.test_id) as tests_taken
            FROM question_history qh
            JOIN program_tests pt ON pt.test_id = qh.test_id
            WHERE qh.user_id = ? AND pt.program_id IN ({placeholders})
            GROUP BY pt.program_id
        """
        rows = conn.execute(query, [user_id] + list(program_ids)).fetchall()
    else:
        rows = conn.execute(
            """SELECT pt.program_id,
                      COUNT(*) as total,
                      SUM(CASE WHEN qh.correct THEN 1 ELSE 0 END) as correct,
                      COUNT(DISTINCT qh.test_id) as tests_taken
               FROM question_history qh
               JOIN program_tests pt ON pt.test_id = qh.test_id
               WHERE qh.user_id = ?
               GROUP BY pt.program_id""",
            (user_id,),
        ).fetchall()

    conn.close()

    result = {}
    for row in rows:
        program_id, total, correct, tests_taken = row
        result[program_id] = {
            "total": total,
            "correct": correct,
            "percent_correct": round(100 * correct / total, 1) if total > 0 else 0,
            "tests_taken": tests_taken
        }
    return result


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


def delete_user_account(user_id):
    """Delete a user account and all associated data.

    This removes:
    - User's question history and test sessions
    - User's collaborator entries (tests and programs)
    - User's favorite tests
    - Tests owned by the user (and their questions, materials, collaborators)
    - Programs owned by the user (and their collaborators, test links)
    - The user record itself
    """
    conn = get_connection()

    # Get user's email for cleaning up email-based collaborator entries
    row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    user_email = row[0] if row else None

    # Delete question history
    conn.execute("DELETE FROM question_history WHERE user_id = ?", (user_id,))

    # Delete test sessions
    conn.execute("DELETE FROM test_sessions WHERE user_id = ?", (user_id,))

    # Delete favorite tests
    conn.execute("DELETE FROM favorite_tests WHERE user_id = ?", (user_id,))

    # Delete test collaborator entries (where user is invited)
    conn.execute("DELETE FROM test_collaborators WHERE user_id = ?", (user_id,))
    if user_email:
        conn.execute("DELETE FROM test_collaborators WHERE user_email = ?", (user_email,))

    # Delete program collaborator entries (where user is invited)
    conn.execute("DELETE FROM program_collaborators WHERE user_id = ?", (user_id,))
    if user_email:
        conn.execute("DELETE FROM program_collaborators WHERE user_email = ?", (user_email,))

    # Get tests owned by user
    test_ids = [row[0] for row in conn.execute(
        "SELECT id FROM tests WHERE owner_id = ?", (user_id,)
    ).fetchall()]

    # Delete data for owned tests
    for test_id in test_ids:
        conn.execute("DELETE FROM questions WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM test_materials WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM test_collaborators WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM question_material_links WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM program_tests WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM favorite_tests WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM question_history WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM test_sessions WHERE test_id = ?", (test_id,))

    # Delete owned tests
    conn.execute("DELETE FROM tests WHERE owner_id = ?", (user_id,))

    # Get programs owned by user
    program_ids = [row[0] for row in conn.execute(
        "SELECT id FROM programs WHERE owner_id = ?", (user_id,)
    ).fetchall()]

    # Delete data for owned programs
    for program_id in program_ids:
        conn.execute("DELETE FROM program_tests WHERE program_id = ?", (program_id,))
        conn.execute("DELETE FROM program_collaborators WHERE program_id = ?", (program_id,))

    # Delete owned programs
    conn.execute("DELETE FROM programs WHERE owner_id = ?", (user_id,))

    # Delete the user record
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    conn.commit()
    conn.close()


# --- Global User Roles ---

def get_user_global_role(user_id):
    """Get the global role for a user. Returns 'knower', 'knowter', or 'admin'."""
    conn = get_connection()
    row = conn.execute(
        "SELECT global_role FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return "knower"


def set_user_global_role(user_id, role):
    """Set the global role for a user. Role must be 'knower', 'knowter', or 'admin'."""
    if role not in ("knower", "knowter", "admin"):
        raise ValueError(f"Invalid role: {role}")
    conn = get_connection()
    conn.execute(
        "UPDATE users SET global_role = ? WHERE id = ?",
        (role, user_id),
    )
    conn.commit()
    conn.close()


def set_user_global_role_by_email(email, role):
    """Set the global role for a user by email. Role must be 'knower', 'knowter', or 'admin'."""
    if role not in ("knower", "knowter", "admin"):
        raise ValueError(f"Invalid role: {role}")
    conn = get_connection()
    conn.execute(
        "UPDATE users SET global_role = ? WHERE username = ?",
        (role, email),
    )
    conn.commit()
    conn.close()


def get_all_users_with_roles():
    """Get all users with their roles (for admin panel)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, username, display_name, global_role FROM users ORDER BY username"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "email": r[1], "display_name": r[2], "global_role": r[3] or "knower"} for r in rows]


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


# --- Collaborators ---

def add_collaborator(test_id, email, role):
    """Add or update a collaborator for a test. New invitations start as 'pending'."""
    conn = get_connection()
    # Try to resolve user_id from email
    row = conn.execute("SELECT id FROM users WHERE username = ?", (email,)).fetchone()
    uid = row[0] if row else None
    try:
        conn.execute(
            "INSERT INTO test_collaborators (test_id, user_email, user_id, role, status) VALUES (?, ?, ?, ?, 'pending')",
            (test_id, email, uid, role),
        )
    except sqlite3.IntegrityError:
        # If re-inviting, only update role, don't change status
        conn.execute(
            "UPDATE test_collaborators SET role = ?, user_id = COALESCE(?, user_id) WHERE test_id = ? AND user_email = ?",
            (role, uid, test_id, email),
        )
    conn.commit()
    conn.close()


def remove_collaborator(test_id, email):
    conn = get_connection()
    conn.execute("DELETE FROM test_collaborators WHERE test_id = ? AND user_email = ?", (test_id, email))
    conn.commit()
    conn.close()


def update_collaborator_role(test_id, email, new_role):
    conn = get_connection()
    conn.execute(
        "UPDATE test_collaborators SET role = ? WHERE test_id = ? AND user_email = ?",
        (new_role, test_id, email),
    )
    conn.commit()
    conn.close()


def get_collaborators(test_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_email, user_id, role, invited_at, status FROM test_collaborators WHERE test_id = ? ORDER BY invited_at",
        (test_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "email": r[1], "user_id": r[2], "role": r[3], "invited_at": r[4], "status": r[5]} for r in rows]


def _min_role(role1, role2):
    """Return the lesser of two roles based on privilege hierarchy."""
    role_order = {"student": 0, "guest": 1, "reviewer": 2, "admin": 3}
    r1 = role_order.get(role1, 0)
    r2 = role_order.get(role2, 0)
    roles = ["student", "guest", "reviewer", "admin"]
    return roles[min(r1, r2)]


def get_visibility_options_for_test(test_visibility):
    """Return all visibility options for program_visibility selector.

    Visibility hierarchy (from least to most open): hidden < private < restricted < public

    All 4 options are always available. The effective visibility is computed as the
    more restrictive of the test's base visibility and the program_visibility.
    For example, if a test is 'private' and program_visibility is 'public', the
    effective visibility for program members is still 'private'.

    'restricted' means users can see and take the test, but materials are hidden
    from users without explicit access.
    """
    # Return all options - the effective visibility constraint is applied at access check time
    return ["public", "restricted", "private", "hidden"]


def get_effective_visibility(test_visibility, program_visibility):
    """Return the more restrictive of two visibility levels.

    Visibility hierarchy (from least to most open): hidden < private < restricted < public

    The effective visibility is the minimum (more restrictive) of the two.
    For example:
    - test='private', program='public' → effective='private'
    - test='public', program='restricted' → effective='restricted'
    - test='restricted', program='hidden' → effective='hidden'
    """
    visibility_order = {"hidden": 0, "private": 1, "restricted": 2, "public": 3}
    test_level = visibility_order.get(test_visibility, 3)
    program_level = visibility_order.get(program_visibility, 3)
    # Return the more restrictive (lower level) visibility
    levels = ["hidden", "private", "restricted", "public"]
    return levels[min(test_level, program_level)]


def get_user_role_for_test(test_id, user_id):
    """Return the collaboration role for a user on a test, or None.
    Only returns role if invitation is accepted.
    Checks direct test collaborators first, then program-level collaborators.
    For program access, returns the program role if program_visibility grants access."""
    conn = get_connection()
    # Direct test collaborator (only if accepted)
    row = conn.execute(
        """SELECT tc.role FROM test_collaborators tc
           LEFT JOIN users u ON u.username = tc.user_email
           WHERE tc.test_id = ? AND (tc.user_id = ? OR u.id = ?) AND tc.status = 'accepted'
           LIMIT 1""",
        (test_id, user_id, user_id),
    ).fetchone()
    if row:
        conn.close()
        return row[0]
    # Program-level collaborator (test belongs to a program the user collaborates on, only if accepted)
    # program_visibility determines access level for program members
    row = conn.execute(
        """SELECT pc.role, pt.program_visibility FROM program_collaborators pc
           JOIN program_tests pt ON pt.program_id = pc.program_id
           LEFT JOIN users u ON u.username = pc.user_email
           WHERE pt.test_id = ? AND (pc.user_id = ? OR u.id = ?) AND pc.status = 'accepted'
           LIMIT 1""",
        (test_id, user_id, user_id),
    ).fetchone()
    conn.close()
    if row:
        program_role = row[0]
        program_visibility = row[1] or "public"
        # Program members get access based on program_visibility
        # They get their program role for the test (student = take test only, guest = view, etc.)
        # If program_visibility is public/private, they can access; if hidden, they can still access as program member
        return program_role
    return None


def has_direct_test_access(test_id, user_id):
    """Check if user has direct access to a test (not through program membership).

    Returns True if user is a direct collaborator on the test (with accepted status).
    Does NOT check program-level access - use this for hidden test visibility checks.
    """
    if not user_id:
        return False
    conn = get_connection()
    row = conn.execute(
        """SELECT tc.id FROM test_collaborators tc
           LEFT JOIN users u ON u.username = tc.user_email
           WHERE tc.test_id = ? AND (tc.user_id = ? OR u.id = ?) AND tc.status = 'accepted'
           LIMIT 1""",
        (test_id, user_id, user_id),
    ).fetchone()
    conn.close()
    return row is not None


def get_shared_tests(user_id):
    """Return tests shared with a user (as collaborator, only accepted invitations)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.id, t.owner_id, t.title, t.description, t.author, t.is_public,
                  (SELECT COUNT(*) FROM questions WHERE questions.test_id = t.id) as q_count,
                  t.language, tc.role, t.visibility
           FROM tests t
           JOIN test_collaborators tc ON tc.test_id = t.id
           LEFT JOIN users u ON u.username = tc.user_email
           WHERE (tc.user_id = ? OR u.id = ?) AND tc.status = 'accepted'
           ORDER BY t.title""",
        (user_id, user_id),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "author": r[4], "is_public": r[5], "question_count": r[6], "language": r[7] or "", "role": r[8],
         "visibility": r[9] or "public"}
        for r in rows
    ]


def resolve_collaborator_user_id(email, user_id):
    """Fill in user_id for collaborator entries matching this email."""
    conn = get_connection()
    conn.execute(
        "UPDATE test_collaborators SET user_id = ? WHERE user_email = ? AND user_id IS NULL",
        (user_id, email),
    )
    conn.execute(
        "UPDATE program_collaborators SET user_id = ? WHERE user_email = ? AND user_id IS NULL",
        (user_id, email),
    )
    conn.commit()
    conn.close()


# --- Program Collaborators ---

def add_program_collaborator(program_id, email, role):
    """Add or update a collaborator for a program. New invitations start as 'pending'."""
    conn = get_connection()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (email,)).fetchone()
    uid = row[0] if row else None
    try:
        conn.execute(
            "INSERT INTO program_collaborators (program_id, user_email, user_id, role, status) VALUES (?, ?, ?, ?, 'pending')",
            (program_id, email, uid, role),
        )
    except sqlite3.IntegrityError:
        # If re-inviting, only update role, don't change status
        conn.execute(
            "UPDATE program_collaborators SET role = ?, user_id = COALESCE(?, user_id) WHERE program_id = ? AND user_email = ?",
            (role, uid, program_id, email),
        )
    conn.commit()
    conn.close()


def remove_program_collaborator(program_id, email):
    conn = get_connection()
    conn.execute("DELETE FROM program_collaborators WHERE program_id = ? AND user_email = ?", (program_id, email))
    conn.commit()
    conn.close()


def update_program_collaborator_role(program_id, email, new_role):
    conn = get_connection()
    conn.execute(
        "UPDATE program_collaborators SET role = ? WHERE program_id = ? AND user_email = ?",
        (new_role, program_id, email),
    )
    conn.commit()
    conn.close()


def get_program_collaborators(program_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, user_email, user_id, role, invited_at, status FROM program_collaborators WHERE program_id = ? ORDER BY invited_at",
        (program_id,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "email": r[1], "user_id": r[2], "role": r[3], "invited_at": r[4], "status": r[5]} for r in rows]


def get_user_role_for_program(program_id, user_id):
    """Return the collaboration role for a user on a program, or None.
    Only returns role if invitation is accepted."""
    conn = get_connection()
    row = conn.execute(
        """SELECT pc.role FROM program_collaborators pc
           LEFT JOIN users u ON u.username = pc.user_email
           WHERE pc.program_id = ? AND (pc.user_id = ? OR u.id = ?) AND pc.status = 'accepted'
           LIMIT 1""",
        (program_id, user_id, user_id),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def get_shared_programs(user_id):
    """Return programs shared with a user (as collaborator, only accepted invitations)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT p.id, p.owner_id, p.title, p.description, p.created_at,
                  (SELECT COUNT(*) FROM program_tests WHERE program_id = p.id) as test_count,
                  p.visibility, pc.role
           FROM programs p
           JOIN program_collaborators pc ON pc.program_id = p.id
           LEFT JOIN users u ON u.username = pc.user_email
           WHERE (pc.user_id = ? OR u.id = ?) AND pc.status = 'accepted'
           ORDER BY p.title""",
        (user_id, user_id),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "owner_id": r[1], "title": r[2], "description": r[3],
         "created_at": r[4], "test_count": r[5], "visibility": r[6] or "public", "role": r[7]}
        for r in rows
    ]


# --- Invitations ---

def get_pending_invitations(user_id):
    """Get all pending test and program invitations for a user."""
    conn = get_connection()
    # Get user email
    user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user_row:
        conn.close()
        return {"tests": [], "programs": []}
    user_email = user_row[0]

    # Pending test invitations
    test_rows = conn.execute(
        """SELECT tc.id, t.id as test_id, t.title, tc.role, tc.invited_at,
                  u.display_name as inviter_name, u.username as inviter_email
           FROM test_collaborators tc
           JOIN tests t ON t.id = tc.test_id
           LEFT JOIN users u ON u.id = t.owner_id
           WHERE (tc.user_id = ? OR tc.user_email = ?) AND tc.status = 'pending'
           ORDER BY tc.invited_at DESC""",
        (user_id, user_email),
    ).fetchall()

    # Pending program invitations
    program_rows = conn.execute(
        """SELECT pc.id, p.id as program_id, p.title, pc.role, pc.invited_at,
                  u.display_name as inviter_name, u.username as inviter_email
           FROM program_collaborators pc
           JOIN programs p ON p.id = pc.program_id
           LEFT JOIN users u ON u.id = p.owner_id
           WHERE (pc.user_id = ? OR pc.user_email = ?) AND pc.status = 'pending'
           ORDER BY pc.invited_at DESC""",
        (user_id, user_email),
    ).fetchall()

    conn.close()

    tests = [
        {"id": r[0], "test_id": r[1], "title": r[2], "role": r[3], "invited_at": r[4],
         "inviter_name": r[5] or r[6], "inviter_email": r[6]}
        for r in test_rows
    ]
    programs = [
        {"id": r[0], "program_id": r[1], "title": r[2], "role": r[3], "invited_at": r[4],
         "inviter_name": r[5] or r[6], "inviter_email": r[6]}
        for r in program_rows
    ]

    return {"tests": tests, "programs": programs}


def accept_test_invitation(test_id, user_id):
    """Accept a test invitation."""
    conn = get_connection()
    user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user_row:
        conn.execute(
            """UPDATE test_collaborators SET status = 'accepted', user_id = ?
               WHERE test_id = ? AND (user_id = ? OR user_email = ?) AND status = 'pending'""",
            (user_id, test_id, user_id, user_row[0]),
        )
        conn.commit()
    conn.close()


def decline_test_invitation(test_id, user_id):
    """Decline a test invitation (removes the invitation)."""
    conn = get_connection()
    user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user_row:
        conn.execute(
            """DELETE FROM test_collaborators
               WHERE test_id = ? AND (user_id = ? OR user_email = ?) AND status = 'pending'""",
            (test_id, user_id, user_row[0]),
        )
        conn.commit()
    conn.close()


def accept_program_invitation(program_id, user_id):
    """Accept a program invitation."""
    conn = get_connection()
    user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user_row:
        conn.execute(
            """UPDATE program_collaborators SET status = 'accepted', user_id = ?
               WHERE program_id = ? AND (user_id = ? OR user_email = ?) AND status = 'pending'""",
            (user_id, program_id, user_id, user_row[0]),
        )
        conn.commit()
    conn.close()


def decline_program_invitation(program_id, user_id):
    """Decline a program invitation (removes the invitation)."""
    conn = get_connection()
    user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if user_row:
        conn.execute(
            """DELETE FROM program_collaborators
               WHERE program_id = ? AND (user_id = ? OR user_email = ?) AND status = 'pending'""",
            (program_id, user_id, user_row[0]),
        )
        conn.commit()
    conn.close()


def get_pending_invitation_count(user_id):
    """Get the count of pending invitations for badge display."""
    conn = get_connection()
    user_row = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user_row:
        conn.close()
        return 0

    test_count = conn.execute(
        """SELECT COUNT(*) FROM test_collaborators
           WHERE (user_id = ? OR user_email = ?) AND status = 'pending'""",
        (user_id, user_row[0]),
    ).fetchone()[0]

    program_count = conn.execute(
        """SELECT COUNT(*) FROM program_collaborators
           WHERE (user_id = ? OR user_email = ?) AND status = 'pending'""",
        (user_id, user_row[0]),
    ).fetchone()[0]

    conn.close()
    return test_count + program_count


def get_favorite_tests(user_id):
    """Return set of test_ids that are favorited by the user."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT test_id FROM favorite_tests WHERE user_id = ? AND test_id IS NOT NULL",
        (user_id,),
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


# --- Surveys ---

def create_survey(title, description="", survey_type="periodic", valid_from=None, valid_until=None):
    """Create a new survey. Returns survey_id."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO surveys (title, description, survey_type, valid_from, valid_until)
           VALUES (?, ?, ?, ?, ?)""",
        (title, description, survey_type, valid_from, valid_until),
    )
    survey_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return survey_id


def update_survey(survey_id, title, description="", valid_from=None, valid_until=None):
    """Update survey details."""
    conn = get_connection()
    conn.execute(
        """UPDATE surveys SET title = ?, description = ?, valid_from = ?, valid_until = ?
           WHERE id = ?""",
        (title, description, valid_from, valid_until, survey_id),
    )
    conn.commit()
    conn.close()


def delete_survey(survey_id):
    """Delete a survey and all related data."""
    conn = get_connection()
    conn.execute("DELETE FROM surveys WHERE id = ?", (survey_id,))
    conn.commit()
    conn.close()


def get_survey(survey_id):
    """Get survey by ID."""
    conn = get_connection()
    row = conn.execute(
        """SELECT id, title, description, survey_type, is_active, created_at,
                  valid_from, valid_until FROM surveys WHERE id = ?""",
        (survey_id,),
    ).fetchone()
    conn.close()
    if row:
        return {
            "id": row[0], "title": row[1], "description": row[2],
            "survey_type": row[3], "is_active": bool(row[4]), "created_at": row[5],
            "valid_from": row[6], "valid_until": row[7]
        }
    return None


def get_all_surveys():
    """Get all surveys."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, title, description, survey_type, is_active, created_at,
                  valid_from, valid_until,
                  (SELECT COUNT(*) FROM survey_questions WHERE survey_id = surveys.id) as q_count,
                  (SELECT COUNT(*) FROM survey_responses WHERE survey_id = surveys.id) as response_count
           FROM surveys ORDER BY created_at DESC"""
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "title": r[1], "description": r[2], "survey_type": r[3],
         "is_active": bool(r[4]), "created_at": r[5], "valid_from": r[6], "valid_until": r[7],
         "question_count": r[8], "response_count": r[9]}
        for r in rows
    ]


def set_active_survey(survey_id, survey_type):
    """Set a survey as the active survey for its type, deactivating others of the same type."""
    conn = get_connection()
    conn.execute("UPDATE surveys SET is_active = 0 WHERE survey_type = ?", (survey_type,))
    conn.execute("UPDATE surveys SET is_active = 1 WHERE id = ?", (survey_id,))
    conn.commit()
    conn.close()


def get_active_periodic_survey():
    """Get the currently active periodic survey."""
    conn = get_connection()
    row = conn.execute(
        """SELECT id, title, description, survey_type, is_active, created_at,
                  valid_from, valid_until FROM surveys
           WHERE survey_type = 'periodic' AND is_active = 1"""
    ).fetchone()
    conn.close()
    if row:
        return {
            "id": row[0], "title": row[1], "description": row[2],
            "survey_type": row[3], "is_active": bool(row[4]), "created_at": row[5],
            "valid_from": row[6], "valid_until": row[7]
        }
    return None


def get_active_initial_survey():
    """Get the active initial survey for new survey-based knowters."""
    conn = get_connection()
    row = conn.execute(
        """SELECT id, title, description, survey_type, is_active, created_at,
                  valid_from, valid_until FROM surveys
           WHERE survey_type = 'initial' AND is_active = 1"""
    ).fetchone()
    conn.close()
    if row:
        return {
            "id": row[0], "title": row[1], "description": row[2],
            "survey_type": row[3], "is_active": bool(row[4]), "created_at": row[5],
            "valid_from": row[6], "valid_until": row[7]
        }
    return None


# --- Survey Questions ---

def add_survey_question(survey_id, question_num, question_type, question_text, options=None, required=True):
    """Add a question to a survey."""
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO survey_questions (survey_id, question_num, question_type, question_text, options, required)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (survey_id, question_num, question_type, question_text,
         json.dumps(options or [], ensure_ascii=False), required),
    )
    q_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return q_id


def update_survey_question(question_id, question_type, question_text, options=None, required=True):
    """Update a survey question."""
    conn = get_connection()
    conn.execute(
        """UPDATE survey_questions SET question_type = ?, question_text = ?, options = ?, required = ?
           WHERE id = ?""",
        (question_type, question_text, json.dumps(options or [], ensure_ascii=False), required, question_id),
    )
    conn.commit()
    conn.close()


def delete_survey_question(question_id):
    """Delete a survey question."""
    conn = get_connection()
    conn.execute("DELETE FROM survey_questions WHERE id = ?", (question_id,))
    conn.commit()
    conn.close()


def get_survey_questions(survey_id):
    """Get all questions for a survey."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, question_num, question_type, question_text, options, required
           FROM survey_questions WHERE survey_id = ? ORDER BY question_num""",
        (survey_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "question_num": r[1], "question_type": r[2], "question_text": r[3],
         "options": json.loads(r[4]) if r[4] else [], "required": bool(r[5])}
        for r in rows
    ]


def get_next_survey_question_num(survey_id):
    """Get next question number for a survey."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COALESCE(MAX(question_num), 0) + 1 FROM survey_questions WHERE survey_id = ?",
        (survey_id,),
    ).fetchone()
    conn.close()
    return row[0]


# --- Survey Responses ---

def submit_survey_response(survey_id, user_id, answers):
    """Submit a complete survey response. answers = [{question_id, answer_text, answer_options}]"""
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO survey_responses (survey_id, user_id) VALUES (?, ?)",
        (survey_id, user_id),
    )
    response_id = cursor.lastrowid
    for ans in answers:
        conn.execute(
            """INSERT INTO survey_answers (response_id, question_id, answer_text, answer_options)
               VALUES (?, ?, ?, ?)""",
            (response_id, ans["question_id"], ans.get("answer_text", ""),
             json.dumps(ans.get("answer_options", []), ensure_ascii=False)),
        )
    conn.commit()
    conn.close()
    return response_id


def has_completed_survey(user_id, survey_id):
    """Check if user has completed a specific survey."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM survey_responses WHERE user_id = ? AND survey_id = ?",
        (user_id, survey_id),
    ).fetchone()
    conn.close()
    return row is not None


def get_survey_response_count(survey_id):
    """Get number of responses for a survey."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM survey_responses WHERE survey_id = ?",
        (survey_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def get_survey_responses(survey_id):
    """Get all responses for a survey with user info."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT sr.id, sr.user_id, sr.completed_at, u.username, u.display_name
           FROM survey_responses sr
           JOIN users u ON u.id = sr.user_id
           WHERE sr.survey_id = ?
           ORDER BY sr.completed_at DESC""",
        (survey_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "user_id": r[1], "completed_at": r[2],
         "email": r[3], "display_name": r[4] or r[3]}
        for r in rows
    ]


def get_survey_response_answers(response_id):
    """Get all answers for a specific survey response."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT sa.id, sa.question_id, sa.answer_text, sa.answer_options, sq.question_text
           FROM survey_answers sa
           JOIN survey_questions sq ON sq.id = sa.question_id
           WHERE sa.response_id = ?
           ORDER BY sq.question_num""",
        (response_id,),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "question_id": r[1], "answer_text": r[2],
         "answer_options": json.loads(r[3]) if r[3] else [], "question_text": r[4]}
        for r in rows
    ]


def get_survey_answer_statistics(survey_id):
    """Get answer statistics for each question in a survey."""
    conn = get_connection()
    questions = get_survey_questions(survey_id)
    stats = {}
    for q in questions:
        if q["question_type"] in ("multiple_choice", "rating"):
            rows = conn.execute(
                """SELECT sa.answer_text, COUNT(*) as cnt
                   FROM survey_answers sa
                   JOIN survey_responses sr ON sr.id = sa.response_id
                   WHERE sr.survey_id = ? AND sa.question_id = ?
                   GROUP BY sa.answer_text""",
                (survey_id, q["id"]),
            ).fetchall()
            stats[q["id"]] = {"counts": {r[0]: r[1] for r in rows}, "question": q}
        elif q["question_type"] == "checkbox":
            rows = conn.execute(
                """SELECT sa.answer_options
                   FROM survey_answers sa
                   JOIN survey_responses sr ON sr.id = sa.response_id
                   WHERE sr.survey_id = ? AND sa.question_id = ?""",
                (survey_id, q["id"]),
            ).fetchall()
            option_counts = {}
            for r in rows:
                opts = json.loads(r[0]) if r[0] else []
                for opt in opts:
                    option_counts[opt] = option_counts.get(opt, 0) + 1
            stats[q["id"]] = {"counts": option_counts, "question": q}
        else:  # text
            rows = conn.execute(
                """SELECT sa.answer_text
                   FROM survey_answers sa
                   JOIN survey_responses sr ON sr.id = sa.response_id
                   WHERE sr.survey_id = ? AND sa.question_id = ?""",
                (survey_id, q["id"]),
            ).fetchall()
            stats[q["id"]] = {"text_responses": [r[0] for r in rows if r[0]], "question": q}
    conn.close()
    return stats


# --- User Survey Status ---

def get_user_survey_status(user_id):
    """Get survey status for a user."""
    conn = get_connection()
    row = conn.execute(
        """SELECT id, knowter_access_type, initial_survey_completed, last_periodic_survey_id,
                  last_periodic_survey_date, survey_deadline, access_revoked, revoked_at, pending_approval,
                  access_on_hold
           FROM user_survey_status WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    conn.close()
    if row:
        return {
            "id": row[0], "knowter_access_type": row[1],
            "initial_survey_completed": bool(row[2]),
            "last_periodic_survey_id": row[3], "last_periodic_survey_date": row[4],
            "survey_deadline": row[5], "access_revoked": bool(row[6]), "revoked_at": row[7],
            "pending_approval": bool(row[8]), "access_on_hold": bool(row[9]) if row[9] is not None else False
        }
    return None


def create_user_survey_status(user_id, knowter_access_type, initial_completed=False, pending_approval=False):
    """Create user survey status record."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO user_survey_status
           (user_id, knowter_access_type, initial_survey_completed, pending_approval)
           VALUES (?, ?, ?, ?)""",
        (user_id, knowter_access_type, initial_completed, pending_approval),
    )
    conn.commit()
    conn.close()


def update_user_survey_status(user_id, initial_completed=None, pending_approval=None,
                               last_survey_id=None, deadline=None, access_revoked=None,
                               access_on_hold=None):
    """Update user survey status."""
    conn = get_connection()
    updates = []
    params = []
    if initial_completed is not None:
        updates.append("initial_survey_completed = ?")
        params.append(initial_completed)
    if pending_approval is not None:
        updates.append("pending_approval = ?")
        params.append(pending_approval)
    if last_survey_id is not None:
        updates.append("last_periodic_survey_id = ?")
        params.append(last_survey_id)
        updates.append("last_periodic_survey_date = CURRENT_TIMESTAMP")
    if deadline is not None:
        updates.append("survey_deadline = ?")
        params.append(deadline)
    if access_on_hold is not None:
        updates.append("access_on_hold = ?")
        params.append(access_on_hold)
    if access_revoked is not None:
        updates.append("access_revoked = ?")
        params.append(access_revoked)
        if access_revoked:
            updates.append("revoked_at = CURRENT_TIMESTAMP")
        else:
            updates.append("revoked_at = NULL")
    if updates:
        params.append(user_id)
        conn.execute(f"UPDATE user_survey_status SET {', '.join(updates)} WHERE user_id = ?", params)
        conn.commit()
    conn.close()


def revoke_survey_based_access(user_id):
    """Revoke knowter access for a survey-based user."""
    conn = get_connection()
    conn.execute(
        """UPDATE user_survey_status SET access_revoked = 1, revoked_at = CURRENT_TIMESTAMP
           WHERE user_id = ?""",
        (user_id,),
    )
    # Downgrade user role to knower
    conn.execute("UPDATE users SET global_role = 'knower' WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def put_access_on_hold(user_id):
    """Put knowter access on hold until survey is completed. Does not revoke access."""
    conn = get_connection()
    conn.execute(
        """UPDATE user_survey_status SET access_on_hold = 1
           WHERE user_id = ?""",
        (user_id,),
    )
    conn.commit()
    conn.close()


def release_access_hold(user_id):
    """Release access hold after survey completion."""
    from datetime import datetime, timedelta
    deadline = (datetime.now() + timedelta(days=30)).isoformat()
    conn = get_connection()
    conn.execute(
        """UPDATE user_survey_status SET access_on_hold = 0, survey_deadline = ?
           WHERE user_id = ?""",
        (deadline, user_id),
    )
    conn.commit()
    conn.close()


def approve_knowter_access(user_id):
    """Approve a user's knowter access request. User becomes knowter but must complete initial survey."""
    conn = get_connection()
    # User becomes knowter but initial_survey_completed remains false
    # They'll need to complete the survey when accessing knowter features
    conn.execute(
        """UPDATE user_survey_status SET pending_approval = 0
           WHERE user_id = ?""",
        (user_id,),
    )
    conn.execute("UPDATE users SET global_role = 'knowter' WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_users_pending_approval():
    """Get users who have completed initial survey and are pending admin approval."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT uss.user_id, u.username, u.display_name, uss.last_periodic_survey_date
           FROM user_survey_status uss
           JOIN users u ON u.id = uss.user_id
           WHERE uss.pending_approval = 1 AND uss.access_revoked = 0
           ORDER BY uss.last_periodic_survey_date DESC"""
    ).fetchall()
    conn.close()
    return [
        {"user_id": r[0], "email": r[1], "display_name": r[2] or r[1], "survey_date": r[3]}
        for r in rows
    ]


def get_users_needing_survey():
    """Get users who need to complete a survey (deadline approaching)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT uss.user_id, u.username, u.display_name, uss.survey_deadline,
                  uss.last_periodic_survey_date, uss.knowter_access_type
           FROM user_survey_status uss
           JOIN users u ON u.id = uss.user_id
           WHERE uss.knowter_access_type = 'survey'
             AND uss.access_revoked = 0
             AND uss.pending_approval = 0
             AND u.global_role = 'knowter'
           ORDER BY uss.survey_deadline"""
    ).fetchall()
    conn.close()
    return [
        {"user_id": r[0], "email": r[1], "display_name": r[2] or r[1],
         "deadline": r[3], "last_survey_date": r[4], "access_type": r[5]}
        for r in rows
    ]


def get_users_with_overdue_surveys():
    """Get users whose survey deadline has passed."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT uss.user_id, u.username, u.display_name, uss.survey_deadline
           FROM user_survey_status uss
           JOIN users u ON u.id = uss.user_id
           WHERE uss.knowter_access_type = 'survey'
             AND uss.access_revoked = 0
             AND uss.pending_approval = 0
             AND u.global_role = 'knowter'
             AND uss.survey_deadline < datetime('now')
           ORDER BY uss.survey_deadline"""
    ).fetchall()
    conn.close()
    return [
        {"user_id": r[0], "email": r[1], "display_name": r[2] or r[1], "deadline": r[3]}
        for r in rows
    ]


def get_pending_approval_count():
    """Get count of users pending approval."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) FROM user_survey_status WHERE pending_approval = 1 AND access_revoked = 0"
    ).fetchone()
    conn.close()
    return row[0] if row else 0
