# Knowting Club

**Turn notes into knowledge** — An open-source educational platform for creating, sharing, and taking interactive quizzes linked to video and document materials.

Built with [Streamlit](https://streamlit.io) and [Supabase](https://supabase.com).

## Features

- **Interactive quizzes** — Multiple-choice questions with explanations, topic tags, and performance tracking
- **Video-based learning** — YouTube videos with configurable pause points that trigger quiz questions at specific timestamps
- **Material references** — Link questions to videos (with timestamps), PDFs (with page numbers), images, and URLs
- **AI-powered tools** — Generate topics and questions from video transcripts, and auto-match existing questions to video segments using Hugging Face models
- **Courses (Programs)** — Group tests into structured courses with shared collaborators
- **Collaboration** — Invite users as students, guests, reviewers, or admins with granular permissions per test or course
- **Performance dashboard** — Track scores, topic-level statistics, and daily progress over time
- **Multi-language** — Full UI in Spanish, English, French, and Catalan
- **Import/Export** — JSON-based test format for sharing and backup
- **Google OAuth** — Authentication via Google accounts
- **Surveys** — Built-in survey system for collecting user feedback

## Project Structure

```
.
├── app.py                  # Main Streamlit application (~6000 lines)
├── db.py                   # Database layer (Supabase client)
├── translations.py         # i18n strings (es, en, fr, ca)
├── supabase_schema.sql     # Database schema + RPC functions
├── migrate_to_supabase.py  # One-time SQLite → Supabase migration script
├── requirements.txt        # Python dependencies
├── test_template.json      # Example JSON format for test import/export
├── assets/
│   └── KnowtingLogo.png    # App logo
├── legal/
│   ├── terms_*.md          # Terms and conditions (4 languages)
│   └── privacy_policy_*.md # Privacy policy (4 languages)
└── .streamlit/
    └── secrets.toml        # Local secrets (not committed)
```

## Prerequisites

- Python 3.10+
- A [Supabase](https://supabase.com) project (free tier works)
- A [Google Cloud](https://console.cloud.google.com) OAuth 2.0 client for authentication
- (Optional) A [Hugging Face](https://huggingface.co) API key for AI features

## Setup

### 1. Clone the repository

```bash
git clone git@github.com:mvcharcos/knowting.git
cd knowting
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create the database

1. Create a new project on [Supabase](https://supabase.com)
2. Open the **SQL Editor** in your Supabase dashboard
3. Paste the contents of `supabase_schema.sql` and run it
4. This creates all 18 tables, indexes, and RPC functions

### 4. Configure secrets

Create `.streamlit/secrets.toml` (this file is gitignored):

```toml
# Hugging Face (optional — for AI topic/question generation)
HF_API_KEY = "hf_your_api_key_here"
HF_MODEL = "Qwen/Qwen2.5-72B-Instruct"

[supabase]
url = "https://your-project-id.supabase.co"
key = "your-service-role-key"

[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "generate-a-random-hex-string"
client_id = "your-google-oauth-client-id.apps.googleusercontent.com"
client_secret = "your-google-oauth-client-secret"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

#### Getting the credentials

- **Supabase**: Go to Project Settings → API. Use the project URL and the `service_role` key (not the `anon` key).
- **Google OAuth**: Create credentials in [Google Cloud Console](https://console.cloud.google.com/apis/credentials). Set the authorized redirect URI to `http://localhost:8501/oauth2callback` for local development.
- **Hugging Face**: Get a token from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). Any model supporting chat completion works; the default is `Qwen/Qwen2.5-72B-Instruct`.
- **Cookie secret**: Generate one with `python -c "import secrets; print(secrets.token_hex(32))"`.

### 5. Set the initial admin

Edit line 43 in `app.py` to set your email as the initial admin:

```python
set_user_global_role_by_email("your-email@example.com", "admin")
```

### 6. Run the app

```bash
streamlit run app.py
```

The app will be available at `http://localhost:8501`.

## Database Schema

The database has 18 tables organized around these concepts:

| Table | Purpose |
|-------|---------|
| `users` | User accounts (Google OAuth) |
| `tests` | Quiz definitions with metadata |
| `questions` | Multiple-choice questions linked to tests |
| `test_materials` | Videos, PDFs, images, URLs linked to tests |
| `question_materials` | Links between questions and materials (with context like timestamps) |
| `test_sessions` | Completed quiz attempts with scores |
| `question_history` | Per-question answer history |
| `favorite_tests` | User bookmarks |
| `programs` | Courses grouping multiple tests |
| `program_tests` | Tests within a program |
| `test_collaborators` | Per-test user access and roles |
| `program_collaborators` | Per-program user access and roles |
| `test_tags` | Topic tags for tests |
| `surveys` | Configurable user surveys |
| `survey_questions` | Survey question definitions |
| `survey_responses` | Completed survey submissions |
| `survey_answers` | Individual survey answers |
| `user_survey_status` | Survey-based access tracking |

Seven PostgreSQL RPC functions handle complex queries (visibility filtering, topic statistics, role resolution). See `supabase_schema.sql` for details.

## User Roles

### Global roles (set by admins)

| Role | Access |
|------|--------|
| `tester` | Can take public tests only (minimal UI) |
| `visitor` | Can take tests and view materials; sees dashboard and courses as disabled preview (default for new users) |
| `knower` | Full access: create tests, track progress, invite collaborators |
| `knowter` | Knower + create and manage courses |
| `admin` | Full platform access |

### Test/Program roles (per-resource collaboration)

| Role | Permissions |
|------|-------------|
| `student` | Take the test (read-only) |
| `guest` | Take the test + view materials |
| `reviewer` | Edit questions, see debug info in video mode |
| `admin` | Full test management (edit, delete, invite) |

## Test JSON Format

Tests can be imported and exported as JSON. See `test_template.json` for the full schema:

```json
{
  "title": "My Test",
  "description": "...",
  "author": "...",
  "language": "es",
  "visibility": "public",
  "tags": ["Topic A", "Topic B"],
  "materials": [...],
  "collaborators": [...],
  "questions": [
    {
      "id": 1,
      "tag": "Topic A",
      "question": "What is...?",
      "options": ["A", "B", "C", "D"],
      "answer_index": 0,
      "explanation": "Because...",
      "material_refs": [
        {"material_id": 1, "context": "1:30-2:00"}
      ]
    }
  ]
}
```

## Translations

The app supports 4 languages: Spanish (`es`), English (`en`), French (`fr`), and Catalan (`ca`).

All UI strings are in `translations.py` as a flat dictionary. To add or modify translations, edit the `TRANSLATIONS` dict. Each key maps to a dict of language codes:

```python
"key_name": {"es": "...", "en": "...", "fr": "...", "ca": "..."},
```

String interpolation uses Python's `.format()` syntax: `{name}`, `{n}`, etc.

## Architecture Notes

- **`app.py`** is a single-file Streamlit app using page routing via `st.session_state.page`. It contains all UI logic, view functions, and JavaScript for the video player.
- **`db.py`** is a pure data layer with ~100 functions. All functions return plain dicts/lists so `app.py` has no direct database dependency. Swapping the database backend only requires rewriting `db.py`.
- **Video study mode** uses the YouTube IFrame API with custom JavaScript for auto-pause, quiz overlay, answer shuffling, and duplicate prevention.
- **`@st.dialog`** is avoided for dialogs with interactive buttons (broken in Streamlit 1.53+). Inline containers with session state toggles are used instead.

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Test locally with `streamlit run app.py`
5. Submit a pull request

When adding new UI text, always add translations for all 4 languages in `translations.py`.

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for the full text.
