import streamlit as st
import random
import base64
import os
from translations import t
from db import (
    init_db, user_exists, get_or_create_google_user, record_answer, get_question_stats,
    create_session, update_session_score, get_user_sessions,
    get_session_wrong_answers, get_topic_statistics, get_tests_performance,
    get_user_test_ids, get_user_session_count, get_user_program_ids, get_programs_performance,
    get_user_profile, update_user_profile, delete_user_account,
    get_user_global_role, set_user_global_role, set_user_global_role_by_email, get_all_users_with_roles,
    toggle_favorite, get_favorite_tests,
    get_all_tests, get_test, get_test_questions, get_test_questions_by_ids,
    get_test_tags, add_test_tag, rename_test_tag, delete_test_tag, create_test, update_test, delete_test, import_test_from_json,
    add_question, update_question, delete_question, get_next_question_num,
    get_test_materials, get_material_by_id, add_test_material, update_test_material, delete_test_material, update_material_transcript, update_material_pause_times,
    get_question_material_links, get_question_material_links_bulk, set_question_material_links,
    create_program, update_program, delete_program, get_program,
    get_all_programs, add_test_to_program, remove_test_from_program, update_program_test_visibility,
    get_program_tests, get_program_questions, get_program_tags, get_visibility_options_for_test, get_effective_visibility,
    add_collaborator, remove_collaborator, update_collaborator_role,
    get_collaborators, get_user_role_for_test, has_direct_test_access, get_shared_tests,
    resolve_collaborator_user_id,
    add_program_collaborator, remove_program_collaborator, update_program_collaborator_role,
    get_program_collaborators, get_user_role_for_program, get_shared_programs,
    get_pending_invitations,
    accept_test_invitation, decline_test_invitation,
    accept_program_invitation, decline_program_invitation,
    # Survey functions
    create_survey, update_survey, delete_survey, get_survey, get_all_surveys,
    set_active_survey, get_active_periodic_survey, get_active_initial_survey,
    add_survey_question, update_survey_question, delete_survey_question, get_survey_questions, get_next_survey_question_num,
    submit_survey_response, has_completed_survey, get_survey_responses, get_survey_response_answers, get_survey_answer_statistics,
    get_user_survey_status, create_user_survey_status, update_user_survey_status,
    revoke_survey_based_access, approve_knowter_access,
    get_users_pending_approval, get_users_needing_survey, get_users_with_overdue_surveys, get_pending_approval_count,
)

init_db()

# Set initial admin user (only this one is hardcoded, others managed via admin panel)
set_user_global_role_by_email("mcharcos@socib.es", "admin")


def _is_logged_in():
    """Return True if user is authenticated."""
    return bool(st.session_state.get("user_id"))


def _try_login():
    """Attempt to log in the user silently, supporting both st.user and st.experimental_user.

    For new users (not in database), stores pending registration info and requires
    terms acceptance before creating the account.
    """
    if st.session_state.get("user_id"):
        return

    user_info = getattr(st, "user", getattr(st, "experimental_user", None))

    if user_info and hasattr(user_info, "is_logged_in"):
        try:
            if user_info.is_logged_in:
                email = user_info.email
                name = getattr(user_info, "name", email) or email

                # Check if user already exists
                if user_exists(email):
                    # Existing user - log in directly
                    user_id = get_or_create_google_user(email, name)
                    resolve_collaborator_user_id(email, user_id)
                    st.session_state.user_id = user_id
                    st.session_state.username = name
                else:
                    # New user - store pending registration for terms acceptance
                    st.session_state.pending_registration = {
                        "email": email,
                        "name": name,
                    }
        except Exception as e:
            st.warning(t("auth_not_configured", e=e))


def _difficulty_score(q, question_stats):
    """Return a score that prioritizes questions the user gets wrong more often."""
    stats = question_stats.get(q["id"])
    if stats is None:
        return 0.5
    total = stats["correct"] + stats["wrong"]
    if total == 0:
        return 0.5
    return stats["wrong"] / total


def select_balanced_questions(questions, selected_tags, num_questions, question_stats=None):
    """Select questions balanced across selected tags, prioritizing difficult ones."""
    filtered = [q for q in questions if q["tag"] in selected_tags]

    if not filtered:
        return []

    if num_questions >= len(filtered):
        random.shuffle(filtered)
        return filtered

    questions_by_tag = {}
    for q in filtered:
        tag = q["tag"]
        if tag not in questions_by_tag:
            questions_by_tag[tag] = []
        questions_by_tag[tag].append(q)

    for tag in questions_by_tag:
        if question_stats:
            questions_by_tag[tag].sort(
                key=lambda q: _difficulty_score(q, question_stats),
                reverse=True,
            )
        else:
            random.shuffle(questions_by_tag[tag])

    selected = []
    tag_list = list(questions_by_tag.keys())
    tag_index = 0

    while len(selected) < num_questions:
        tag = tag_list[tag_index % len(tag_list)]
        if questions_by_tag[tag]:
            selected.append(questions_by_tag[tag].pop(0))
        else:
            tag_list.remove(tag)
            if not tag_list:
                break
        tag_index += 1

    random.shuffle(selected)
    return selected


def shuffle_question_options(questions):
    """Shuffle options for each question, updating answer_index accordingly."""
    for q in questions:
        correct_option = q["options"][q["answer_index"]]
        shuffled = list(q["options"])
        random.shuffle(shuffled)
        q["options"] = shuffled
        q["answer_index"] = shuffled.index(correct_option)
    return questions


def reset_quiz():
    """Reset quiz state."""
    for key in ["quiz_started", "questions", "current_index", "answered",
                "score", "show_result", "selected_answer", "wrong_questions",
                "round_history", "current_round", "current_test_id",
                "current_session_id", "session_score_saved", "active_quiz_level"]:
        if key in st.session_state:
            del st.session_state[key]


LANGUAGE_OPTIONS = ["", "es", "en", "fr", "ca", "de", "pt", "it"]
LANGUAGE_KEYS = {"": "", "es": "lang_es", "en": "lang_en", "fr": "lang_fr", "ca": "lang_ca", "de": "lang_de", "pt": "lang_pt", "it": "lang_it"}
LANGUAGE_FLAGS = {"es": "🇪🇸", "en": "🇬🇧", "fr": "🇫🇷", "ca": "🇦🇩", "de": "🇩🇪", "pt": "🇵🇹", "it": "🇮🇹"}
UI_LANGUAGES = ["es", "en", "fr", "ca"]
UI_LANG_LABELS = {"es": "🇪🇸 ES", "en": "🇬🇧 EN", "fr": "🇫🇷 FR", "ca": "🇦🇩 CA"}


def _fetch_youtube_transcript(url):
    """Fetch transcript for a YouTube video. Returns formatted text or empty string."""
    import re
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return ""
    # Extract video ID
    m = re.search(r'(?:v=|youtu\.be/|/embed/|/v/)([a-zA-Z0-9_-]{11})', url)
    if not m:
        return ""
    video_id = m.group(1)
    try:
        api = YouTubeTranscriptApi()
        lang = st.session_state.get("lang", "es")
        try:
            result = api.fetch(video_id, languages=[lang, "en", "es", "fr", "ca"])
        except Exception:
            result = api.fetch(video_id)
        lines = []
        for snippet in result.snippets:
            mins = int(snippet.start) // 60
            secs = int(snippet.start) % 60
            lines.append(f"[{mins}:{secs:02d}] {snippet.text}")
        return "\n".join(lines)
    except Exception:
        return ""


@st.dialog(t("transcript"), width="large")
def _show_transcript_dialog(transcript_text):
    st.text_area(t("transcript"), value=transcript_text, height=400, disabled=True)


def _extract_youtube_id(url):
    """Extract YouTube video ID from various URL formats."""
    import re
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        r'(?:youtube\.com/v/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _seconds_to_mmss(seconds):
    """Convert seconds to mm:ss format."""
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _mmss_to_seconds(mmss):
    """Convert mm:ss or m:ss format to seconds."""
    import re
    match = re.match(r'^(\d+):(\d{1,2})$', mmss.strip())
    if match:
        m, s = int(match.group(1)), int(match.group(2))
        return m * 60 + s
    return None


def _show_pause_time_editor_inline(material_id, youtube_url, current_pause_times):
    """Inline pause time editor (replaces broken @st.dialog)."""
    import json as _json

    state_key = f"editing_pause_times_{material_id}"

    # Initialize from database on first open
    if state_key not in st.session_state or st.session_state.get("_pause_dialog_mat_id") != material_id:
        pause_list = []
        if current_pause_times:
            try:
                parsed = _json.loads(current_pause_times)
                for item in parsed:
                    pause_list.append({"t": item["t"], "n": item.get("n", 1)})
            except:
                pass
        st.session_state[state_key] = pause_list
        st.session_state["_pause_dialog_mat_id"] = material_id

    pause_times = st.session_state[state_key]

    st.subheader(t("pause_time_selector_title"))

    # Embed YouTube video with time capture capability
    video_id = _extract_youtube_id(youtube_url)
    if video_id:
        video_html = f'''
        <style>
            #player-container {{ width: 100%; }}
            #capture-btn {{
                background-color: #ff4b4b; color: white; border: none;
                padding: 12px 24px; font-size: 16px; border-radius: 8px;
                cursor: pointer; margin-top: 10px;
                display: inline-flex; align-items: center; gap: 8px;
            }}
            #capture-btn:hover {{ background-color: #ff3333; }}
            #time-display {{
                display: inline-block; font-size: 24px; font-weight: bold;
                color: #333; font-family: monospace; background: #f0f2f6;
                padding: 10px 20px; border-radius: 8px; min-width: 80px; text-align: center;
            }}
            #capture-controls {{ margin-top: 12px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
            #adding-feedback {{
                background: #d4edda; border: 2px solid #28a745; color: #155724;
                padding: 8px 16px; border-radius: 8px; font-size: 16px;
                display: none; margin-top: 10px; text-align: center;
            }}
            #captured-time-display {{ font-size: 28px; font-weight: bold; font-family: monospace; }}
        </style>
        <div id="player-container">
            <div id="player"></div>
            <div id="capture-controls">
                <button id="capture-btn" onclick="captureTime()">⏱️ {t("mark_pause_time")}</button>
                <span id="time-display">0:00</span>
            </div>
            <div id="adding-feedback">
                ✓ <span id="captured-time-display"></span> {t("copied")}! → {t("paste_below")}
            </div>
        </div>
        <script>
            var player; var timeUpdateInterval;
            var tag = document.createElement('script');
            tag.src = "https://www.youtube.com/iframe_api";
            var firstScriptTag = document.getElementsByTagName('script')[0];
            firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);
            function onYouTubeIframeAPIReady() {{
                player = new YT.Player('player', {{
                    height: '315', width: '100%', videoId: '{video_id}',
                    playerVars: {{ 'playsinline': 1, 'rel': 0 }},
                    events: {{ 'onReady': onPlayerReady }}
                }});
            }}
            function onPlayerReady(event) {{
                updateTimeDisplay();
                timeUpdateInterval = setInterval(updateTimeDisplay, 500);
            }}
            function formatTime(seconds) {{
                var mins = Math.floor(seconds / 60);
                var secs = seconds % 60;
                return mins + ":" + (secs < 10 ? "0" : "") + secs;
            }}
            function updateTimeDisplay() {{
                if (player && player.getCurrentTime) {{
                    var seconds = Math.floor(player.getCurrentTime());
                    document.getElementById('time-display').textContent = formatTime(seconds);
                }}
            }}
            function captureTime() {{
                if (player && player.getCurrentTime) {{
                    var seconds = Math.floor(player.getCurrentTime());
                    player.pauseVideo();
                    var timeStr = formatTime(seconds);
                    navigator.clipboard.writeText(timeStr).then(function() {{
                        document.getElementById('captured-time-display').textContent = timeStr;
                        document.getElementById('adding-feedback').style.display = 'block';
                    }}).catch(function() {{
                        document.getElementById('captured-time-display').textContent = timeStr;
                        document.getElementById('adding-feedback').style.display = 'block';
                    }});
                }}
            }}
        </script>
        '''
        st.components.v1.html(video_html, height=460)

    # Manual time entry
    col_time, col_questions, col_add = st.columns([3, 2, 2])
    with col_time:
        new_time = st.text_input(t("time_mmss"), placeholder="0:00", key=f"pause_time_input_{material_id}")
    with col_questions:
        new_q_count = st.number_input(t("num_questions"), min_value=1, max_value=10, value=1, key=f"pause_q_count_{material_id}")
    with col_add:
        st.write("")
        if st.button(f"➕ {t('add_time')}", key=f"add_pause_time_{material_id}", type="primary"):
            seconds = _mmss_to_seconds(new_time)
            if seconds is not None:
                existing_times = [p["t"] for p in pause_times]
                if seconds not in existing_times:
                    pause_times.append({"t": seconds, "n": new_q_count})
                    pause_times.sort(key=lambda x: x["t"])
                    st.session_state[state_key] = pause_times
                    st.rerun()
                else:
                    st.warning(t("time_already_exists"))
            else:
                st.warning(t("invalid_time_format"))

    # Display marked pause times
    if pause_times:
        for i, pt in enumerate(pause_times):
            col_display, col_q, col_del = st.columns([2, 2, 1])
            with col_display:
                st.write(f"⏱️ **{_seconds_to_mmss(pt['t'])}**")
            with col_q:
                new_n = st.number_input(
                    t("questions_at_pause"),
                    min_value=1, max_value=10, value=pt["n"],
                    key=f"pause_q_{material_id}_{i}",
                    label_visibility="collapsed"
                )
                if new_n != pt["n"]:
                    pause_times[i]["n"] = new_n
                    st.session_state[state_key] = pause_times
            with col_del:
                if st.button("🗑️", key=f"del_pause_{material_id}_{i}"):
                    pause_times.pop(i)
                    st.session_state[state_key] = pause_times
                    st.rerun()
    else:
        st.info(t("no_pause_times"))

    # Save and cancel buttons
    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button(t("save_pause_times"), type="primary", key=f"save_pause_{material_id}"):
            pause_json = _json.dumps(pause_times) if pause_times else ""
            update_material_pause_times(material_id, pause_json)
            widget_key = f"edit_mat_pause_{material_id}"
            st.session_state[widget_key] = _format_pause_times(pause_json)
            if state_key in st.session_state:
                del st.session_state[state_key]
            if "_pause_dialog_mat_id" in st.session_state:
                del st.session_state["_pause_dialog_mat_id"]
            st.session_state.pop(f"_show_pause_editor_{material_id}", None)
            st.success(t("pause_times_saved"))
            st.rerun()
    with col_cancel:
        if st.button(t("cancel"), key=f"cancel_pause_{material_id}"):
            if state_key in st.session_state:
                del st.session_state[state_key]
            if "_pause_dialog_mat_id" in st.session_state:
                del st.session_state["_pause_dialog_mat_id"]
            st.session_state.pop(f"_show_pause_editor_{material_id}", None)
            st.rerun()


def _show_new_material_pause_time_inline(youtube_url):
    """Inline pause time editor for new materials (replaces broken @st.dialog)."""
    import json as _json

    if "new_material_editing_pause_times" not in st.session_state:
        st.session_state.new_material_editing_pause_times = []

    pause_times = st.session_state.new_material_editing_pause_times

    st.subheader(t("pause_time_selector_title"))

    video_id = _extract_youtube_id(youtube_url)
    if video_id:
        video_html = f'''
        <style>
            #player-container {{ width: 100%; }}
            #capture-btn {{
                background-color: #ff4b4b; color: white; border: none;
                padding: 12px 24px; font-size: 16px; border-radius: 8px;
                cursor: pointer; margin-top: 10px;
                display: inline-flex; align-items: center; gap: 8px;
            }}
            #capture-btn:hover {{ background-color: #ff3333; }}
            #time-display {{
                display: inline-block; font-size: 24px; font-weight: bold;
                color: #333; font-family: monospace; background: #f0f2f6;
                padding: 10px 20px; border-radius: 8px; min-width: 80px; text-align: center;
            }}
            #capture-controls {{ margin-top: 12px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
            #captured-box {{
                background: #d4edda; border: 2px solid #28a745; border-radius: 12px;
                padding: 15px 20px; margin-top: 15px; display: none; text-align: center;
            }}
            #captured-time {{ font-size: 32px; font-weight: bold; color: #155724; font-family: monospace; display: block; margin: 5px 0; }}
            #captured-label {{ color: #155724; font-size: 14px; }}
            #captured-hint {{ color: #666; font-size: 13px; margin-top: 8px; }}
        </style>
        <div id="player-container">
            <div id="player"></div>
            <div id="capture-controls">
                <button id="capture-btn" onclick="captureTime()">⏱️ {t("mark_pause_time")}</button>
                <span id="time-display">0:00</span>
            </div>
            <div id="captured-box">
                <span id="captured-label">✓ {t("captured_time")}</span>
                <span id="captured-time">--:--</span>
                <span id="captured-hint">{t("copy_time_hint")}</span>
            </div>
        </div>
        <script>
            var player; var timeUpdateInterval;
            var tag = document.createElement('script');
            tag.src = "https://www.youtube.com/iframe_api";
            var firstScriptTag = document.getElementsByTagName('script')[0];
            firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);
            function onYouTubeIframeAPIReady() {{
                player = new YT.Player('player', {{
                    height: '315', width: '100%', videoId: '{video_id}',
                    playerVars: {{ 'playsinline': 1, 'rel': 0 }},
                    events: {{ 'onReady': onPlayerReady }}
                }});
            }}
            function onPlayerReady(event) {{
                updateTimeDisplay();
                timeUpdateInterval = setInterval(updateTimeDisplay, 500);
            }}
            function formatTime(seconds) {{
                var mins = Math.floor(seconds / 60);
                var secs = seconds % 60;
                return mins + ":" + (secs < 10 ? "0" : "") + secs;
            }}
            function updateTimeDisplay() {{
                if (player && player.getCurrentTime) {{
                    var seconds = Math.floor(player.getCurrentTime());
                    document.getElementById('time-display').textContent = formatTime(seconds);
                }}
            }}
            function captureTime() {{
                if (player && player.getCurrentTime) {{
                    var seconds = Math.floor(player.getCurrentTime());
                    var timeStr = formatTime(seconds);
                    player.pauseVideo();
                    document.getElementById('captured-time').textContent = timeStr;
                    document.getElementById('captured-box').style.display = 'block';
                    navigator.clipboard.writeText(timeStr).catch(function() {{}});
                }}
            }}
        </script>
        '''
        st.components.v1.html(video_html, height=480)

    st.divider()
    st.markdown(f"**{t('enter_time_manually')}**")
    col_time, col_questions, col_add = st.columns([2, 2, 1])
    with col_time:
        new_time = st.text_input("", placeholder="0:00", key="new_mat_pause_time_input", label_visibility="collapsed")
    with col_questions:
        new_q_count = st.number_input(t("questions_at_pause"), min_value=1, max_value=10, value=1, key="new_mat_pause_q_count")
    with col_add:
        if st.button(t("add_time"), key="new_mat_add_pause_time_btn", type="primary"):
            seconds = _mmss_to_seconds(new_time)
            if seconds is not None:
                existing_times = [p["t"] for p in pause_times]
                if seconds not in existing_times:
                    pause_times.append({"t": seconds, "n": new_q_count})
                    pause_times.sort(key=lambda x: x["t"])
                    st.session_state.new_material_editing_pause_times = pause_times
                    st.rerun()
                else:
                    st.warning(t("time_already_exists"))
            else:
                st.warning(t("invalid_time_format"))

    if pause_times:
        for i, pt in enumerate(list(pause_times)):
            col_display, col_q, col_del = st.columns([2, 2, 1])
            with col_display:
                st.write(f"⏱️ **{_seconds_to_mmss(pt['t'])}**")
            with col_q:
                new_n = st.number_input(
                    t("questions_at_pause"),
                    min_value=1, max_value=10, value=pt["n"],
                    key=f"new_mat_pause_q_{i}",
                    label_visibility="collapsed"
                )
                if new_n != pt["n"]:
                    pause_times[i]["n"] = new_n
                    st.session_state.new_material_editing_pause_times = pause_times
            with col_del:
                if st.button("🗑️", key=f"new_mat_del_pause_{i}"):
                    pause_times.pop(i)
                    st.session_state.new_material_editing_pause_times = pause_times
                    st.rerun()
    else:
        st.info(t("no_pause_times"))

    col_save, col_cancel = st.columns(2)
    with col_save:
        if st.button(t("save_pause_times"), type="primary", key="new_mat_save_pause_times_btn"):
            st.session_state.new_material_pause_times = pause_times.copy()
            if "new_material_editing_pause_times" in st.session_state:
                del st.session_state["new_material_editing_pause_times"]
            st.session_state.pop("_show_new_mat_pause_editor", None)
            st.rerun()
    with col_cancel:
        if st.button(t("cancel"), key="new_mat_cancel_pause_times_btn"):
            if "new_material_editing_pause_times" in st.session_state:
                del st.session_state["new_material_editing_pause_times"]
            st.session_state.pop("_show_new_mat_pause_editor", None)
            st.rerun()


def _generate_topics_from_transcript(transcript_text, existing_tags=None):
    """Use Hugging Face to generate topic suggestions from a transcript."""
    import os
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        st.error(t("hf_not_installed"))
        return []
    api_key = os.environ.get("HF_API_KEY") or (st.secrets["HF_API_KEY"] if "HF_API_KEY" in st.secrets else "")
    if not api_key:
        st.error(t("hf_api_key_required"))
        return []
    model_id = os.environ.get("HF_MODEL") or (st.secrets["HF_MODEL"] if "HF_MODEL" in st.secrets else "Qwen/Qwen2.5-72B-Instruct")
    lang = st.session_state.get("lang", "es")
    lang_names = {"es": "Spanish", "en": "English", "fr": "French", "ca": "Catalan"}
    lang_name = lang_names.get(lang, "Spanish")
    existing_str = ", ".join(existing_tags) if existing_tags else "none"
    system_prompt = (
        f"You are an educational content analyzer. Extract main topics from video transcripts. "
        f"Return ONLY a list of short topic names (2-4 words each), one per line, no numbering, no bullets. "
        f"Topics should be in {lang_name}."
    )
    user_prompt = (
        f"Existing topics already in the test: {existing_str}. "
        f"Do not repeat existing topics. Suggest 5-15 new topics.\n\n"
        f"Transcript:\n{transcript_text[:6000]}"
    )
    try:
        client = InferenceClient(token=api_key)
        response = client.chat_completion(
            model=model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=500,
            temperature=0.7,
        )
        text = response.choices[0].message.content
        lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
        return lines
    except Exception as e:
        st.error(f"{t('transcript_error')} {e}")
        return []


def _show_generate_topics_inline(test_id, transcript_text, existing_tags, material_id):
    """Inline topic generator (replaces broken @st.dialog)."""
    st.subheader(t("generate_topics_title"))
    if "generated_topics" not in st.session_state:
        with st.spinner(t("generating_topics")):
            suggestions = _generate_topics_from_transcript(transcript_text, existing_tags)
        st.session_state.generated_topics = suggestions

    topics = st.session_state.generated_topics
    st.write(t("generated_topics_instructions"))

    edited_text = st.text_area(
        t("topics"),
        value="\n".join(topics),
        height=300,
        key=f"gen_topics_editor_{material_id}",
    )

    existing_set = {t_name.strip().lower() for t_name in existing_tags}
    new_topics = [line.strip() for line in edited_text.split("\n") if line.strip()]
    dupes = [tp for tp in new_topics if tp.strip().lower() in existing_set]
    if dupes:
        st.warning(t("duplicate_topics_warning", topics=", ".join(dupes)))

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button(t("confirm"), type="primary", key=f"confirm_gen_topics_{material_id}"):
            added = 0
            for topic_name in new_topics:
                if topic_name.strip().lower() not in existing_set:
                    add_test_tag(test_id, topic_name.strip())
                    existing_set.add(topic_name.strip().lower())
                    added += 1
            if "generated_topics" in st.session_state:
                del st.session_state["generated_topics"]
            st.session_state[f"_show_gen_topics_{material_id}"] = False
            st.success(t("topics_added", n=added))
            st.rerun()
    with col_cancel:
        if st.button(t("cancel"), key=f"cancel_gen_topics_{material_id}"):
            if "generated_topics" in st.session_state:
                del st.session_state["generated_topics"]
            st.session_state[f"_show_gen_topics_{material_id}"] = False
            st.rerun()


def _generate_questions_from_transcript(transcript_text, num_questions=5):
    """Use Hugging Face to generate quiz questions from a transcript."""
    import os
    import json as _json
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        st.error(t("hf_not_installed"))
        return []
    api_key = os.environ.get("HF_API_KEY") or (st.secrets["HF_API_KEY"] if "HF_API_KEY" in st.secrets else "")
    if not api_key:
        st.error(t("hf_api_key_required"))
        return []
    model_id = os.environ.get("HF_MODEL") or (st.secrets["HF_MODEL"] if "HF_MODEL" in st.secrets else "Qwen/Qwen2.5-72B-Instruct")
    lang = st.session_state.get("lang", "es")
    lang_names = {"es": "Spanish", "en": "English", "fr": "French", "ca": "Catalan"}
    lang_name = lang_names.get(lang, "Spanish")
    system_prompt = (
        f"You are a quiz question generator for educational content. "
        f"Generate multiple choice questions based on video transcripts with timestamps. "
        f"Each question must have exactly 4 options (A, B, C, D) with only one correct answer. "
        f"For each question, identify the time range in the video where the relevant content appears. "
        f"Questions and options should be in {lang_name}. "
        f"Return ONLY valid JSON array, no other text."
    )

    client = InferenceClient(token=api_key)
    all_questions = []
    # Generate in batches of up to 20 questions to stay within token limits
    batch_size = 20
    remaining = num_questions
    transcript_chunk = transcript_text[:12000]

    while remaining > 0:
        batch_count = min(remaining, batch_size)
        # Scale max_tokens: ~300 tokens per question
        max_tokens = min(batch_count * 300, 8000)

        already_generated = ""
        if all_questions:
            existing_q = [q.get("question", "")[:60] for q in all_questions[-10:]]
            already_generated = f"\nDo NOT repeat these existing questions: {'; '.join(existing_q)}\n"

        user_prompt = (
            f"Generate exactly {batch_count} multiple choice questions from this transcript. "
            f"The transcript has timestamps in format [m:ss]. For each question, include the time range "
            f"(time_start and time_end) where the relevant content appears in the video.\n"
            f"{already_generated}"
            f"Return as JSON array with this exact format:\n"
            f'[{{"question": "...", "options": ["A) ...", "B) ...", "C) ...", "D) ..."], "correct": 0, '
            f'"explanation": "...", "time_start": "0:00", "time_end": "1:30"}}]\n'
            f"where 'correct' is the index (0-3) of the correct option, and times are in m:ss format.\n\n"
            f"Transcript:\n{transcript_chunk}"
        )
        try:
            response = client.chat_completion(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.7,
            )
            text = response.choices[0].message.content.strip()
            # Try to extract JSON from response
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            batch_questions = _json.loads(text)
            if isinstance(batch_questions, list):
                all_questions.extend(batch_questions)
        except Exception as e:
            st.error(f"{t('transcript_error')} {e}")
            break

        remaining -= batch_count

    return all_questions


def _show_generate_questions_inline(test_id, material_id, transcript_text):
    """Inline question generator (replaces broken @st.dialog)."""
    st.subheader(t("generate_questions_title"))
    # Step 1: Ask how many questions to generate
    if "generated_questions" not in st.session_state:
        st.write(t("how_many_questions"))
        num_questions = st.slider("", min_value=1, max_value=500, value=5, key=f"gen_q_count_{material_id}")

        col_gen, col_cancel = st.columns(2)
        with col_gen:
            if st.button(t("generate_btn"), type="primary", key=f"start_gen_questions_{material_id}"):
                with st.spinner(t("generating_questions")):
                    questions = _generate_questions_from_transcript(transcript_text, num_questions=num_questions)
                st.session_state.generated_questions = questions
                st.rerun()
        with col_cancel:
            if st.button(t("cancel"), key=f"cancel_gen_questions_step1_{material_id}"):
                st.session_state[f"_show_gen_questions_{material_id}"] = False
                st.rerun()
        return

    # Step 2: Show generated questions
    questions = st.session_state.generated_questions
    if not questions:
        st.warning(t("no_questions_generated"))
        if st.button(t("cancel"), key=f"cancel_gen_questions_empty_{material_id}"):
            if "generated_questions" in st.session_state:
                del st.session_state["generated_questions"]
            st.session_state[f"_show_gen_questions_{material_id}"] = False
            st.rerun()
        return

    st.write(t("generated_questions_instructions"))

    # Let user select which questions to add
    selected = []
    for i, q in enumerate(questions):
        time_label = ""
        time_start = q.get("time_start", "")
        time_end = q.get("time_end", "")
        if time_start and time_end:
            time_label = f" ({time_start} - {time_end})"
        with st.expander(f"**{i+1}. {q.get('question', '')}**{time_label}", expanded=True):
            include = st.checkbox(t("include_question"), value=True, key=f"include_q_{material_id}_{i}")
            if include:
                selected.append(i)
            if time_start and time_end:
                st.caption(t("video_time_range", start=time_start, end=time_end))
            st.write(f"**{t('options')}:**")
            opts = q.get("options", [])
            correct_idx = q.get("correct", 0)
            for j, opt in enumerate(opts):
                prefix = "✓ " if j == correct_idx else "  "
                st.write(f"{prefix}{opt}")
            if q.get("explanation"):
                st.write(f"**{t('explanation')}:** {q['explanation']}")

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button(t("confirm"), type="primary", key=f"confirm_gen_questions_{material_id}"):
            added = 0
            next_num = get_next_question_num(test_id)
            for i in selected:
                q = questions[i]
                opts = q.get("options", [t("option_a"), t("option_b"), t("option_c"), t("option_d")])
                clean_opts = []
                for opt in opts:
                    opt_clean = opt.strip()
                    if len(opt_clean) > 2 and opt_clean[1] in ").]":
                        opt_clean = opt_clean[2:].strip()
                    clean_opts.append(opt_clean)
                q_id = add_question(
                    test_id, next_num + added, "general",
                    q.get("question", ""),
                    clean_opts,
                    q.get("correct", 0),
                    q.get("explanation", ""),
                    source=f"material:{material_id}",
                )
                time_start = q.get("time_start", "")
                time_end = q.get("time_end", "")
                context = f"{time_start}-{time_end}" if time_start and time_end else ""
                set_question_material_links(q_id, [{"material_id": material_id, "context": context}])
                added += 1
            del st.session_state["generated_questions"]
            st.session_state[f"_show_gen_questions_{material_id}"] = False
            st.success(t("questions_added", n=added))
            st.rerun()
    with col_cancel:
        if st.button(t("cancel"), key=f"cancel_gen_questions_{material_id}"):
            if "generated_questions" in st.session_state:
                del st.session_state["generated_questions"]
            st.session_state[f"_show_gen_questions_{material_id}"] = False
            st.rerun()


def _parse_pause_times(text):
    """Parse pause times like '1:30(2), 3:00, 5:45(3)' to JSON string of [{t, n}]."""
    import json as _j, re as _re
    if not text or not text.strip():
        return ""
    stops = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        # Extract optional (N) suffix for question count
        m = _re.match(r'^([\d:]+)\s*(?:\((\d+)\))?$', part)
        if not m:
            continue
        time_str, n_str = m.group(1), m.group(2)
        n = int(n_str) if n_str else 1
        pieces = time_str.split(":")
        try:
            if len(pieces) == 2:
                secs = int(pieces[0]) * 60 + int(pieces[1])
            else:
                secs = int(pieces[0])
        except ValueError:
            continue
        stops.append({"t": secs, "n": n})
    stops.sort(key=lambda x: x["t"])
    return _j.dumps(stops) if stops else ""


def _format_pause_times(pause_json):
    """Convert JSON pause_times back to display string like '1:30(2), 3:00'."""
    import json as _j
    if not pause_json:
        return ""
    try:
        stops = _j.loads(pause_json)
    except (ValueError, TypeError):
        return pause_json  # return as-is if not valid JSON
    # Handle old format (plain list of seconds)
    if stops and isinstance(stops[0], (int, float)):
        stops = [{"t": s, "n": 1} for s in stops]
    parts = []
    for s in stops:
        mins = int(s["t"]) // 60
        secs = int(s["t"]) % 60
        time_str = f"{mins}:{secs:02d}"
        n = s.get("n", 1)
        if n > 1:
            time_str += f"({n})"
        parts.append(time_str)
    return ", ".join(parts)


def _lang_display(code):
    """Return display label for a language code."""
    if not code:
        return ""
    flag = LANGUAGE_FLAGS.get(code, "")
    name = t(LANGUAGE_KEYS.get(code, ""), ) if code in LANGUAGE_KEYS else code
    return f"{flag} {name}".strip()


def _time_to_secs(time_str):
    """Convert a time string like '1:30' or '1:00-2:00' (uses start) to seconds."""
    time_str = time_str.strip().split("-")[0].strip()  # take start of range
    pieces = time_str.split(":")
    try:
        return int(pieces[0]) * 60 + int(pieces[1]) if len(pieces) == 2 else int(pieces[0])
    except (ValueError, IndexError):
        return 0


def _render_material_refs(question_db_id, test_id):
    """Show clickable material references for a question after explanation.

    Does not show references if effective visibility is 'restricted' and user lacks explicit access.
    For private/hidden visibility, user already has explicit access if they can see the test.
    """
    # Check if materials should be hidden due to restricted visibility
    test = get_test(test_id)
    if not test:
        return
    # Check both test visibility and program visibility (if coming from a program)
    visibility = test.get("visibility", "public")
    program_visibility = st.session_state.get("test_program_visibility", "public")
    effective_visibility = get_effective_visibility(visibility, program_visibility)
    if effective_visibility == "restricted":
        logged_in_uid = st.session_state.get("user_id")
        user_role = get_user_role_for_test(test_id, logged_in_uid) if logged_in_uid else None
        is_owner = logged_in_uid and test["owner_id"] == logged_in_uid
        # Show materials if user has any explicit access (owner, global admin, or any collaboration role)
        can_see_materials = _is_global_admin() or is_owner or user_role is not None
        if not can_see_materials:
            return  # Skip showing material references
    # For private/hidden, user already has explicit access if they can see the test

    links = get_question_material_links(question_db_id)
    if not links:
        return
    materials = get_test_materials(test_id)
    mat_by_id = {m["id"]: m for m in materials}
    type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
    refs = []
    for lk in links:
        mat = mat_by_id.get(lk["material_id"])
        if not mat:
            continue
        icon = type_icons.get(mat["material_type"], "📎")
        title = mat["title"] or mat["url"] or t("no_title")
        ctx = lk.get("context", "")
        if mat["material_type"] == "youtube" and mat.get("url"):
            if ctx:
                label = f"{icon} {title} ({ctx})"
                # Link to first timestamp (handles ranges like 1:00-2:00)
                first_time = ctx.split(",")[0].strip()
                secs = _time_to_secs(first_time)
                refs.append(f'<a href="{mat["url"]}&t={secs}" target="_blank" style="text-decoration:none;">{label}</a>')
            else:
                refs.append(f'<a href="{mat["url"]}" target="_blank" style="text-decoration:none;">{icon} {title}</a>')
        elif mat["material_type"] == "url" and mat.get("url"):
            refs.append(f'<a href="{mat["url"]}" target="_blank" style="text-decoration:none;">{icon} {title}</a>')
        elif mat["material_type"] == "pdf" and ctx:
            refs.append(f"{icon} {title} (p. {ctx})")
        else:
            refs.append(f"{icon} {title}")
    if refs:
        st.caption(t("material_references") + ": " + " · ".join(refs), unsafe_allow_html=True)


def _show_material_inline(mat, label):
    """Inline material viewer (replaces broken @st.dialog)."""
    st.subheader(label)
    if mat["material_type"] == "youtube" and mat["url"]:
        st.video(mat["url"])
    elif mat["material_type"] == "url" and mat["url"]:
        st.markdown(f"🔗 **[{mat['url']}]({mat['url']})**")
        import streamlit.components.v1 as components
        components.iframe(mat["url"], height=600, scrolling=True)
    elif mat["material_type"] == "image" and mat["file_data"]:
        st.image(mat["file_data"])
    elif mat["material_type"] == "pdf" and mat["file_data"]:
        import base64
        import streamlit.components.v1 as components
        b64 = base64.b64encode(mat["file_data"]).decode()
        pdf_html = f"""
<!DOCTYPE html>
<html>
<head>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
<style>
body {{ margin: 0; background: #525659; overflow-y: auto; }}
canvas {{ display: block; margin: 4px auto; }}
</style>
</head>
<body>
<script>
pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
var pdfData = atob("{b64}");
var uint8 = new Uint8Array(pdfData.length);
for (var i = 0; i < pdfData.length; i++) uint8[i] = pdfData.charCodeAt(i);
pdfjsLib.getDocument({{data: uint8}}).promise.then(function(pdf) {{
  for (var p = 1; p <= pdf.numPages; p++) {{
    (function(pageNum) {{
      pdf.getPage(pageNum).then(function(page) {{
        var scale = 1.5;
        var viewport = page.getViewport({{scale: scale}});
        var canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        document.body.appendChild(canvas);
        page.render({{canvasContext: canvas.getContext('2d'), viewport: viewport}});
      }});
    }})(p);
  }}
}});
</script>
</body>
</html>
"""
        components.html(pdf_html, height=620, scrolling=True)
        st.download_button(
            t("download"),
            data=mat["file_data"],
            file_name=f"{label}.pdf",
            key=f"dl_pdf_{mat['id']}",
        )
    else:
        st.write(t("no_preview"))
    if st.button(t("close"), key=f"close_mat_{mat['id']}", width="stretch"):
        for k in list(st.session_state.keys()):
            if k.startswith("show_mat_"):
                del st.session_state[k]
        st.rerun()


@st.dialog("🧠", width="large")
def _show_study_dialog(mat, label, questions):
    """Play a YouTube video, auto-pause after 60s, show question, resume on answer."""
    import re
    import json as _json
    import streamlit.components.v1 as components

    url = mat.get("url", "")
    match = re.search(r"(?:v=|youtu\.be/|embed/)([\w-]{11})", url)
    video_id = match.group(1) if match else ""

    if not video_id:
        st.video(url)
        return

    # Prepare questions JSON for JS (strip file data, include material refs)
    test_id = st.session_state.get("selected_test")
    all_materials = get_test_materials(test_id) if test_id else []
    mat_by_id = {m["id"]: m for m in all_materials}
    q_db_ids = [q["db_id"] for q in questions]
    all_links = get_question_material_links_bulk(q_db_ids) if q_db_ids else {}

    current_mat_id = mat.get("id")
    q_data = []
    for q in questions:
        refs = []
        q_timestamp = -1  # Timestamp in seconds for this question relative to current material
        for lk in all_links.get(q["db_id"], []):
            m = mat_by_id.get(lk["material_id"])
            if not m:
                continue
            type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
            icon = type_icons.get(m["material_type"], "📎")
            title = m["title"] or m["url"] or ""
            ctx = lk.get("context", "")
            url = m.get("url", "")
            # Extract timestamp if this link is for the current material
            if lk["material_id"] == current_mat_id and ctx:
                first_part = ctx.split("-")[0].strip()
                first_part = first_part.split(",")[0].strip()
                ts = _time_to_secs(first_part)
                if ts >= 0:
                    q_timestamp = ts
            if m["material_type"] == "youtube" and url and ctx:
                first = ctx.split(",")[0].strip()
                secs = _time_to_secs(first)
                refs.append({"icon": icon, "title": title, "ctx": ctx, "url": f"{url}&t={secs}"})
            elif m["material_type"] in ("youtube", "url") and url:
                refs.append({"icon": icon, "title": title, "ctx": ctx, "url": url})
            else:
                suffix = f" (p. {ctx})" if ctx else ""
                refs.append({"icon": icon, "title": title + suffix, "ctx": "", "url": ""})
        q_data.append({
            "question": q["question"], "options": q["options"],
            "answer_index": q["answer_index"], "explanation": q.get("explanation", ""),
            "tag": q.get("tag", ""), "refs": refs, "ts": q_timestamp,
        })
    q_json = _json.dumps(q_data, ensure_ascii=False)

    lang = st.session_state.get("lang", "es")
    correct_label = t("correct")
    incorrect_label = t("incorrect")
    continue_label = t("study_continue")

    # Parse configured pause times or fall back to repeating 60s
    pause_times_raw = mat.get("pause_times", "")
    if pause_times_raw:
        try:
            stops_list = _json.loads(pause_times_raw)
        except (ValueError, TypeError):
            stops_list = []
        # Handle old format (plain list of seconds) -> convert to {t, n} objects
        if stops_list and isinstance(stops_list[0], (int, float)):
            stops_list = [{"t": s, "n": 1} for s in stops_list]
    else:
        stops_list = []
    stops_json = _json.dumps(stops_list) if stops_list else "[]"

    study_html = f"""
<!DOCTYPE html>
<html><head>
<style>
body {{ margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#0e1117; color:#fafafa; }}
#player-wrap {{ position:relative; }}
#quiz-overlay {{
  display:none; position:absolute; top:0; left:0; width:100%; height:100%;
  background:rgba(14,17,23,0.95); z-index:10;
  flex-direction:column; padding:0; box-sizing:border-box;
}}
#quiz-overlay.active {{ display:flex; }}
#quiz-content {{
  flex:1; overflow-y:auto; padding:16px 16px 8px;
}}
#quiz-content h3 {{ margin:0 0 6px; font-size:1.1em; }}
#quiz-content .tag {{ color:#888; font-size:0.85em; margin-bottom:12px; }}
#btn-area {{
  flex-shrink:0; padding:8px 16px 12px;
}}
.opt-btn {{
  display:block; width:100%; padding:8px 12px; margin:3px 0; border:1px solid #444;
  border-radius:8px; background:#1a1d24; color:#fafafa; font-size:0.9em;
  cursor:pointer; text-align:left;
}}
.opt-btn:hover {{ background:#262a33; }}
.opt-btn.correct {{ background:#1b5e20; border-color:#4caf50; cursor:default; }}
.opt-btn.wrong {{ background:#b71c1c; border-color:#f44336; cursor:default; }}
.opt-btn.dimmed {{ opacity:0.5; cursor:default; }}
.result {{ margin:8px 0; padding:6px 10px; border-radius:6px; font-weight:bold; font-size:0.9em; }}
.result.ok {{ background:#1b5e20; }}
.result.fail {{ background:#b71c1c; }}
.explanation {{ margin:6px 0; padding:8px 10px; background:#1a2733; border-radius:6px; font-size:0.85em; }}
#refs-area {{ margin:6px 0; padding:8px 10px; background:#1a2733; border-radius:6px; font-size:0.8em; }}
#countdown {{ position:absolute; top:8px; right:12px; background:rgba(0,0,0,0.7); color:#fff;
  padding:4px 10px; border-radius:12px; font-size:0.85em; z-index:5; }}
.continue-btn {{
  display:block; width:100%; padding:12px 16px; border:none;
  border-radius:8px; background:#1b6ef3; color:#fff; font-size:1em; font-weight:bold;
  cursor:pointer; text-align:center;
}}
.continue-btn:hover {{ background:#1558c9; }}
</style>
</head><body>
<div id="player-wrap">
  <div id="player"></div>
  <div id="countdown"></div>
  <div id="quiz-overlay">
    <div id="quiz-content"></div>
    <div id="btn-area"></div>
  </div>
</div>
<script>
var QUESTIONS = {q_json};
var CORRECT_LABEL = {_json.dumps(correct_label)};
var INCORRECT_LABEL = {_json.dumps(incorrect_label)};
var CONTINUE_LABEL = {_json.dumps(continue_label)};
var CONFIGURED_STOPS = {stops_json};
var FALLBACK_INTERVAL = 60;

var tag = document.createElement('script');
tag.src = "https://www.youtube.com/iframe_api";
document.head.appendChild(tag);

var player, timer = null, questionShown = false;
var stopIdx = 0;
var questionsRemaining = 0;
var shownGlobalIds = [];  // tracks global QUESTIONS indices already shown
// If configured stops exist, use them; otherwise use repeating fallback
var useConfigured = CONFIGURED_STOPS.length > 0;
var prevStop = 0;
var nextStop = useConfigured ? CONFIGURED_STOPS[0].t : FALLBACK_INTERVAL;
var overlay = document.getElementById('quiz-overlay');
var countdownEl = document.getElementById('countdown');

function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    width: '100%', height: '400',
    videoId: '{video_id}',
    events: {{
      'onReady': function(e) {{ e.target.playVideo(); }},
      'onStateChange': onStateChange
    }}
  }});
}}

function onStateChange(e) {{
  if (e.data == YT.PlayerState.PLAYING) {{
    questionShown = false;
    startTimer();
  }} else {{
    stopTimer();
  }}
}}

function startTimer() {{
  stopTimer();
  if (nextStop < 0) return; // no more stops
  timer = setInterval(function() {{
    var cur = player.getCurrentTime();
    updateCountdown(cur);
    if (cur >= nextStop && !questionShown) {{
      questionShown = true;
      stopTimer();
      player.pauseVideo();
      questionsRemaining = (useConfigured && stopIdx < CONFIGURED_STOPS.length) ? CONFIGURED_STOPS[stopIdx].n : 1;
      showQuestion();
    }}
  }}, 500);
}}

function stopTimer() {{
  if (timer) {{ clearInterval(timer); timer = null; }}
}}

function updateCountdown(cur) {{
  if (nextStop < 0) {{ countdownEl.style.display = 'none'; return; }}
  var remaining = Math.max(0, Math.ceil(nextStop - cur));
  if (remaining > 0 && !questionShown) {{
    var m = Math.floor(remaining / 60);
    var s = remaining % 60;
    countdownEl.textContent = (m > 0 ? m + ':' : '') + (s < 10 ? '0' : '') + s;
    countdownEl.style.display = 'block';
  }} else {{
    countdownEl.style.display = 'none';
  }}
}}

function advanceStop() {{
  prevStop = nextStop;
  if (useConfigured) {{
    stopIdx++;
    if (stopIdx < CONFIGURED_STOPS.length) {{
      nextStop = CONFIGURED_STOPS[stopIdx].t;
    }} else {{
      nextStop = -1; // no more stops
    }}
  }} else {{
    nextStop = player.getCurrentTime() + FALLBACK_INTERVAL;
  }}
}}

function resumeVideo() {{
  overlay.classList.remove('active');
  document.getElementById('quiz-content').innerHTML = '';
  document.getElementById('btn-area').innerHTML = '';
  advanceStop();
  questionShown = false;
  player.playVideo();
}}

function getQuestionsForSegment() {{
  var currentStop = nextStop >= 0 ? nextStop : Infinity;
  var filtered = QUESTIONS.filter(function(q) {{
    if (q.ts < 0) return false;
    return q.ts >= prevStop && q.ts < currentStop;
  }});
  if (filtered.length === 0) {{
    filtered = QUESTIONS.filter(function(q) {{ return q.ts >= 0; }});
  }}
  if (filtered.length === 0) {{
    filtered = QUESTIONS;
  }}
  return filtered;
}}

function showQuestion() {{
  var contentArea = document.getElementById('quiz-content');
  var btnArea = document.getElementById('btn-area');
  var available = getQuestionsForSegment();
  // Build list of global indices for the segment questions
  var availableGlobal = available.map(function(q) {{ return QUESTIONS.indexOf(q); }});
  // Filter to unseen questions (not yet shown in this session)
  var unseenIndices = [];
  for (var i = 0; i < available.length; i++) {{
    if (shownGlobalIds.indexOf(availableGlobal[i]) === -1) {{
      unseenIndices.push(i);
    }}
  }}
  // If all segment questions were shown, reset globally and retry
  if (unseenIndices.length === 0) {{
    shownGlobalIds = [];
    for (var i = 0; i < available.length; i++) {{ unseenIndices.push(i); }}
  }}
  // Pick a random unseen question
  var pick = unseenIndices[Math.floor(Math.random() * unseenIndices.length)];
  var q = available[pick];
  shownGlobalIds.push(availableGlobal[pick]);

  var html = '<h3>' + escHtml(q.question) + '</h3>';
  html += '<div class="tag">' + escHtml(q.tag.replace(/_/g, ' ')) + '</div>';
  for (var i = 0; i < q.options.length; i++) {{
    html += '<button class="opt-btn" data-idx="' + i + '" data-correct="' + q.answer_index + '">'
          + escHtml(q.options[i]) + '</button>';
  }}
  html += '<div id="result-area"></div>';
  if (q.explanation) {{
    html += '<div class="explanation" style="display:none" id="expl-area"><b>Explanation:</b> ' + escHtml(q.explanation) + '</div>';
  }}
  if (q.refs && q.refs.length > 0) {{
    html += '<div style="display:none" id="refs-area"><b>📚 References:</b> ';
    var parts = [];
    for (var r = 0; r < q.refs.length; r++) {{
      var ref = q.refs[r];
      if (ref.url) {{
        parts.push('<a href="' + ref.url + '" target="_blank" style="color:#1a73e8;text-decoration:none;">' + ref.icon + ' ' + escHtml(ref.title) + (ref.ctx ? ' (' + escHtml(ref.ctx) + ')' : '') + '</a>');
      }} else {{
        parts.push(ref.icon + ' ' + escHtml(ref.title));
      }}
    }}
    html += parts.join(' &middot; ') + '</div>';
  }}
  contentArea.innerHTML = html;
  btnArea.innerHTML = '';
  overlay.classList.add('active');

  contentArea.querySelectorAll('.opt-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var idx = parseInt(this.dataset.idx);
      var correct = parseInt(this.dataset.correct);
      var buttons = contentArea.querySelectorAll('.opt-btn');
      buttons.forEach(function(b) {{
        var bi = parseInt(b.dataset.idx);
        if (bi === correct) b.classList.add('correct');
        else if (bi === idx && idx !== correct) b.classList.add('wrong');
        else b.classList.add('dimmed');
        b.style.pointerEvents = 'none';
      }});
      var ra = document.getElementById('result-area');
      if (idx === correct) {{
        ra.innerHTML = '<div class="result ok">' + escHtml(CORRECT_LABEL) + '</div>';
      }} else {{
        ra.innerHTML = '<div class="result fail">' + escHtml(INCORRECT_LABEL) + '</div>';
      }}
      var expl = document.getElementById('expl-area');
      if (expl) expl.style.display = 'block';
      var refsEl = document.getElementById('refs-area');
      if (refsEl) refsEl.style.display = 'block';
      questionsRemaining--;
      var continueBtn = document.createElement('button');
      continueBtn.className = 'continue-btn';
      if (questionsRemaining > 0) {{
        continueBtn.textContent = '→';
        continueBtn.addEventListener('click', function() {{
          showQuestion();
        }});
      }} else {{
        continueBtn.textContent = CONTINUE_LABEL;
        continueBtn.addEventListener('click', function() {{
          resumeVideo();
        }});
      }}
      btnArea.appendChild(continueBtn);
    }});
  }});
}}

function escHtml(s) {{
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}
</script>
</body></html>
"""
    st.info(t("study_watch"))
    components.html(study_html, height=460)

    if st.button(t("close"), key="close_study_dialog", width="stretch"):
        for k in list(st.session_state.keys()):
            if k.startswith("study_"):
                del st.session_state[k]
        st.rerun()


def _toggle_bulk_test(test_id):
    """Callback to toggle test selection in bulk delete mode."""
    if "bulk_delete_tests" not in st.session_state:
        st.session_state.bulk_delete_tests = set()
    if test_id in st.session_state.bulk_delete_tests:
        st.session_state.bulk_delete_tests.discard(test_id)
    else:
        st.session_state.bulk_delete_tests.add(test_id)

def _toggle_bulk_question(db_id):
    """Callback to toggle question selection in bulk delete mode."""
    if "bulk_delete_questions" not in st.session_state:
        st.session_state.bulk_delete_questions = set()
    if db_id in st.session_state.bulk_delete_questions:
        st.session_state.bulk_delete_questions.discard(db_id)
    else:
        st.session_state.bulk_delete_questions.add(db_id)


def _get_test_export_data(test_id):
    """Generate export data for a test. Returns (json_string, title)."""
    import json as _json
    test = get_test(test_id)
    if not test:
        return "{}", "unknown"
    questions = get_test_questions(test_id)
    materials = get_test_materials(test_id)
    q_db_ids = [q["db_id"] for q in questions]
    all_q_mat_links = get_question_material_links_bulk(q_db_ids)

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
    return _json.dumps(export_data, ensure_ascii=False, indent=2), test["title"]


def _render_test_card(test, favorites, prefix="", has_access=True, bulk_delete_mode=False, performance=None, can_edit=False):
    """Render a single test card with heart, performance circle, select/edit/export buttons."""
    test_id = test["id"]
    is_fav = test_id in favorites
    logged_in = _is_logged_in()

    # Helper to get performance circle based on percent correct
    def _get_perf_circle(pct):
        if pct >= 95:
            return "🟢"  # Green: excellent (>= 95% correct)
        elif pct >= 80:
            return "🟡"  # Yellow: good (80-95% correct)
        elif pct >= 50:
            return "🟠"  # Orange: needs work (50-80% correct)
        else:
            return "🔴"  # Red: struggling (< 50% correct)

    with st.container(border=True):
        # Determine columns based on mode
        if bulk_delete_mode:
            col_check, col_info, col_btn = st.columns([0.5, 4, 1.5])
            with col_check:
                # Initialize set if needed
                if "bulk_delete_tests" not in st.session_state:
                    st.session_state.bulk_delete_tests = set()
                is_selected = test_id in st.session_state.bulk_delete_tests
                st.checkbox("", value=is_selected, key=f"{prefix}bulk_select_{test_id}",
                           label_visibility="collapsed", on_change=_toggle_bulk_test, args=(test_id,))
        elif logged_in:
            col_fav, col_info, col_btn = st.columns([0.5, 3.5, 2])
            with col_fav:
                heart = "❤️" if is_fav else "🤍"
                if st.button(heart, key=f"{prefix}fav_{test_id}"):
                    toggle_favorite(st.session_state.user_id, test_id)
                    st.rerun()
        else:
            col_info, col_btn = st.columns([4, 2])
        with col_info:
            title_display = test["title"]
            if test.get("visibility") == "private" and not has_access:
                title_display = "🔒 " + title_display
            # Add performance circle if user has history for this test
            if performance and test_id in performance:
                perf = performance[test_id]
                circle = _get_perf_circle(perf["percent_correct"])
                title_display = f"{circle} {title_display}"
            elif logged_in:
                title_display = f"⚪ {title_display}"  # Grey: no stats yet
            st.subheader(title_display)
            if test.get("description"):
                st.write(test["description"])
            meta = t("n_questions", n=test['question_count'])
            if test.get("author"):
                meta += f"  ·  {t('author', name=test['author'])}"
            if test.get("language"):
                meta += f"  ·  {_lang_display(test['language'])}"
            st.caption(meta)
        with col_btn:
            # Show buttons in a row: Select, Edit (if can_edit), Export (if can_edit)
            btn_cols = st.columns(3 if can_edit else 1)
            col_idx = 0
            with btn_cols[col_idx]:
                if has_access:
                    if st.button("▶️", key=f"{prefix}select_{test_id}", width="stretch", help=t("select")):
                        st.session_state.selected_test = test_id
                        st.session_state.pop("test_program_visibility", None)  # Clear program context
                        st.session_state.page = "Configurar Test"
                        st.rerun()
                else:
                    st.button("▶️", key=f"{prefix}select_{test_id}", width="stretch", disabled=True, help=t("select"))
            if can_edit:
                col_idx += 1
                with btn_cols[col_idx]:
                    if st.button("✏️", key=f"{prefix}edit_{test_id}", width="stretch", help=t("edit_test")):
                        st.session_state.editing_test_id = test_id
                        st.session_state.page = "Editar Test"
                        st.rerun()
                col_idx += 1
                with btn_cols[col_idx]:
                    export_data, export_title = _get_test_export_data(test_id)
                    st.download_button(
                        "⬇️",
                        data=export_data,
                        file_name=f"{export_title}.json",
                        mime="application/json",
                        key=f"{prefix}export_{test_id}",
                        width="stretch",
                        help=t("export_json"),
                    )


def _show_import_test_inline():
    """Inline test importer (replaces broken @st.dialog)."""
    import json as json_module

    st.subheader(t("import_test"))
    st.write(t("import_test_desc"))

    uploaded_file = st.file_uploader(t("select_json_file"), type=["json"], key="import_test_file")

    if uploaded_file is not None:
        try:
            content = uploaded_file.read().decode("utf-8")
            json_data = json_module.loads(content)

            # Preview
            if isinstance(json_data, dict):
                st.info(f"**{t('title')}:** {json_data.get('title', 'N/A')}")
                if json_data.get("description"):
                    st.caption(json_data["description"])
                q_count = len(json_data.get("questions", []))
            else:
                q_count = len(json_data)
            st.caption(t("n_questions", n=q_count))

            col1, col2 = st.columns(2)
            with col1:
                if st.button(t("import"), type="primary", width="stretch", key="import_test_confirm"):
                    try:
                        test_id, title = import_test_from_json(st.session_state.user_id, json_data)
                        st.session_state.import_success = t("test_imported", title=title)
                        st.session_state["_show_import_test"] = False
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(t("import_error", error=str(e)))
            with col2:
                if st.button(t("cancel"), width="stretch", key="import_test_cancel"):
                    st.session_state["_show_import_test"] = False
                    st.rerun()
        except json_module.JSONDecodeError:
            st.error(t("invalid_json"))
    else:
        if st.button(t("cancel"), key="import_test_cancel_no_file"):
            st.session_state["_show_import_test"] = False
            st.rerun()


def _show_import_questions_inline(test_id, materials):
    """Inline question importer (replaces broken @st.dialog)."""
    import json as json_module

    st.subheader(t("import_questions"))
    st.write(t("import_questions_desc"))

    # Material selection
    material_options = [(0, t("no_material"))]
    for mat in materials:
        label = mat.get("title") or mat.get("url") or f"Material #{mat['id']}"
        material_options.append((mat["id"], label))

    selected_mat = st.selectbox(
        t("associate_material"),
        options=[opt[0] for opt in material_options],
        format_func=lambda x: next((opt[1] for opt in material_options if opt[0] == x), ""),
        key="import_q_material"
    )

    uploaded_file = st.file_uploader(t("select_json_file"), type=["json"], key="import_questions_file")

    if uploaded_file is not None:
        try:
            content = uploaded_file.read().decode("utf-8")
            json_data = json_module.loads(content)

            # Extract questions array
            if isinstance(json_data, dict):
                questions_list = json_data.get("questions", [])
            elif isinstance(json_data, list):
                questions_list = json_data
            else:
                questions_list = []

            if not questions_list:
                st.warning(t("no_questions_in_file"))
                return

            st.caption(t("n_questions", n=len(questions_list)))

            col1, col2 = st.columns(2)
            with col1:
                if st.button(t("import"), type="primary", width="stretch", key="import_q_confirm"):
                    next_num = get_next_question_num(test_id)
                    imported_count = 0
                    for i, q in enumerate(questions_list):
                        q_id = add_question(
                            test_id,
                            next_num + i,
                            q.get("tag", "general"),
                            q.get("question", ""),
                            q.get("options", []),
                            q.get("answer_index", 0),
                            q.get("explanation", ""),
                            source="json_import"
                        )
                        if selected_mat and selected_mat != 0:
                            set_question_material_links(q_id, [{"material_id": selected_mat, "context": ""}])
                        imported_count += 1
                    st.session_state.import_q_success = t("questions_imported", n=imported_count)
                    st.session_state["_show_import_questions"] = False
                    st.rerun()
            with col2:
                if st.button(t("cancel"), width="stretch", key="import_q_cancel"):
                    st.session_state["_show_import_questions"] = False
                    st.rerun()
        except json_module.JSONDecodeError:
            st.error(t("invalid_json"))
    else:
        if st.button(t("cancel"), key="import_q_cancel_no_file"):
            st.session_state["_show_import_questions"] = False
            st.rerun()


def _get_legal_file_path(doc_type, lang):
    """Get the path to a legal document file based on type and language."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    filename = f"{doc_type}_{lang}.md"
    return os.path.join(base_dir, "legal", filename)


def _read_legal_document(doc_type):
    """Read a legal document in the current language, falling back to English."""
    lang = st.session_state.get("lang", "es")
    filepath = _get_legal_file_path(doc_type, lang)

    # Try current language first
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    # Fallback to English
    filepath_en = _get_legal_file_path(doc_type, "en")
    if os.path.exists(filepath_en):
        with open(filepath_en, "r", encoding="utf-8") as f:
            return f.read()

    return ""


def show_home_page():
    """Display the home page with app introduction, video, and plans."""
    st.header(t("home_welcome"))
    st.write(t("home_intro"))

    st.divider()

    # Video section
    st.subheader(f"🎬 {t('home_video_title')}")
    # Different videos for each language
    home_videos = {
        "es": "https://youtu.be/8KRWO8JdyPI",
        "en": "https://youtu.be/kSYJ9CeXnO8",
        "fr": "https://youtu.be/XgYThH36kGM",
        "ca": "https://youtu.be/o71o6B3l9Qg",
    }
    current_lang = st.session_state.get("lang", "es")
    st.video(home_videos.get(current_lang, home_videos["es"]))

    st.divider()

    # Plans section
    st.subheader(f"📋 {t('home_plans_title')}")

    logged_in = _is_logged_in()
    current_role = _get_global_role() if logged_in else None

    col1, col2, col3 = st.columns(3)

    # Visitor plan (no login)
    with col1:
        with st.container(border=True):
            st.markdown(f"### 👁️ {t('home_plan_visitor')}")
            st.caption(t("home_plan_visitor_desc"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_take_public_tests')}")
            st.markdown(f"✅ {t('home_feature_view_materials')}")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("&nbsp;")
            st.markdown("---")
            if not logged_in:
                if st.button(t("home_get_started"), key="visitor_start", width="stretch"):
                    st.session_state.page = "Tests"
                    st.rerun()

    # Knower plan (free)
    with col2:
        is_current = logged_in and current_role == "knower"
        with st.container(border=True):
            st.markdown(f"### 🎓 {t('home_plan_knower')}")
            st.caption(t("home_plan_knower_access"))
            if is_current:
                st.success(t("home_current_plan"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_take_public_tests')}")
            st.markdown(f"✅ {t('home_feature_view_materials')}")
            st.markdown(f"✅ {t('home_feature_create_tests')}")
            st.markdown(f"✅ {t('home_feature_upload_materials')}")
            st.markdown(f"✅ {t('home_feature_track_progress')}")
            st.markdown(f"✅ {t('home_feature_invite_collaborators')}")
            st.markdown("&nbsp;")
            st.markdown("---")
            if not logged_in:
                st.button("🔑", on_click=st.login, help=t("login_with_google"), key="knower_login", width="stretch")

    # Knowter plan (premium)
    with col3:
        is_current = logged_in and current_role == "knowter"
        is_knower = logged_in and current_role == "knower"
        with st.container(border=True):
            st.markdown(f"### 🚀 {t('home_plan_knowter')}")
            st.caption(t("home_plan_knowter_access"))
            if is_current:
                st.success(t("home_current_plan"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_take_public_tests')}")
            st.markdown(f"✅ {t('home_feature_view_materials')}")
            st.markdown(f"✅ {t('home_feature_create_tests')}")
            st.markdown(f"✅ {t('home_feature_upload_materials')}")
            st.markdown(f"✅ {t('home_feature_track_progress')}")
            st.markdown(f"✅ {t('home_feature_invite_collaborators')}")
            st.markdown(f"✅ {t('home_feature_create_courses')}")
            st.markdown(f"✅ {t('home_feature_course_collaborators')}")
            st.markdown(f"✅ {t('home_feature_advanced_visibility')}")
            st.markdown(f"📋 {t('home_feature_periodic_survey')}")
            st.markdown("---")
            if not logged_in:
                st.button("🔑", on_click=st.login, help=t("login_with_google"), key="knowter_login", width="stretch")
            elif is_knower:
                # Check if user already has a pending request
                #status = get_user_survey_status(st.session_state.get("user_id"))
                #if status and status.get("pending_approval"):
                #    st.info(t("pending_admin_review"))
                #else:
                    # Show "Become Knowter" button for knowers who want to upgrade
                #    if st.button(t("become_knowter"), key="become_knowter_btn", width="stretch"):
                #        st.session_state.page = "Choose Access Type"
                #        st.rerun()
                # Knowter plan coming soon - button disabled
                st.button(
                    f"{t('become_knowter')} ({t('paid_plan_coming_soon')})",
                    key="become_knowter_btn",
                    width="stretch",
                    disabled=True
                )


def show_privacy_policy():
    """Display the privacy policy page."""
    if st.button(f"← {t('back')}"):
        st.session_state.page = "Home"
        st.rerun()

    content = _read_legal_document("privacy_policy")
    if content:
        st.markdown(content)
    else:
        st.warning("Privacy policy not available.")


def show_terms_and_conditions():
    """Display the terms and conditions page."""
    if st.button(f"← {t('back')}"):
        st.session_state.page = "Home"
        st.rerun()

    content = _read_legal_document("terms")
    if content:
        st.markdown(content)
    else:
        st.warning("Terms and conditions not available.")


def show_choose_access_type():
    """Show the page for choosing Knowter access type (paid or survey-based)."""
    if st.button(f"← {t('back')}"):
        st.session_state.page = "Home"
        st.rerun()

    st.header(t("choose_access_type"))
    st.write(t("choose_access_type_desc"))

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.subheader(f"💳 {t('paid_plan')}")
            st.write(t("paid_plan_desc"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_create_courses')}")
            st.markdown(f"✅ {t('home_feature_advanced_visibility')}")
            st.markdown(f"🚫 {t('home_feature_periodic_survey')}")
            st.markdown("---")
            st.button(
                t("paid_plan_coming_soon"),
                key="paid_plan_btn",
                width="stretch",
                disabled=True
            )

    with col2:
        with st.container(border=True):
            st.subheader(f"📋 {t('survey_based_plan')}")
            st.write(t("survey_based_plan_desc"))
            st.markdown("---")
            st.markdown(f"✅ {t('home_feature_create_courses')}")
            st.markdown(f"✅ {t('home_feature_advanced_visibility')}")
            st.markdown(f"📋 {t('home_feature_periodic_survey')}")
            st.markdown("---")
            if st.button(t("request_survey_access"), key="survey_access_btn", width="stretch"):
                user_id = st.session_state.get("user_id")
                # Create pending approval request
                status = get_user_survey_status(user_id)
                if not status:
                    create_user_survey_status(user_id, "survey", initial_completed=False, pending_approval=True)
                else:
                    update_user_survey_status(user_id, pending_approval=True)
                st.success(t("access_request_sent"))
                st.session_state.page = "Home"
                st.rerun()


def show_terms_acceptance():
    """Show the terms and privacy policy acceptance screen for new user registration."""
    pending = st.session_state.get("pending_registration", {})
    if not pending:
        return False

    st.header(t("welcome_new_user"))
    st.write(t("accept_terms_intro"))

    # Show terms and conditions in an expander
    with st.expander(f"📜 {t('terms_and_conditions')}", expanded=False):
        terms_content = _read_legal_document("terms")
        if terms_content:
            st.markdown(terms_content)

    # Show privacy policy in an expander
    with st.expander(f"🔒 {t('privacy_policy')}", expanded=False):
        privacy_content = _read_legal_document("privacy_policy")
        if privacy_content:
            st.markdown(privacy_content)

    st.divider()

    # Acceptance checkboxes
    accept_terms = st.checkbox(t("i_accept_terms"), key="accept_terms_checkbox")
    accept_privacy = st.checkbox(t("i_accept_privacy"), key="accept_privacy_checkbox")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(t("create_account"), type="primary", disabled=not (accept_terms and accept_privacy)):
            if accept_terms and accept_privacy:
                # Create the user account
                email = pending["email"]
                name = pending["name"]
                user_id = get_or_create_google_user(email, name)
                resolve_collaborator_user_id(email, user_id)
                st.session_state.user_id = user_id
                st.session_state.username = name
                # Clear pending registration
                del st.session_state.pending_registration
                st.rerun()
            else:
                st.warning(t("must_accept_both"))
    with col2:
        if st.button(t("cancel_registration"), type="secondary"):
            # Clear pending registration and logout
            del st.session_state.pending_registration
            st.logout()
            st.rerun()

    return True


def show_test_catalog():
    """Show a searchable catalog of available tests."""
    user_id = st.session_state.get("user_id")
    all_tests = get_all_tests(user_id)
    logged_in = _is_logged_in()

    # Get user's performance for all tests
    test_performance = {}
    if logged_in:
        test_ids = [tt["id"] for tt in all_tests]
        test_performance = get_tests_performance(user_id, test_ids)

    st.header(t("available_tests"))

    # --- Pending Test Invitations Section ---
    if logged_in:
        invitations = get_pending_invitations(user_id)
        test_invitations = invitations["tests"]

        if test_invitations:
            with st.expander(f"📩 {t('pending_invitations')} ({len(test_invitations)})", expanded=True):
                for inv in test_invitations:
                    with st.container(border=True):
                        col_info, col_actions = st.columns([3, 1])
                        with col_info:
                            st.markdown(f"**{t('test_invitation')}:** {inv['title']}")
                            st.caption(f"{t('invited_by', name=inv['inviter_name'])} {t('invited_as', role=inv['role'])}")
                        with col_actions:
                            c1, c2 = st.columns(2)
                            if c1.button("✓", key=f"accept_test_{inv['test_id']}", help=t("accept_invitation")):
                                accept_test_invitation(inv['test_id'], user_id)
                                st.success(t("invitation_accepted"))
                                st.rerun()
                            if c2.button("✕", key=f"decline_test_{inv['test_id']}", help=t("decline_invitation")):
                                decline_test_invitation(inv['test_id'], user_id)
                                st.info(t("invitation_declined"))
                                st.rerun()
            st.divider()

    # Show import success message if any
    if "import_success" in st.session_state:
        st.success(st.session_state.import_success)
        del st.session_state.import_success

    # Admin buttons: create, import, bulk delete
    bulk_delete_mode = False
    if _is_global_admin():
        col_create, col_import, col_bulk = st.columns([1, 1, 1])
        with col_create:
            if _can_create_tests():
                if st.button(t("create_test"), type="secondary", width="stretch"):
                    st.session_state.page = "Crear Test"
                    st.rerun()
        with col_import:
            if st.button(t("import_test"), type="secondary", width="stretch"):
                st.session_state["_show_import_test"] = not st.session_state.get("_show_import_test", False)
                st.rerun()
        with col_bulk:
            if all_tests:
                bulk_delete_mode = st.toggle(t("bulk_delete_mode"), key="test_bulk_delete_mode")
                if bulk_delete_mode:
                    if "bulk_delete_tests" not in st.session_state:
                        st.session_state.bulk_delete_tests = set()
    elif logged_in and _can_create_tests():
        if st.button(t("create_test"), type="secondary"):
            st.session_state.page = "Crear Test"
            st.rerun()

    # Show inline import test form when toggled
    if st.session_state.get("_show_import_test", False):
        with st.container(border=True):
            _show_import_test_inline()

    if not all_tests:
        st.info(t("no_tests"))
        return

    col_search, col_lang = st.columns([3, 1])
    with col_search:
        search = st.text_input(t("search_test"), placeholder=t("search_placeholder"), key="test_search")
    with col_lang:
        # Build language filter options from available tests
        available_langs = sorted({tt["language"] for tt in all_tests if tt.get("language")})
        lang_options = [""] + available_langs
        lang_labels = {code: _lang_display(code) if code else t("all_languages") for code in lang_options}
        selected_lang = st.selectbox(
            t("language"), options=lang_options,
            format_func=lambda x: lang_labels[x],
            key="test_lang_filter",
        )

    favorites = get_favorite_tests(st.session_state.user_id) if logged_in else set()

    # Show bulk delete controls below the create button
    if bulk_delete_mode:
        selected_count = len(st.session_state.get("bulk_delete_tests", set()))
        col_info, col_del = st.columns([3, 1])
        with col_info:
            st.info(t("selected_items", n=selected_count))
        with col_del:
            if st.button(t("delete_selected"), type="primary", disabled=selected_count == 0, width="stretch"):
                for test_id in st.session_state.bulk_delete_tests:
                    delete_test(test_id)
                st.session_state.bulk_delete_tests = set()
                st.success(t("tests_deleted", n=selected_count))
                st.rerun()

    filtered_tests = [
        tt for tt in all_tests
        if (not search or search.lower() in tt["title"].lower())
        and (not selected_lang or tt.get("language") == selected_lang)
    ]

    if not filtered_tests:
        st.info(t("no_tests_found"))
        return

    fav_tests = [tt for tt in filtered_tests if tt["id"] in favorites]
    other_tests = [tt for tt in filtered_tests if tt["id"] not in favorites]

    # Shared with me
    shared_tests = []
    shared_test_ids = set()
    if logged_in:
        shared_tests = get_shared_tests(st.session_state.user_id)
        shared_test_ids = {tt["id"] for tt in shared_tests}

    # Build set of test IDs the user has access to (owner or collaborator)
    accessible_ids = shared_test_ids.copy()
    if logged_in:
        accessible_ids |= {tt["id"] for tt in all_tests if tt.get("owner_id") == st.session_state.user_id}

    def _has_access(tt):
        vis = tt.get("visibility", "public")
        if vis in ("public", "restricted"):
            return True
        return tt["id"] in accessible_ids

    def _can_edit(tt):
        """Check if current user can edit this test."""
        if not logged_in:
            return False
        if _is_global_admin():
            return True
        if tt.get("owner_id") == st.session_state.user_id:
            return True
        # Check if user has reviewer/admin role on this test
        role = get_user_role_for_test(tt["id"], st.session_state.user_id)
        return role in ("reviewer", "admin")

    if fav_tests:
        st.subheader(t("favorites"))
        for test in fav_tests:
            _render_test_card(test, favorites, prefix="fav_", has_access=_has_access(test), bulk_delete_mode=bulk_delete_mode, performance=test_performance, can_edit=_can_edit(test))

    if shared_tests:
        st.subheader(t("shared_with_me"))
        for test in shared_tests:
            if test["id"] not in {tt["id"] for tt in fav_tests}:
                # For shared tests, check the role they have
                can_edit_shared = test.get("role") in ("reviewer", "admin") or _is_global_admin()
                _render_test_card(test, favorites, prefix="shared_", has_access=True, bulk_delete_mode=bulk_delete_mode, performance=test_performance, can_edit=can_edit_shared)

    other_tests = [tt for tt in other_tests if tt["id"] not in shared_test_ids]
    if other_tests:
        if fav_tests or shared_tests:
            st.subheader(t("all_tests"))
        for test in other_tests:
            _render_test_card(test, favorites, has_access=_has_access(test), bulk_delete_mode=bulk_delete_mode, performance=test_performance, can_edit=_can_edit(test))


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
    tags = get_test_tags(test_id)

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
    materials = get_test_materials(test_id)
    show_materials = True
    # Check both test visibility and program visibility (if coming from a program)
    program_visibility = st.session_state.get("test_program_visibility", "public")
    effective_visibility = get_effective_visibility(visibility, program_visibility)
    if effective_visibility == "restricted":
        # For restricted visibility, show materials to users with explicit access (any role)
        logged_in_uid = st.session_state.get("user_id")
        user_role = get_user_role_for_test(test_id, logged_in_uid) if logged_in_uid else None
        is_owner = logged_in_uid and test["owner_id"] == logged_in_uid
        # Show materials if user has any explicit access (owner, global admin, or any collaboration role)
        can_see_materials = _is_global_admin() or is_owner or user_role is not None
        show_materials = can_see_materials
    # For private/hidden visibility, user already has explicit access if they can see the test
    if materials and show_materials:
        with st.expander(t("reference_materials", n=len(materials)), expanded=True):
            for mat in materials:
                type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
                icon = type_icons.get(mat["material_type"], "📎")
                label = mat["title"] or mat["url"] or t("no_title")
                has_download = mat["material_type"] in ("pdf", "image") and mat.get("file_data")
                has_link = mat["material_type"] in ("url", "youtube") and mat.get("url")
                has_extra = has_download or has_link
                num_icons = 2 + (1 if has_extra else 0)
                cols = st.columns([6] + [1] * num_icons)
                with cols[0]:
                    st.write(f"{icon} {label}")
                col_idx = 1
                if has_download:
                    with cols[col_idx]:
                        ext = "pdf" if mat["material_type"] == "pdf" else "png"
                        st.download_button(
                            "⬇️", data=mat["file_data"],
                            file_name=f"{label}.{ext}",
                            key=f"dl_mat_{mat['id']}",
                            help=t("tooltip_download"),
                        )
                    col_idx += 1
                elif has_link:
                    with cols[col_idx]:
                        st.markdown(f'<a href="{mat["url"]}" target="_blank" style="text-decoration:none;font-size:1.4em;" title="{t("tooltip_open_link")}">🔗</a>', unsafe_allow_html=True)
                    col_idx += 1
                with cols[col_idx]:
                    if st.button("👁️", key=f"view_mat_{mat['id']}", help=t("tooltip_view_material")):
                        for k in list(st.session_state.keys()):
                            if k.startswith("show_mat_"):
                                del st.session_state[k]
                        st.session_state.pop("study_mat_id", None)
                        st.session_state[f"show_mat_{mat['id']}"] = True
                        st.rerun()
                with cols[col_idx + 1]:
                    is_youtube = mat["material_type"] == "youtube" and mat.get("url")
                    can_study = is_youtube and bool(questions)
                    if can_study:
                        if st.button("🧠", key=f"study_mat_{mat['id']}", help=t("tooltip_study_with_questions")):
                            for k in list(st.session_state.keys()):
                                if k.startswith("show_mat_") or k.startswith("study_"):
                                    del st.session_state[k]
                            st.session_state.study_mat_id = mat['id']
                            st.rerun()
                    else:
                        st.button("🧠", key=f"study_mat_{mat['id']}", disabled=True, help=t("tooltip_study_with_questions"))

            # Render inline viewer for the active material (only one at a time)
            for mat in materials:
                if st.session_state.get(f"show_mat_{mat['id']}"):
                    label = mat["title"] or mat["url"] or t("no_title")
                    with st.container(border=True):
                        _show_material_inline(mat, label)
                    break

            # Render study dialog if active
            study_mat_id = st.session_state.get("study_mat_id")
            if study_mat_id:
                for mat in materials:
                    if mat["id"] == study_mat_id:
                        label = mat["title"] or mat["url"] or t("no_title")
                        _show_study_dialog(mat, label, questions)
                        break

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
            all_q_db_ids = [q["db_id"] for q in questions]
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
                if _is_logged_in():
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
                    if _is_logged_in():
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


def show_create_test():
    """Show the create test form."""
    st.header(t("create_new_test"))

    if st.button(t("back")):
        st.session_state.page = "Tests"
        st.rerun()

    title = st.text_input(t("test_title"), key="new_test_title")
    description = st.text_area(t("description"), key="new_test_desc")
    language = st.selectbox(
        t("language"), options=LANGUAGE_OPTIONS,
        format_func=lambda x: _lang_display(x) if x else "—",
        key="new_test_lang",
    )

    uploaded_json = st.file_uploader(
        t("import_json"),
        type=["json"],
        key="new_test_json",
    )

    if st.button(t("create_test_btn"), type="primary"):
        if not title.strip():
            st.warning(t("title_required"))
        else:
            author = st.session_state.get("username", "")
            test_id = create_test(st.session_state.user_id, title.strip(), description.strip(), author, language)

            if uploaded_json is not None:
                import json
                try:
                    data = json.loads(uploaded_json.read())
                    if isinstance(data, dict):
                        # Import metadata if not manually set
                        if not title.strip() and data.get("title"):
                            update_test(test_id, data["title"], data.get("description", ""),
                                        data.get("author", ""), data.get("language", ""),
                                        data.get("visibility", "public"))
                        elif data.get("visibility") or data.get("language"):
                            update_test(test_id, title.strip(), description.strip(),
                                        st.session_state.get("username", ""),
                                        data.get("language", language),
                                        data.get("visibility", "public"))

                        # Import materials
                        mat_id_map = {}
                        for mat in data.get("materials", []):
                            old_id = mat.get("id")
                            new_mat_id = add_test_material(
                                test_id, mat.get("material_type", "url"),
                                mat.get("title", ""), mat.get("url", ""),
                                pause_times=mat.get("pause_times", ""),
                                transcript=mat.get("transcript", ""),
                            )
                            if old_id is not None:
                                mat_id_map[old_id] = new_mat_id

                        # Import collaborators
                        for collab in data.get("collaborators", []):
                            email = collab.get("email", "").strip()
                            role = collab.get("role", "guest")
                            if email:
                                add_collaborator(test_id, email, role)

                        questions_list = data.get("questions", [])
                    else:
                        questions_list = data
                        mat_id_map = {}

                    for i, q in enumerate(questions_list, 1):
                        q_id = add_question(
                            test_id, i,
                            q.get("tag", "general"),
                            q["question"],
                            q["options"],
                            q["answer_index"],
                            q.get("explanation", ""),
                            source="json_import",
                        )
                        # Import material references
                        refs = q.get("material_refs", [])
                        if refs and mat_id_map:
                            links = []
                            for ref in refs:
                                new_mid = mat_id_map.get(ref.get("material_id"))
                                if new_mid:
                                    links.append({"material_id": new_mid, "context": ref.get("context", "")})
                            if links:
                                set_question_material_links(q_id, links)
                except (json.JSONDecodeError, KeyError) as e:
                    st.error(t("json_import_error", e=e))

            st.session_state.editing_test_id = test_id
            st.session_state.page = "Editar Test"
            st.rerun()


def show_test_editor():
    """Show the test editor page for editing metadata and questions."""
    import json as _json_editor

    # Handle captured pause time from video player (via URL params) FIRST
    # This must happen before any early returns to ensure params are processed
    params = st.query_params
    capture_t = params.get("capture_t")
    capture_mat_id = params.get("capture_mat_id")
    if capture_t is not None and capture_mat_id is not None:
        try:
            captured_seconds = int(capture_t)
            captured_questions = int(params.get("capture_n", "1"))
            captured_mat_id_int = int(capture_mat_id)

            # Fetch the material directly by ID
            mat = get_material_by_id(captured_mat_id_int)
            if mat:
                # Restore session state if lost during redirect
                if "editing_test_id" not in st.session_state:
                    st.session_state.editing_test_id = mat["test_id"]
                    st.session_state.page = "Editar Test"

                # Update the pause times
                existing_pause_times = []
                if mat.get("pause_times"):
                    try:
                        existing_pause_times = _json_editor.loads(mat["pause_times"])
                    except:
                        pass
                existing_times = [p["t"] for p in existing_pause_times]
                if captured_seconds not in existing_times:
                    existing_pause_times.append({"t": captured_seconds, "n": captured_questions})
                    existing_pause_times.sort(key=lambda x: x["t"])
                    pause_json = _json_editor.dumps(existing_pause_times)
                    update_material_pause_times(captured_mat_id_int, pause_json)
                    st.session_state.pause_time_added = f"✓ {t('time_added')}: {_seconds_to_mmss(captured_seconds)}"

                # Clear dialog session state so it reads fresh data next time
                dialog_state_key = f"editing_pause_times_{captured_mat_id_int}"
                if dialog_state_key in st.session_state:
                    del st.session_state[dialog_state_key]
                if "_pause_dialog_mat_id" in st.session_state:
                    del st.session_state["_pause_dialog_mat_id"]

            # Clear the params and rerun
            st.query_params.clear()
            st.rerun()
        except (ValueError, TypeError) as e:
            st.error(f"Error processing captured time: {e}")
            st.query_params.clear()

    test_id = st.session_state.get("editing_test_id")
    if not test_id:
        st.session_state.page = "Tests"
        st.rerun()
        return

    test = get_test(test_id)
    if not test:
        st.error(t("test_not_found"))
        return

    user_id = st.session_state.get("user_id")
    is_owner = test["owner_id"] == user_id
    if _is_global_admin():
        user_role = "owner"  # Global admins have full access to all tests
    elif is_owner:
        user_role = "owner"
    else:
        user_role = get_user_role_for_test(test_id, user_id)
    if not user_role:
        st.error(t("no_permission"))
        return
    read_only = user_role in ("guest", "student")

    questions = get_test_questions(test_id)

    st.header(t("edit_colon", name=test['title']))

    if st.button(t("back")):
        if "editing_test_id" in st.session_state:
            del st.session_state.editing_test_id
        st.session_state.page = "Tests"
        st.rerun()

    # --- Metadata ---
    meta_disabled = user_role not in ("owner", "admin")
    st.subheader(t("test_info"))
    new_title = st.text_input(t("title"), value=test["title"], key="edit_title", disabled=meta_disabled)
    new_desc = st.text_area(t("description"), value=test["description"] or "", key="edit_desc", disabled=meta_disabled)
    new_author = st.text_input(t("author_label"), value=test["author"] or "", key="edit_author", disabled=meta_disabled)
    current_lang_index = LANGUAGE_OPTIONS.index(test.get("language", "")) if test.get("language", "") in LANGUAGE_OPTIONS else 0
    new_language = st.selectbox(
        t("language"), options=LANGUAGE_OPTIONS,
        index=current_lang_index,
        format_func=lambda x: _lang_display(x) if x else "—",
        key="edit_lang",
        disabled=meta_disabled,
    )
    visibility_options = ["public", "restricted", "private", "hidden"]
    visibility_labels = {
        "public": t("visibility_public"),
        "restricted": t("visibility_restricted"),
        "private": t("visibility_private"),
        "hidden": t("visibility_hidden"),
    }
    current_vis = test.get("visibility", "public")
    current_vis_index = visibility_options.index(current_vis) if current_vis in visibility_options else 0
    new_visibility = st.selectbox(
        t("visibility"), options=visibility_options,
        index=current_vis_index,
        format_func=lambda x: visibility_labels[x],
        key="edit_visibility",
        disabled=meta_disabled,
    )

    if not meta_disabled:
        if st.button(t("save_info"), type="primary"):
            if not new_title.strip():
                st.warning(t("title_required"))
            else:
                update_test(test_id, new_title.strip(), new_desc.strip(), new_author.strip(), new_language, new_visibility)
                if "editing_test_id" in st.session_state:
                    del st.session_state.editing_test_id
                st.session_state.selected_test = test_id
                st.session_state.page = "Configurar Test"
                st.rerun()

    st.divider()

    materials = get_test_materials(test_id)

    # Show success message if time was just added (set by query param handler at top)
    if "pause_time_added" in st.session_state:
        st.success(st.session_state.pause_time_added)
        del st.session_state.pause_time_added

    # --- Collaborators ---
    if user_role in ("owner", "admin"):
        st.subheader(t("collaborators"))
        collabs = get_collaborators(test_id)
        if collabs:
            for c in collabs:
                col_email, col_status, col_role, col_del = st.columns([2.5, 1, 2, 0.5])
                with col_email:
                    st.write(c["email"])
                with col_status:
                    status = c.get("status", "accepted")
                    if status == "pending":
                        st.caption(f"⏳ {t('status_pending')}")
                    else:
                        st.caption(f"✓ {t('status_accepted')}")
                with col_role:
                    role_options = ["student", "guest", "reviewer", "admin"]
                    role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
                    new_role = st.selectbox(
                        t("role_label"), options=role_options, index=role_options.index(c["role"]),
                        format_func=lambda x: role_labels[x], key=f"collab_role_{c['id']}",
                    )
                    if new_role != c["role"]:
                        update_collaborator_role(test_id, c["email"], new_role)
                        st.rerun()
                with col_del:
                    if st.button("🗑️", key=f"del_collab_{c['id']}"):
                        remove_collaborator(test_id, c["email"])
                        st.success(t("collaborator_removed"))
                        st.rerun()

        st.write(t("invite_user"))
        col_inv_email, col_inv_role, col_inv_btn = st.columns([3, 2, 1])
        with col_inv_email:
            inv_email = st.text_input(t("email_placeholder"), key="invite_email", label_visibility="collapsed", placeholder=t("email_placeholder"))
        with col_inv_role:
            inv_role_options = ["student", "guest", "reviewer", "admin"]
            inv_role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
            inv_role = st.selectbox(t("role_label"), options=inv_role_options, format_func=lambda x: inv_role_labels[x], key="invite_role", label_visibility="collapsed")
        with col_inv_btn:
            if st.button(t("invite_btn"), type="secondary"):
                if not inv_email.strip():
                    st.warning(t("email_required"))
                elif inv_email.strip() == st.session_state.get("username"):
                    st.warning(t("cannot_invite_self"))
                else:
                    add_collaborator(test_id, inv_email.strip(), inv_role)
                    st.success(t("invitation_sent"))
                    st.rerun()

        st.divider()

    # --- Materials (owner and admin only) ---
    if user_role in ("owner", "admin"):
        st.subheader(t("reference_materials_header"))

        for mat in materials:
            type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
            icon = type_icons.get(mat["material_type"], "📎")
            label = mat["title"] or mat["url"] or t("no_title")
            with st.expander(f"{icon} {label}"):
                new_title = st.text_input(t("material_title"), value=mat["title"] or "", key=f"edit_mat_title_{mat['id']}")
                new_url = ""
                if mat["material_type"] in ("youtube", "url"):
                    new_url = st.text_input(t("url"), value=mat["url"] or "", key=f"edit_mat_url_{mat['id']}")
                if mat["material_type"] == "image" and mat["file_data"]:
                    st.image(mat["file_data"], width=200)
                elif mat["material_type"] == "pdf" and mat["file_data"]:
                    st.download_button(
                        t("download"),
                        data=mat["file_data"],
                        file_name=f"{label}.pdf",
                        key=f"dl_mat_{mat['id']}",
                    )
                is_yt = mat["material_type"] == "youtube"
                cols = st.columns([1, 1, 1, 1, 1, 1] if is_yt else [1, 1, 1])
                with cols[0]:
                    if st.button(t("save_material"), key=f"save_mat_{mat['id']}", type="primary"):
                        update_test_material(mat["id"], new_title.strip(), new_url.strip())
                        st.rerun()
                with cols[1]:
                    gen_q_key = f"_show_gen_questions_{mat['id']}"
                    if st.button(t("generate"), key=f"gen_mat_{mat['id']}"):
                        if is_yt:
                            transcript_text = mat.get("transcript", "")
                            if not transcript_text:
                                transcript_text = _fetch_youtube_transcript(mat["url"])
                                if transcript_text:
                                    update_material_transcript(mat["id"], transcript_text)
                            if transcript_text:
                                st.session_state[gen_q_key] = not st.session_state.get(gen_q_key, False)
                                st.rerun()
                            else:
                                st.warning(t("no_transcript"))
                        else:
                            # For other materials, create placeholder questions
                            next_num = get_next_question_num(test_id)
                            mat_label = mat["title"] or t("no_title")
                            for i in range(3):
                                add_question(
                                    test_id, next_num + i, "general",
                                    t("generated_question", name=mat_label, n=i+1),
                                    [t("option_a"), t("option_b"), t("option_c"), t("option_d")],
                                    0, t("generated_explanation"),
                                    source=f"material:{mat['id']}",
                                )
                            st.rerun()
                if is_yt:
                    with cols[2]:
                        if st.button(f"📜 {t('transcript')}", key=f"transcript_mat_{mat['id']}"):
                            transcript_text = mat.get("transcript", "")
                            if not transcript_text:
                                transcript_text = _fetch_youtube_transcript(mat["url"])
                                if transcript_text:
                                    update_material_transcript(mat["id"], transcript_text)
                            if transcript_text:
                                _show_transcript_dialog(transcript_text)
                            else:
                                st.warning(t("no_transcript"))
                    with cols[3]:
                        gen_topics_key = f"_show_gen_topics_{mat['id']}"
                        if st.button(f"🏷️ {t('generate_topics_btn')}", key=f"gen_topics_mat_{mat['id']}"):
                            transcript_text = mat.get("transcript", "")
                            if not transcript_text:
                                transcript_text = _fetch_youtube_transcript(mat["url"])
                                if transcript_text:
                                    update_material_transcript(mat["id"], transcript_text)
                            if transcript_text:
                                st.session_state[gen_topics_key] = not st.session_state.get(gen_topics_key, False)
                                st.rerun()
                            else:
                                st.warning(t("no_transcript"))
                    with cols[4]:
                        editor_key = f"_show_pause_editor_{mat['id']}"
                        if st.button("⏱️", key=f"pause_times_mat_{mat['id']}", help=t("pause_time_selector_title")):
                            st.session_state[editor_key] = not st.session_state.get(editor_key, False)
                            st.rerun()
                with cols[-1]:
                    if st.button("🗑️", key=f"del_mat_{mat['id']}"):
                        delete_test_material(mat["id"])
                        st.rerun()

                # Show inline pause time editor when toggled
                if st.session_state.get(f"_show_pause_editor_{mat['id']}", False):
                    with st.container(border=True):
                        _show_pause_time_editor_inline(mat["id"], mat["url"], mat.get("pause_times", ""))

                # Show inline generate topics editor when toggled
                if st.session_state.get(f"_show_gen_topics_{mat['id']}", False):
                    transcript_text = mat.get("transcript", "")
                    if transcript_text:
                        existing_tags = get_test_tags(test_id)
                        with st.container(border=True):
                            _show_generate_topics_inline(test_id, transcript_text, existing_tags, mat["id"])

                # Show inline generate questions editor when toggled
                if st.session_state.get(f"_show_gen_questions_{mat['id']}", False):
                    transcript_text = mat.get("transcript", "")
                    if transcript_text:
                        with st.container(border=True):
                            _show_generate_questions_inline(test_id, mat["id"], transcript_text)

        st.write(t("add_material_label"))
        mat_type = st.selectbox(t("material_type"), ["pdf", "youtube", "image", "url"],
                                format_func=lambda x: {"pdf": t("pdf"), "youtube": t("youtube"), "image": t("image"), "url": t("url_type")}[x],
                                key="new_mat_type")
        mat_title = st.text_input(t("material_title"), key="new_mat_title")

        mat_url = ""
        mat_file = None
        mat_pause_times = ""
        if mat_type in ("youtube", "url"):
            col_url, col_pause_btn = st.columns([4, 1]) if mat_type == "youtube" else (st.columns([1]),)
            with col_url if mat_type == "youtube" else st.container():
                mat_url = st.text_input(t("url"), key="new_mat_url")
            if mat_type == "youtube":
                with col_pause_btn:
                    st.write("")  # Spacing to align with input
                    # Show pause times button only if URL is entered
                    if mat_url.strip() and _extract_youtube_id(mat_url.strip()):
                        if st.button(t("set_pause_times"), key="new_mat_pause_btn"):
                            st.session_state["_show_new_mat_pause_editor"] = not st.session_state.get("_show_new_mat_pause_editor", False)
                            st.rerun()
                # Show inline pause time editor when toggled
                if st.session_state.get("_show_new_mat_pause_editor", False) and mat_url.strip():
                    with st.container(border=True):
                        _show_new_material_pause_time_inline(mat_url.strip())
                # Get pause times from session state if set via editor
                if "new_material_pause_times" in st.session_state:
                    stored_pause_times = st.session_state.new_material_pause_times
                    if stored_pause_times:
                        st.info(f"⏱️ {len(stored_pause_times)} {t('marked_pause_times').lower()}")
                # Also allow manual entry
                mat_pause_times = st.text_input(t("pause_times_label"), key="new_mat_pause_times", help=t("pause_times_help"))
        else:
            file_types = ["pdf"] if mat_type == "pdf" else ["png", "jpg", "jpeg", "gif"]
            mat_file = st.file_uploader(t("file"), type=file_types, key="new_mat_file")

        if st.button(t("add_material_btn"), type="secondary"):
            file_data = mat_file.read() if mat_file else None
            if mat_type in ("youtube", "url") and not mat_url.strip():
                st.warning(t("url_required"))
            elif mat_type in ("pdf", "image") and not file_data:
                st.warning(t("file_required"))
            else:
                # Use pause times from dialog if available, otherwise parse text input
                if mat_type == "youtube" and "new_material_pause_times" in st.session_state and st.session_state.new_material_pause_times:
                    import json as _json
                    pause_json = _json.dumps(st.session_state.new_material_pause_times)
                    del st.session_state.new_material_pause_times
                else:
                    pause_json = _parse_pause_times(mat_pause_times) if mat_type == "youtube" else ""
                transcript = ""
                if mat_type == "youtube" and mat_url.strip():
                    transcript = _fetch_youtube_transcript(mat_url.strip())
                add_test_material(test_id, mat_type, mat_title.strip(), mat_url.strip(), file_data,
                                  pause_times=pause_json, transcript=transcript)
                st.rerun()

        st.divider()

    # --- Topics (owner and admin only) ---
    if user_role in ("owner", "admin"):
        st.subheader(t("topics"))

        tags = get_test_tags(test_id)
        tag_counts = {}
        for q in questions:
            tag_counts[q["tag"]] = tag_counts.get(q["tag"], 0) + 1

        tag_edits = {}
        for tag in tags:
            count = tag_counts.get(tag, 0)
            confirm_key = f"confirm_del_tag_{tag}"

            if st.session_state.get(confirm_key):
                st.warning(t("delete_topic_confirm", tag=tag, n=count))
                col_del_q, col_blank, col_cancel = st.columns(3)
                with col_del_q:
                    if st.button(t("delete_questions_btn"), key=f"deltag_delq_{tag}"):
                        delete_test_tag(test_id, tag, delete_questions=True)
                        del st.session_state[confirm_key]
                        st.rerun()
                with col_blank:
                    if st.button(t("leave_blank"), key=f"deltag_blank_{tag}"):
                        delete_test_tag(test_id, tag, delete_questions=False)
                        del st.session_state[confirm_key]
                        st.rerun()
                with col_cancel:
                    if st.button(t("cancel"), key=f"deltag_cancel_{tag}"):
                        del st.session_state[confirm_key]
                        st.rerun()
            else:
                col_name, col_count, col_del = st.columns([3, 1, 0.5])
                with col_name:
                    new_name = st.text_input(t("topic_label"), value=tag, key=f"tag_name_{tag}", label_visibility="collapsed")
                    tag_edits[tag] = new_name
                with col_count:
                    st.caption(t("n_questions_abbrev", n=count))
                with col_del:
                    if st.button("🗑️", key=f"del_tag_{tag}"):
                        st.session_state[confirm_key] = True
                        st.rerun()

        if tag_edits:
            if st.button(t("save_topic_changes")):
                for old_tag, new_tag in tag_edits.items():
                    if new_tag.strip() != old_tag and new_tag.strip():
                        rename_test_tag(test_id, old_tag, new_tag.strip())
                st.rerun()

        st.write(t("add_topic_label"))
        col_new_tag, col_add_tag = st.columns([3, 1])
        with col_new_tag:
            new_tag_name = st.text_input(t("topic_label"), key="new_tag_name", label_visibility="collapsed", placeholder=t("topic_name_placeholder"))
        with col_add_tag:
            if st.button(t("add_btn")):
                if new_tag_name and new_tag_name.strip():
                    add_test_tag(test_id, new_tag_name.strip())
                    st.rerun()

        st.divider()

    # --- Questions ---
    all_tags = get_test_tags(test_id)

    with st.expander(t("questions_header", n=len(questions)), expanded=False):
        # Show import success message if any
        if st.session_state.get("import_q_success"):
            st.success(st.session_state.pop("import_q_success"))

        if not read_only:
            col_add_q, col_import_q, col_bulk_q = st.columns([1, 1, 1])
            with col_add_q:
                if st.button(t("add_question"), width="stretch"):
                    next_num = get_next_question_num(test_id)
                    default_tag = all_tags[0] if all_tags else "general"
                    add_question(test_id, next_num, default_tag, t("new_question_text"), [t("option_a"), t("option_b"), t("option_c"), t("option_d")], 0, "")
                    st.rerun()
            with col_import_q:
                if st.button(t("import_questions"), width="stretch"):
                    st.session_state["_show_import_questions"] = not st.session_state.get("_show_import_questions", False)
                    st.rerun()
            with col_bulk_q:
                q_bulk_delete = st.toggle(t("bulk_delete_mode"), key="q_bulk_delete_mode")
                if q_bulk_delete:
                    if "bulk_delete_questions" not in st.session_state:
                        st.session_state.bulk_delete_questions = set()

            # Show inline import questions form when toggled
            if st.session_state.get("_show_import_questions", False):
                with st.container(border=True):
                    _show_import_questions_inline(test_id, materials)
        else:
            q_bulk_delete = False

        # Pre-load all question-material links (needed for material filter)
        all_q_db_ids = [q["db_id"] for q in questions]
        all_q_mat_links = get_question_material_links_bulk(all_q_db_ids) if all_q_db_ids else {}
        mat_by_id = {m["id"]: m for m in materials}

        # --- Search & Filter ---
        if questions:
            q_search = st.text_input(t("search_keywords"), key="q_filter_search", placeholder=t("search_placeholder"))
            col_topic, col_mat, col_from, col_to = st.columns([2, 2, 1, 1])
            with col_topic:
                topic_options = [""] + list(all_tags)
                q_filter_topic = st.selectbox(
                    t("filter_by_topic"), options=topic_options,
                    format_func=lambda x: t("all_topics") if x == "" else x,
                    key="q_filter_topic",
                )
            with col_mat:
                mat_options = [0] + [m["id"] for m in materials]
                type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
                mat_labels = {0: t("all_materials")}
                for m in materials:
                    icon = type_icons.get(m["material_type"], "📎")
                    mat_labels[m["id"]] = f"{icon} {m['title'] or m['url'] or t('no_title')}"
                q_filter_mat = st.selectbox(
                    t("filter_by_material"), options=mat_options,
                    format_func=lambda x: mat_labels.get(x, ""),
                    key="q_filter_material",
                )
            q_nums = [q["id"] for q in questions]
            min_num, max_num = min(q_nums), max(q_nums)
            with col_from:
                q_from = st.number_input(t("from_number"), min_value=min_num, max_value=max_num, value=min_num, key="q_filter_from")
            with col_to:
                q_to = st.number_input(t("to_number"), min_value=min_num, max_value=max_num, value=max_num, key="q_filter_to")

            # Apply filters
            filtered_questions = questions
            if q_search.strip():
                kw = q_search.strip().lower()
                filtered_questions = [
                    q for q in filtered_questions
                    if kw in q["question"].lower()
                    or kw in q.get("explanation", "").lower()
                    or any(kw in opt.lower() for opt in q.get("options", []))
                ]
            if q_filter_topic:
                filtered_questions = [q for q in filtered_questions if q["tag"] == q_filter_topic]
            if q_filter_mat:
                linked_db_ids = {db_id for db_id, links in all_q_mat_links.items() if any(lk["material_id"] == q_filter_mat for lk in links)}
                filtered_questions = [q for q in filtered_questions if q["db_id"] in linked_db_ids]
            if q_from > min_num or q_to < max_num:
                filtered_questions = [q for q in filtered_questions if q_from <= q["id"] <= q_to]

            if len(filtered_questions) != len(questions):
                st.caption(t("questions_shown", shown=len(filtered_questions), total=len(questions)))
        else:
            filtered_questions = questions

        # Bulk delete controls (operate on filtered set)
        if q_bulk_delete and filtered_questions:
            filtered_q_ids = {q["db_id"] for q in filtered_questions}
            selected_count = len(st.session_state.get("bulk_delete_questions", set()))
            col_sel_all, col_info_q, col_del_q = st.columns([1, 2, 1])
            with col_sel_all:
                all_selected = st.session_state.get("bulk_delete_questions", set()) >= filtered_q_ids
                if st.checkbox(t("select_all"), value=all_selected, key="q_select_all"):
                    st.session_state.bulk_delete_questions = st.session_state.get("bulk_delete_questions", set()) | filtered_q_ids
                else:
                    if all_selected:
                        st.session_state.bulk_delete_questions = st.session_state.get("bulk_delete_questions", set()) - filtered_q_ids
            with col_info_q:
                selected_count = len(st.session_state.get("bulk_delete_questions", set()))
                st.info(t("selected_items", n=selected_count))
            with col_del_q:
                if st.button(t("delete_selected"), type="primary", disabled=selected_count == 0, width="stretch", key="del_selected_questions"):
                    for db_id in st.session_state.bulk_delete_questions:
                        delete_question(db_id)
                    deleted_n = len(st.session_state.bulk_delete_questions)
                    st.session_state.bulk_delete_questions = set()
                    st.success(t("questions_deleted", n=deleted_n))
                    st.rerun()

        if not filtered_questions and questions:
            st.info(t("no_matching_questions"))

        for q in filtered_questions:
            if q_bulk_delete:
                cb_col, exp_col = st.columns([0.05, 0.95])
                with cb_col:
                    is_selected = q["db_id"] in st.session_state.get("bulk_delete_questions", set())
                    st.checkbox("", value=is_selected, key=f"q_bulk_cb_{q['db_id']}",
                               label_visibility="collapsed", on_change=_toggle_bulk_question, args=(q["db_id"],))
                expander_parent = exp_col
            else:
                expander_parent = st
            with expander_parent.expander(f"#{q['id']} — {q['question'][:80]}"):
                q_key = f"q_{q['db_id']}"
                source = q.get("source", "manual")
                if source == "manual":
                    source_label = t("source_manual")
                elif source == "json_import":
                    source_label = t("source_json")
                elif source.startswith("material:"):
                    source_label = t("source_material", id=source.split(':')[1])
                else:
                    source_label = source
                st.caption(t("source", name=source_label))
                # Build tag options for selectbox
                tag_options = list(all_tags)
                if q["tag"] and q["tag"] not in tag_options:
                    tag_options.append(q["tag"])
                if not tag_options:
                    tag_options = [""]
                current_idx = tag_options.index(q["tag"]) if q["tag"] in tag_options else 0
                q_tag = st.selectbox(t("topic_label"), options=tag_options, index=current_idx, key=f"{q_key}_tag", disabled=read_only)
                q_text = st.text_area(t("question_label"), value=q["question"], key=f"{q_key}_text", disabled=read_only)
                q_explanation = st.text_area(t("explanation_label"), value=q.get("explanation", ""), key=f"{q_key}_expl", disabled=read_only)

                st.write(t("options_header"))
                options = []
                can_remove = not read_only and len(q["options"]) > 2
                for oi in range(len(q["options"])):
                    if can_remove:
                        opt_col, rm_col = st.columns([0.9, 0.1])
                        with opt_col:
                            opt = st.text_input(t("option_n", n=oi + 1), value=q["options"][oi], key=f"{q_key}_opt_{oi}", disabled=read_only)
                        with rm_col:
                            st.markdown("<div style='margin-top:1.65rem'></div>", unsafe_allow_html=True)
                            if st.button("✕", key=f"{q_key}_rm_opt_{oi}", help=t("remove_option_n", n=oi + 1)):
                                new_opts = [o for j, o in enumerate(q["options"]) if j != oi]
                                new_ans = q["answer_index"]
                                if oi < new_ans:
                                    new_ans -= 1
                                elif oi == new_ans:
                                    new_ans = 0
                                new_ans = min(new_ans, len(new_opts) - 1)
                                update_question(q["db_id"], q["tag"], q["question"], new_opts, new_ans, q.get("explanation", ""))
                                st.rerun()
                    else:
                        opt = st.text_input(t("option_n", n=oi + 1), value=q["options"][oi], key=f"{q_key}_opt_{oi}", disabled=read_only)
                    options.append(opt)

                if not read_only:
                    if st.button(t("add_option"), key=f"{q_key}_add_opt"):
                        new_opts = q["options"] + [t("option_n", n=len(q['options']) + 1)]
                        update_question(q["db_id"], q["tag"], q["question"], new_opts, q["answer_index"], q.get("explanation", ""))
                        st.rerun()

                q_answer = st.selectbox(
                    t("correct_answer_select"),
                    range(len(options)),
                    index=q["answer_index"],
                    format_func=lambda i: options[i] if i < len(options) else "",
                    key=f"{q_key}_ans",
                    disabled=read_only,
                )

                # --- Material references ---
                if materials and not read_only:
                    st.write(t("material_references"))
                    existing_links = {lk["material_id"]: lk["context"] for lk in all_q_mat_links.get(q["db_id"], [])}
                    q_mat_links = {}
                    for mat in materials:
                        mid = mat["id"]
                        type_icons = {"pdf": "📄", "youtube": "▶️", "image": "🖼️", "url": "🔗"}
                        icon = type_icons.get(mat["material_type"], "📎")
                        mlabel = mat["title"] or mat["url"] or t("no_title")
                        is_linked = st.checkbox(f"{icon} {mlabel}", value=mid in existing_links, key=f"{q_key}_mat_{mid}")
                        if is_linked:
                            ctx = existing_links.get(mid, "")
                            if mat["material_type"] == "youtube":
                                ctx = st.text_input(t("timestamps_hint"), value=ctx, key=f"{q_key}_mat_ctx_{mid}")
                            elif mat["material_type"] == "pdf":
                                ctx = st.text_input(t("pages_hint"), value=ctx, key=f"{q_key}_mat_ctx_{mid}")
                            q_mat_links[mid] = ctx

                if not read_only:
                    col_save, col_del = st.columns(2)
                    with col_save:
                        if st.button(t("save_question"), key=f"{q_key}_save", type="primary"):
                            update_question(q["db_id"], q_tag.strip(), q_text.strip(), options, q_answer, q_explanation.strip())
                            if materials:
                                links = [{"material_id": mid, "context": ctx} for mid, ctx in q_mat_links.items()]
                                set_question_material_links(q["db_id"], links)
                            st.success(t("question_updated"))
                            st.rerun()
                    with col_del:
                        if st.button(t("delete_question"), key=f"{q_key}_del"):
                            delete_question(q["db_id"])
                            st.rerun()

    st.divider()

    # --- Delete test (owner only) ---
    if user_role == "owner":
        st.subheader(t("danger_zone"))
        if st.button(t("delete_full_test"), type="secondary"):
            st.session_state[f"confirm_delete_{test_id}"] = True

        if st.session_state.get(f"confirm_delete_{test_id}"):
            st.warning(t("confirm_delete"))
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button(t("yes_delete"), type="primary"):
                    delete_test(test_id)
                    if "editing_test_id" in st.session_state:
                        del st.session_state.editing_test_id
                    st.session_state.page = "Tests"
                    st.rerun()
            with col_no:
                if st.button(t("cancel")):
                    del st.session_state[f"confirm_delete_{test_id}"]
                    st.rerun()


def _get_avatar_html(avatar_bytes, size=35):
    """Return HTML for a circular avatar image, or initials if no avatar."""
    if avatar_bytes:
        b64 = base64.b64encode(avatar_bytes).decode()
        return f'<img src="data:image/png;base64,{b64}" style="width:{size}px;height:{size}px;border-radius:50%;object-fit:cover;">'
    initial = st.session_state.get("username", "?")[0].upper()
    return (
        f'<div style="width:{size}px;height:{size}px;border-radius:50%;'
        f'background:#4A90D9;color:white;display:flex;align-items:center;'
        f'justify-content:center;font-size:{size//2}px;font-weight:bold;">'
        f'{initial}</div>'
    )


def _load_profile_to_session():
    """Load user profile from DB into session state if not cached."""
    if "profile_loaded" not in st.session_state:
        profile = get_user_profile(st.session_state.user_id)
        st.session_state.display_name = profile["display_name"] or st.session_state.username
        st.session_state.avatar_bytes = profile["avatar"]
        st.session_state.global_role = get_user_global_role(st.session_state.user_id)
        st.session_state.profile_loaded = True


def _get_global_role():
    """Get the current user's global role. Returns 'knower' if not logged in."""
    return st.session_state.get("global_role", "knower")


def _is_knowter_or_admin():
    """Check if user has knowter or admin global role."""
    return _get_global_role() in ("knowter", "admin")


def _is_global_admin():
    """Check if user has admin global role."""
    return _get_global_role() == "admin"


def _can_create_tests():
    """Check if user can create tests. Students cannot create tests."""
    return _get_global_role() in ("knower", "knowter", "admin")


def _can_create_programs():
    """Check if user can create programs. Only knowter and admin users can create programs."""
    return _get_global_role() in ("knowter", "admin")


def _needs_survey():
    """Check if current user needs to complete a survey to maintain access.

    Returns (needs_survey: bool, survey: dict or None)
    """
    if not _is_logged_in():
        return False, None
    user_id = st.session_state.get("user_id")
    status = get_user_survey_status(user_id)
    if not status or status["knowter_access_type"] != "survey":
        return False, None
    if status["access_revoked"]:
        return False, None
    if status["pending_approval"]:
        return False, None

    # Check if periodic survey is needed
    active_survey = get_active_periodic_survey()
    if active_survey and not has_completed_survey(user_id, active_survey["id"]):
        return True, active_survey
    return False, None


def _check_survey_deadline():
    """Check if user's survey deadline has passed and return warning info.

    Returns dict with keys: warning, days_remaining, deadline, overdue
    """
    if not _is_logged_in():
        return None
    user_id = st.session_state.get("user_id")
    status = get_user_survey_status(user_id)
    if not status or status["knowter_access_type"] != "survey":
        return None
    if status["access_revoked"] or status["pending_approval"]:
        return None

    if not status["survey_deadline"]:
        return None

    from datetime import datetime
    try:
        deadline = datetime.fromisoformat(status["survey_deadline"])
        now = datetime.now()
        days_remaining = (deadline - now).days

        return {
            "deadline": deadline.strftime("%Y-%m-%d"),
            "days_remaining": days_remaining,
            "warning": days_remaining <= 7,
            "overdue": days_remaining < 0
        }
    except (ValueError, TypeError):
        return None


def _is_pending_approval():
    """Check if user is awaiting admin approval for knowter access."""
    if not _is_logged_in():
        return False
    user_id = st.session_state.get("user_id")
    status = get_user_survey_status(user_id)
    if not status:
        return False
    return status.get("pending_approval", False)


def _needs_survey_for_feature():
    """Check if a survey-based knowter needs to complete a survey before accessing knowter features.
    Returns a tuple: (needs_survey, survey_type, survey)
    - needs_survey: True if user needs to complete a survey
    - survey_type: 'initial' or 'periodic'
    - survey: The survey object to show, or None
    """
    if not _is_logged_in():
        return (False, None, None)

    user_id = st.session_state.get("user_id")
    current_role = st.session_state.get("current_role", "knower")

    # Only check for knowters with survey-based access
    if current_role != "knowter":
        return (False, None, None)

    status = get_user_survey_status(user_id)
    if not status:
        return (False, None, None)

    # Paid or granted access types don't need surveys
    if status.get("knowter_access_type") in ("paid", "granted"):
        return (False, None, None)

    # Check if initial survey is not completed
    if not status.get("initial_survey_completed"):
        survey = get_active_initial_survey()
        if survey and not has_completed_survey(user_id, survey["id"]):
            return (True, "initial", survey)

    # Check if access is on hold (needs periodic survey)
    if status.get("access_on_hold"):
        survey = get_active_periodic_survey()
        if survey and not has_completed_survey(user_id, survey["id"]):
            return (True, "periodic", survey)

    # Check if deadline passed and needs periodic survey
    from datetime import datetime
    deadline = status.get("survey_deadline")
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline)
            if datetime.now() > deadline_dt:
                # Deadline passed, put on hold and require survey
                from db import put_access_on_hold
                put_access_on_hold(user_id)
                survey = get_active_periodic_survey()
                if survey and not has_completed_survey(user_id, survey["id"]):
                    return (True, "periodic", survey)
        except (ValueError, TypeError):
            pass

    return (False, None, None)


def _show_survey_required_message(survey_type, survey, return_page):
    """Show a message that a survey is required and a button to take it."""
    st.warning(t("access_on_hold_message"))

    if survey:
        if st.button(t("take_survey_now"), type="primary"):
            st.session_state["return_after_survey"] = return_page
            if survey_type == "initial":
                st.session_state.page = "Take Initial Survey"
            else:
                st.session_state.page = "Take Periodic Survey"
                st.session_state["pending_survey"] = survey
            st.rerun()
    else:
        st.info(t("no_active_survey"))


def show_profile():
    """Show profile settings page."""
    st.header(t("profile_header"))

    profile = get_user_profile(st.session_state.user_id)
    current_name = profile["display_name"] or st.session_state.username
    current_avatar = profile["avatar"]

    if current_avatar:
        st.image(current_avatar, width=120)
    else:
        st.markdown(_get_avatar_html(None, size=120), unsafe_allow_html=True)

    st.divider()

    display_name = st.text_input(t("display_name"), value=current_name, key="profile_name_input")

    uploaded_file = st.file_uploader(
        t("upload_photo"),
        type=["png", "jpg", "jpeg"],
        key="profile_avatar_upload",
    )

    if st.button(t("save"), type="primary"):
        avatar_data = None
        if uploaded_file is not None:
            avatar_data = uploaded_file.read()
        elif current_avatar:
            avatar_data = current_avatar

        if avatar_data is not None:
            update_user_profile(st.session_state.user_id, display_name, avatar_data)
        else:
            update_user_profile(st.session_state.user_id, display_name)

        st.session_state.display_name = display_name
        st.session_state.avatar_bytes = avatar_data
        st.session_state.username = display_name
        st.success(t("profile_updated"))
        st.session_state.page = st.session_state.get("prev_page", "Tests")
        st.rerun()

    # Delete account section
    st.divider()
    with st.expander(f"⚠️ {t('delete_account')}", expanded=False):
        st.warning(t("delete_account_warning"))
        # Get user's email from the database
        from db import get_connection
        conn = get_connection()
        row = conn.execute("SELECT username FROM users WHERE id = ?", (st.session_state.user_id,)).fetchone()
        conn.close()
        user_email = row[0] if row else ""

        confirm_email = st.text_input(
            t("delete_account_confirm_email"),
            key="delete_account_confirm_input",
            placeholder=user_email,
        )
        email_matches = confirm_email.lower().strip() == user_email.lower().strip()
        if st.button(t("delete_account"), type="primary", disabled=not email_matches):
            delete_user_account(st.session_state.user_id)
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.logout()
            st.rerun()


def show_admin_panel():
    """Show the admin panel for managing user roles."""
    if not _is_global_admin():
        st.error(t("no_permission"))
        return

    st.header(t("admin_panel"))
    st.write(t("admin_panel_desc"))

    # Get all users
    users = get_all_users_with_roles()

    if not users:
        st.info(t("no_users"))
        return

    # Search filter
    search = st.text_input(t("search_user"), placeholder=t("search_user_placeholder"), key="admin_user_search")

    filtered_users = users
    if search:
        search_lower = search.lower()
        filtered_users = [u for u in users if search_lower in u["email"].lower() or (u["display_name"] and search_lower in u["display_name"].lower())]

    st.write(t("total_users", n=len(filtered_users)))

    # Role options (student role removed from global roles - only applies at test/program level)
    role_options = ["tester", "knower", "knowter", "admin"]
    role_labels = {
        "tester": t("global_role_tester"),
        "knower": t("global_role_knower"),
        "knowter": t("global_role_knowter"),
        "admin": t("global_role_admin"),
    }

    # Display users in a table-like format
    for user in filtered_users:
        display = user["display_name"] or user["email"]
        current_role = user["global_role"] or "knower"
        # Handle legacy roles by treating them as 'knower'
        if current_role in ("student", "free"):
            current_role = "knower"
        elif current_role == "premium":
            current_role = "knowter"
        current_idx = role_options.index(current_role) if current_role in role_options else 0
        is_self = user["id"] == st.session_state.user_id

        col_info, col_role, col_delete = st.columns([3, 2, 0.5])
        with col_info:
            st.write(f"**{display}**")
            if user["display_name"]:
                st.caption(user["email"])
        with col_role:
            new_role = st.selectbox(
                t("role"),
                options=role_options,
                index=current_idx,
                format_func=lambda x: role_labels.get(x, x),
                label_visibility="collapsed",
                key=f"role_select_{user['id']}",
            )
            if new_role != current_role:
                set_user_global_role(user["id"], new_role)
                st.rerun()
        with col_delete:
            if is_self:
                st.button("🗑️", key=f"delete_user_{user['id']}", disabled=True, help=t("cannot_delete_self"))
            else:
                with st.popover("🗑️", help=t("delete_user")):
                    st.warning(t("delete_user_confirm", user=display))
                    st.caption(t("delete_user_admin_warning"))
                    if st.button(t("delete_user"), key=f"confirm_delete_{user['id']}", type="primary"):
                        delete_user_account(user["id"])
                        st.success(t("user_deleted", user=display))
                        st.rerun()


# --- Survey Pages ---

def show_survey_page(survey):
    """Display a survey for the user to complete."""
    user_id = st.session_state.get("user_id")

    # Check if user has already completed this survey
    if has_completed_survey(user_id, survey["id"]):
        st.header(f"📋 {survey['title']}")
        st.success(t("survey_already_completed"))

        # Show appropriate message based on survey type
        status = get_user_survey_status(user_id)
        if survey["survey_type"] == "initial" and status and status.get("pending_approval"):
            st.info(t("pending_approval_message"))

        if st.button(t("back")):
            st.session_state.page = "Home"
            st.rerun()
        return

    st.header(f"📋 {survey['title']}")
    if survey.get("description"):
        st.write(survey["description"])

    st.divider()

    questions = get_survey_questions(survey["id"])
    if not questions:
        st.warning(t("no_questions"))
        return

    # Initialize answers in session state
    if "survey_answers" not in st.session_state:
        st.session_state.survey_answers = {}

    all_required_answered = True

    for q in questions:
        st.write(f"**{q['question_num']}. {q['question_text']}**")
        if q["required"]:
            st.caption(f"* {t('required_field')}")

        key = f"survey_q_{q['id']}"

        if q["question_type"] == "multiple_choice":
            options = q["options"] if q["options"] else []
            if options:
                answer = st.radio(
                    "", options=options,
                    key=key, label_visibility="collapsed"
                )
                st.session_state.survey_answers[q["id"]] = {"answer_text": answer}
            else:
                st.warning("No options defined")
                if q["required"]:
                    all_required_answered = False

        elif q["question_type"] == "text":
            answer = st.text_area("", key=key, label_visibility="collapsed")
            st.session_state.survey_answers[q["id"]] = {"answer_text": answer}
            if q["required"] and not answer.strip():
                all_required_answered = False

        elif q["question_type"] == "rating":
            options = q["options"] if q["options"] else ["1", "2", "3", "4", "5"]
            answer = st.select_slider("", options=options, key=key, label_visibility="collapsed")
            st.session_state.survey_answers[q["id"]] = {"answer_text": answer}

        elif q["question_type"] == "checkbox":
            selected = []
            for i, opt in enumerate(q["options"]):
                if st.checkbox(opt, key=f"{key}_{i}"):
                    selected.append(opt)
            st.session_state.survey_answers[q["id"]] = {"answer_options": selected}
            if q["required"] and not selected:
                all_required_answered = False

        st.divider()

    if st.button(t("submit_survey"), type="primary", disabled=not all_required_answered):
        answers = [
            {"question_id": q_id, **data}
            for q_id, data in st.session_state.survey_answers.items()
        ]
        user_id = st.session_state.get("user_id")
        submit_survey_response(survey["id"], user_id, answers)

        # Update user status based on survey type
        from datetime import datetime, timedelta
        new_deadline = (datetime.now() + timedelta(days=30)).isoformat()
        status = get_user_survey_status(user_id)

        if survey["survey_type"] == "initial":
            # Initial survey completed - set deadline and release any hold
            if status:
                update_user_survey_status(
                    user_id,
                    initial_completed=True,
                    deadline=new_deadline,
                    access_on_hold=False
                )
            else:
                # This shouldn't happen in normal flow, but handle it
                create_user_survey_status(user_id, "survey", initial_completed=True, pending_approval=False)
                update_user_survey_status(user_id, deadline=new_deadline)
        elif survey["survey_type"] == "periodic" and status:
            # Periodic survey - reset deadline and release any hold
            update_user_survey_status(
                user_id,
                last_survey_id=survey["id"],
                deadline=new_deadline,
                access_on_hold=False
            )

        # Clear answers and show success
        if "survey_answers" in st.session_state:
            del st.session_state.survey_answers
        st.success(t("survey_completed"))
        # Return to the page they were trying to access, or home
        return_page = st.session_state.get("return_after_survey", "Home")
        st.session_state.page = return_page
        if "return_after_survey" in st.session_state:
            del st.session_state["return_after_survey"]
        st.rerun()


def show_admin_surveys():
    """Show admin panel for managing surveys."""
    if not _is_global_admin():
        st.error(t("no_permission"))
        return

    st.header(f"📋 {t('surveys')}")

    # Navigation tabs
    tab1, tab2, tab3 = st.tabs([t("surveys"), t("users_pending_approval"), t("users_needing_survey")])

    with tab1:
        _show_survey_management()

    with tab2:
        _show_pending_approvals()

    with tab3:
        _show_survey_users()


def _show_survey_management():
    """Show survey list and management UI."""
    # Create new survey button
    if st.button(t("create_survey"), type="primary"):
        st.session_state.creating_survey = True
        st.rerun()

    # Show survey creation form
    if st.session_state.get("creating_survey"):
        _show_survey_creation_form()
        return

    # Show survey editor if editing
    if st.session_state.get("editing_survey_id"):
        _show_survey_editor(st.session_state.editing_survey_id)
        return

    # Show survey statistics if viewing
    if st.session_state.get("viewing_survey_stats"):
        _show_survey_statistics(st.session_state.viewing_survey_stats)
        return

    # List all surveys
    surveys = get_all_surveys()
    if not surveys:
        st.info(t("no_surveys"))
        return

    for survey in surveys:
        with st.container(border=True):
            col_info, col_actions = st.columns([3, 2])
            with col_info:
                title_display = survey["title"]
                if survey["is_active"]:
                    title_display = f"✅ {title_display}"
                st.subheader(title_display)
                type_label = t(f"survey_type_{survey['survey_type']}")
                st.caption(f"{t('survey_type')}: {type_label}")
                st.caption(f"{t('n_questions', n=survey['question_count'])} · {t('n_responses', n=survey['response_count'])}")

            with col_actions:
                btn_cols = st.columns(4)
                with btn_cols[0]:
                    if st.button("✏️", key=f"edit_survey_{survey['id']}", help=t("edit")):
                        st.session_state.editing_survey_id = survey["id"]
                        st.rerun()
                with btn_cols[1]:
                    if st.button("📊", key=f"stats_survey_{survey['id']}", help=t("survey_statistics")):
                        st.session_state.viewing_survey_stats = survey["id"]
                        st.rerun()
                with btn_cols[2]:
                    if not survey["is_active"]:
                        if st.button("✅", key=f"activate_survey_{survey['id']}", help=t("set_as_active")):
                            set_active_survey(survey["id"], survey["survey_type"])
                            st.success(t("survey_activated"))
                            st.rerun()
                    else:
                        st.button("✅", key=f"active_survey_{survey['id']}", disabled=True, help=t("active_survey"))
                with btn_cols[3]:
                    with st.popover("🗑️", help=t("delete")):
                        st.warning(t("confirm_delete"))
                        if st.button(t("delete"), key=f"confirm_del_survey_{survey['id']}", type="primary"):
                            delete_survey(survey["id"])
                            st.rerun()


def _show_survey_creation_form():
    """Show form to create a new survey."""
    st.subheader(t("create_survey"))

    if st.button(t("back")):
        st.session_state.creating_survey = False
        st.rerun()

    title = st.text_input(t("survey_title"), key="new_survey_title")
    description = st.text_area(t("survey_description"), key="new_survey_desc")

    survey_types = ["initial", "periodic", "feedback"]
    type_labels = {
        "initial": t("survey_type_initial"),
        "periodic": t("survey_type_periodic"),
        "feedback": t("survey_type_feedback"),
    }
    survey_type = st.selectbox(
        t("survey_type"),
        options=survey_types,
        format_func=lambda x: type_labels[x],
        key="new_survey_type"
    )

    if st.button(t("create_survey"), type="primary", key="create_survey_btn"):
        if not title.strip():
            st.warning(t("title_required"))
        else:
            survey_id = create_survey(title.strip(), description.strip(), survey_type)
            st.session_state.creating_survey = False
            st.session_state.editing_survey_id = survey_id
            st.success(t("survey_saved"))
            st.rerun()


def _show_survey_editor(survey_id):
    """Show survey editor with question management."""
    survey = get_survey(survey_id)
    if not survey:
        st.error("Survey not found")
        st.session_state.editing_survey_id = None
        st.rerun()
        return

    st.subheader(f"{t('edit_survey')}: {survey['title']}")

    if st.button(t("back_to_surveys")):
        st.session_state.editing_survey_id = None
        st.rerun()

    # Survey metadata
    with st.expander(t("survey_description"), expanded=False):
        new_title = st.text_input(t("survey_title"), value=survey["title"], key="edit_survey_title")
        new_desc = st.text_area(t("survey_description"), value=survey["description"] or "", key="edit_survey_desc")
        if st.button(t("save"), key="save_survey_meta"):
            update_survey(survey_id, new_title, new_desc, survey.get("valid_from"), survey.get("valid_until"))
            st.success(t("survey_saved"))
            st.rerun()

    st.divider()

    # Questions section
    st.subheader(t("survey_questions"))

    questions = get_survey_questions(survey_id)

    # Add new question form
    with st.expander(f"➕ {t('add_survey_question')}", expanded=False):
        q_types = ["multiple_choice", "text", "rating", "checkbox"]
        q_type_labels = {
            "multiple_choice": t("question_type_multiple_choice"),
            "text": t("question_type_text"),
            "rating": t("question_type_rating"),
            "checkbox": t("question_type_checkbox"),
        }

        new_q_text = st.text_area(t("question"), key="new_sq_text")
        new_q_type = st.selectbox(
            t("question_type"),
            options=q_types,
            format_func=lambda x: q_type_labels[x],
            key="new_sq_type"
        )

        # Options for choice-based questions
        new_q_options = []
        if new_q_type in ("multiple_choice", "checkbox", "rating"):
            st.write(t("options"))
            for i in range(5):
                opt = st.text_input(t("option_placeholder", n=i+1), key=f"new_sq_opt_{i}")
                if opt.strip():
                    new_q_options.append(opt.strip())

        new_q_required = st.checkbox(t("required_field"), value=True, key="new_sq_required")

        if st.button(t("add_survey_question"), type="primary", key="add_sq_btn"):
            if not new_q_text.strip():
                st.warning(t("question") + " is required")
            else:
                next_num = get_next_survey_question_num(survey_id)
                add_survey_question(survey_id, next_num, new_q_type, new_q_text.strip(), new_q_options, new_q_required)
                st.success(t("question_saved"))
                st.rerun()

    # Display existing questions
    for q in questions:
        with st.container(border=True):
            col_q, col_actions = st.columns([4, 1])
            with col_q:
                q_type_label = t(f"question_type_{q['question_type']}")
                st.write(f"**{q['question_num']}. {q['question_text']}**")
                st.caption(f"{q_type_label} | {'*' if q['required'] else ''}{t('required_field') if q['required'] else t('optional_field')}")
                if q["options"]:
                    st.caption(f"{t('options')}: {', '.join(q['options'])}")
            with col_actions:
                with st.popover("🗑️", help=t("delete")):
                    if st.button(t("delete"), key=f"del_sq_{q['id']}", type="primary"):
                        delete_survey_question(q["id"])
                        st.success(t("question_deleted"))
                        st.rerun()


def _show_survey_statistics(survey_id):
    """Show survey response statistics."""
    survey = get_survey(survey_id)
    if not survey:
        st.session_state.viewing_survey_stats = None
        st.rerun()
        return

    st.subheader(f"📊 {survey['title']}")

    if st.button(t("back_to_surveys")):
        st.session_state.viewing_survey_stats = None
        st.rerun()

    responses = get_survey_responses(survey_id)
    st.write(t("n_responses", n=len(responses)))

    if not responses:
        st.info(t("no_responses_yet"))
        return

    # Show statistics per question
    stats = get_survey_answer_statistics(survey_id)

    for q_id, stat in stats.items():
        q = stat.get("question", {})
        st.subheader(f"{q.get('question_num', '?')}. {q.get('question_text', 'Unknown')}")

        if "counts" in stat:
            # Multiple choice, rating, checkbox
            counts = stat["counts"]
            if counts:
                import pandas as pd
                df = pd.DataFrame(list(counts.items()), columns=["Option", "Count"])
                st.bar_chart(df.set_index("Option"))
        elif "text_responses" in stat:
            # Text responses
            for i, resp in enumerate(stat["text_responses"][:10]):
                st.caption(f"• {resp}")
            if len(stat["text_responses"]) > 10:
                st.caption(f"... and {len(stat['text_responses']) - 10} more")

        st.divider()

    # Show individual responses
    with st.expander(t("view_responses")):
        for resp in responses:
            st.write(f"**{resp['display_name']}** ({resp['email']})")
            st.caption(f"{resp['completed_at']}")
            answers = get_survey_response_answers(resp["id"])
            for ans in answers:
                st.write(f"• {ans['question_text']}: {ans['answer_text'] or ', '.join(ans['answer_options'])}")
            st.divider()


def _show_pending_approvals():
    """Show users pending approval for knowter access."""
    pending = get_users_pending_approval()

    if not pending:
        st.info(t("no_users"))
        return

    st.write(f"**{len(pending)} {t('users_pending_approval')}**")

    for user in pending:
        with st.container(border=True):
            col_info, col_action = st.columns([3, 1])
            with col_info:
                st.write(f"**{user['display_name']}**")
                st.caption(user["email"])
                if user["survey_date"]:
                    st.caption(f"📅 {user['survey_date']}")
            with col_action:
                if st.button(t("approve_access"), key=f"approve_{user['user_id']}", type="primary"):
                    approve_knowter_access(user["user_id"])
                    st.success(t("access_approved"))
                    st.rerun()


def _show_survey_users():
    """Show users with survey deadlines."""
    users = get_users_needing_survey()
    overdue = get_users_with_overdue_surveys()

    if overdue:
        st.subheader(f"⚠️ {t('overdue')} ({len(overdue)})")
        for user in overdue:
            with st.container(border=True):
                col_info, col_action = st.columns([3, 1])
                with col_info:
                    st.write(f"**{user['display_name']}**")
                    st.caption(user["email"])
                    st.caption(f"⏰ {user['deadline']}")
                with col_action:
                    if st.button(t("revoke_access"), key=f"revoke_{user['user_id']}", type="primary"):
                        revoke_survey_based_access(user["user_id"])
                        st.success(t("access_revoked"))
                        st.rerun()

    if users:
        st.subheader(t("users_needing_survey"))
        for user in users:
            # Skip if already in overdue list
            if any(o["user_id"] == user["user_id"] for o in overdue):
                continue
            with st.container(border=True):
                st.write(f"**{user['display_name']}**")
                st.caption(user["email"])
                if user["deadline"]:
                    from datetime import datetime
                    try:
                        deadline = datetime.fromisoformat(user["deadline"])
                        days = (deadline - datetime.now()).days
                        if days <= 7:
                            st.warning(t("days_remaining", n=days))
                        else:
                            st.caption(f"📅 {t('survey_deadline')}: {user['deadline'][:10]}")
                    except (ValueError, TypeError):
                        st.caption(f"📅 {user['deadline']}")
    elif not overdue:
        st.info(t("no_users"))


def _toggle_bulk_program(prog_id):
    """Callback to toggle program selection in bulk delete mode."""
    if "bulk_delete_programs" not in st.session_state:
        st.session_state.bulk_delete_programs = set()
    if prog_id in st.session_state.bulk_delete_programs:
        st.session_state.bulk_delete_programs.discard(prog_id)
    else:
        st.session_state.bulk_delete_programs.add(prog_id)


def _get_program_export_data(program_id):
    """Generate export data for a program including its tests. Returns (json_string, title)."""
    import json as _json
    prog = get_program(program_id)
    if not prog:
        return "{}", "unknown"

    # Get program tests with their details
    prog_tests = get_program_tests(program_id)
    export_tests = []
    for pt in prog_tests:
        # Get full test data for each test in the program
        test_export_data, _ = _get_test_export_data(pt["id"])
        test_data = _json.loads(test_export_data)
        # Add program-specific visibility
        test_data["program_visibility"] = pt.get("program_visibility", "public")
        export_tests.append(test_data)

    # Get program collaborators
    export_collabs = []
    for c in get_program_collaborators(program_id):
        export_collabs.append({"email": c["email"], "role": c["role"]})

    export_data = {
        "title": prog["title"],
        "description": prog.get("description", ""),
        "visibility": prog.get("visibility", "public"),
        "collaborators": export_collabs,
        "tests": export_tests,
    }
    return _json.dumps(export_data, ensure_ascii=False, indent=2), prog["title"]


def _render_program_card(prog, user_id, has_access=True, prefix="", bulk_delete_mode=False):
    """Render a single program card with select/edit/export buttons."""
    is_owner = prog.get("owner_id") == user_id
    prog_role = get_user_role_for_program(prog["id"], user_id) if not is_owner else None
    can_edit = _is_global_admin() or is_owner or prog_role in ("reviewer", "admin")

    with st.container(border=True):
        if bulk_delete_mode:
            col_check, col_info, col_btn = st.columns([0.5, 4, 1.5])
            with col_check:
                # Initialize set if needed
                if "bulk_delete_programs" not in st.session_state:
                    st.session_state.bulk_delete_programs = set()
                is_selected = prog["id"] in st.session_state.bulk_delete_programs
                st.checkbox("", value=is_selected, key=f"{prefix}bulk_select_prog_{prog['id']}",
                           label_visibility="collapsed", on_change=_toggle_bulk_program, args=(prog["id"],))
        else:
            col_info, col_btn = st.columns([3.5, 2])
        with col_info:
            title_display = prog["title"]
            if prog.get("visibility") == "private" and not has_access:
                title_display = "🔒 " + title_display
            st.subheader(title_display)
            if prog.get("description"):
                st.write(prog["description"])
            st.caption(t("n_tests", n=prog['test_count']))
        with col_btn:
            # Show buttons in a row: Select, Edit (if can_edit), Export (if can_edit)
            btn_cols = st.columns(3 if can_edit else 1)
            col_idx = 0
            with btn_cols[col_idx]:
                if has_access:
                    if st.button("▶️", key=f"{prefix}prog_sel_{prog['id']}", width="stretch", help=t("select")):
                        st.session_state.selected_program = prog["id"]
                        st.session_state.page = "Configurar Curso"
                        st.rerun()
                else:
                    st.button("▶️", key=f"{prefix}prog_sel_{prog['id']}", width="stretch", disabled=True, help=t("select"))
            if can_edit:
                col_idx += 1
                with btn_cols[col_idx]:
                    if st.button("✏️", key=f"{prefix}prog_edit_{prog['id']}", width="stretch", help=t("edit_course")):
                        st.session_state.editing_program_id = prog["id"]
                        st.session_state.page = "Editar Curso"
                        st.rerun()
                col_idx += 1
                with btn_cols[col_idx]:
                    export_data, export_title = _get_program_export_data(prog["id"])
                    st.download_button(
                        "⬇️",
                        data=export_data,
                        file_name=f"{export_title}.json",
                        mime="application/json",
                        key=f"{prefix}prog_export_{prog['id']}",
                        width="stretch",
                        help=t("export_course"),
                    )


def show_programs():
    """Show the program catalog."""
    user_id = st.session_state.user_id
    programs = get_all_programs(user_id)
    shared_programs = get_shared_programs(user_id)
    shared_prog_ids = {p["id"] for p in shared_programs}

    st.header(t("courses_header"))

    # --- Pending Program Invitations Section ---
    invitations = get_pending_invitations(user_id)
    program_invitations = invitations["programs"]

    if program_invitations:
        with st.expander(f"📩 {t('pending_invitations')} ({len(program_invitations)})", expanded=True):
            for inv in program_invitations:
                with st.container(border=True):
                    col_info, col_actions = st.columns([3, 1])
                    with col_info:
                        st.markdown(f"**{t('program_invitation')}:** {inv['title']}")
                        st.caption(f"{t('invited_by', name=inv['inviter_name'])} {t('invited_as', role=inv['role'])}")
                    with col_actions:
                        c1, c2 = st.columns(2)
                        if c1.button("✓", key=f"accept_prog_cat_{inv['program_id']}", help=t("accept_invitation")):
                            accept_program_invitation(inv['program_id'], user_id)
                            st.success(t("invitation_accepted"))
                            st.rerun()
                        if c2.button("✕", key=f"decline_prog_cat_{inv['program_id']}", help=t("decline_invitation")):
                            decline_program_invitation(inv['program_id'], user_id)
                            st.info(t("invitation_declined"))
                            st.rerun()
        st.divider()

    # Admin bulk delete mode and create button
    bulk_delete_mode = False
    if _is_global_admin():
        col_create, col_bulk = st.columns([1, 1])
        with col_create:
            if st.button(t("create_course"), type="secondary", width="stretch"):
                st.session_state.page = "Crear Curso"
                st.rerun()
        with col_bulk:
            bulk_delete_mode = st.toggle(t("bulk_delete_mode"), key="prog_bulk_delete_mode")
            if bulk_delete_mode:
                if "bulk_delete_programs" not in st.session_state:
                    st.session_state.bulk_delete_programs = set()
    elif _can_create_programs():
        if st.button(t("create_course"), type="secondary"):
            st.session_state.page = "Crear Curso"
            st.rerun()

    # Show bulk delete controls below the create button
    if bulk_delete_mode:
        selected_count = len(st.session_state.get("bulk_delete_programs", set()))
        col_info, col_del = st.columns([3, 1])
        with col_info:
            st.info(t("selected_items", n=selected_count))
        with col_del:
            if st.button(t("delete_selected"), type="primary", disabled=selected_count == 0, width="stretch"):
                for prog_id in st.session_state.bulk_delete_programs:
                    delete_program(prog_id)
                st.session_state.bulk_delete_programs = set()
                st.success(t("courses_deleted", n=selected_count))
                st.rerun()

    # Build accessible set
    accessible_ids = shared_prog_ids | {p["id"] for p in programs if p.get("owner_id") == user_id}

    def _prog_has_access(p):
        if p.get("visibility", "public") in ("public", "restricted"):
            return True
        return p["id"] in accessible_ids

    my_progs = [p for p in programs if p.get("owner_id") == user_id]
    other_progs = [p for p in programs if p.get("owner_id") != user_id and p["id"] not in shared_prog_ids]

    if my_progs:
        st.subheader(t("my_courses"))
        for prog in my_progs:
            _render_program_card(prog, user_id, has_access=True, prefix="my_", bulk_delete_mode=bulk_delete_mode)

    if shared_programs:
        st.subheader(t("shared_courses"))
        for prog in shared_programs:
            _render_program_card(prog, user_id, has_access=True, prefix="shared_", bulk_delete_mode=bulk_delete_mode)

    if other_progs:
        if my_progs or shared_programs:
            st.subheader(t("all_courses"))
        for prog in other_progs:
            _render_program_card(prog, user_id, has_access=_prog_has_access(prog), bulk_delete_mode=bulk_delete_mode)

    if not programs and not shared_programs:
        st.info(t("no_courses"))


def show_create_program():
    """Show create program form."""
    # Only premium and admin users can create programs
    if not _can_create_programs():
        st.error(t("no_permission"))
        if st.button(t("back_to_courses")):
            st.session_state.page = "Cursos"
            st.rerun()
        return

    # Check if survey-based knowter needs to complete a survey
    needs_survey, survey_type, survey = _needs_survey_for_feature()
    if needs_survey:
        st.header(t("create_new_course"))
        _show_survey_required_message(survey_type, survey, "Crear Curso")
        return

    st.header(t("create_new_course"))

    if st.button(t("back")):
        st.session_state.page = "Cursos"
        st.rerun()

    title = st.text_input(t("course_title"), key="new_prog_title")
    description = st.text_area(t("description"), key="new_prog_desc")

    if st.button(t("create_course_btn"), type="primary"):
        if not title.strip():
            st.warning(t("title_required"))
        else:
            program_id = create_program(st.session_state.user_id, title.strip(), description.strip())
            st.session_state.editing_program_id = program_id
            st.session_state.page = "Editar Curso"
            st.rerun()


def show_program_editor():
    """Show program editor page."""
    # Check if survey-based knowter needs to complete a survey
    needs_survey, survey_type, survey = _needs_survey_for_feature()
    if needs_survey:
        st.header(t("edit_course"))
        _show_survey_required_message(survey_type, survey, "Editar Curso")
        return

    program_id = st.session_state.get("editing_program_id")
    if not program_id:
        st.session_state.page = "Cursos"
        st.rerun()
        return

    prog = get_program(program_id)
    if not prog:
        st.error(t("course_not_found"))
        return

    user_id = st.session_state.get("user_id")
    is_owner = prog["owner_id"] == user_id
    if _is_global_admin():
        prog_role = "owner"  # Global admins have full access to all programs
    elif is_owner:
        prog_role = "owner"
    else:
        prog_role = get_user_role_for_program(program_id, user_id)
    if not prog_role:
        st.error(t("no_permission"))
        return
    read_only = prog_role in ("guest", "student")
    meta_disabled = prog_role not in ("owner", "admin")

    st.header(t("edit_colon", name=prog['title']))

    if st.button(t("back")):
        if "editing_program_id" in st.session_state:
            del st.session_state.editing_program_id
        st.session_state.page = "Cursos"
        st.rerun()

    # --- Metadata ---
    st.subheader(t("course_info"))
    new_title = st.text_input(t("title"), value=prog["title"], key="edit_prog_title", disabled=meta_disabled)
    new_desc = st.text_area(t("description"), value=prog["description"] or "", key="edit_prog_desc", disabled=meta_disabled)

    visibility_options = ["public", "restricted", "private", "hidden"]
    visibility_labels = {
        "public": t("visibility_public"),
        "restricted": t("visibility_restricted"),
        "private": t("visibility_private"),
        "hidden": t("visibility_hidden"),
    }
    current_vis = prog.get("visibility", "public")
    current_vis_index = visibility_options.index(current_vis) if current_vis in visibility_options else 0
    new_visibility = st.selectbox(
        t("visibility"), options=visibility_options,
        index=current_vis_index,
        format_func=lambda x: visibility_labels[x],
        key="edit_prog_visibility",
        disabled=meta_disabled,
    )

    if not meta_disabled:
        if st.button(t("save_info"), type="primary"):
            if not new_title.strip():
                st.warning(t("title_required"))
            else:
                update_program(program_id, new_title.strip(), new_desc.strip(), new_visibility)
                st.success(t("info_updated"))
                st.rerun()

    st.divider()

    # --- Tests in program ---
    st.subheader(t("tests_included"))

    # Visibility labels for program access
    visibility_labels = {
        "public": t("visibility_public"),
        "restricted": t("visibility_restricted"),
        "private": t("visibility_private"),
        "hidden": t("visibility_hidden"),
    }

    prog_tests = get_program_tests(program_id)
    if prog_tests:
        for pt in prog_tests:
            if not meta_disabled:
                col_info, col_vis, col_rm = st.columns([3, 2, 0.5])
                with col_info:
                    st.write(f"**{pt['title']}** ({t('n_questions', n=pt['question_count'])})")
                with col_vis:
                    # Get available visibility options based on test's base visibility
                    test_visibility = pt.get("test_visibility", "public")
                    available_options = get_visibility_options_for_test(test_visibility)
                    current_visibility = pt.get("program_visibility", test_visibility)
                    # Ensure current visibility is in available options
                    if current_visibility not in available_options:
                        current_visibility = available_options[0]
                    current_idx = available_options.index(current_visibility)
                    new_visibility = st.selectbox(
                        t("course_visibility"),
                        options=available_options,
                        index=current_idx,
                        format_func=lambda x: visibility_labels.get(x, x),
                        key=f"prog_visibility_{pt['id']}",
                        label_visibility="collapsed",
                    )
                    if new_visibility != current_visibility:
                        update_program_test_visibility(program_id, pt["id"], new_visibility)
                        st.rerun()
                with col_rm:
                    if st.button("🗑️", key=f"rm_pt_{pt['id']}"):
                        remove_test_from_program(program_id, pt["id"])
                        st.rerun()
            else:
                visibility_label = visibility_labels.get(pt.get("program_visibility", "public"), t("visibility_public"))
                st.write(f"**{pt['title']}** ({t('n_questions', n=pt['question_count'])}) - {visibility_label}")
    else:
        st.info(t("no_tests_in_course"))

    # Add test (owner and admin only)
    if not meta_disabled:
        all_tests = get_all_tests(st.session_state.user_id)
        current_test_ids = {pt["id"] for pt in prog_tests}
        # Only show tests the user has admin or reviewer access to
        available_tests = []
        for tt in all_tests:
            if tt["id"] in current_test_ids:
                continue
            # Check if user is owner or has admin/reviewer role
            if tt.get("owner_id") == st.session_state.user_id:
                available_tests.append(tt)
            else:
                user_role = get_user_role_for_test(tt["id"], st.session_state.user_id)
                if user_role in ("admin", "reviewer"):
                    available_tests.append(tt)

        if available_tests:
            st.write(t("add_test_label"))
            # Create a dict mapping test_id to test info for visibility lookup
            test_info_map = {tt["id"]: tt for tt in available_tests}
            col_test, col_vis_add = st.columns([3, 2])
            with col_test:
                test_options = {tt["id"]: f"{tt['title']} ({t('n_questions_abbrev', n=tt['question_count'])})" for tt in available_tests}
                selected_test_id = st.selectbox(
                    t("test_label"), options=list(test_options.keys()),
                    format_func=lambda x: test_options[x],
                    key="add_prog_test",
                    label_visibility="collapsed",
                )
            with col_vis_add:
                # Get visibility options based on selected test's base visibility
                selected_test_visibility = test_info_map[selected_test_id].get("visibility", "public")
                add_visibility_options = get_visibility_options_for_test(selected_test_visibility)
                add_program_visibility = st.selectbox(
                    t("course_visibility"),
                    options=add_visibility_options,
                    index=0,
                    format_func=lambda x: visibility_labels.get(x, x),
                    key="add_prog_test_visibility",
                    label_visibility="collapsed",
                )
            if st.button(t("add_test_btn")):
                add_test_to_program(program_id, selected_test_id, add_program_visibility)
                st.rerun()

    st.divider()

    # --- Collaborators (owner and admin only) ---
    if prog_role in ("owner", "admin"):
        st.subheader(t("collaborators"))
        collabs = get_program_collaborators(program_id)
        if collabs:
            for c in collabs:
                col_email, col_status, col_role, col_del = st.columns([2.5, 1, 2, 0.5])
                with col_email:
                    st.write(c["email"])
                with col_status:
                    status = c.get("status", "accepted")
                    if status == "pending":
                        st.caption(f"⏳ {t('status_pending')}")
                    else:
                        st.caption(f"✓ {t('status_accepted')}")
                with col_role:
                    role_options = ["student", "guest", "reviewer", "admin"]
                    role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
                    new_role = st.selectbox(
                        t("role_label"), options=role_options, index=role_options.index(c["role"]),
                        format_func=lambda x: role_labels[x], key=f"prog_collab_role_{c['id']}",
                    )
                    if new_role != c["role"]:
                        update_program_collaborator_role(program_id, c["email"], new_role)
                        st.rerun()
                with col_del:
                    if st.button("🗑️", key=f"del_prog_collab_{c['id']}"):
                        remove_program_collaborator(program_id, c["email"])
                        st.success(t("collaborator_removed"))
                        st.rerun()

        st.write(t("invite_user"))
        col_inv_email, col_inv_role, col_inv_btn = st.columns([3, 2, 1])
        with col_inv_email:
            inv_email = st.text_input(t("email_placeholder"), key="prog_invite_email", label_visibility="collapsed", placeholder=t("email_placeholder"))
        with col_inv_role:
            inv_role_options = ["student", "guest", "reviewer", "admin"]
            inv_role_labels = {"student": t("role_student"), "guest": t("role_guest"), "reviewer": t("role_reviewer"), "admin": t("role_admin")}
            inv_role = st.selectbox(t("role_label"), options=inv_role_options, format_func=lambda x: inv_role_labels[x], key="prog_invite_role", label_visibility="collapsed")
        with col_inv_btn:
            if st.button(t("invite_btn"), key="prog_invite_btn", type="secondary"):
                if not inv_email.strip():
                    st.warning(t("email_required"))
                elif inv_email.strip() == st.session_state.get("username"):
                    st.warning(t("cannot_invite_self"))
                else:
                    add_program_collaborator(program_id, inv_email.strip(), inv_role)
                    st.success(t("invitation_sent"))
                    st.rerun()

    st.divider()

    # --- Delete program (owner only) ---
    if prog_role == "owner":
        st.subheader(t("danger_zone"))
        if st.button(t("delete_course"), type="secondary"):
            st.session_state[f"confirm_delete_prog_{program_id}"] = True

        if st.session_state.get(f"confirm_delete_prog_{program_id}"):
            st.warning(t("confirm_delete"))
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button(t("yes_delete"), key="prog_del_yes", type="primary"):
                    delete_program(program_id)
                    if "editing_program_id" in st.session_state:
                        del st.session_state.editing_program_id
                    st.session_state.page = "Cursos"
                    st.rerun()
            with col_no:
                if st.button(t("cancel"), key="prog_del_no"):
                    del st.session_state[f"confirm_delete_prog_{program_id}"]
                    st.rerun()


def show_program_config():
    """Show configuration for a program before starting quiz."""
    program_id = st.session_state.get("selected_program")
    if not program_id:
        st.session_state.page = "Cursos"
        st.rerun()
        return

    prog = get_program(program_id)
    if not prog:
        st.error(t("course_not_found"))
        return

    # Access control for private/hidden programs (restricted and public are open to everyone)
    visibility = prog.get("visibility", "public")
    if visibility not in ("public", "restricted"):
        logged_in_uid = st.session_state.get("user_id")
        has_prog_access = (
            logged_in_uid and (
                prog["owner_id"] == logged_in_uid
                or get_user_role_for_program(program_id, logged_in_uid) is not None
            )
        )
        if not has_prog_access:
            st.error(t("course_private_no_access"))
            if st.button(t("back_to_courses")):
                if "selected_program" in st.session_state:
                    del st.session_state.selected_program
                st.session_state.page = "Cursos"
                st.rerun()
            return

    questions = get_program_questions(program_id)
    tags = get_program_tags(program_id)
    prog_tests = get_program_tests(program_id)

    # Check if user can edit
    logged_in_uid = st.session_state.get("user_id")
    is_owner = prog.get("owner_id") == logged_in_uid
    prog_role = get_user_role_for_program(program_id, logged_in_uid) if logged_in_uid and not is_owner else None
    can_edit_program = _is_global_admin() or is_owner or prog_role in ("reviewer", "admin")

    # Get performance data for all tests in the program
    logged_in = _is_logged_in()
    test_ids = [pt["id"] for pt in prog_tests]
    test_performance = get_tests_performance(logged_in_uid, test_ids) if logged_in else {}

    # Helper to get performance circle
    def _get_perf_circle(pct):
        if pct >= 95:
            return "🟢"  # Green: excellent
        elif pct >= 80:
            return "🟡"  # Yellow: good
        elif pct >= 50:
            return "🟠"  # Orange: needs work
        else:
            return "🔴"  # Red: struggling

    st.header(prog["title"])
    if prog.get("description"):
        st.write(prog["description"])

    st.caption(t("n_tests_n_questions", nt=len(prog_tests), nq=len(questions)))

    # Tests included section with performance and action buttons
    st.subheader(t("tests_included"))
    visible_tests_count = 0
    for pt in prog_tests:
        test_id = pt["id"]
        program_visibility = pt.get("program_visibility", "public")

        # Check if user can see this test based on effective visibility
        test_data = get_test(test_id)
        test_owner_id = test_data.get("owner_id") if test_data else None
        test_base_visibility = test_data.get("visibility", "public") if test_data else "public"

        # Compute effective visibility (the more restrictive of test base and program visibility)
        effective_visibility = get_effective_visibility(test_base_visibility, program_visibility)

        # Check direct access (for private/hidden visibility)
        has_direct_access = (
            _is_global_admin()
            or test_owner_id == logged_in_uid
            or has_direct_test_access(test_id, logged_in_uid)
        )

        # For hidden tests, skip unless user has direct access
        if effective_visibility == "hidden" and not has_direct_access:
            continue  # Skip this hidden test

        visible_tests_count += 1
        perf = test_performance.get(test_id, {})
        pct = perf.get("percent_correct", 0)
        total_answered = perf.get("total", 0)

        # Check edit permission for this specific test
        test_role = get_user_role_for_test(test_id, logged_in_uid) if logged_in_uid else None
        can_edit_test = _is_global_admin() or test_owner_id == logged_in_uid or test_role in ("reviewer", "admin")

        # Determine if user can access this test based on effective visibility
        can_access_test = (
            effective_visibility in ("public", "restricted")
            or has_direct_access
        )

        with st.container(border=True):
            # Title row with performance circle and buttons
            col_info, col_btns = st.columns([3, 2])

            with col_info:
                # Title with performance circle and visibility indicator
                if logged_in and total_answered > 0:
                    circle = _get_perf_circle(pct)
                    title_display = f"{circle} **{pt['title']}**"
                elif logged_in:
                    title_display = f"⚪ **{pt['title']}**"
                else:
                    title_display = f"**{pt['title']}**"

                # Add visibility indicator for restricted/private tests
                if effective_visibility == "private" and not has_direct_access:
                    title_display += " 🔒"
                elif effective_visibility == "restricted":
                    title_display += " 🔓"

                st.markdown(title_display)
                st.caption(t("n_questions", n=pt["question_count"]))

            with col_btns:
                # Action buttons: View, Edit (if can_edit), Export (if can_edit)
                num_btns = 3 if can_edit_test else 1
                btn_cols = st.columns(num_btns)
                btn_idx = 0

                with btn_cols[btn_idx]:
                    # Disable button for private tests without access
                    btn_disabled = not can_access_test
                    btn_help = t("select") if can_access_test else t("test_private_no_access")
                    if st.button("▶️", key=f"prog_view_test_{test_id}", width="stretch", help=btn_help, disabled=btn_disabled):
                        st.session_state.selected_test = test_id
                        st.session_state.test_program_visibility = program_visibility  # For material visibility
                        st.session_state.page = "Configurar Test"
                        st.rerun()

                if can_edit_test:
                    btn_idx += 1
                    with btn_cols[btn_idx]:
                        if st.button("✏️", key=f"prog_edit_test_{test_id}", width="stretch", help=t("edit_test")):
                            st.session_state.editing_test_id = test_id
                            st.rerun()
                    btn_idx += 1
                    with btn_cols[btn_idx]:
                        export_data, export_title = _get_test_export_data(test_id)
                        st.download_button(
                            "⬇️",
                            data=export_data,
                            file_name=f"{export_title}.json",
                            mime="application/json",
                            key=f"prog_export_test_{test_id}",
                            width="stretch",
                            help=t("export_json"),
                        )

            # Expandable performance details
            if logged_in and total_answered > 0:
                with st.expander(t("your_progress")):
                    m1, m2, m3 = st.columns(3)
                    m1.metric(t("total_answered"), total_answered)
                    m2.metric(t("correct_answers"), perf.get("correct", 0))
                    m3.metric(t("percent_correct"), f"{pct}%")

                    # Get topic stats for this test
                    topic_stats = get_topic_statistics(logged_in_uid, test_id)
                    if topic_stats and len(topic_stats) > 1:
                        sorted_topics = sorted(topic_stats.items(), key=lambda x: x[1]["percent_correct"], reverse=True)
                        best_tag, best_stats = sorted_topics[0]
                        worst_tag, worst_stats = sorted_topics[-1]
                        best_display = best_tag.replace("_", " ").title()
                        worst_display = worst_tag.replace("_", " ").title()
                        col_best, col_worst = st.columns(2)
                        col_best.metric(t("best_topic"), best_display, f"{best_stats['percent_correct']}%")
                        col_worst.metric(t("worst_topic"), worst_display, f"{worst_stats['percent_correct']}%")

    if visible_tests_count == 0:
        st.info(t("no_tests_in_course"))

    # Action buttons row
    btn_cols = st.columns(4 if can_edit_program else 2)
    col_idx = 0
    with btn_cols[col_idx]:
        if st.button(t("back_to_courses"), width="stretch"):
            if "selected_program" in st.session_state:
                del st.session_state.selected_program
            st.session_state.page = "Cursos"
            st.rerun()
    col_idx += 1
    if can_edit_program:
        with btn_cols[col_idx]:
            if st.button(f"✏️ {t('edit_program')}", width="stretch"):
                st.session_state.editing_program_id = program_id
                st.session_state.page = "Editar Curso"
                st.rerun()
        col_idx += 1
        with btn_cols[col_idx]:
            export_data, export_title = _get_program_export_data(program_id)
            st.download_button(
                t("export_json"),
                data=export_data,
                file_name=f"{export_title}.json",
                mime="application/json",
                key="export_program_json",
                width="stretch",
            )

    if not questions:
        st.warning(t("no_course_questions"))
        return

    st.subheader(t("configuration"))

    num_questions = st.number_input(
        t("num_questions"),
        min_value=1,
        max_value=len(questions),
        value=min(25, len(questions)),
    )

    st.write(t("topics_to_include"))
    selected_tags = []
    cols = st.columns(2)
    for i, tag in enumerate(tags):
        tag_display = tag.replace("_", " ").title()
        if cols[i % 2].checkbox(tag_display, value=True, key=f"prog_tag_{tag}"):
            selected_tags.append(tag)

    if not selected_tags:
        st.warning(t("select_at_least_one_topic"))
    else:
        filtered_count = len([q for q in questions if q["tag"] in selected_tags])
        st.info(t("available_questions_with_topics", n=filtered_count))

        if st.button(t("start_test"), type="primary"):
            stats = get_question_stats(st.session_state.user_id, program_id) if _is_logged_in() else None
            quiz_questions = select_balanced_questions(
                questions, selected_tags, num_questions, stats
            )
            session_id = None
            if _is_logged_in():
                session_id = create_session(
                    st.session_state.user_id, None,
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
            st.session_state.current_test_id = 0
            st.session_state.current_session_id = session_id
            st.session_state.quiz_started = True
            st.session_state.page = "Cursos"
            st.rerun()


def main():
    st.set_page_config(page_title="Knowting Club", page_icon="📚")

    _try_login()

    # Check if there's a pending registration (new user needs to accept terms)
    if st.session_state.get("pending_registration"):
        show_terms_acceptance()
        return

    if _is_logged_in():
        _load_profile_to_session()

    if "page" not in st.session_state:
        st.session_state.page = "Home"

    logged_in = _is_logged_in()
    _is_tester_user = logged_in and _get_global_role() == "tester"
    _show_full_ui = logged_in and not _is_tester_user

    # Redirect tester/non-logged-in users away from Home to Tests
    if st.session_state.page == "Home" and not _show_full_ui:
        st.session_state.page = "Tests"

    # Top bar: title + avatar/login (hidden for non-logged-in and tester users)
    if _show_full_ui:
        col_title, col_avatar = st.columns([6, 1])
        with col_title:
            st.title("Knowting Club")
            st.subheader(f"*{t('tagline')}*")
    else:
        col_avatar = st.columns([1])[0]
    with col_avatar:
        if logged_in:
            avatar_bytes = st.session_state.get("avatar_bytes")
            display_name = st.session_state.get("display_name", st.session_state.username)
            with st.popover("👤"):
                if avatar_bytes:
                    st.image(avatar_bytes, width=60)
                st.write(f"**{display_name}**")
                st.divider()
                if st.button(t("profile"), key="menu_profile", width="stretch"):
                    st.session_state.prev_page = st.session_state.page
                    st.session_state.page = "Perfil"
                    st.rerun()
                if st.button(t("logout"), key="menu_logout", width="stretch"):
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.logout()
                    st.rerun()
        else:
            col_login, col_privacy, col_terms = st.columns([1, 1, 1])
            with col_login:
                st.button("🔑", on_click=st.login, help=t("login_with_google"))
            with col_privacy:
                if st.button("🔒", key="header_privacy", help=t("privacy_policy")):
                    st.session_state.page = "Privacy Policy"
                    st.rerun()
            with col_terms:
                if st.button("📜", key="header_terms", help=t("terms_and_conditions")):
                    st.session_state.page = "Terms"
                    st.rerun()

    # Sidebar navigation
    is_tester = logged_in and _get_global_role() == "tester"
    show_full_ui = logged_in and not is_tester
    with st.sidebar:
        # Logo - hidden for non-logged-in and tester users
        if show_full_ui:
            st.image("assets/KnowtingLogo.png", use_container_width=True)

        # Language toggle - hidden for non-logged-in and tester users
        if show_full_ui:
            current_lang = st.session_state.get("lang", "es")
            current_idx = UI_LANGUAGES.index(current_lang) if current_lang in UI_LANGUAGES else 0
            selected_ui_lang = st.selectbox(
                "🌐", options=UI_LANGUAGES,
                index=current_idx,
                format_func=lambda x: UI_LANG_LABELS.get(x, x),
                key="lang_toggle",
                label_visibility="collapsed",
            )
            if selected_ui_lang != current_lang:
                st.session_state.lang = selected_ui_lang
                st.rerun()

        st.markdown("---")
        nav_items = []
        # Home - hidden for non-logged-in and tester users
        if show_full_ui:
            nav_items.append(("🏠", "Home", t("home")))
        nav_items.append(("📝", "Tests", t("tests")))
        if show_full_ui:
            nav_items.append(("📊", "Dashboard", t("dashboard")))
            nav_items.append(("📚", "Cursos", t("courses")))
        if logged_in and _is_global_admin():
            pending_count = get_pending_approval_count()
            surveys_label = t("surveys")
            if pending_count > 0:
                surveys_label = f"{surveys_label} ({pending_count})"
            nav_items.append(("📋", "Surveys", surveys_label))
            nav_items.append(("⚙️", "Admin", t("admin_panel")))
        for icon, page_id, display in nav_items:
            is_active = st.session_state.page == page_id
            btn_type = "primary" if is_active else "secondary"
            if st.button(f"{icon}  {display}", key=f"nav_{page_id}", width="stretch", type=btn_type):
                st.session_state.page = page_id
                st.rerun()
        st.markdown("---")

        # Legal links at the bottom of sidebar (icons with tooltips)
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("🔒", key="nav_privacy", help=t("privacy_policy")):
                st.session_state.page = "Privacy Policy"
                st.rerun()
        with col2:
            if st.button("📜", key="nav_terms", help=t("terms_and_conditions")):
                st.session_state.page = "Terms"
                st.rerun()

    if "quiz_started" not in st.session_state:
        st.session_state.quiz_started = False

    # Check for pause time capture query params and route to editor
    if st.query_params.get("capture_t") is not None and st.query_params.get("capture_mat_id") is not None:
        st.session_state.page = "Editar Test"

    if logged_in and st.session_state.page == "Perfil":
        show_profile()
    elif logged_in and st.session_state.page == "Dashboard" and not st.session_state.quiz_started:
        show_dashboard()
    elif st.session_state.page == "Configurar Test":
        show_test_config()
    elif logged_in and st.session_state.page == "Crear Test":
        show_create_test()
    elif logged_in and st.session_state.page == "Editar Test":
        show_test_editor()
    elif logged_in and st.session_state.page == "Cursos" and not st.session_state.quiz_started:
        show_programs()
    elif logged_in and st.session_state.page == "Crear Curso":
        show_create_program()
    elif logged_in and st.session_state.page == "Editar Curso":
        show_program_editor()
    elif logged_in and st.session_state.page == "Configurar Curso":
        show_program_config()
    elif logged_in and _is_global_admin() and st.session_state.page == "Admin":
        show_admin_panel()
    elif logged_in and _is_global_admin() and st.session_state.page == "Surveys":
        show_admin_surveys()
    elif logged_in and st.session_state.page == "Choose Access Type":
        show_choose_access_type()
    elif logged_in and st.session_state.page == "Take Initial Survey":
        initial_survey = get_active_initial_survey()
        if initial_survey:
            show_survey_page(initial_survey)
        else:
            st.warning(t("no_active_survey"))
            if st.button(t("back")):
                st.session_state.page = "Home"
                st.rerun()
    elif logged_in and st.session_state.page == "Take Periodic Survey":
        pending_survey = st.session_state.get("pending_survey")
        if pending_survey:
            show_survey_page(pending_survey)
        else:
            periodic_survey = get_active_periodic_survey()
            if periodic_survey:
                show_survey_page(periodic_survey)
            else:
                st.warning(t("no_active_survey"))
                if st.button(t("back")):
                    st.session_state.page = "Home"
                    st.rerun()
    elif st.session_state.page == "Home":
        # Show pending approval message or survey deadline warning
        if logged_in:
            if _is_pending_approval():
                st.info(t("pending_approval_message"))
            else:
                deadline_info = _check_survey_deadline()
                if deadline_info and deadline_info.get("warning"):
                    if deadline_info.get("overdue"):
                        st.error(t("survey_deadline_warning", date=deadline_info["deadline"]))
                    else:
                        st.warning(t("survey_deadline_warning", date=deadline_info["deadline"]))
                    # Check if they need to take the survey
                    needs, survey = _needs_survey()
                    if needs and survey:
                        if st.button(t("take_initial_survey"), type="primary"):
                            st.session_state.page = "Take Periodic Survey"
                            st.session_state.pending_survey = survey
                            st.rerun()
        show_home_page()
    elif st.session_state.page == "Privacy Policy":
        show_privacy_policy()
    elif st.session_state.page == "Terms":
        show_terms_and_conditions()
    elif st.session_state.quiz_started:
        show_quiz()
    elif st.session_state.page == "Tests":
        show_test_catalog()
    else:
        show_home_page()


if __name__ == "__main__":
    main()
