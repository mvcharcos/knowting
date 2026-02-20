import hashlib
import json
import os
from pathlib import Path

import streamlit as st
from supabase import create_client

TESTS_DIR = Path(__file__).parent / "tests"

_client = None


def _get_client():
    global _client
    if _client is None:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        _client = create_client(url, key)
    return _client


def _sb():
    """Shorthand for the Supabase client."""
    return _get_client()


def init_db():
    """No-op: schema is managed via supabase_schema.sql."""
    auto_import_json_tests()


# ---------------------------------------------------------------------------
# JSON import
# ---------------------------------------------------------------------------

def auto_import_json_tests():
    """Import JSON test files from disk into Supabase if not already imported."""
    if not TESTS_DIR.exists():
        return
    for file in TESTS_DIR.glob("*.json"):
        row = _sb().table("tests").select("id").eq("source_file", file.stem).execute()
        if row.data:
            test_id = row.data[0]["id"]
        else:
            test_id = _import_json_file(file)
        # Backfill test_id in old tables
        if test_id:
            for table in ["test_sessions", "question_history", "favorite_tests"]:
                _sb().table(table).update({"test_id": test_id}).eq("test_file", file.stem).is_("test_id", "null").execute()


def _import_json_file(file_path):
    """Import a single JSON test file. Returns test_id."""
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

    res = _sb().table("tests").insert({
        "owner_id": None, "title": title, "description": description, "author": author,
        "source_file": file_path.stem, "is_public": True, "language": language, "visibility": visibility,
    }).execute()
    test_id = res.data[0]["id"]

    # Import materials
    mat_id_map = {}
    materials_data = data.get("materials", []) if isinstance(data, dict) else []
    for mat in materials_data:
        old_id = mat.get("id")
        mres = _sb().table("test_materials").insert({
            "test_id": test_id, "material_type": mat.get("material_type", "url"),
            "title": mat.get("title", ""), "url": mat.get("url", ""),
            "pause_times": mat.get("pause_times", ""), "transcript": mat.get("transcript", ""),
        }).execute()
        if old_id is not None:
            mat_id_map[old_id] = mres.data[0]["id"]

    # Import collaborators
    collabs_data = data.get("collaborators", []) if isinstance(data, dict) else []
    for collab in collabs_data:
        email = collab.get("email", "").strip()
        role = collab.get("role", "guest")
        if email and role in ("student", "guest", "reviewer", "admin"):
            _sb().table("test_collaborators").upsert({
                "test_id": test_id, "user_email": email, "role": role, "status": "pending",
            }, on_conflict="test_id,user_email").execute()

    # Import questions
    for q in questions_data:
        qres = _sb().table("questions").insert({
            "test_id": test_id, "question_num": q["id"], "tag": q["tag"],
            "question": q["question"], "options": q["options"],
            "answer_index": q["answer_index"], "explanation": q.get("explanation", ""),
        }).execute()
        q_db_id = qres.data[0]["id"]
        for ref in q.get("material_refs", []):
            new_mid = mat_id_map.get(ref.get("material_id"))
            if new_mid:
                _sb().table("question_materials").insert({
                    "question_id": q_db_id, "material_id": new_mid, "context": ref.get("context", ""),
                }).execute()

    # Populate test_tags
    tags = list({q["tag"] for q in questions_data if q.get("tag")})
    if tags:
        rows = [{"test_id": test_id, "tag": t} for t in tags]
        _sb().table("test_tags").upsert(rows, on_conflict="test_id,tag").execute()

    return test_id


def import_test_from_json(owner_id, json_content):
    """Import a test from JSON content. Returns (test_id, title) or raises ValueError."""
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
        raise ValueError("No questions found in JSON")

    res = _sb().table("tests").insert({
        "owner_id": owner_id, "title": title, "description": description, "author": author,
        "is_public": True, "language": language, "visibility": visibility,
    }).execute()
    test_id = res.data[0]["id"]

    # Materials
    mat_id_map = {}
    for mat in materials_data:
        old_id = mat.get("id")
        mres = _sb().table("test_materials").insert({
            "test_id": test_id, "material_type": mat.get("material_type", "url"),
            "title": mat.get("title", ""), "url": mat.get("url", ""),
            "pause_times": mat.get("pause_times", ""), "transcript": mat.get("transcript", ""),
        }).execute()
        if old_id is not None:
            mat_id_map[old_id] = mres.data[0]["id"]

    # Collaborators
    for collab in collabs_data:
        email = collab.get("email", "").strip()
        role = collab.get("role", "guest")
        if email and role in ("student", "guest", "reviewer", "admin"):
            _sb().table("test_collaborators").upsert({
                "test_id": test_id, "user_email": email, "role": role, "status": "pending",
            }, on_conflict="test_id,user_email").execute()

    # Questions
    for q in questions_data:
        qres = _sb().table("questions").insert({
            "test_id": test_id, "question_num": q.get("id", 0), "tag": q.get("tag", ""),
            "question": q.get("question", ""), "options": q.get("options", []),
            "answer_index": q.get("answer_index", 0), "explanation": q.get("explanation", ""),
        }).execute()
        q_db_id = qres.data[0]["id"]
        for ref in q.get("material_refs", []):
            new_mid = mat_id_map.get(ref.get("material_id"))
            if new_mid:
                _sb().table("question_materials").insert({
                    "question_id": q_db_id, "material_id": new_mid, "context": ref.get("context", ""),
                }).execute()

    # Tags
    tags = list({q.get("tag", "") for q in questions_data if q.get("tag")})
    if tags:
        rows = [{"test_id": test_id, "tag": t} for t in tags]
        _sb().table("test_tags").upsert(rows, on_conflict="test_id,tag").execute()

    return test_id, title


# ---------------------------------------------------------------------------
# Test CRUD
# ---------------------------------------------------------------------------

def create_test(owner_id, title, description="", author="", language=""):
    res = _sb().table("tests").insert({
        "owner_id": owner_id, "title": title, "description": description,
        "author": author, "is_public": True, "language": language,
    }).execute()
    return res.data[0]["id"]


def update_test(test_id, title, description="", author="", language="", visibility="public"):
    _sb().table("tests").update({
        "title": title, "description": description, "author": author,
        "language": language, "visibility": visibility, "updated_at": "now()",
    }).eq("id", test_id).execute()


def delete_test(test_id):
    row = _sb().table("tests").select("source_file").eq("id", test_id).execute()
    source_file = row.data[0]["source_file"] if row.data and row.data[0].get("source_file") else None

    _sb().table("question_history").delete().eq("test_id", test_id).execute()
    if source_file:
        _sb().table("question_history").delete().eq("test_file", source_file).is_("test_id", "null").execute()
    _sb().table("test_sessions").delete().eq("test_id", test_id).execute()
    if source_file:
        _sb().table("test_sessions").delete().eq("test_file", source_file).is_("test_id", "null").execute()
    _sb().table("favorite_tests").delete().eq("test_id", test_id).execute()
    if source_file:
        _sb().table("favorite_tests").delete().eq("test_file", source_file).is_("test_id", "null").execute()
    # CASCADE handles questions, materials, collaborators, program_tests
    _sb().table("tests").delete().eq("id", test_id).execute()


def get_test(test_id):
    res = _sb().table("tests").select(
        "id, owner_id, title, description, author, is_public, created_at, updated_at, language, visibility"
    ).eq("id", test_id).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {
        "id": r["id"], "owner_id": r["owner_id"], "title": r["title"],
        "description": r["description"], "author": r["author"], "is_public": r["is_public"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
        "language": r.get("language") or "", "visibility": r.get("visibility") or "public",
    }


def get_all_tests(user_id=None):
    if user_id:
        res = _sb().rpc("get_all_tests_for_user", {"p_user_id": user_id}).execute()
    else:
        res = _sb().rpc("get_all_tests_public").execute()
    return [
        {"id": r["id"], "owner_id": r["owner_id"], "title": r["title"], "description": r["description"],
         "author": r["author"], "is_public": r["is_public"], "question_count": r["q_count"],
         "language": r.get("language") or "", "visibility": r.get("visibility") or "public"}
        for r in res.data
    ]


# ---------------------------------------------------------------------------
# Question CRUD
# ---------------------------------------------------------------------------

def get_test_questions(test_id):
    res = _sb().table("questions").select(
        "id, question_num, tag, question, options, answer_index, explanation, source"
    ).eq("test_id", test_id).order("question_num").execute()
    return [
        {"id": r["question_num"], "tag": r["tag"], "question": r["question"],
         "options": r["options"] if isinstance(r["options"], list) else json.loads(r["options"]),
         "answer_index": r["answer_index"], "explanation": r["explanation"],
         "db_id": r["id"], "source": r.get("source") or "manual"}
        for r in res.data
    ]


def get_test_questions_by_ids(test_id, question_nums):
    if not question_nums:
        return []
    res = _sb().table("questions").select(
        "id, question_num, tag, question, options, answer_index, explanation, source"
    ).eq("test_id", test_id).in_("question_num", list(question_nums)).order("question_num").execute()
    return [
        {"id": r["question_num"], "tag": r["tag"], "question": r["question"],
         "options": r["options"] if isinstance(r["options"], list) else json.loads(r["options"]),
         "answer_index": r["answer_index"], "explanation": r["explanation"],
         "db_id": r["id"], "source": r.get("source") or "manual"}
        for r in res.data
    ]


def add_question(test_id, question_num, tag, question, options, answer_index, explanation="", source="manual"):
    res = _sb().table("questions").insert({
        "test_id": test_id, "question_num": question_num, "tag": tag,
        "question": question, "options": options,
        "answer_index": answer_index, "explanation": explanation, "source": source,
    }).execute()
    q_id = res.data[0]["id"]
    if tag and tag.strip():
        _sb().table("test_tags").upsert(
            {"test_id": test_id, "tag": tag}, on_conflict="test_id,tag"
        ).execute()
    _sb().table("tests").update({"updated_at": "now()"}).eq("id", test_id).execute()
    return q_id


def get_next_question_num(test_id):
    res = _sb().table("questions").select("question_num").eq("test_id", test_id).order("question_num", desc=True).limit(1).execute()
    if res.data:
        return res.data[0]["question_num"] + 1
    return 1


def update_question(db_id, tag, question, options, answer_index, explanation=""):
    _sb().table("questions").update({
        "tag": tag, "question": question, "options": options,
        "answer_index": answer_index, "explanation": explanation,
    }).eq("id", db_id).execute()
    if tag and tag.strip():
        test_row = _sb().table("questions").select("test_id").eq("id", db_id).execute()
        if test_row.data:
            tid = test_row.data[0]["test_id"]
            _sb().table("test_tags").upsert(
                {"test_id": tid, "tag": tag}, on_conflict="test_id,tag"
            ).execute()
            _sb().table("tests").update({"updated_at": "now()"}).eq("id", tid).execute()


def delete_question(db_id):
    _sb().table("questions").delete().eq("id", db_id).execute()


def get_test_tags(test_id):
    res = _sb().table("test_tags").select("tag").eq("test_id", test_id).order("tag").execute()
    return [r["tag"] for r in res.data if r["tag"]]


def add_test_tag(test_id, tag):
    _sb().table("test_tags").upsert(
        {"test_id": test_id, "tag": tag}, on_conflict="test_id,tag"
    ).execute()


def rename_test_tag(test_id, old_tag, new_tag):
    existing = _sb().table("test_tags").select("id").eq("test_id", test_id).eq("tag", new_tag).execute()
    if existing.data:
        _sb().table("test_tags").delete().eq("test_id", test_id).eq("tag", old_tag).execute()
    else:
        _sb().table("test_tags").update({"tag": new_tag}).eq("test_id", test_id).eq("tag", old_tag).execute()
    _sb().table("questions").update({"tag": new_tag}).eq("test_id", test_id).eq("tag", old_tag).execute()
    _sb().table("tests").update({"updated_at": "now()"}).eq("id", test_id).execute()


def delete_test_tag(test_id, tag, delete_questions=False):
    if delete_questions:
        _sb().table("questions").delete().eq("test_id", test_id).eq("tag", tag).execute()
    else:
        _sb().table("questions").update({"tag": ""}).eq("test_id", test_id).eq("tag", tag).execute()
    _sb().table("test_tags").delete().eq("test_id", test_id).eq("tag", tag).execute()
    _sb().table("tests").update({"updated_at": "now()"}).eq("id", test_id).execute()


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def get_test_materials(test_id):
    res = _sb().table("test_materials").select(
        "id, material_type, title, url, file_data, created_at, pause_times, questions_per_pause, transcript"
    ).eq("test_id", test_id).order("created_at").execute()
    return [
        {"id": r["id"], "material_type": r["material_type"], "title": r["title"],
         "url": r["url"], "file_data": r.get("file_data"), "created_at": r["created_at"],
         "pause_times": r.get("pause_times") or "", "questions_per_pause": r.get("questions_per_pause") or 1,
         "transcript": r.get("transcript") or ""}
        for r in res.data
    ]


def get_material_by_id(material_id):
    res = _sb().table("test_materials").select(
        "id, test_id, material_type, title, url, file_data, created_at, pause_times, questions_per_pause, transcript"
    ).eq("id", material_id).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {
        "id": r["id"], "test_id": r["test_id"], "material_type": r["material_type"],
        "title": r["title"], "url": r["url"], "file_data": r.get("file_data"),
        "created_at": r["created_at"], "pause_times": r.get("pause_times") or "",
        "questions_per_pause": r.get("questions_per_pause") or 1, "transcript": r.get("transcript") or "",
    }


def add_test_material(test_id, material_type, title, url="", file_data=None, pause_times="", questions_per_pause=1, transcript=""):
    row = {
        "test_id": test_id, "material_type": material_type, "title": title,
        "url": url, "pause_times": pause_times, "questions_per_pause": questions_per_pause,
        "transcript": transcript,
    }
    # Skip file_data for now (local files only)
    res = _sb().table("test_materials").insert(row).execute()
    return res.data[0]["id"]


def update_test_material(material_id, title, url="", pause_times="", questions_per_pause=1):
    _sb().table("test_materials").update({
        "title": title, "url": url, "pause_times": pause_times,
        "questions_per_pause": questions_per_pause,
    }).eq("id", material_id).execute()


def delete_test_material(material_id):
    _sb().table("test_materials").delete().eq("id", material_id).execute()


def update_material_transcript(material_id, transcript):
    _sb().table("test_materials").update({"transcript": transcript}).eq("id", material_id).execute()


def update_material_pause_times(material_id, pause_times):
    _sb().table("test_materials").update({"pause_times": pause_times}).eq("id", material_id).execute()


# ---------------------------------------------------------------------------
# Question-Material Links
# ---------------------------------------------------------------------------

def get_question_material_links(question_id):
    res = _sb().table("question_materials").select("material_id, context").eq("question_id", question_id).execute()
    return [{"material_id": r["material_id"], "context": r.get("context") or ""} for r in res.data]


def get_question_material_links_bulk(question_ids):
    if not question_ids:
        return {}
    res = _sb().table("question_materials").select("question_id, material_id, context").in_("question_id", list(question_ids)).execute()
    result = {}
    for r in res.data:
        result.setdefault(r["question_id"], []).append({"material_id": r["material_id"], "context": r.get("context") or ""})
    return result


def set_question_material_links(question_id, links):
    _sb().table("question_materials").delete().eq("question_id", question_id).execute()
    for link in links:
        _sb().table("question_materials").insert({
            "question_id": question_id, "material_id": link["material_id"],
            "context": link.get("context", ""),
        }).execute()


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

def create_program(owner_id, title, description=""):
    res = _sb().table("programs").insert({
        "owner_id": owner_id, "title": title, "description": description,
    }).execute()
    return res.data[0]["id"]


def update_program(program_id, title, description="", visibility="public"):
    _sb().table("programs").update({
        "title": title, "description": description, "visibility": visibility,
    }).eq("id", program_id).execute()


def delete_program(program_id):
    _sb().table("programs").delete().eq("id", program_id).execute()


def get_program(program_id):
    res = _sb().table("programs").select("id, owner_id, title, description, created_at, visibility").eq("id", program_id).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {"id": r["id"], "owner_id": r["owner_id"], "title": r["title"],
            "description": r["description"], "created_at": r["created_at"],
            "visibility": r.get("visibility") or "public"}


def get_all_programs(user_id):
    res = _sb().rpc("get_all_programs_for_user", {"p_user_id": user_id}).execute()
    return [
        {"id": r["id"], "owner_id": r["owner_id"], "title": r["title"], "description": r["description"],
         "created_at": r["created_at"], "test_count": r["test_count"],
         "visibility": r.get("visibility") or "public"}
        for r in res.data
    ]


def add_test_to_program(program_id, test_id, program_visibility=None):
    if program_visibility not in ("public", "private", "restricted", "hidden", None):
        program_visibility = None
    if program_visibility is None:
        row = _sb().table("tests").select("visibility").eq("id", test_id).execute()
        program_visibility = row.data[0]["visibility"] if row.data and row.data[0].get("visibility") else "public"
    _sb().table("program_tests").upsert({
        "program_id": program_id, "test_id": test_id, "program_visibility": program_visibility,
    }, on_conflict="program_id,test_id").execute()


def remove_test_from_program(program_id, test_id):
    _sb().table("program_tests").delete().eq("program_id", program_id).eq("test_id", test_id).execute()


def get_program_tests(program_id):
    res = _sb().table("program_tests").select(
        "test_id, program_visibility, tests(id, title, description, author, visibility)"
    ).eq("program_id", program_id).execute()
    result = []
    for r in res.data:
        t = r["tests"]
        # Get question count
        qc = _sb().table("questions").select("id", count="exact").eq("test_id", t["id"]).execute()
        q_count = qc.count if qc.count is not None else 0
        result.append({
            "id": t["id"], "title": t["title"], "description": t["description"], "author": t["author"],
            "question_count": q_count,
            "program_visibility": r.get("program_visibility") or t.get("visibility") or "public",
            "test_visibility": t.get("visibility") or "public",
        })
    result.sort(key=lambda x: x["title"])
    return result


def update_program_test_visibility(program_id, test_id, program_visibility):
    if program_visibility not in ("public", "private", "restricted", "hidden"):
        program_visibility = "public"
    _sb().table("program_tests").update({
        "program_visibility": program_visibility,
    }).eq("program_id", program_id).eq("test_id", test_id).execute()


def get_program_questions(program_id):
    # Get test_ids in the program
    pt = _sb().table("program_tests").select("test_id").eq("program_id", program_id).execute()
    test_ids = [r["test_id"] for r in pt.data]
    if not test_ids:
        return []
    res = _sb().table("questions").select(
        "id, question_num, tag, question, options, answer_index, explanation, source"
    ).in_("test_id", test_ids).order("test_id").order("question_num").execute()
    return [
        {"id": r["question_num"], "tag": r["tag"], "question": r["question"],
         "options": r["options"] if isinstance(r["options"], list) else json.loads(r["options"]),
         "answer_index": r["answer_index"], "explanation": r["explanation"],
         "db_id": r["id"], "source": r.get("source") or "manual"}
        for r in res.data
    ]


def get_program_tags(program_id):
    pt = _sb().table("program_tests").select("test_id").eq("program_id", program_id).execute()
    test_ids = [r["test_id"] for r in pt.data]
    if not test_ids:
        return []
    res = _sb().table("test_tags").select("tag").in_("test_id", test_ids).execute()
    tags = sorted({r["tag"] for r in res.data if r["tag"]})
    return tags


# ---------------------------------------------------------------------------
# User auth
# ---------------------------------------------------------------------------

def _hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt


def create_user(username, password):
    hashed, salt = _hash_password(password)
    try:
        _sb().table("users").insert({
            "username": username, "password_hash": hashed, "salt": salt, "global_role": "tester",
        }).execute()
        return True
    except Exception:
        return False


def authenticate(username, password):
    res = _sb().table("users").select("id, password_hash, salt").eq("username", username).execute()
    if not res.data:
        return None
    r = res.data[0]
    hashed, _ = _hash_password(password, r["salt"])
    if hashed == r["password_hash"]:
        return r["id"]
    return None


def user_exists(email):
    res = _sb().table("users").select("id").eq("username", email).execute()
    return len(res.data) > 0


def get_or_create_google_user(email, name):
    res = _sb().table("users").select("id, display_name").eq("username", email).execute()
    if res.data:
        # Update display_name if not set yet
        if name and not res.data[0].get("display_name"):
            _sb().table("users").update({"display_name": name}).eq("id", res.data[0]["id"]).execute()
        return res.data[0]["id"]
    try:
        ins = _sb().table("users").insert({
            "username": email, "password_hash": "oauth_google", "salt": "oauth",
            "display_name": name, "global_role": "tester",
        }).execute()
        return ins.data[0]["id"]
    except Exception:
        res = _sb().table("users").select("id").eq("username", email).execute()
        return res.data[0]["id"] if res.data else None


# ---------------------------------------------------------------------------
# Sessions and history
# ---------------------------------------------------------------------------

def create_session(user_id, test_id, score, total):
    res = _sb().table("test_sessions").insert({
        "user_id": user_id, "test_file": str(test_id) if test_id else "",
        "test_id": test_id, "score": score, "total": total,
    }).execute()
    return res.data[0]["id"]


def update_session_score(session_id, score, total):
    _sb().table("test_sessions").update({"score": score, "total": total}).eq("id", session_id).execute()


def record_answer(user_id, test_id, question_id, correct, session_id=None):
    _sb().table("question_history").insert({
        "user_id": user_id, "test_file": str(test_id) if test_id else "",
        "test_id": test_id if test_id else None, "question_id": question_id,
        "correct": correct, "session_id": session_id,
    }).execute()


def get_question_stats(user_id, test_id):
    res = _sb().table("question_history").select("question_id, correct").eq("user_id", user_id).eq("test_id", test_id).execute()
    stats = {}
    for r in res.data:
        qid = r["question_id"]
        if qid not in stats:
            stats[qid] = {"correct": 0, "wrong": 0}
        if r["correct"]:
            stats[qid]["correct"] += 1
        else:
            stats[qid]["wrong"] += 1
    return stats


def get_user_sessions(user_id):
    res = _sb().table("test_sessions").select(
        "id, test_id, score, total, started_at, tests(title)"
    ).eq("user_id", user_id).order("started_at", desc=True).execute()
    return [
        {"id": r["id"], "test_id": r["test_id"], "score": r["score"], "total": r["total"],
         "date": r["started_at"],
         "title": r["tests"]["title"] if r.get("tests") and r["tests"] else str(r.get("test_id", ""))}
        for r in res.data
    ]


def get_session_wrong_answers(session_id):
    res = _sb().table("question_history").select("question_id, test_id").eq("session_id", session_id).eq("correct", False).execute()
    return [{"question_id": r["question_id"], "test_id": r["test_id"]} for r in res.data]


def get_all_wrong_question_ids(user_id, test_id=None):
    query = _sb().table("question_history").select("question_id, test_id, correct").eq("user_id", user_id)
    if test_id:
        query = query.eq("test_id", test_id)
    res = query.execute()
    # Aggregate
    agg = {}
    for r in res.data:
        key = (r["question_id"], r["test_id"])
        if key not in agg:
            agg[key] = {"correct": 0, "wrong": 0}
        if r["correct"]:
            agg[key]["correct"] += 1
        else:
            agg[key]["wrong"] += 1
    return [
        {"question_id": k[0], "test_id": k[1], "correct": v["correct"], "wrong": v["wrong"]}
        for k, v in agg.items() if v["wrong"] > v["correct"]
    ]


def get_topic_statistics(user_id, test_id):
    """Get statistics for each topic in a test for a specific user."""
    # Overall stats
    res = _sb().rpc("get_topic_stats", {"p_user_id": user_id, "p_test_id": test_id}).execute()
    stats = {}
    for row in res.data:
        tag = row["tag"]
        total = row["total"]
        correct = row["correct"]
        incorrect = row["incorrect"]
        stats[tag] = {
            "total": total, "correct": correct, "incorrect": incorrect,
            "percent_correct": round(100 * correct / total, 1) if total > 0 else 0,
            "history": [],
        }

    # Daily history
    hres = _sb().rpc("get_topic_daily_history", {"p_user_id": user_id, "p_test_id": test_id}).execute()
    for row in hres.data:
        tag = row["tag"]
        if tag in stats:
            correct = row["correct"]
            incorrect = row["incorrect"]
            total = correct + incorrect
            stats[tag]["history"].append({
                "date": row["answer_date"],
                "correct": correct, "incorrect": incorrect,
                "percent": round(100 * correct / total, 1) if total > 0 else 0,
            })
    return stats


def get_tests_performance(user_id, test_ids=None):
    query = _sb().table("question_history").select("test_id, correct").eq("user_id", user_id)
    if test_ids:
        query = query.in_("test_id", list(test_ids))
    res = query.execute()
    agg = {}
    for r in res.data:
        tid = r["test_id"]
        if tid not in agg:
            agg[tid] = {"total": 0, "correct": 0}
        agg[tid]["total"] += 1
        if r["correct"]:
            agg[tid]["correct"] += 1
    return {
        tid: {**v, "percent_correct": round(100 * v["correct"] / v["total"], 1) if v["total"] > 0 else 0}
        for tid, v in agg.items()
    }


def get_user_test_ids(user_id):
    res = _sb().table("question_history").select("test_id").eq("user_id", user_id).not_.is_("test_id", "null").execute()
    return list({r["test_id"] for r in res.data})


def get_user_session_count(user_id):
    res = _sb().table("test_sessions").select("id", count="exact").eq("user_id", user_id).execute()
    return res.count if res.count is not None else 0


def get_user_program_ids(user_id):
    # Get test_ids user has answered
    test_ids = get_user_test_ids(user_id)
    if not test_ids:
        return []
    res = _sb().table("program_tests").select("program_id").in_("test_id", test_ids).execute()
    return list({r["program_id"] for r in res.data})


def get_programs_performance(user_id, program_ids=None):
    # Get program -> test mapping
    query = _sb().table("program_tests").select("program_id, test_id")
    if program_ids:
        query = query.in_("program_id", list(program_ids))
    pt_res = query.execute()
    if not pt_res.data:
        return {}
    prog_test_map = {}
    all_test_ids = set()
    for r in pt_res.data:
        prog_test_map.setdefault(r["program_id"], set()).add(r["test_id"])
        all_test_ids.add(r["test_id"])

    # Get question history for these tests
    qh_res = _sb().table("question_history").select("test_id, correct").eq("user_id", user_id).in_("test_id", list(all_test_ids)).execute()
    # Aggregate by test
    test_stats = {}
    for r in qh_res.data:
        tid = r["test_id"]
        if tid not in test_stats:
            test_stats[tid] = {"total": 0, "correct": 0}
        test_stats[tid]["total"] += 1
        if r["correct"]:
            test_stats[tid]["correct"] += 1

    result = {}
    for pid, tids in prog_test_map.items():
        total = sum(test_stats.get(tid, {}).get("total", 0) for tid in tids)
        correct = sum(test_stats.get(tid, {}).get("correct", 0) for tid in tids)
        tests_taken = sum(1 for tid in tids if tid in test_stats)
        if total > 0:
            result[pid] = {
                "total": total, "correct": correct,
                "percent_correct": round(100 * correct / total, 1),
                "tests_taken": tests_taken,
            }
    return result


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def get_user_profile(user_id):
    res = _sb().table("users").select("display_name, avatar").eq("id", user_id).execute()
    if not res.data:
        return {"display_name": None, "avatar": None}
    r = res.data[0]
    return {"display_name": r["display_name"], "avatar": r.get("avatar")}


def update_user_profile(user_id, display_name=None, avatar_bytes=None):
    update = {"display_name": display_name}
    # Skip avatar_bytes for now (local file storage)
    _sb().table("users").update(update).eq("id", user_id).execute()


def delete_user_account(user_id):
    """Delete a user account and all associated data."""
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    user_email = user_row.data[0]["username"] if user_row.data else None

    _sb().table("question_history").delete().eq("user_id", user_id).execute()
    _sb().table("test_sessions").delete().eq("user_id", user_id).execute()
    _sb().table("favorite_tests").delete().eq("user_id", user_id).execute()
    _sb().table("test_collaborators").delete().eq("user_id", user_id).execute()
    if user_email:
        _sb().table("test_collaborators").delete().eq("user_email", user_email).execute()
    _sb().table("program_collaborators").delete().eq("user_id", user_id).execute()
    if user_email:
        _sb().table("program_collaborators").delete().eq("user_email", user_email).execute()
    _sb().table("survey_responses").delete().eq("user_id", user_id).execute()
    _sb().table("user_survey_status").delete().eq("user_id", user_id).execute()

    # Delete owned tests (CASCADE handles questions, materials, etc.)
    _sb().table("tests").delete().eq("owner_id", user_id).execute()
    # Delete owned programs (CASCADE handles program_tests, collaborators)
    _sb().table("programs").delete().eq("owner_id", user_id).execute()
    # Delete the user
    _sb().table("users").delete().eq("id", user_id).execute()


# ---------------------------------------------------------------------------
# Global User Roles
# ---------------------------------------------------------------------------

def get_user_global_role(user_id):
    res = _sb().table("users").select("global_role").eq("id", user_id).execute()
    if res.data and res.data[0].get("global_role"):
        return res.data[0]["global_role"]
    return "knower"


def set_user_global_role(user_id, role):
    if role not in ("knower", "knowter", "tester", "admin"):
        raise ValueError(f"Invalid role: {role}")
    _sb().table("users").update({"global_role": role}).eq("id", user_id).execute()


def set_user_global_role_by_email(email, role):
    if role not in ("knower", "knowter", "tester", "admin"):
        raise ValueError(f"Invalid role: {role}")
    _sb().table("users").update({"global_role": role}).eq("username", email).execute()


def get_all_users_with_roles():
    res = _sb().table("users").select("id, username, display_name, global_role").order("username").execute()
    return [{"id": r["id"], "email": r["username"], "display_name": r["display_name"],
             "global_role": r.get("global_role") or "knower"} for r in res.data]


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

def toggle_favorite(user_id, test_id):
    res = _sb().table("favorite_tests").select("id").eq("user_id", user_id).eq("test_id", test_id).execute()
    if res.data:
        _sb().table("favorite_tests").delete().eq("id", res.data[0]["id"]).execute()
        return False
    else:
        _sb().table("favorite_tests").insert({
            "user_id": user_id, "test_file": str(test_id), "test_id": test_id,
        }).execute()
        return True


# ---------------------------------------------------------------------------
# Collaborators
# ---------------------------------------------------------------------------

def add_collaborator(test_id, email, role):
    uid_row = _sb().table("users").select("id").eq("username", email).execute()
    uid = uid_row.data[0]["id"] if uid_row.data else None
    try:
        _sb().table("test_collaborators").insert({
            "test_id": test_id, "user_email": email, "user_id": uid, "role": role, "status": "pending",
        }).execute()
    except Exception:
        # Already exists - update role
        update = {"role": role}
        if uid:
            update["user_id"] = uid
        _sb().table("test_collaborators").update(update).eq("test_id", test_id).eq("user_email", email).execute()


def remove_collaborator(test_id, email):
    _sb().table("test_collaborators").delete().eq("test_id", test_id).eq("user_email", email).execute()


def update_collaborator_role(test_id, email, new_role):
    _sb().table("test_collaborators").update({"role": new_role}).eq("test_id", test_id).eq("user_email", email).execute()


def get_collaborators(test_id):
    res = _sb().table("test_collaborators").select(
        "id, user_email, user_id, role, invited_at, status"
    ).eq("test_id", test_id).order("invited_at").execute()
    return [{"id": r["id"], "email": r["user_email"], "user_id": r["user_id"],
             "role": r["role"], "invited_at": r["invited_at"], "status": r["status"]} for r in res.data]


def _min_role(role1, role2):
    role_order = {"student": 0, "guest": 1, "reviewer": 2, "admin": 3}
    r1 = role_order.get(role1, 0)
    r2 = role_order.get(role2, 0)
    roles = ["student", "guest", "reviewer", "admin"]
    return roles[min(r1, r2)]


def get_visibility_options_for_test(test_visibility):
    return ["public", "restricted", "private", "hidden"]


def get_effective_visibility(test_visibility, program_visibility):
    visibility_order = {"hidden": 0, "private": 1, "restricted": 2, "public": 3}
    test_level = visibility_order.get(test_visibility, 3)
    program_level = visibility_order.get(program_visibility, 3)
    levels = ["hidden", "private", "restricted", "public"]
    return levels[min(test_level, program_level)]


def get_user_role_for_test(test_id, user_id):
    res = _sb().rpc("get_user_test_role", {"p_test_id": test_id, "p_user_id": user_id}).execute()
    if res.data:
        return res.data[0]["role"]
    return None


def has_direct_test_access(test_id, user_id):
    if not user_id:
        return False
    res = _sb().table("test_collaborators").select("id").eq("test_id", test_id).eq("status", "accepted").or_(f"user_id.eq.{user_id}").execute()
    # Also check by email
    if res.data:
        return True
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    if user_row.data:
        email = user_row.data[0]["username"]
        res2 = _sb().table("test_collaborators").select("id").eq("test_id", test_id).eq("user_email", email).eq("status", "accepted").execute()
        return len(res2.data) > 0
    return False


def get_shared_tests(user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    email = user_row.data[0]["username"] if user_row.data else ""
    res = _sb().table("test_collaborators").select(
        "test_id, role, tests(id, owner_id, title, description, author, is_public, language, visibility)"
    ).eq("status", "accepted").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()
    result = []
    for r in res.data:
        t = r["tests"]
        if not t:
            continue
        qc = _sb().table("questions").select("id", count="exact").eq("test_id", t["id"]).execute()
        result.append({
            "id": t["id"], "owner_id": t["owner_id"], "title": t["title"], "description": t["description"],
            "author": t["author"], "is_public": t["is_public"], "question_count": qc.count or 0,
            "language": t.get("language") or "", "role": r["role"],
            "visibility": t.get("visibility") or "public",
        })
    return result


def resolve_collaborator_user_id(email, user_id):
    _sb().table("test_collaborators").update({"user_id": user_id}).eq("user_email", email).is_("user_id", "null").execute()
    _sb().table("program_collaborators").update({"user_id": user_id}).eq("user_email", email).is_("user_id", "null").execute()


# ---------------------------------------------------------------------------
# Program Collaborators
# ---------------------------------------------------------------------------

def add_program_collaborator(program_id, email, role):
    uid_row = _sb().table("users").select("id").eq("username", email).execute()
    uid = uid_row.data[0]["id"] if uid_row.data else None
    try:
        _sb().table("program_collaborators").insert({
            "program_id": program_id, "user_email": email, "user_id": uid, "role": role, "status": "pending",
        }).execute()
    except Exception:
        update = {"role": role}
        if uid:
            update["user_id"] = uid
        _sb().table("program_collaborators").update(update).eq("program_id", program_id).eq("user_email", email).execute()


def remove_program_collaborator(program_id, email):
    _sb().table("program_collaborators").delete().eq("program_id", program_id).eq("user_email", email).execute()


def update_program_collaborator_role(program_id, email, new_role):
    _sb().table("program_collaborators").update({"role": new_role}).eq("program_id", program_id).eq("user_email", email).execute()


def get_program_collaborators(program_id):
    res = _sb().table("program_collaborators").select(
        "id, user_email, user_id, role, invited_at, status"
    ).eq("program_id", program_id).order("invited_at").execute()
    return [{"id": r["id"], "email": r["user_email"], "user_id": r["user_id"],
             "role": r["role"], "invited_at": r["invited_at"], "status": r["status"]} for r in res.data]


def get_user_role_for_program(program_id, user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    email = user_row.data[0]["username"] if user_row.data else ""
    res = _sb().table("program_collaborators").select("role").eq("program_id", program_id).eq("status", "accepted").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()
    return res.data[0]["role"] if res.data else None


def get_shared_programs(user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    email = user_row.data[0]["username"] if user_row.data else ""
    res = _sb().table("program_collaborators").select(
        "role, programs(id, owner_id, title, description, created_at, visibility)"
    ).eq("status", "accepted").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()
    result = []
    for r in res.data:
        p = r["programs"]
        if not p:
            continue
        tc = _sb().table("program_tests").select("id", count="exact").eq("program_id", p["id"]).execute()
        result.append({
            "id": p["id"], "owner_id": p["owner_id"], "title": p["title"], "description": p["description"],
            "created_at": p["created_at"], "test_count": tc.count or 0,
            "visibility": p.get("visibility") or "public", "role": r["role"],
        })
    return result


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

def get_pending_invitations(user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    if not user_row.data:
        return {"tests": [], "programs": []}
    user_email = user_row.data[0]["username"]

    # Test invitations
    tres = _sb().table("test_collaborators").select(
        "id, test_id, role, invited_at, tests(id, title, owner_id, users:owner_id(display_name, username))"
    ).eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{user_email}").execute()
    tests = []
    for r in tres.data:
        t = r.get("tests") or {}
        inviter = t.get("users") or {}
        tests.append({
            "id": r["id"], "test_id": r["test_id"], "title": t.get("title", ""),
            "role": r["role"], "invited_at": r["invited_at"],
            "inviter_name": inviter.get("display_name") or inviter.get("username", ""),
            "inviter_email": inviter.get("username", ""),
        })

    # Program invitations
    pres = _sb().table("program_collaborators").select(
        "id, program_id, role, invited_at, programs(id, title, owner_id, users:owner_id(display_name, username))"
    ).eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{user_email}").execute()
    programs = []
    for r in pres.data:
        p = r.get("programs") or {}
        inviter = p.get("users") or {}
        programs.append({
            "id": r["id"], "program_id": r["program_id"], "title": p.get("title", ""),
            "role": r["role"], "invited_at": r["invited_at"],
            "inviter_name": inviter.get("display_name") or inviter.get("username", ""),
            "inviter_email": inviter.get("username", ""),
        })

    return {"tests": tests, "programs": programs}


def accept_test_invitation(test_id, user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    if user_row.data:
        email = user_row.data[0]["username"]
        _sb().table("test_collaborators").update({"status": "accepted", "user_id": user_id}).eq("test_id", test_id).eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()


def decline_test_invitation(test_id, user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    if user_row.data:
        email = user_row.data[0]["username"]
        _sb().table("test_collaborators").delete().eq("test_id", test_id).eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()


def accept_program_invitation(program_id, user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    if user_row.data:
        email = user_row.data[0]["username"]
        _sb().table("program_collaborators").update({"status": "accepted", "user_id": user_id}).eq("program_id", program_id).eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()


def decline_program_invitation(program_id, user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    if user_row.data:
        email = user_row.data[0]["username"]
        _sb().table("program_collaborators").delete().eq("program_id", program_id).eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()


def get_pending_invitation_count(user_id):
    user_row = _sb().table("users").select("username").eq("id", user_id).execute()
    if not user_row.data:
        return 0
    email = user_row.data[0]["username"]
    tc = _sb().table("test_collaborators").select("id", count="exact").eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()
    pc = _sb().table("program_collaborators").select("id", count="exact").eq("status", "pending").or_(f"user_id.eq.{user_id},user_email.eq.{email}").execute()
    return (tc.count or 0) + (pc.count or 0)


def get_favorite_tests(user_id):
    res = _sb().table("favorite_tests").select("test_id").eq("user_id", user_id).not_.is_("test_id", "null").execute()
    return {r["test_id"] for r in res.data}


# ---------------------------------------------------------------------------
# Surveys
# ---------------------------------------------------------------------------

def create_survey(title, description="", survey_type="periodic", valid_from=None, valid_until=None):
    res = _sb().table("surveys").insert({
        "title": title, "description": description, "survey_type": survey_type,
        "valid_from": valid_from, "valid_until": valid_until,
    }).execute()
    return res.data[0]["id"]


def update_survey(survey_id, title, description="", valid_from=None, valid_until=None):
    _sb().table("surveys").update({
        "title": title, "description": description,
        "valid_from": valid_from, "valid_until": valid_until,
    }).eq("id", survey_id).execute()


def delete_survey(survey_id):
    _sb().table("surveys").delete().eq("id", survey_id).execute()


def get_survey(survey_id):
    res = _sb().table("surveys").select(
        "id, title, description, survey_type, is_active, created_at, valid_from, valid_until"
    ).eq("id", survey_id).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {
        "id": r["id"], "title": r["title"], "description": r["description"],
        "survey_type": r["survey_type"], "is_active": bool(r["is_active"]),
        "created_at": r["created_at"], "valid_from": r["valid_from"], "valid_until": r["valid_until"],
    }


def get_all_surveys():
    res = _sb().table("surveys").select(
        "id, title, description, survey_type, is_active, created_at, valid_from, valid_until"
    ).order("created_at", desc=True).execute()
    result = []
    for r in res.data:
        qc = _sb().table("survey_questions").select("id", count="exact").eq("survey_id", r["id"]).execute()
        rc = _sb().table("survey_responses").select("id", count="exact").eq("survey_id", r["id"]).execute()
        result.append({
            "id": r["id"], "title": r["title"], "description": r["description"],
            "survey_type": r["survey_type"], "is_active": bool(r["is_active"]),
            "created_at": r["created_at"], "valid_from": r["valid_from"], "valid_until": r["valid_until"],
            "question_count": qc.count or 0, "response_count": rc.count or 0,
        })
    return result


def set_active_survey(survey_id, survey_type):
    _sb().table("surveys").update({"is_active": False}).eq("survey_type", survey_type).execute()
    _sb().table("surveys").update({"is_active": True}).eq("id", survey_id).execute()


def get_active_periodic_survey():
    res = _sb().table("surveys").select(
        "id, title, description, survey_type, is_active, created_at, valid_from, valid_until"
    ).eq("survey_type", "periodic").eq("is_active", True).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {
        "id": r["id"], "title": r["title"], "description": r["description"],
        "survey_type": r["survey_type"], "is_active": True,
        "created_at": r["created_at"], "valid_from": r["valid_from"], "valid_until": r["valid_until"],
    }


def get_active_initial_survey():
    res = _sb().table("surveys").select(
        "id, title, description, survey_type, is_active, created_at, valid_from, valid_until"
    ).eq("survey_type", "initial").eq("is_active", True).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {
        "id": r["id"], "title": r["title"], "description": r["description"],
        "survey_type": r["survey_type"], "is_active": True,
        "created_at": r["created_at"], "valid_from": r["valid_from"], "valid_until": r["valid_until"],
    }


# ---------------------------------------------------------------------------
# Survey Questions
# ---------------------------------------------------------------------------

def add_survey_question(survey_id, question_num, question_type, question_text, options=None, required=True):
    res = _sb().table("survey_questions").insert({
        "survey_id": survey_id, "question_num": question_num, "question_type": question_type,
        "question_text": question_text, "options": options or [], "required": required,
    }).execute()
    return res.data[0]["id"]


def update_survey_question(question_id, question_type, question_text, options=None, required=True):
    _sb().table("survey_questions").update({
        "question_type": question_type, "question_text": question_text,
        "options": options or [], "required": required,
    }).eq("id", question_id).execute()


def delete_survey_question(question_id):
    _sb().table("survey_questions").delete().eq("id", question_id).execute()


def get_survey_questions(survey_id):
    res = _sb().table("survey_questions").select(
        "id, question_num, question_type, question_text, options, required"
    ).eq("survey_id", survey_id).order("question_num").execute()
    return [
        {"id": r["id"], "question_num": r["question_num"], "question_type": r["question_type"],
         "question_text": r["question_text"],
         "options": r["options"] if isinstance(r["options"], list) else json.loads(r["options"] or "[]"),
         "required": bool(r["required"])}
        for r in res.data
    ]


def get_next_survey_question_num(survey_id):
    res = _sb().table("survey_questions").select("question_num").eq("survey_id", survey_id).order("question_num", desc=True).limit(1).execute()
    if res.data:
        return res.data[0]["question_num"] + 1
    return 1


# ---------------------------------------------------------------------------
# Survey Responses
# ---------------------------------------------------------------------------

def submit_survey_response(survey_id, user_id, answers):
    res = _sb().table("survey_responses").insert({
        "survey_id": survey_id, "user_id": user_id,
    }).execute()
    response_id = res.data[0]["id"]
    for ans in answers:
        _sb().table("survey_answers").insert({
            "response_id": response_id, "question_id": ans["question_id"],
            "answer_text": ans.get("answer_text", ""),
            "answer_options": ans.get("answer_options", []),
        }).execute()
    return response_id


def has_completed_survey(user_id, survey_id):
    res = _sb().table("survey_responses").select("id").eq("user_id", user_id).eq("survey_id", survey_id).execute()
    return len(res.data) > 0


def get_survey_response_count(survey_id):
    res = _sb().table("survey_responses").select("id", count="exact").eq("survey_id", survey_id).execute()
    return res.count if res.count is not None else 0


def get_survey_responses(survey_id):
    res = _sb().table("survey_responses").select(
        "id, user_id, completed_at, users(username, display_name)"
    ).eq("survey_id", survey_id).order("completed_at", desc=True).execute()
    return [
        {"id": r["id"], "user_id": r["user_id"], "completed_at": r["completed_at"],
         "email": r["users"]["username"] if r.get("users") else "",
         "display_name": (r["users"]["display_name"] or r["users"]["username"]) if r.get("users") else ""}
        for r in res.data
    ]


def get_survey_response_answers(response_id):
    res = _sb().table("survey_answers").select(
        "id, question_id, answer_text, answer_options, survey_questions(question_text, question_num)"
    ).eq("response_id", response_id).execute()
    result = []
    for r in res.data:
        sq = r.get("survey_questions") or {}
        result.append({
            "id": r["id"], "question_id": r["question_id"], "answer_text": r["answer_text"],
            "answer_options": r["answer_options"] if isinstance(r["answer_options"], list) else json.loads(r["answer_options"] or "[]"),
            "question_text": sq.get("question_text", ""),
        })
    result.sort(key=lambda x: (x.get("question_text", "")))
    return result


def get_survey_answer_statistics(survey_id):
    questions = get_survey_questions(survey_id)
    stats = {}
    for q in questions:
        answers_res = _sb().table("survey_answers").select(
            "answer_text, answer_options, survey_responses!inner(survey_id)"
        ).eq("question_id", q["id"]).eq("survey_responses.survey_id", survey_id).execute()

        if q["question_type"] in ("multiple_choice", "rating"):
            counts = {}
            for r in answers_res.data:
                val = r["answer_text"]
                counts[val] = counts.get(val, 0) + 1
            stats[q["id"]] = {"counts": counts, "question": q}
        elif q["question_type"] == "checkbox":
            option_counts = {}
            for r in answers_res.data:
                opts = r["answer_options"] if isinstance(r["answer_options"], list) else json.loads(r["answer_options"] or "[]")
                for opt in opts:
                    option_counts[opt] = option_counts.get(opt, 0) + 1
            stats[q["id"]] = {"counts": option_counts, "question": q}
        else:
            stats[q["id"]] = {"text_responses": [r["answer_text"] for r in answers_res.data if r["answer_text"]], "question": q}
    return stats


# ---------------------------------------------------------------------------
# User Survey Status
# ---------------------------------------------------------------------------

def get_user_survey_status(user_id):
    res = _sb().table("user_survey_status").select(
        "id, knowter_access_type, initial_survey_completed, last_periodic_survey_id, "
        "last_periodic_survey_date, survey_deadline, access_revoked, revoked_at, pending_approval, access_on_hold"
    ).eq("user_id", user_id).execute()
    if not res.data:
        return None
    r = res.data[0]
    return {
        "id": r["id"], "knowter_access_type": r["knowter_access_type"],
        "initial_survey_completed": bool(r["initial_survey_completed"]),
        "last_periodic_survey_id": r["last_periodic_survey_id"],
        "last_periodic_survey_date": r["last_periodic_survey_date"],
        "survey_deadline": r["survey_deadline"],
        "access_revoked": bool(r["access_revoked"]), "revoked_at": r["revoked_at"],
        "pending_approval": bool(r["pending_approval"]),
        "access_on_hold": bool(r["access_on_hold"]) if r.get("access_on_hold") is not None else False,
    }


def create_user_survey_status(user_id, knowter_access_type, initial_completed=False, pending_approval=False):
    _sb().table("user_survey_status").insert({
        "user_id": user_id, "knowter_access_type": knowter_access_type,
        "initial_survey_completed": initial_completed, "pending_approval": pending_approval,
    }).execute()


def update_user_survey_status(user_id, initial_completed=None, pending_approval=None,
                               last_survey_id=None, deadline=None, access_revoked=None,
                               access_on_hold=None):
    update = {}
    if initial_completed is not None:
        update["initial_survey_completed"] = initial_completed
    if pending_approval is not None:
        update["pending_approval"] = pending_approval
    if last_survey_id is not None:
        update["last_periodic_survey_id"] = last_survey_id
        update["last_periodic_survey_date"] = "now()"
    if deadline is not None:
        update["survey_deadline"] = deadline
    if access_on_hold is not None:
        update["access_on_hold"] = access_on_hold
    if access_revoked is not None:
        update["access_revoked"] = access_revoked
        if access_revoked:
            update["revoked_at"] = "now()"
        else:
            update["revoked_at"] = None
    if update:
        _sb().table("user_survey_status").update(update).eq("user_id", user_id).execute()


def revoke_survey_based_access(user_id):
    _sb().table("user_survey_status").update({
        "access_revoked": True, "revoked_at": "now()",
    }).eq("user_id", user_id).execute()
    _sb().table("users").update({"global_role": "knower"}).eq("id", user_id).execute()


def put_access_on_hold(user_id):
    _sb().table("user_survey_status").update({"access_on_hold": True}).eq("user_id", user_id).execute()


def release_access_hold(user_id):
    from datetime import datetime, timedelta
    deadline = (datetime.now() + timedelta(days=30)).isoformat()
    _sb().table("user_survey_status").update({
        "access_on_hold": False, "survey_deadline": deadline,
    }).eq("user_id", user_id).execute()


def approve_knowter_access(user_id):
    _sb().table("user_survey_status").update({"pending_approval": False}).eq("user_id", user_id).execute()
    _sb().table("users").update({"global_role": "knowter"}).eq("id", user_id).execute()


def get_users_pending_approval():
    res = _sb().table("user_survey_status").select(
        "user_id, last_periodic_survey_date, users(username, display_name)"
    ).eq("pending_approval", True).eq("access_revoked", False).execute()
    result = []
    for r in res.data:
        u = r.get("users") or {}
        result.append({
            "user_id": r["user_id"], "email": u.get("username", ""),
            "display_name": u.get("display_name") or u.get("username", ""),
            "survey_date": r["last_periodic_survey_date"],
        })
    return result


def get_users_needing_survey():
    res = _sb().table("user_survey_status").select(
        "user_id, survey_deadline, last_periodic_survey_date, knowter_access_type, "
        "users!inner(username, display_name, global_role)"
    ).eq("knowter_access_type", "survey").eq("access_revoked", False).eq("pending_approval", False).execute()
    result = []
    for r in res.data:
        u = r.get("users") or {}
        if u.get("global_role") != "knowter":
            continue
        result.append({
            "user_id": r["user_id"], "email": u.get("username", ""),
            "display_name": u.get("display_name") or u.get("username", ""),
            "deadline": r["survey_deadline"], "last_survey_date": r["last_periodic_survey_date"],
            "access_type": r["knowter_access_type"],
        })
    result.sort(key=lambda x: x.get("deadline") or "")
    return result


def get_users_with_overdue_surveys():
    from datetime import datetime
    now = datetime.now().isoformat()
    res = _sb().table("user_survey_status").select(
        "user_id, survey_deadline, users!inner(username, display_name, global_role)"
    ).eq("knowter_access_type", "survey").eq("access_revoked", False).eq("pending_approval", False).lt("survey_deadline", now).execute()
    result = []
    for r in res.data:
        u = r.get("users") or {}
        if u.get("global_role") != "knowter":
            continue
        result.append({
            "user_id": r["user_id"], "email": u.get("username", ""),
            "display_name": u.get("display_name") or u.get("username", ""),
            "deadline": r["survey_deadline"],
        })
    result.sort(key=lambda x: x.get("deadline") or "")
    return result


def get_pending_approval_count():
    res = _sb().table("user_survey_status").select("id", count="exact").eq("pending_approval", True).eq("access_revoked", False).execute()
    return res.count if res.count is not None else 0


# ---------------------------------------------------------------------------
# Global Statistics
# ---------------------------------------------------------------------------

def get_global_statistics():
    res = _sb().rpc("get_global_stats").execute()
    if res.data:
        r = res.data[0]
        return {
            "users": r["users_count"], "tests": r["tests_count"],
            "courses": r["courses_count"], "sessions": r["sessions_count"],
            "materials": r["materials_count"],
        }
    return {"users": 0, "tests": 0, "courses": 0, "sessions": 0, "materials": 0}
