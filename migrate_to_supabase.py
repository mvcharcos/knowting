"""
One-time migration script: SQLite (data/users.db) → Supabase.

Usage:
    python migrate_to_supabase.py

Requires:
    - .streamlit/secrets.toml with valid [supabase] url and key
    - supabase_schema.sql already executed in the Supabase SQL Editor
    - pip install supabase
"""

import json
import sqlite3
import sys
import tomllib
from pathlib import Path

from supabase import create_client

SQLITE_PATH = Path(__file__).parent / "data" / "users.db"
SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"


def get_supabase_client():
    with open(SECRETS_PATH, "rb") as f:
        secrets = tomllib.load(f)
    url = secrets["supabase"]["url"]
    key = secrets["supabase"]["key"]
    return create_client(url, key)


def get_sqlite_rows(conn, table, order_by="id"):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
    return [dict(r) for r in cur.fetchall()]


def migrate_table(sb, conn, table, *, transform=None, skip_cols=None):
    """Migrate a single table. Returns old_id -> new_id mapping."""
    rows = get_sqlite_rows(conn, table)
    if not rows:
        print(f"  {table}: 0 rows (skipped)")
        return {}

    id_map = {}
    for row in rows:
        old_id = row.pop("id")
        if skip_cols:
            for col in skip_cols:
                row.pop(col, None)
        if transform:
            row = transform(row)
        try:
            result = sb.table(table).insert(row).execute()
            new_id = result.data[0]["id"]
            id_map[old_id] = new_id
        except Exception as e:
            print(f"  ERROR inserting into {table} (old id={old_id}): {e}")
            continue

    print(f"  {table}: {len(id_map)}/{len(rows)} rows migrated")
    return id_map


def main():
    if not SQLITE_PATH.exists():
        print(f"SQLite database not found: {SQLITE_PATH}")
        sys.exit(1)

    sb = get_supabase_client()
    conn = sqlite3.connect(str(SQLITE_PATH))

    print("Starting migration from SQLite to Supabase...\n")

    # 1. Users (skip avatar BLOB)
    print("[1/14] Users")
    user_map = migrate_table(sb, conn, "users", skip_cols=["avatar"])

    # 2. Tests (remap owner_id)
    print("[2/14] Tests")
    def transform_test(row):
        if row.get("owner_id") and row["owner_id"] in user_map:
            row["owner_id"] = user_map[row["owner_id"]]
        return row
    test_map = migrate_table(sb, conn, "tests", transform=transform_test)

    # 3. Questions (remap test_id, parse options JSON)
    print("[3/14] Questions")
    def transform_question(row):
        if row.get("test_id") and row["test_id"] in test_map:
            row["test_id"] = test_map[row["test_id"]]
        # SQLite stores options as JSON text, Supabase expects JSONB
        if isinstance(row.get("options"), str):
            try:
                row["options"] = json.loads(row["options"])
            except (json.JSONDecodeError, TypeError):
                pass
        return row
    question_map = migrate_table(sb, conn, "questions", transform=transform_question)

    # 4. Test Materials (remap test_id, skip file_data BLOB)
    print("[4/14] Test Materials")
    def transform_material(row):
        if row.get("test_id") and row["test_id"] in test_map:
            row["test_id"] = test_map[row["test_id"]]
        return row
    material_map = migrate_table(sb, conn, "test_materials",
                                  transform=transform_material,
                                  skip_cols=["file_data"])

    # 5. Question-Material Links (remap question_id and material_id)
    print("[5/14] Question Materials")
    def transform_qm(row):
        if row.get("question_id") and row["question_id"] in question_map:
            row["question_id"] = question_map[row["question_id"]]
        if row.get("material_id") and row["material_id"] in material_map:
            row["material_id"] = material_map[row["material_id"]]
        return row
    migrate_table(sb, conn, "question_materials", transform=transform_qm)

    # 6. Programs (remap owner_id)
    print("[6/14] Programs")
    def transform_program(row):
        if row.get("owner_id") and row["owner_id"] in user_map:
            row["owner_id"] = user_map[row["owner_id"]]
        return row
    program_map = migrate_table(sb, conn, "programs", transform=transform_program)

    # 7. Program Tests (remap program_id and test_id, skip elevated_role)
    print("[7/14] Program Tests")
    def transform_pt(row):
        if row.get("program_id") and row["program_id"] in program_map:
            row["program_id"] = program_map[row["program_id"]]
        if row.get("test_id") and row["test_id"] in test_map:
            row["test_id"] = test_map[row["test_id"]]
        row.pop("elevated_role", None)
        return row
    migrate_table(sb, conn, "program_tests", transform=transform_pt)

    # 8. Test Collaborators (remap test_id and user_id)
    print("[8/14] Test Collaborators")
    def transform_tc(row):
        if row.get("test_id") and row["test_id"] in test_map:
            row["test_id"] = test_map[row["test_id"]]
        if row.get("user_id") and row["user_id"] in user_map:
            row["user_id"] = user_map[row["user_id"]]
        return row
    migrate_table(sb, conn, "test_collaborators", transform=transform_tc)

    # 9. Program Collaborators (remap program_id and user_id)
    print("[9/14] Program Collaborators")
    def transform_pc(row):
        if row.get("program_id") and row["program_id"] in program_map:
            row["program_id"] = program_map[row["program_id"]]
        if row.get("user_id") and row["user_id"] in user_map:
            row["user_id"] = user_map[row["user_id"]]
        return row
    migrate_table(sb, conn, "program_collaborators", transform=transform_pc)

    # 10. Test Tags (remap test_id)
    print("[10/14] Test Tags")
    def transform_tag(row):
        if row.get("test_id") and row["test_id"] in test_map:
            row["test_id"] = test_map[row["test_id"]]
        return row
    migrate_table(sb, conn, "test_tags", transform=transform_tag)

    # 11. Test Sessions (remap user_id and test_id)
    print("[11/14] Test Sessions")
    def transform_session(row):
        if row.get("user_id") and row["user_id"] in user_map:
            row["user_id"] = user_map[row["user_id"]]
        if row.get("test_id") and row["test_id"] in test_map:
            row["test_id"] = test_map[row["test_id"]]
        return row
    session_map = migrate_table(sb, conn, "test_sessions", transform=transform_session)

    # 12. Question History (remap user_id, test_id, session_id)
    print("[12/14] Question History")
    def transform_qh(row):
        if row.get("user_id") and row["user_id"] in user_map:
            row["user_id"] = user_map[row["user_id"]]
        if row.get("test_id") and row["test_id"] in test_map:
            row["test_id"] = test_map[row["test_id"]]
        if row.get("session_id") and row["session_id"] in session_map:
            row["session_id"] = session_map[row["session_id"]]
        return row
    migrate_table(sb, conn, "question_history", transform=transform_qh)

    # 13. Surveys
    print("[13/14] Surveys")
    survey_map = migrate_table(sb, conn, "surveys")

    # 14. Survey Questions (remap survey_id, parse options JSON)
    print("[14/14] Survey Questions")
    def transform_sq(row):
        if row.get("survey_id") and row["survey_id"] in survey_map:
            row["survey_id"] = survey_map[row["survey_id"]]
        if isinstance(row.get("options"), str):
            try:
                row["options"] = json.loads(row["options"])
            except (json.JSONDecodeError, TypeError):
                pass
        return row
    migrate_table(sb, conn, "survey_questions", transform=transform_sq)

    conn.close()
    print("\nMigration complete!")


if __name__ == "__main__":
    main()
