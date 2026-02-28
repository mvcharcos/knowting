import streamlit as st
from translations import t
from auth import _is_logged_in
from helpers import select_balanced_questions, shuffle_question_options, reset_quiz
from db import (
    get_user_test_ids, get_user_session_count, get_tests_performance,
    get_all_tests, get_test_questions, get_question_stats,
    get_topic_statistics, get_user_sessions,
    get_user_program_ids, get_programs_performance, get_all_programs,
    create_session,
)


def _show_visitor_preview_dashboard():
    """Show a greyed-out preview of the dashboard for visitor users."""
    st.header(t("dashboard"))
    st.info(f"👁️ {t('visitor_feature_preview')}")

    # Greyed-out CSS overlay
    st.markdown(
        '<style>.visitor-preview { opacity: 0.35; pointer-events: none; }</style>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="visitor-preview">', unsafe_allow_html=True)

    # --- Fake trophies section ---
    st.subheader(t("your_trophies"))
    cols = st.columns(4)
    fake_trophies = ["🏆", "📚", "🎯", "🥇", "🥈", "🥉", "🧠"]
    for i, icon in enumerate(fake_trophies):
        with cols[i % 4]:
            st.markdown(f"<div style='text-align:center;font-size:2em;opacity:0.3;'>🔒</div>", unsafe_allow_html=True)

    st.divider()

    # --- Fake global performance ---
    st.subheader(t("global_performance"))
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(t("tests_taken"), "—")
    col2.metric(t("total_questions"), "—")
    col3.metric(t("correct_answers"), "—")
    col4.metric(t("average_score"), "—")

    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown(f"### {t('visitor_upgrade_message')}")
    if st.button(t("become_knower"), key="visitor_upgrade_dashboard", type="primary"):
        st.session_state.page = "Home"
        st.rerun()


def show_dashboard():
    """Show the results dashboard with trophies, global stats, and test performance."""
    st.header(t("dashboard"))

    user_id = st.session_state.user_id

    # Get user's test history
    test_ids = get_user_test_ids(user_id)
    session_count = get_user_session_count(user_id)

    # Get performance data if user has taken tests
    if test_ids:
        test_performance = get_tests_performance(user_id, test_ids)
        total_questions = sum(p["total"] for p in test_performance.values())
        total_correct = sum(p["correct"] for p in test_performance.values())
        avg_score = round(100 * total_correct / total_questions, 1) if total_questions > 0 else 0
        earned_trophies = _compute_user_trophies(user_id, test_performance, session_count)
        earned_keys = {key for key, _, _ in earned_trophies}
    else:
        test_performance = {}
        total_questions = 0
        total_correct = 0
        avg_score = 0
        earned_keys = set()

    # --- Trophies Section ---
    st.subheader(t("your_trophies"))

    # Define all available trophies with descriptions
    all_trophies = [
        ("first_test", "🏆", t("trophy_first_test"), t("trophy_first_test_desc")),
        ("5_tests", "📚", t("trophy_5_tests"), t("trophy_5_tests_desc")),
        ("10_tests", "🎯", t("trophy_10_tests"), t("trophy_10_tests_desc")),
        ("perfect", "🥇", t("trophy_perfect"), t("trophy_perfect_desc")),
        ("excellent", "🥈", t("trophy_excellent"), t("trophy_excellent_desc")),
        ("great", "🥉", t("trophy_great"), t("trophy_great_desc")),
        ("topic_master", "🧠", t("trophy_topic_master"), t("trophy_topic_master_desc")),
    ]

    # Display all trophies in a grid
    cols = st.columns(4)
    for i, (key, icon, name, desc) in enumerate(all_trophies):
        with cols[i % 4]:
            is_earned = key in earned_keys
            if is_earned:
                st.markdown(f"<div style='text-align:center;font-size:2em;'>{icon}</div>", unsafe_allow_html=True)
                st.caption(f"<div style='text-align:center;'><b>{name}</b></div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='text-align:center;font-size:2em;opacity:0.3;'>🔒</div>", unsafe_allow_html=True)
                st.caption(f"<div style='text-align:center;color:#888;'>{name}</div>", unsafe_allow_html=True)
            st.caption(f"<div style='text-align:center;font-size:0.8em;color:#666;'>{desc}</div>", unsafe_allow_html=True)

    st.divider()

    # If user has not taken any tests, show message and stop here
    if not test_ids:
        st.info(t("no_tests_taken"))
        return

    # --- Global Performance Section ---
    st.subheader(t("global_performance"))
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(t("tests_taken"), len(test_ids))
    col2.metric(t("total_questions"), total_questions)
    col3.metric(t("correct_answers"), total_correct)
    col4.metric(t("average_score"), f"{avg_score}%")

    st.divider()

    # --- Tests with Performance ---
    st.subheader(t("your_tests"))

    # Get test details for all tests the user has taken
    all_tests = get_all_tests(user_id)
    tests_by_id = {tt["id"]: tt for tt in all_tests}

    # Helper to get performance circle
    def _get_perf_circle(pct):
        if pct >= 95:
            return "🟢"
        elif pct >= 80:
            return "🟡"
        elif pct >= 50:
            return "🟠"
        else:
            return "🔴"

    for test_id in test_ids:
        test = tests_by_id.get(test_id)
        if not test:
            continue

        perf = test_performance.get(test_id, {})
        pct = perf.get("percent_correct", 0)
        circle = _get_perf_circle(pct)

        # Get topic statistics for this test
        topic_stats = get_topic_statistics(user_id, test_id)
        best_topic = None
        worst_topic = None
        if topic_stats:
            sorted_topics = sorted(topic_stats.items(), key=lambda x: x[1]["percent_correct"], reverse=True)
            if sorted_topics:
                best_tag, best_stats = sorted_topics[0]
                best_topic = (best_tag.replace("_", " ").title(), best_stats["percent_correct"])
                if len(sorted_topics) > 1:
                    worst_tag, worst_stats = sorted_topics[-1]
                    worst_topic = (worst_tag.replace("_", " ").title(), worst_stats["percent_correct"], worst_tag)

        with st.container(border=True):
            col_info, col_actions = st.columns([3, 2])

            with col_info:
                st.markdown(f"### {circle} {test['title']}")
                st.metric(t("overall_score"), f"{pct}%", label_visibility="collapsed")
                total_answered = perf.get('total', 0)
                correct_count = perf.get('correct', 0)
                st.caption(f"{t('questions_answered')}: {total_answered} · {t('correct_answers')}: {correct_count}")

                if best_topic:
                    st.write(f"✅ **{t('best_topic')}:** {best_topic[0]} ({best_topic[1]}%)")
                if worst_topic:
                    st.write(f"⚠️ **{t('worst_topic')}:** {worst_topic[0]} ({worst_topic[1]}%)")

            with col_actions:
                # Retake test button
                if st.button(t("retake_test"), key=f"retake_{test_id}", width="stretch"):
                    st.session_state.selected_test = test_id
                    st.session_state.page = "Configurar Test"
                    st.rerun()

                # Practice worst topic button (if available)
                if worst_topic:
                    if st.button(t("practice_worst_topic"), key=f"practice_worst_{test_id}", width="stretch"):
                        _start_topic_focused_test(test_id, worst_topic[2])

    # --- Programs Section ---
    program_ids = get_user_program_ids(user_id)
    if program_ids:
        st.divider()
        st.subheader(t("your_courses"))

        # Get program performance data
        program_performance = get_programs_performance(user_id, program_ids)

        # Get program details
        all_programs = get_all_programs(user_id)
        programs_by_id = {p["id"]: p for p in all_programs}

        for program_id in program_ids:
            program = programs_by_id.get(program_id)
            if not program:
                continue

            perf = program_performance.get(program_id, {})
            pct = perf.get("percent_correct", 0)
            circle = _get_perf_circle(pct)
            total_answered = perf.get("total", 0)
            correct_count = perf.get("correct", 0)
            tests_taken = perf.get("tests_taken", 0)

            with st.container(border=True):
                col_info, col_actions = st.columns([3, 2])

                with col_info:
                    st.markdown(f"### {circle} {program['title']}")
                    st.metric(t("overall_score"), f"{pct}%", label_visibility="collapsed")
                    st.caption(f"{t('tests_taken')}: {tests_taken} · {t('questions_answered')}: {total_answered} · {t('correct_answers')}: {correct_count}")

                with col_actions:
                    # Go to program button
                    if st.button(t("view_course"), key=f"view_prog_{program_id}", width="stretch"):
                        st.session_state.selected_program = program_id
                        st.session_state.page = "Configurar Curso"
                        st.rerun()


def _compute_user_trophies(user_id, test_performance, session_count):
    """Compute trophies/achievements based on user's performance.

    Returns a list of tuples: (key, icon, name) for earned trophies.
    """
    trophies = []

    # First test completed
    if session_count >= 1:
        trophies.append(("first_test", "🏆", t("trophy_first_test")))

    # 5 tests completed
    if session_count >= 5:
        trophies.append(("5_tests", "📚", t("trophy_5_tests")))

    # 10 tests completed
    if session_count >= 10:
        trophies.append(("10_tests", "🎯", t("trophy_10_tests")))

    # Check for perfect scores, excellent, and great scores
    has_perfect = False
    has_excellent = False
    has_great = False
    topic_master_count = 0

    for test_id, perf in test_performance.items():
        pct = perf.get("percent_correct", 0)
        if pct >= 100:
            has_perfect = True
        if pct >= 90:
            has_excellent = True
        if pct >= 80:
            has_great = True

        # Check for topic master (90%+ on all topics in a test)
        topic_stats = get_topic_statistics(user_id, test_id)
        if topic_stats:
            all_topics_excellent = all(s["percent_correct"] >= 90 for s in topic_stats.values())
            if all_topics_excellent and len(topic_stats) >= 2:
                topic_master_count += 1

    if has_perfect:
        trophies.append(("perfect", "🥇", t("trophy_perfect")))
    if has_excellent and not has_perfect:
        trophies.append(("excellent", "🥈", t("trophy_excellent")))
    if has_great and not has_excellent:
        trophies.append(("great", "🥉", t("trophy_great")))
    if topic_master_count > 0:
        trophies.append(("topic_master", "🧠", t("trophy_topic_master")))

    return trophies


def _start_topic_focused_test(test_id, tag):
    """Start a test focused on a specific topic."""
    questions = get_test_questions(test_id)
    topic_questions = [q for q in questions if q["tag"] == tag]

    if not topic_questions:
        return

    logged_in = _is_logged_in()
    stats_data = get_question_stats(st.session_state.user_id, test_id) if logged_in else None
    quiz_questions = select_balanced_questions(
        topic_questions, [tag], min(25, len(topic_questions)), stats_data
    )

    session_id = None
    if logged_in:
        session_id = create_session(
            st.session_state.user_id, test_id,
            0, len(quiz_questions),
        )

    st.session_state.questions = shuffle_question_options(quiz_questions)
    st.session_state.current_index = 0
    st.session_state.score = 0
    st.session_state.answered = False
    st.session_state.show_result = False
    st.session_state.selected_answer = None
    st.session_state.wrong_questions = []
    st.session_state.round_history = []
    st.session_state.current_round = 1
    st.session_state.current_test_id = test_id
    st.session_state.session_id = session_id
    st.session_state.view = "quiz"
    st.session_state.quiz_started = True
    st.session_state.page = "Tests"
    st.rerun()
