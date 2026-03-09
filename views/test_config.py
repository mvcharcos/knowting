import random

import streamlit as st
from translations import t
from auth import _is_logged_in, _get_global_role, _can_create_tests, _is_global_admin
from helpers import (
    select_balanced_questions, shuffle_question_options, reset_quiz,
    _render_material_refs,
    _lang_display, _difficulty_score, _time_to_secs,
)
from db import (
    get_test, get_test_questions,
    get_question_stats, get_test_tags,
    create_session, update_session_score, record_answer,
    get_user_role_for_test, has_direct_test_access,
    get_question_material_links,
    get_effective_visibility, get_collaborators,
    get_question_material_links_bulk, get_topic_statistics,
    get_materials_for_test, get_test_material_questions,
)


def _parse_time_to_sec(time_str):
    """Parse 'm:ss' or 'h:mm:ss' to integer seconds. Returns None on failure."""
    if not time_str:
        return None
    parts = time_str.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    return None


def _fuzzy_score(a, b):
    """Fuzzy partial ratio between two strings (0–100)."""
    if not a or not b:
        return 0
    try:
        from rapidfuzz import fuzz
        return fuzz.partial_ratio(a.lower().strip(), b.lower().strip())
    except ImportError:
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        return int(len(a_words & b_words) / len(a_words) * 100) if a_words else 0


def _assign_questions_to_segments(segments, questions, facts):
    """
    Match questions to segments via facts (two-step chain):
      1. fact → segment  : fact.evidence in segment.clean_text (fuzzy ≥ 65)
      2. question → fact : same subject_concept + most similar evidence (≥ 35)
    Returns {segment_index: [question_dicts]}.
    """
    FACT_SEG_THRESHOLD = 65
    Q_FACT_MIN_SCORE = 35

    # Step 1 — place each fact in its best-matching segment
    fact_to_seg = {}
    for fi, fact in enumerate(facts):
        fact_ev = fact.get("evidence", "")
        best_si, best_score = None, 0
        for si, seg in enumerate(segments):
            seg_clean = seg.get("clean_text", "")
            if fact_ev.lower().strip() in seg_clean.lower():
                fact_to_seg[fi] = si
                break
            score = _fuzzy_score(fact_ev, seg_clean)
            if score > best_score:
                best_score, best_si = score, si
        if fi not in fact_to_seg and best_si is not None and best_score >= FACT_SEG_THRESHOLD:
            fact_to_seg[fi] = best_si

    # Step 2 — match each question to its best fact (same concept, closest evidence)
    seg_questions = {si: [] for si in range(len(segments))}
    seen_questions = set()

    for q in questions:
        q_text = q.get("question", "")
        if q_text in seen_questions:
            continue
        q_concept = str(q.get("concept", "")).strip().lower()
        q_evidence = q.get("evidence", "")

        candidates = [
            (fi, fact) for fi, fact in enumerate(facts)
            if str(fact.get("subject_concept", "")).strip().lower() == q_concept
            and fi in fact_to_seg
        ]
        if not candidates:
            continue

        best_fi, _ = max(candidates,
                         key=lambda x: _fuzzy_score(q_evidence, x[1].get("evidence", "")))
        if _fuzzy_score(q_evidence, facts[best_fi].get("evidence", "")) < Q_FACT_MIN_SCORE:
            continue

        seen_questions.add(q_text)
        seg_questions[fact_to_seg[best_fi]].append({
            "question": q_text,
            "options": q.get("options", []),
            "answer_index": q.get("answer_index", 0),
            "answer": q.get("answer", ""),
        })

    return seg_questions


def _build_youtube_quiz_html(mat):
    """Return self-contained HTML for a YouTube player that pauses at segment
    boundaries and shows questions matched via facts grounded in that segment."""
    import json
    from helpers import _extract_youtube_id

    video_id = _extract_youtube_id(mat.get("url", ""))
    if not video_id:
        return None

    segments = (mat.get("segments_json") or {}).get("segments", [])
    raw_qs = mat.get("questions_json") or []
    questions = raw_qs if isinstance(raw_qs, list) else raw_qs.get("questions", [])
    fact_nodes = [
        n for n in (mat.get("facts_json") or {}).get("nodes", [])
        if n.get("kind") == "fact"
    ]

    seg_questions = _assign_questions_to_segments(segments, questions, fact_nodes)

    pause_data = []
    for si, seg in enumerate(segments):
        end_sec = _parse_time_to_sec(seg.get("end_time", ""))
        if end_sec is None:
            continue
        pause_data.append({
            "end_sec": end_sec,
            "summary": seg.get("summary", ""),
            "segment_id": seg.get("segment_id", ""),
            "questions": seg_questions.get(si, []),
        })

    pause_data_json = json.dumps(pause_data, ensure_ascii=False)
    continue_label = t("mat_continue_video")
    segment_label = t("mat_segment_label")

    return f'''
<style>
  #yt-wrap {{ position: relative; font-family: sans-serif; }}
  #yt-player {{ width: 100%; }}
  #quiz-overlay {{
    display: none; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.88); flex-direction: column;
    align-items: center; justify-content: flex-start;
    padding: 16px; box-sizing: border-box; overflow-y: auto;
  }}
  #quiz-overlay.visible {{ display: flex; }}
  #seg-info {{ color: #ccc; font-size: 13px; margin-bottom: 10px; text-align: center; }}
  .q-card {{
    background: #fff; border-radius: 10px; padding: 16px;
    max-width: 580px; width: 100%; margin-bottom: 12px;
  }}
  .q-text {{ font-size: 15px; font-weight: bold; margin-bottom: 10px; }}
  .opt-btn {{
    display: block; width: 100%; text-align: left;
    padding: 9px 13px; margin: 5px 0;
    background: #f0f2f6; border: 2px solid #dde;
    border-radius: 7px; cursor: pointer; font-size: 14px;
  }}
  .opt-btn:hover:not([disabled]) {{ background: #e0e4f5; }}
  .opt-btn.correct {{ background: #d4edda !important; border-color: #28a745 !important; }}
  .opt-btn.incorrect {{ background: #f8d7da !important; border-color: #dc3545 !important; }}
  .opt-btn.show-correct {{ background: #d4edda !important; border-color: #28a745 !important; }}
  .open-ans {{ background: #d4edda; border-radius: 7px; padding: 10px; font-size: 14px; margin-top: 6px; }}
  #cont-btn {{
    background: #ff4b4b; color: white; border: none;
    padding: 12px 28px; font-size: 15px; border-radius: 8px;
    cursor: pointer; margin-top: 4px; display: none;
  }}
</style>
<div id="yt-wrap">
  <div id="yt-player"></div>
  <div id="quiz-overlay">
    <div id="seg-info"></div>
    <div id="qs-container"></div>
    <button id="cont-btn" onclick="continueVideo()">{continue_label}</button>
  </div>
</div>
<script>
var pauseData = {pause_data_json};
var pIdx = 0;
var player;
var timer = null;

(function() {{
  var s = document.createElement('script');
  s.src = 'https://www.youtube.com/iframe_api';
  document.head.appendChild(s);
}})();

function onYouTubeIframeAPIReady() {{
  player = new YT.Player('yt-player', {{
    height: '360', width: '100%', videoId: '{video_id}',
    playerVars: {{ playsinline: 1, rel: 0 }},
    events: {{ onReady: function() {{ startTimer(); }} }}
  }});
}}

function startTimer() {{
  if (timer) clearInterval(timer);
  timer = setInterval(function() {{
    if (pIdx >= pauseData.length) {{ clearInterval(timer); return; }}
    var cur = player.getCurrentTime ? player.getCurrentTime() : 0;
    if (cur >= pauseData[pIdx].end_sec) {{
      player.pauseVideo();
      clearInterval(timer);
      showQuiz(pauseData[pIdx]);
    }}
  }}, 500);
}}

function showQuiz(pd) {{
  var overlay = document.getElementById('quiz-overlay');
  var info = document.getElementById('seg-info');
  var container = document.getElementById('qs-container');
  var contBtn = document.getElementById('cont-btn');

  info.textContent = '{segment_label} ' + pd.segment_id + (pd.summary ? ' — ' + pd.summary : '');
  container.innerHTML = '';
  contBtn.style.display = 'none';

  var total = pd.questions.length;
  var answered = 0;

  function checkDone() {{
    if (answered >= total || total === 0) contBtn.style.display = 'inline-block';
  }}

  pd.questions.forEach(function(q, qi) {{
    var card = document.createElement('div');
    card.className = 'q-card';
    var qtxt = document.createElement('div');
    qtxt.className = 'q-text';
    qtxt.textContent = (qi + 1) + '. ' + q.question;
    card.appendChild(qtxt);

    if (q.options && q.options.length > 0) {{
      var btns = [];
      q.options.forEach(function(opt, oi) {{
        var btn = document.createElement('button');
        btn.className = 'opt-btn';
        btn.textContent = opt;
        btn.onclick = function() {{
          if (btn.getAttribute('disabled')) return;
          btns.forEach(function(b) {{ b.setAttribute('disabled', '1'); }});
          if (oi === q.answer_index) {{
            btn.classList.add('correct');
          }} else {{
            btn.classList.add('incorrect');
            if (btns[q.answer_index]) btns[q.answer_index].classList.add('show-correct');
          }}
          answered++;
          checkDone();
        }};
        btns.push(btn);
        card.appendChild(btn);
      }});
    }} else {{
      var ans = document.createElement('div');
      ans.className = 'open-ans';
      ans.textContent = '✅ ' + (q.answer || '');
      card.appendChild(ans);
      answered++;
    }}
    container.appendChild(card);
  }});

  checkDone();
  overlay.classList.add('visible');
}}

function continueVideo() {{
  pIdx++;
  document.getElementById('quiz-overlay').classList.remove('visible');
  startTimer();
  player.playVideo();
}}
</script>
'''


def _show_material_viewer(mat):
    """Show material title, description and YouTube viewing options for quiz-takers."""
    from views.materials import MATERIAL_ICONS
    from helpers import _extract_youtube_id

    mat_id = mat["id"]
    url = mat.get("url", "")
    title = mat.get("title") or t("no_title")
    icon = MATERIAL_ICONS.get(mat.get("material_type", ""), "📎")
    is_youtube = mat.get("material_type") == "youtube_video"
    video_id = _extract_youtube_id(url) if is_youtube else None

    st.markdown(f"**{icon} {title}**")
    if mat.get("description"):
        st.caption(mat["description"])

    if not video_id:
        if url:
            st.link_button("🔗", url, help=t("mat_open_youtube_btn"))
        return

    view_key = f"mat_view_{mat_id}"
    has_segments = bool(mat.get("segments_json"))

    col_yt, col_app, col_quiz, col_rest = st.columns([1, 1, 1, 6])
    with col_yt:
        st.link_button("🔗", url, help=t("mat_open_youtube_btn"))
    with col_app:
        active = st.session_state.get(view_key) == "simple"
        if st.button("👁", key=f"mat_watch_{mat_id}",
                     type="primary" if active else "secondary",
                     help=t("mat_watch_in_app_btn")):
            st.session_state[view_key] = None if active else "simple"
            st.rerun()
    with col_quiz:
        active_q = st.session_state.get(view_key) == "questions"
        if st.button("🧠", key=f"mat_watch_q_{mat_id}",
                     type="primary" if active_q else "secondary",
                     disabled=not has_segments,
                     help=t("mat_watch_with_questions_btn") if has_segments else t("mat_no_segments_hint")):
            st.session_state[view_key] = None if active_q else "questions"
            st.rerun()

    current_view = st.session_state.get(view_key)
    if current_view == "simple":
        st.components.v1.html(
            f'<iframe width="100%" height="360" src="https://www.youtube.com/embed/{video_id}"'
            f' frameborder="0" allowfullscreen></iframe>',
            height=370,
        )
    elif current_view == "questions":
        html = _build_youtube_quiz_html(mat)
        if html:
            st.components.v1.html(html, height=520, scrolling=True)


def show_test_config():
    """Show configuration for the selected test before starting."""
    test_id = st.session_state.get("selected_test")
    if not test_id:
        st.session_state.page = "Tests"
        st.rerun()
        return

    test = get_test(test_id)
    if not test:
        st.error(t("test_not_found"))
        return

    # Access control for private/hidden tests (restricted and public are open to everyone)
    visibility = test.get("visibility", "public")
    if visibility not in ("public", "restricted"):
        logged_in_uid = st.session_state.get("user_id")
        has_test_access = (
            logged_in_uid and (
                test["owner_id"] == logged_in_uid
                or get_user_role_for_test(test_id, logged_in_uid) is not None
            )
        )
        if not has_test_access:
            st.error(t("test_private_no_access"))
            if st.button(t("back_to_tests")):
                del st.session_state.selected_test
                st.session_state.page = "Tests"
                st.rerun()
            return

    questions = get_test_questions(test_id)
    mat_questions = get_test_material_questions(test_id)
    questions = questions + mat_questions  # combined pool
    tags = sorted({q["tag"] for q in questions} | set(get_test_tags(test_id)))

    st.header(test["title"])
    if test.get("description"):
        st.write(test["description"])
    caption_parts = []
    if test.get("author"):
        caption_parts.append(t("author", name=test['author']))
    if test.get("language"):
        caption_parts.append(_lang_display(test['language']))
    if caption_parts:
        st.caption("  ·  ".join(caption_parts))

    # Show materials if any (but not for 'restricted' visibility unless user has explicit access)
    materials = get_materials_for_test(test_id)
    show_materials = True
    program_visibility = st.session_state.get("test_program_visibility", "public")
    effective_visibility = get_effective_visibility(visibility, program_visibility)
    if effective_visibility == "restricted":
        logged_in_uid = st.session_state.get("user_id")
        user_role = get_user_role_for_test(test_id, logged_in_uid) if logged_in_uid else None
        is_owner = logged_in_uid and test["owner_id"] == logged_in_uid
        can_see_materials = _is_global_admin() or is_owner or user_role is not None
        show_materials = can_see_materials
    if materials and show_materials:
        from views.materials import MATERIAL_ICONS
        from helpers import _extract_youtube_id
        with st.expander(t("reference_materials", n=len(materials)), expanded=True):
            for mat in materials:
                _show_material_viewer(mat)
                st.divider()

    is_owner = _is_logged_in() and test["owner_id"] == st.session_state.user_id
    can_edit = _is_global_admin() or is_owner or (
        _is_logged_in() and get_user_role_for_test(test_id, st.session_state.user_id) in ("guest", "reviewer", "admin")
    )
    # Students can take the test but not view the editor
    num_cols = 1 + (2 if can_edit else 0)
    cols_buttons = st.columns(num_cols)
    col_idx = 0
    with cols_buttons[col_idx]:
        if st.button(t("back_to_tests")):
            del st.session_state.selected_test
            st.session_state.page = "Tests"
            st.rerun()
    if can_edit:
        col_idx += 1
        with cols_buttons[col_idx]:
            if st.button(t("edit_test")):
                st.session_state.editing_test_id = test_id
                st.session_state.page = "Editar Test"
                st.rerun()
        col_idx += 1
        with cols_buttons[col_idx]:
            import json as _json
            # Build full export with materials and question-material links
            all_q_db_ids = [q["db_id"] for q in questions if q.get("db_id") is not None]
            all_q_mat_links = get_question_material_links_bulk(all_q_db_ids) if all_q_db_ids else {}
            export_materials = []
            for mat in materials:
                export_mat = {
                    "id": mat["id"],
                    "material_type": mat["material_type"],
                    "title": mat.get("title", ""),
                    "url": mat.get("url", ""),
                    "pause_times": mat.get("pause_times", ""),
                }
                export_materials.append(export_mat)
            export_questions = []
            for q in questions:
                eq = {
                    "id": q["id"],
                    "tag": q["tag"],
                    "question": q["question"],
                    "options": q["options"],
                    "answer_index": q["answer_index"],
                    "explanation": q.get("explanation", ""),
                }
                links = all_q_mat_links.get(q["db_id"], [])
                if links:
                    eq["material_refs"] = [
                        {"material_id": lk["material_id"], "context": lk.get("context", "")}
                        for lk in links
                    ]
                export_questions.append(eq)
            export_collabs = []
            for c in get_collaborators(test_id):
                export_collabs.append({"email": c["email"], "role": c["role"]})
            export_tags = get_test_tags(test_id)
            export_data = {
                "title": test["title"],
                "description": test.get("description", ""),
                "author": test.get("author", ""),
                "language": test.get("language", ""),
                "visibility": test.get("visibility", "public"),
                "tags": export_tags,
                "materials": export_materials,
                "collaborators": export_collabs,
                "questions": export_questions,
            }
            st.download_button(
                t("export_json"),
                data=_json.dumps(export_data, ensure_ascii=False, indent=2),
                file_name=f"{test['title']}.json",
                mime="application/json",
                key="export_test_json",
            )

    # --- Global Statistics Section ---
    if _is_logged_in():
        topic_stats = get_topic_statistics(st.session_state.user_id, test_id)
        if topic_stats:
            # Calculate global totals
            total_answered = sum(s["total"] for s in topic_stats.values())
            total_correct = sum(s["correct"] for s in topic_stats.values())
            total_incorrect = sum(s["incorrect"] for s in topic_stats.values())
            overall_pct = round(100 * total_correct / total_answered, 1) if total_answered > 0 else 0

            st.subheader(t("your_progress"))
            col1, col2, col3, col4 = st.columns(4)
            col1.metric(t("total_answered"), total_answered)
            col2.metric(t("correct_answers"), total_correct)
            col3.metric(t("incorrect_answers"), total_incorrect)
            col4.metric(t("overall_score"), f"{overall_pct}%")

            # Find best and worst topics
            if len(topic_stats) > 1:
                sorted_topics = sorted(topic_stats.items(), key=lambda x: x[1]["percent_correct"], reverse=True)
                best_tag, best_stats = sorted_topics[0]
                worst_tag, worst_stats = sorted_topics[-1]
                best_display = best_tag.replace("_", " ").title()
                worst_display = worst_tag.replace("_", " ").title()

                col_best, col_worst = st.columns(2)
                col_best.metric(t("best_topic"), best_display, f"{best_stats['percent_correct']}%")
                col_worst.metric(t("worst_topic"), worst_display, f"{worst_stats['percent_correct']}%")

            # Bar chart comparing topics
            if len(topic_stats) > 1:
                import pandas as pd
                chart_data = []
                for tag, stats in topic_stats.items():
                    tag_display = tag.replace("_", " ").title()
                    chart_data.append({
                        "topic": tag_display,
                        t("correct_answers"): stats["correct"],
                        t("incorrect_answers"): stats["incorrect"]
                    })
                df = pd.DataFrame(chart_data)
                st.bar_chart(df, x="topic", y=[t("correct_answers"), t("incorrect_answers")], height=200)

            st.divider()

    st.subheader(t("configuration"))

    if not questions:
        st.info(t("no_questions"))
        return

    num_questions = st.number_input(
        t("num_questions"),
        min_value=1,
        max_value=len(questions),
        value=min(25, len(questions))
    )

    level_options = ["easy", "difficult"]
    level_labels = {"easy": t("level_easy"), "difficult": t("level_difficult")}
    quiz_level = st.selectbox(
        t("level"),
        options=level_options,
        format_func=lambda x: level_labels[x],
        key="quiz_level",
    )

    st.write(t("topics_to_include"))

    # Get topic statistics if user is logged in
    topic_stats = {}
    if _is_logged_in():
        topic_stats = get_topic_statistics(st.session_state.user_id, test_id)

    # Helper to get performance circle based on percent correct
    def _get_performance_circle(pct):
        if pct >= 95:
            return "🟢"  # Green: excellent (>= 95% correct)
        elif pct >= 80:
            return "🟡"  # Yellow: good (80-95% correct)
        elif pct >= 50:
            return "🟠"  # Orange: needs work (50-80% correct)
        else:
            return "🔴"  # Red: struggling (< 50% correct)

    selected_tags = []
    for tag in tags:
        tag_display = tag.replace("_", " ").title()
        stats = topic_stats.get(tag, {})

        # Build label with colored circle and stats summary
        if stats and stats.get("total", 0) > 0:
            pct = stats.get("percent_correct", 0)
            total = stats.get("total", 0)
            circle = _get_performance_circle(pct)
            label = f"{circle} {tag_display} — {pct}% ({total} {t('questions_answered').lower()})"
        else:
            label = f"⚪ {tag_display}"  # Grey circle: no stats yet

        with st.expander(label, expanded=False):
            col_check, col_practice, col_stats = st.columns([1, 1, 2])
            with col_check:
                if st.checkbox(t("select"), value=True, key=f"tag_{tag}"):
                    selected_tags.append(tag)

            with col_practice:
                # Button to start a test focused on this topic only
                if st.button(t("practice_topic"), key=f"practice_{tag}"):
                    # Start test with only this topic selected
                    logged_in = _is_logged_in()
                    topic_questions = [q for q in questions if q["tag"] == tag]
                    if topic_questions:
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
                        st.rerun()

            with col_stats:
                if stats and stats.get("total", 0) > 0:
                    # Summary metrics
                    m1, m2, m3 = st.columns(3)
                    m1.metric(t("correct_answers"), stats["correct"])
                    m2.metric(t("incorrect_answers"), stats["incorrect"])
                    m3.metric(t("percent_correct"), f"{stats['percent_correct']}%")

                    # Progress over time chart
                    history = stats.get("history", [])
                    if len(history) > 1:
                        import pandas as pd
                        st.caption(t("progress_over_time"))
                        df = pd.DataFrame(history)
                        df["date"] = pd.to_datetime(df["date"])
                        st.line_chart(df, x="date", y="percent", height=150)
                    elif len(history) == 1:
                        st.caption(f"{t('progress_over_time')}: {history[0]['percent']}%")
                else:
                    st.caption(t("no_stats_yet"))

    if not selected_tags:
        st.warning(t("select_at_least_one_topic"))
    else:
        filtered_count = len([q for q in questions if q["tag"] in selected_tags])
        st.info(t("available_questions_with_topics", n=filtered_count))

        if st.button(t("start_test"), type="primary"):
            logged_in = _is_logged_in()
            stats = get_question_stats(st.session_state.user_id, test_id) if logged_in else None
            quiz_questions = select_balanced_questions(
                questions, selected_tags, num_questions, stats
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
            st.session_state.current_session_id = session_id
            st.session_state.active_quiz_level = quiz_level
            st.session_state.quiz_started = True
            st.session_state.page = "Tests"
            st.rerun()


def show_quiz():
    """Show the active quiz flow."""
    questions = st.session_state.questions
    current_index = st.session_state.current_index

    if current_index >= len(questions):
        current_round = st.session_state.get("current_round", 1)
        score = st.session_state.score
        total = len(questions)
        wrong = st.session_state.get("wrong_questions", [])

        # Update session score in DB
        session_id = st.session_state.get("current_session_id")
        if _is_logged_in() and session_id and not st.session_state.get("session_score_saved"):
            update_session_score(session_id, score, total)
            st.session_state.session_score_saved = True

        # Save current round to history if not already saved
        history = st.session_state.get("round_history", [])
        if len(history) < current_round:
            history.append({
                "round": current_round,
                "score": score,
                "total": total,
                "wrong": list(wrong),
            })
            st.session_state.round_history = history

        st.header(t("round_completed"))

        # Current round result
        percentage = (score / total) * 100
        st.subheader(t("round_n", n=current_round))
        st.metric(t("score_label"), f"{score}/{total} ({percentage:.1f}%)")

        if percentage >= 80:
            st.success(t("excellent"))
        elif percentage >= 60:
            st.info(t("good_job"))
        else:
            st.warning(t("keep_practicing"))

        # Accumulated summary across all rounds
        if len(history) > 1:
            st.divider()
            st.subheader(t("accumulated_summary"))
            total_all = sum(r["total"] for r in history)
            correct_all = sum(r["score"] for r in history)
            pct_all = (correct_all / total_all) * 100
            st.metric(t("accumulated_total"), f"{correct_all}/{total_all} ({pct_all:.1f}%)")

            for r in history:
                r_pct = (r["score"] / r["total"]) * 100
                icon = "✓" if r_pct == 100 else "○"
                st.write(f"{icon} **{t('round_n', n=r['round'])}:** {r['score']}/{r['total']} ({r_pct:.1f}%)")

        # Show wrong questions from current round
        if wrong:
            st.divider()
            st.subheader(t("wrong_questions_round", n=len(wrong)))
            for i, q in enumerate(wrong, 1):
                tag_display = q["tag"].replace("_", " ").title()
                with st.expander(f"{i}. {q['question']}"):
                    st.caption(t("topic", name=tag_display))
                    correct = q["options"][q["answer_index"]]
                    st.success(t("correct_answer", answer=correct))
                    st.info(t("explanation", text=q['explanation']))
                    _render_material_refs(q["db_id"], st.session_state.current_test_id)

            col1, col2 = st.columns(2)
            with col1:
                if st.button(t("retry_wrong"), type="primary"):
                    next_round = current_round + 1
                    random.shuffle(wrong)
                    new_session_id = None
                    if _is_logged_in():
                        new_session_id = create_session(
                            st.session_state.user_id,
                            st.session_state.current_test_id,
                            0, len(wrong),
                        )
                    st.session_state.questions = shuffle_question_options(wrong)
                    st.session_state.current_index = 0
                    st.session_state.score = 0
                    st.session_state.answered = False
                    st.session_state.selected_answer = None
                    st.session_state.wrong_questions = []
                    st.session_state.current_round = next_round
                    st.session_state.current_session_id = new_session_id
                    st.session_state.session_score_saved = False
                    st.rerun()
            with col2:
                if st.button(t("back_to_start")):
                    reset_quiz()
                    st.rerun()
        else:
            if st.button(t("back_to_start")):
                reset_quiz()
                st.rerun()
        return

    question = questions[current_index]

    col1, col2 = st.columns([3, 1])
    with col1:
        st.progress((current_index) / len(questions))
    with col2:
        st.write(t("question_n_of", current=current_index + 1, total=len(questions)))

    st.subheader(question["question"])

    tag_display = question["tag"].replace("_", " ").title()
    st.caption(t("topic", name=tag_display))

    is_difficult = st.session_state.get("active_quiz_level") == "difficult"

    if not st.session_state.answered:
        if is_difficult:
            user_text = st.text_input(t("your_answer"), key=f"open_answer_{current_index}")
            if st.button(t("submit_answer"), type="primary", key=f"submit_{current_index}"):
                correct_text = question["options"][question["answer_index"]]
                is_correct = user_text.strip().lower() == correct_text.strip().lower()
                st.session_state.selected_answer = user_text.strip()
                st.session_state.answered = True
                if is_correct:
                    st.session_state.score += 1
                else:
                    st.session_state.wrong_questions.append(question)
                if _is_logged_in() and question.get("db_id") is not None:
                    record_answer(
                        st.session_state.user_id,
                        st.session_state.current_test_id,
                        question["id"],
                        is_correct,
                        st.session_state.get("current_session_id"),
                    )
                st.rerun()
        else:
            for i, option in enumerate(question["options"]):
                if st.button(option, key=f"option_{i}", width="stretch"):
                    st.session_state.selected_answer = i
                    st.session_state.answered = True
                    is_correct = i == question["answer_index"]
                    if is_correct:
                        st.session_state.score += 1
                    else:
                        st.session_state.wrong_questions.append(question)
                    if _is_logged_in() and question.get("db_id") is not None:
                        record_answer(
                            st.session_state.user_id,
                            st.session_state.current_test_id,
                            question["id"],
                            is_correct,
                            st.session_state.get("current_session_id"),
                        )
                    st.rerun()

    else:
        correct_index = question["answer_index"]
        correct_text = question["options"][correct_index]

        if is_difficult:
            user_text = st.session_state.selected_answer
            is_correct = user_text.lower() == correct_text.strip().lower()
            if is_correct:
                st.success(t("correct"))
            else:
                st.error(t("incorrect"))
                st.write(f"**{t('your_answer')}** {user_text}")
            st.success(t("correct_answer", answer=correct_text))
        else:
            selected = st.session_state.selected_answer
            for i, option in enumerate(question["options"]):
                if i == correct_index:
                    st.success(f"✓ {option}")
                elif i == selected and selected != correct_index:
                    st.error(f"✗ {option}")
                else:
                    st.write(f"  {option}")

            if selected == correct_index:
                st.success(t("correct"))
            else:
                st.error(t("incorrect"))

        st.info(t("explanation", text=question['explanation']))
        if question.get("db_id") is not None:
            _render_material_refs(question["db_id"], st.session_state.current_test_id)

        if st.button(t("next_question"), type="primary"):
            st.session_state.current_index += 1
            st.session_state.answered = False
            st.session_state.selected_answer = None
            st.rerun()

    st.divider()
    if st.button(t("abandon_test")):
        reset_quiz()
        st.rerun()
