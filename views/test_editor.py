import streamlit as st
import json
import re
from translations import t
from auth import _is_logged_in, _is_global_admin, _can_create_tests, _get_global_role
from helpers import (
    _extract_youtube_id, _fetch_youtube_transcript, _seconds_to_mmss, _mmss_to_seconds,
    _parse_pause_times, _format_pause_times, _lang_display, _time_to_secs,
    _render_material_refs, _show_import_questions_inline, _toggle_bulk_question,
    _get_test_export_data, _show_study_dialog, _show_transcript_dialog,
    LANGUAGE_OPTIONS, LANGUAGE_KEYS,
)
from db import (
    get_test, get_test_questions, get_test_questions_by_ids, get_test_materials,
    get_test_tags, add_test_tag, rename_test_tag, delete_test_tag,
    create_test, update_test, delete_test,
    add_question, update_question, delete_question, get_next_question_num,
    get_material_by_id, add_test_material, update_test_material, delete_test_material,
    update_material_transcript, update_material_pause_times,
    get_question_material_links, get_question_material_links_bulk, set_question_material_links,
    add_collaborator, remove_collaborator, update_collaborator_role,
    get_collaborators, get_user_role_for_test, has_direct_test_access,
    get_effective_visibility,
)


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


def _extract_segment_transcript(full_transcript, start_secs, end_secs):
    """Extract the portion of a timestamped transcript between start_secs and end_secs."""
    import re as _re
    if not full_transcript:
        return ""
    parsed_lines = []
    for line in full_transcript.split("\n"):
        m_ts = _re.match(r'\[(\d+(?::\d{1,2}){1,2})\]', line)
        if m_ts:
            parsed_lines.append((_time_to_secs(m_ts.group(1)), line))
        elif parsed_lines:
            parsed_lines.append((parsed_lines[-1][0], line))
    if not parsed_lines:
        return ""
    start_idx = 0
    for i, (ts, _) in enumerate(parsed_lines):
        if ts >= start_secs:
            start_idx = max(0, i - 1) if i > 0 and ts > start_secs else i
            break
    end_idx = len(parsed_lines)
    for i in range(len(parsed_lines) - 1, -1, -1):
        if parsed_lines[i][0] < end_secs:
            end_idx = i + 1
            break
    return "\n".join(line for _, line in parsed_lines[start_idx:end_idx])


def _find_related_questions(transcript_segment, questions_list):
    """Use Hugging Face to identify which questions are related to a transcript segment.

    Returns a list of question db_ids sorted by relevance (most relevant first),
    or empty list on failure.
    """
    import os
    import json as _json
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        return []
    api_key = os.environ.get("HF_API_KEY") or (st.secrets["HF_API_KEY"] if "HF_API_KEY" in st.secrets else "")
    if not api_key:
        return []
    model_id = os.environ.get("HF_MODEL") or (st.secrets["HF_MODEL"] if "HF_MODEL" in st.secrets else "Qwen/Qwen2.5-72B-Instruct")

    # Build a numbered list of questions for the prompt
    q_list_str = ""
    id_map = {}  # index -> db_id
    for i, q in enumerate(questions_list):
        q_list_str += f"{i + 1}. {q['question'][:120]}\n"
        id_map[i + 1] = q["db_id"]

    system_prompt = (
        "You are an educational content matcher. Given a video transcript segment and a list of questions, "
        "identify which questions are related to the content in the transcript segment. "
        "Return ONLY a JSON array of the question numbers (1-based) sorted by relevance (most relevant first). "
        "Only include questions that are clearly related to the transcript content. "
        "If no questions are related, return an empty array []."
    )
    user_prompt = (
        f"Transcript segment:\n{transcript_segment[:4000]}\n\n"
        f"Questions:\n{q_list_str[:4000]}\n\n"
        f"Return ONLY a JSON array of question numbers, e.g. [3, 7, 1]"
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
            temperature=0.3,
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            text = text.split("```json")[-1].split("```")[0].strip() if "```json" in text else text.split("```")[1].split("```")[0].strip()
        nums = _json.loads(text)
        if isinstance(nums, list):
            return [id_map[n] for n in nums if n in id_map]
    except Exception:
        pass
    return []


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
            author = st.session_state.get("display_name", st.session_state.get("username", ""))
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
                                        st.session_state.get("display_name", st.session_state.get("username", "")),
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

    # --- Warnings: segments lacking questions ---
    import json as _json_warn
    _seg_warnings = []
    q_db_ids_warn = [q["db_id"] for q in questions]
    all_q_mat_links_warn = get_question_material_links_bulk(q_db_ids_warn) if q_db_ids_warn else {}
    # Build reverse map: material_id -> list of (db_id, timestamp_secs)
    _mat_q_times = {}
    # Also build set of db_ids already linked to each material
    _mat_linked_dbids = {}
    for db_id, links in all_q_mat_links_warn.items():
        for lk in links:
            mid = lk["material_id"]
            ctx = lk.get("context", "").strip()
            _mat_linked_dbids.setdefault(mid, set()).add(db_id)
            if ctx:
                _mat_q_times.setdefault(mid, []).append((db_id, _time_to_secs(ctx)))

    for mat in materials:
        if mat.get("material_type") != "youtube":
            continue
        pause_json = mat.get("pause_times", "")
        if not pause_json:
            continue
        try:
            stops = _json_warn.loads(pause_json)
        except (ValueError, TypeError):
            continue
        if not stops:
            continue
        # Handle old format
        if isinstance(stops[0], (int, float)):
            stops = [{"t": s, "n": 1} for s in stops]
        stops.sort(key=lambda x: x["t"])

        mat_label = mat.get("title") or mat.get("url") or "?"
        q_times = _mat_q_times.get(mat["id"], [])
        prev_t = 0
        for si, stop in enumerate(stops):
            stop_t = stop["t"]
            needed = stop.get("n", 1)
            available_count = sum(1 for _, qt in q_times if prev_t <= qt < stop_t)
            if available_count < needed:
                _seg_warnings.append({
                    "material": mat_label,
                    "mat_id": mat["id"],
                    "start": _seconds_to_mmss(prev_t),
                    "end": _seconds_to_mmss(stop_t),
                    "start_secs": prev_t,
                    "end_secs": stop_t,
                    "needed": needed,
                    "available": available_count,
                    "stop_idx": si,
                    "stops": stops,
                    "transcript": mat.get("transcript", ""),
                })
            prev_t = stop_t

    if _seg_warnings and not read_only:
        with st.expander(f"⚠️ {t('warnings')} ({len(_seg_warnings)})", expanded=True):
            for wi, w in enumerate(_seg_warnings):
                wkey = f"warn_{w['mat_id']}_{w['stop_idx']}"
                st.warning(t("segment_missing_questions",
                             material=w["material"], start=w["start"], end=w["end"],
                             needed=w["needed"], available=w["available"]))
                btn_cols = st.columns([1, 1, 1, 1])
                # Button 1: Reduce pause count
                with btn_cols[0]:
                    if st.button("📉", key=f"{wkey}_reduce", help=t("reduce_pause_count", n=w["available"])):
                        new_stops = list(w["stops"])
                        new_stops[w["stop_idx"]]["n"] = max(w["available"], 1) if w["available"] > 0 else 0
                        # If reducing to 0, remove the stop entirely
                        if new_stops[w["stop_idx"]]["n"] == 0:
                            new_stops.pop(w["stop_idx"])
                        update_material_pause_times(w["mat_id"], _json_warn.dumps(new_stops))
                        st.success(t("pause_count_updated"))
                        st.rerun()
                # Button 2: Link existing questions
                with btn_cols[1]:
                    link_key = f"{wkey}_link_open"
                    if st.button("🔗", key=f"{wkey}_link", help=t("link_existing_questions")):
                        st.session_state[link_key] = not st.session_state.get(link_key, False)
                        st.rerun()
                # Button 3: Create new question
                with btn_cols[2]:
                    create_key = f"{wkey}_create_open"
                    if st.button("➕", key=f"{wkey}_create", help=t("create_question_for_segment")):
                        st.session_state[create_key] = not st.session_state.get(create_key, False)
                        st.rerun()
                # Button 4: Show transcript for segment
                with btn_cols[3]:
                    transcript_key = f"{wkey}_transcript_open"
                    if st.button("📜", key=f"{wkey}_transcript", help=t("transcript")):
                        st.session_state[transcript_key] = not st.session_state.get(transcript_key, False)
                        st.rerun()

                # Inline: transcript for segment
                transcript_key = f"{wkey}_transcript_open"
                if st.session_state.get(transcript_key):
                    with st.container(border=True):
                        seg_text = _extract_segment_transcript(w.get("transcript", ""), w["start_secs"], w["end_secs"])
                        if seg_text:
                            st.text_area(
                                f"{t('transcript')} ({w['start']} → {w['end']})",
                                value=seg_text, height=200, disabled=True,
                                key=f"{wkey}_transcript_area",
                            )
                        else:
                            st.info(t("no_transcript"))

                # Inline: link existing questions
                link_key = f"{wkey}_link_open"
                if st.session_state.get(link_key):
                    with st.container(border=True):
                        st.write(t("select_questions_to_link", start=w["start"], end=w["end"]))
                        # Show unlinked questions for this material/segment
                        linked_to_mat = _mat_linked_dbids.get(w["mat_id"], set())
                        unlinked_qs = [q for q in questions if q["db_id"] not in linked_to_mat]
                        if not unlinked_qs:
                            st.info(t("no_matching_questions"))
                        else:
                            # Use AI to find related questions (cached in session state)
                            ai_key = f"{wkey}_ai_related"
                            if ai_key not in st.session_state:
                                seg_text = _extract_segment_transcript(w.get("transcript", ""), w["start_secs"], w["end_secs"])
                                if seg_text:
                                    with st.spinner(t("analyzing_questions")):
                                        st.session_state[ai_key] = _find_related_questions(seg_text, unlinked_qs)
                                else:
                                    st.session_state[ai_key] = []
                            ai_related = st.session_state.get(ai_key, [])
                            ai_related_set = set(ai_related)

                            # Sort: AI-suggested first (in relevance order), then the rest
                            if ai_related:
                                ai_order = {db_id: i for i, db_id in enumerate(ai_related)}
                                sorted_qs = sorted(unlinked_qs, key=lambda q: ai_order.get(q["db_id"], len(ai_related) + q["id"]))
                            else:
                                sorted_qs = unlinked_qs

                            sel_key = f"{wkey}_link_sel"
                            if sel_key not in st.session_state:
                                # Pre-select AI-suggested questions
                                st.session_state[sel_key] = set(ai_related)
                            for q in sorted_qs:
                                is_sel = q["db_id"] in st.session_state[sel_key]
                                label = f"#{q['id']} — {q['question'][:80]}"
                                if q["db_id"] in ai_related_set:
                                    label = f"#{q['id']} — {t('ai_suggested')} — {q['question'][:70]}"
                                if st.checkbox(label, value=is_sel, key=f"{wkey}_lq_{q['db_id']}"):
                                    st.session_state[sel_key].add(q["db_id"])
                                else:
                                    st.session_state[sel_key].discard(q["db_id"])
                            c1, c2 = st.columns(2)
                            with c1:
                                selected_ids = st.session_state.get(sel_key, set())
                                if st.button(t("link_selected"), key=f"{wkey}_link_confirm", type="primary", disabled=len(selected_ids) == 0):
                                    # Compute a timestamp in the middle of the segment for the context
                                    mid_secs = (w["start_secs"] + w["end_secs"]) // 2
                                    ctx_str = _seconds_to_mmss(mid_secs)
                                    for db_id in selected_ids:
                                        existing = get_question_material_links(db_id)
                                        existing.append({"material_id": w["mat_id"], "context": ctx_str})
                                        set_question_material_links(db_id, existing)
                                    st.session_state.pop(sel_key, None)
                                    st.session_state.pop(ai_key, None)
                                    st.session_state.pop(link_key, None)
                                    st.success(t("questions_linked", n=len(selected_ids)))
                                    st.rerun()
                            with c2:
                                if st.button(t("cancel"), key=f"{wkey}_link_cancel"):
                                    st.session_state.pop(sel_key, None)
                                    st.session_state.pop(ai_key, None)
                                    st.session_state.pop(link_key, None)
                                    st.rerun()

                # Inline: create new question for segment
                create_key = f"{wkey}_create_open"
                if st.session_state.get(create_key):
                    with st.container(border=True):
                        all_tags_warn = get_test_tags(test_id)
                        tag_opts = all_tags_warn if all_tags_warn else ["general"]
                        new_q_tag = st.selectbox(t("topic_label"), options=tag_opts, key=f"{wkey}_new_tag")
                        new_q_text = st.text_area(t("question_label"), key=f"{wkey}_new_text")
                        new_q_opts = []
                        for oi in range(4):
                            new_q_opts.append(st.text_input(t("option_n", n=oi + 1), key=f"{wkey}_new_opt_{oi}"))
                        new_q_ans = st.selectbox(
                            t("correct_answer_select"), range(4),
                            format_func=lambda i: new_q_opts[i] if new_q_opts[i] else f"{i + 1}",
                            key=f"{wkey}_new_ans",
                        )
                        new_q_expl = st.text_area(t("explanation_label"), key=f"{wkey}_new_expl")
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button(t("save_question"), key=f"{wkey}_create_save", type="primary"):
                                if new_q_text.strip():
                                    next_num = get_next_question_num(test_id)
                                    mid_secs = (w["start_secs"] + w["end_secs"]) // 2
                                    ctx_str = _seconds_to_mmss(mid_secs)
                                    opts = [o for o in new_q_opts if o.strip()]
                                    if len(opts) < 2:
                                        opts = [t("option_a"), t("option_b")]
                                    q_id = add_question(test_id, next_num, new_q_tag, new_q_text.strip(), opts, min(new_q_ans, len(opts) - 1), new_q_expl.strip())
                                    set_question_material_links(q_id, [{"material_id": w["mat_id"], "context": ctx_str}])
                                    st.session_state.pop(create_key, None)
                                    st.success(t("question_created_for_segment"))
                                    st.rerun()
                        with c2:
                            if st.button(t("cancel"), key=f"{wkey}_create_cancel"):
                                st.session_state.pop(create_key, None)
                                st.rerun()
    elif _seg_warnings and read_only:
        with st.expander(f"⚠️ {t('warnings')} ({len(_seg_warnings)})", expanded=True):
            for w in _seg_warnings:
                st.warning(t("segment_missing_questions",
                             material=w["material"], start=w["start"], end=w["end"],
                             needed=w["needed"], available=w["available"]))

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
                mat_by_id_local = {}
                for m in materials:
                    icon = type_icons.get(m["material_type"], "📎")
                    mat_labels[m["id"]] = f"{icon} {m['title'] or m['url'] or t('no_title')}"
                    mat_by_id_local[m["id"]] = m
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

            # Time range filter (shown when a YouTube material is selected)
            q_filter_time_from = 0
            q_filter_time_to = 0
            if q_filter_mat:
                selected_mat = mat_by_id_local.get(q_filter_mat)
                if selected_mat and selected_mat.get("material_type") == "youtube":
                    import json as _json_time
                    # Build segment options from pause_times
                    pause_json = selected_mat.get("pause_times", "")
                    segments = []  # list of (from_secs, to_secs, label)
                    if pause_json:
                        try:
                            stops = _json_time.loads(pause_json)
                            stop_times = sorted(s["t"] for s in stops)
                            prev = 0
                            for st_time in stop_times:
                                segments.append((prev, st_time, f"{_seconds_to_mmss(prev)} → {_seconds_to_mmss(st_time)}"))
                                prev = st_time
                            # Last segment: from last pause to end (use 0 as unbounded)
                            segments.append((prev, 0, f"{_seconds_to_mmss(prev)} → ..."))
                        except (ValueError, TypeError, KeyError):
                            pass

                    # Segment selector + custom option
                    seg_options = ["all"] + [f"seg_{i}" for i in range(len(segments))] + ["custom"]
                    def _seg_label(key):
                        if key == "all":
                            return t("all_segments")
                        if key == "custom":
                            return t("custom_time_range")
                        idx = int(key.split("_")[1])
                        return segments[idx][2]

                    col_seg, col_t_from, col_t_to = st.columns([2, 1, 1])
                    with col_seg:
                        q_seg_choice = st.selectbox(
                            t("video_segment"), options=seg_options,
                            format_func=_seg_label,
                            key="q_filter_segment",
                        )
                    if q_seg_choice == "custom":
                        with col_t_from:
                            q_time_from_str = st.text_input(t("from_time"), value="", placeholder="0:00", key="q_filter_time_from")
                        with col_t_to:
                            q_time_to_str = st.text_input(t("to_time"), value="", placeholder="0:00", key="q_filter_time_to")
                        q_filter_time_from = _time_to_secs(q_time_from_str) if q_time_from_str.strip() else 0
                        q_filter_time_to = _time_to_secs(q_time_to_str) if q_time_to_str.strip() else 0
                    elif q_seg_choice != "all":
                        seg_idx = int(q_seg_choice.split("_")[1])
                        q_filter_time_from = segments[seg_idx][0]
                        q_filter_time_to = segments[seg_idx][1]

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
                if q_filter_time_from or q_filter_time_to:
                    # Filter by material AND time range using the context timestamps
                    linked_db_ids = set()
                    for db_id, links in all_q_mat_links.items():
                        for lk in links:
                            if lk["material_id"] != q_filter_mat:
                                continue
                            ctx = lk.get("context", "").strip()
                            if not ctx:
                                continue
                            q_secs = _time_to_secs(ctx)
                            if q_filter_time_from and q_secs < q_filter_time_from:
                                continue
                            if q_filter_time_to and q_secs > q_filter_time_to:
                                continue
                            linked_db_ids.add(db_id)
                    filtered_questions = [q for q in filtered_questions if q["db_id"] in linked_db_ids]
                else:
                    linked_db_ids = {db_id for db_id, links in all_q_mat_links.items() if any(lk["material_id"] == q_filter_mat for lk in links)}
                    filtered_questions = [q for q in filtered_questions if q["db_id"] in linked_db_ids]
            if q_from > min_num or q_to < max_num:
                filtered_questions = [q for q in filtered_questions if q_from <= q["id"] <= q_to]

            if len(filtered_questions) != len(questions):
                st.caption(t("questions_shown", shown=len(filtered_questions), total=len(questions)))
        else:
            filtered_questions = questions

        # --- Pagination ---
        QUESTIONS_PER_PAGE_OPTIONS = [10, 20, 50, 100]
        if filtered_questions:
            col_psize, col_pnav, col_plabel = st.columns([1, 2, 1])
            with col_psize:
                per_page = st.selectbox(
                    t("per_page"), options=QUESTIONS_PER_PAGE_OPTIONS,
                    index=1, key="q_per_page", label_visibility="collapsed",
                    format_func=lambda x: f"{x} {t('per_page')}",
                )
            total_pages = max(1, (len(filtered_questions) + per_page - 1) // per_page)
            # Reset page if filters changed and current page exceeds total
            if st.session_state.get("q_page", 1) > total_pages:
                st.session_state["q_page"] = 1
            current_page = st.session_state.get("q_page", 1)
            with col_pnav:
                nav_cols = st.columns([1, 1, 1, 1])
                with nav_cols[0]:
                    if st.button("⏮", key="q_page_first", disabled=current_page <= 1):
                        st.session_state["q_page"] = 1
                        st.rerun()
                with nav_cols[1]:
                    if st.button("◀", key="q_page_prev", disabled=current_page <= 1):
                        st.session_state["q_page"] = current_page - 1
                        st.rerun()
                with nav_cols[2]:
                    if st.button("▶", key="q_page_next", disabled=current_page >= total_pages):
                        st.session_state["q_page"] = current_page + 1
                        st.rerun()
                with nav_cols[3]:
                    if st.button("⏭", key="q_page_last", disabled=current_page >= total_pages):
                        st.session_state["q_page"] = total_pages
                        st.rerun()
            with col_plabel:
                st.markdown(f"<div style='text-align:right;padding-top:0.5rem'>{t('page_of', page=current_page, total=total_pages)}</div>", unsafe_allow_html=True)
            start_idx = (current_page - 1) * per_page
            page_questions = filtered_questions[start_idx:start_idx + per_page]
        else:
            page_questions = filtered_questions

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

        for q in page_questions:
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
