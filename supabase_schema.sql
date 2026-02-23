-- Supabase Schema for Knowting Club
-- Run this in the Supabase SQL Editor to create all tables.

-- 1. Users
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    display_name TEXT,
    avatar BYTEA,
    global_role TEXT DEFAULT 'visitor'
);

-- 2. Tests
CREATE TABLE IF NOT EXISTS tests (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    author TEXT DEFAULT '',
    source_file TEXT,
    is_public BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    language TEXT DEFAULT '',
    visibility TEXT DEFAULT 'public'
);

-- 3. Questions
CREATE TABLE IF NOT EXISTS questions (
    id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    question_num INTEGER NOT NULL,
    tag TEXT NOT NULL,
    question TEXT NOT NULL,
    options JSONB NOT NULL,
    answer_index INTEGER NOT NULL,
    explanation TEXT DEFAULT '',
    source TEXT DEFAULT 'manual'
);

-- 4. Test Materials
CREATE TABLE IF NOT EXISTS test_materials (
    id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    material_type TEXT NOT NULL,
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    file_data BYTEA,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    pause_times TEXT DEFAULT '',
    questions_per_pause INTEGER DEFAULT 1,
    transcript TEXT DEFAULT ''
);

-- 5. Question-Material Links
CREATE TABLE IF NOT EXISTS question_materials (
    id SERIAL PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL REFERENCES test_materials(id) ON DELETE CASCADE,
    context TEXT DEFAULT ''
);

-- 6. Test Sessions
CREATE TABLE IF NOT EXISTS test_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    test_file TEXT NOT NULL DEFAULT '',
    test_id INTEGER REFERENCES tests(id),
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Question History
CREATE TABLE IF NOT EXISTS question_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    test_file TEXT NOT NULL DEFAULT '',
    test_id INTEGER REFERENCES tests(id),
    question_id INTEGER NOT NULL,
    correct BOOLEAN NOT NULL,
    session_id INTEGER REFERENCES test_sessions(id),
    answered_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. Favorite Tests
CREATE TABLE IF NOT EXISTS favorite_tests (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    test_file TEXT NOT NULL DEFAULT '',
    test_id INTEGER REFERENCES tests(id),
    UNIQUE(user_id, test_file)
);

-- 9. Programs
CREATE TABLE IF NOT EXISTS programs (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    visibility TEXT DEFAULT 'public'
);

-- 10. Program Tests
CREATE TABLE IF NOT EXISTS program_tests (
    id SERIAL PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    program_visibility TEXT DEFAULT 'public',
    UNIQUE(program_id, test_id)
);

-- 11. Program Collaborators
CREATE TABLE IF NOT EXISTS program_collaborators (
    id SERIAL PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    user_email TEXT NOT NULL,
    user_id INTEGER,
    role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined')),
    invited_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(program_id, user_email)
);

-- 12. Test Collaborators
CREATE TABLE IF NOT EXISTS test_collaborators (
    id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    user_email TEXT NOT NULL,
    user_id INTEGER,
    role TEXT NOT NULL CHECK(role IN ('student','guest','reviewer','admin')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined')),
    invited_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(test_id, user_email)
);

-- 13. Test Tags
CREATE TABLE IF NOT EXISTS test_tags (
    id SERIAL PRIMARY KEY,
    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    UNIQUE(test_id, tag)
);

-- 14. Surveys
CREATE TABLE IF NOT EXISTS surveys (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    survey_type TEXT NOT NULL CHECK(survey_type IN ('initial', 'periodic', 'feedback')),
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ
);

-- 15. Survey Questions
CREATE TABLE IF NOT EXISTS survey_questions (
    id SERIAL PRIMARY KEY,
    survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    question_num INTEGER NOT NULL,
    question_type TEXT NOT NULL CHECK(question_type IN ('multiple_choice', 'text', 'rating', 'checkbox')),
    question_text TEXT NOT NULL,
    options JSONB DEFAULT '[]',
    required BOOLEAN DEFAULT TRUE
);

-- 16. Survey Responses
CREATE TABLE IF NOT EXISTS survey_responses (
    id SERIAL PRIMARY KEY,
    survey_id INTEGER NOT NULL REFERENCES surveys(id),
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    completed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(survey_id, user_id)
);

-- 17. Survey Answers
CREATE TABLE IF NOT EXISTS survey_answers (
    id SERIAL PRIMARY KEY,
    response_id INTEGER NOT NULL REFERENCES survey_responses(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES survey_questions(id) ON DELETE CASCADE,
    answer_text TEXT DEFAULT '',
    answer_options JSONB DEFAULT '[]'
);

-- 18. User Survey Status
CREATE TABLE IF NOT EXISTS user_survey_status (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    knowter_access_type TEXT CHECK(knowter_access_type IN ('survey', 'paid', 'granted')),
    initial_survey_completed BOOLEAN DEFAULT FALSE,
    pending_approval BOOLEAN DEFAULT FALSE,
    last_periodic_survey_id INTEGER,
    last_periodic_survey_date TIMESTAMPTZ,
    survey_deadline TIMESTAMPTZ,
    access_on_hold BOOLEAN DEFAULT FALSE,
    access_revoked BOOLEAN DEFAULT FALSE,
    revoked_at TIMESTAMPTZ
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_questions_test_id ON questions(test_id);
CREATE INDEX IF NOT EXISTS idx_question_history_user_test ON question_history(user_id, test_id);
CREATE INDEX IF NOT EXISTS idx_test_sessions_user ON test_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_test_materials_test_id ON test_materials(test_id);
CREATE INDEX IF NOT EXISTS idx_question_materials_question ON question_materials(question_id);
CREATE INDEX IF NOT EXISTS idx_test_collaborators_test ON test_collaborators(test_id);
CREATE INDEX IF NOT EXISTS idx_program_collaborators_program ON program_collaborators(program_id);
CREATE INDEX IF NOT EXISTS idx_test_tags_test ON test_tags(test_id);
CREATE INDEX IF NOT EXISTS idx_favorite_tests_user ON favorite_tests(user_id);
CREATE INDEX IF NOT EXISTS idx_program_tests_program ON program_tests(program_id);
CREATE INDEX IF NOT EXISTS idx_survey_questions_survey ON survey_questions(survey_id);
CREATE INDEX IF NOT EXISTS idx_survey_responses_survey ON survey_responses(survey_id);
CREATE INDEX IF NOT EXISTS idx_survey_answers_response ON survey_answers(response_id);

-- RPC functions for complex queries

-- Get all tests with question counts (for logged-in user with visibility filtering)
CREATE OR REPLACE FUNCTION get_all_tests_for_user(p_user_id INTEGER)
RETURNS TABLE(
    id INTEGER, owner_id INTEGER, title TEXT, description TEXT, author TEXT,
    is_public BOOLEAN, q_count BIGINT, language TEXT, visibility TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT t.id, t.owner_id, t.title, t.description, t.author, t.is_public,
           (SELECT COUNT(*) FROM questions WHERE questions.test_id = t.id) as q_count,
           t.language, t.visibility
    FROM tests t
    LEFT JOIN test_collaborators tc ON tc.test_id = t.id
        AND (tc.user_id = p_user_id OR tc.user_email = (SELECT username FROM users WHERE users.id = p_user_id))
        AND tc.status = 'accepted'
    LEFT JOIN program_tests pt ON pt.test_id = t.id
    LEFT JOIN program_collaborators pc ON pc.program_id = pt.program_id
        AND (pc.user_id = p_user_id OR pc.user_email = (SELECT username FROM users WHERE users.id = p_user_id))
        AND pc.status = 'accepted'
    WHERE t.visibility IN ('public', 'private', 'restricted')
       OR t.owner_id = p_user_id
       OR tc.id IS NOT NULL
       OR pc.id IS NOT NULL
    ORDER BY t.title;
END;
$$ LANGUAGE plpgsql;

-- Get all tests (public, no user context)
CREATE OR REPLACE FUNCTION get_all_tests_public()
RETURNS TABLE(
    id INTEGER, owner_id INTEGER, title TEXT, description TEXT, author TEXT,
    is_public BOOLEAN, q_count BIGINT, language TEXT, visibility TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT t.id, t.owner_id, t.title, t.description, t.author, t.is_public,
           (SELECT COUNT(*) FROM questions WHERE questions.test_id = t.id) as q_count,
           t.language, t.visibility
    FROM tests t
    WHERE t.visibility IN ('public', 'private', 'restricted')
    ORDER BY t.title;
END;
$$ LANGUAGE plpgsql;

-- Get all programs for a user
CREATE OR REPLACE FUNCTION get_all_programs_for_user(p_user_id INTEGER)
RETURNS TABLE(
    id INTEGER, owner_id INTEGER, title TEXT, description TEXT, created_at TIMESTAMPTZ,
    test_count BIGINT, visibility TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT p.id, p.owner_id, p.title, p.description, p.created_at,
           (SELECT COUNT(*) FROM program_tests WHERE program_id = p.id) as test_count,
           p.visibility
    FROM programs p
    LEFT JOIN program_collaborators pc ON pc.program_id = p.id
        AND (pc.user_id = p_user_id OR pc.user_email = (SELECT username FROM users WHERE users.id = p_user_id))
        AND pc.status = 'accepted'
    WHERE p.visibility IN ('public', 'private', 'restricted')
       OR p.owner_id = p_user_id
       OR pc.id IS NOT NULL
    ORDER BY p.title;
END;
$$ LANGUAGE plpgsql;

-- Get topic statistics for a user on a test
CREATE OR REPLACE FUNCTION get_topic_stats(p_user_id INTEGER, p_test_id INTEGER)
RETURNS TABLE(
    tag TEXT, total BIGINT, correct BIGINT, incorrect BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT q.tag,
           COUNT(*) as total,
           SUM(CASE WHEN qh.correct THEN 1 ELSE 0 END) as correct,
           SUM(CASE WHEN NOT qh.correct THEN 1 ELSE 0 END) as incorrect
    FROM question_history qh
    JOIN questions q ON qh.question_id = q.question_num AND qh.test_id = q.test_id
    WHERE qh.user_id = p_user_id AND qh.test_id = p_test_id
    GROUP BY q.tag
    ORDER BY q.tag;
END;
$$ LANGUAGE plpgsql;

-- Get topic daily history
CREATE OR REPLACE FUNCTION get_topic_daily_history(p_user_id INTEGER, p_test_id INTEGER)
RETURNS TABLE(
    tag TEXT, answer_date DATE, correct BIGINT, incorrect BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT q.tag,
           DATE(qh.answered_at) as answer_date,
           SUM(CASE WHEN qh.correct THEN 1 ELSE 0 END) as correct,
           SUM(CASE WHEN NOT qh.correct THEN 1 ELSE 0 END) as incorrect
    FROM question_history qh
    JOIN questions q ON qh.question_id = q.question_num AND qh.test_id = q.test_id
    WHERE qh.user_id = p_user_id AND qh.test_id = p_test_id
    GROUP BY q.tag, DATE(qh.answered_at)
    ORDER BY q.tag, answer_date;
END;
$$ LANGUAGE plpgsql;

-- Get user role for test (checks direct + program access)
CREATE OR REPLACE FUNCTION get_user_test_role(p_test_id INTEGER, p_user_id INTEGER)
RETURNS TABLE(role TEXT, program_visibility TEXT) AS $$
BEGIN
    -- Direct test collaborator
    RETURN QUERY
    SELECT tc.role, NULL::TEXT as program_visibility
    FROM test_collaborators tc
    LEFT JOIN users u ON u.username = tc.user_email
    WHERE tc.test_id = p_test_id AND (tc.user_id = p_user_id OR u.id = p_user_id) AND tc.status = 'accepted'
    LIMIT 1;

    IF FOUND THEN RETURN; END IF;

    -- Program-level collaborator
    RETURN QUERY
    SELECT pc.role, pt.program_visibility
    FROM program_collaborators pc
    JOIN program_tests pt ON pt.program_id = pc.program_id
    LEFT JOIN users u ON u.username = pc.user_email
    WHERE pt.test_id = p_test_id AND (pc.user_id = p_user_id OR u.id = p_user_id) AND pc.status = 'accepted'
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- Get global statistics
CREATE OR REPLACE FUNCTION get_global_stats()
RETURNS TABLE(users_count BIGINT, tests_count BIGINT, courses_count BIGINT, sessions_count BIGINT, materials_count BIGINT) AS $$
BEGIN
    RETURN QUERY
    SELECT
        (SELECT COUNT(*) FROM users),
        (SELECT COUNT(*) FROM tests),
        (SELECT COUNT(*) FROM programs),
        (SELECT COUNT(*) FROM test_sessions),
        (SELECT COUNT(*) FROM test_materials);
END;
$$ LANGUAGE plpgsql;
