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
    get_test, get_test_questions, get_test_questions_by_ids,
    get_test_tags, add_test_tag, rename_test_tag, delete_test_tag,
    create_test, update_test, delete_test,
    add_question, update_question, delete_question, get_next_question_num,
    get_question_material_links, get_question_material_links_bulk, set_question_material_links,
    add_collaborator, remove_collaborator, update_collaborator_role,
    get_collaborators, get_user_role_for_test, has_direct_test_access,
    get_effective_visibility,
    get_materials_for_test, link_material_to_test, unlink_material_from_test, get_all_materials,
    add_test_material, update_material_pause_times,
)


def _show_linked_materials(test_id, linked_materials):
    from views.materials import MATERIAL_ICONS, VISIBILITY_ICONS
    st.subheader(t("test_linked_materials"))

    user_id = st.session_state.get("user_id")
    linked_ids = {m["id"] for m in linked_materials}

    if linked_materials:
        for mat in linked_materials:
            icon = MATERIAL_ICONS.get(mat.get("material_type", ""), "📎")
            vis_icon = VISIBILITY_ICONS.get(mat.get("visibility", "public"), "🌐")
            with st.container(border=True):
                col_info, col_btn = st.columns([5, 1])
                with col_info:
                    st.markdown(f"**{icon} {mat.get('title') or t('no_title')}** {vis_icon}")
                    if mat.get("description"):
                        st.caption(mat["description"])
                with col_btn:
                    if st.button(t("test_unlink_material_btn"), key=f"unlink_mat_{mat['id']}", use_container_width=True):
                        unlink_material_from_test(test_id, mat["id"])
                        st.rerun()
                # Show material graph if available
                if mat.get("graph_json"):
                    with st.expander(t("material_graph_expander"), expanded=False):
                        from views.research import _display_concept_graph
                        _display_concept_graph(mat["graph_json"], key_prefix=f"mat_{mat['id']}")
                # Show material facts if available
                if mat.get("facts_json"):
                    with st.expander(t("material_facts_title"), expanded=False):
                        from views.research import _display_grounded_graph
                        _display_grounded_graph(mat["facts_json"])
                # Show material segments if available
                if mat.get("segments_json"):
                    with st.expander(t("material_segments_title"), expanded=False):
                        from views.research import _display_segments
                        _display_segments(mat["segments_json"], grounded_data=mat.get("facts_json"),
                                          questions=mat.get("questions_json") or [],
                                          key_prefix=f"editor_mat_{mat['id']}")
                # Show material questions if available
                raw_qs = mat.get("questions_json") or []
                qs = raw_qs if isinstance(raw_qs, list) else raw_qs.get("questions", [])
                if qs:
                    with st.expander(t("material_questions_expander", n=len(qs)), expanded=False):
                        for i, q in enumerate(qs, 1):
                            st.markdown(f"**{i}. {q.get('question', '')}**")
                            options = q.get("options", [])
                            if options:
                                ans_idx = q.get("answer_index", 0)
                                for j, opt in enumerate(options):
                                    prefix = "✅" if j == ans_idx else "○"
                                    st.caption(f"{prefix} {opt}")
                            else:
                                # Open-ended question: show text answer
                                answer = q.get("answer", q.get("explanation", ""))
                                if answer:
                                    st.caption(f"✅ {answer}")
                                evidence = q.get("evidence", "")
                                if evidence:
                                    st.caption(f"📄 _{evidence}_")
                            if i < len(qs):
                                st.divider()
    else:
        st.caption(t("test_no_linked_materials"))

    # Link new material from library
    all_mats = get_all_materials(user_id)
    available = [m for m in all_mats if m["id"] not in linked_ids]
    if available:
        col_sel, col_btn = st.columns([4, 1])
        with col_sel:
            selected = st.selectbox(
                t("test_select_material"),
                options=available,
                format_func=lambda m: f"{MATERIAL_ICONS.get(m.get('material_type', ''), '📎')} {m.get('title') or t('no_title')}",
                key=f"link_mat_select_{test_id}",
                label_visibility="collapsed",
            )
        with col_btn:
            if st.button(t("test_link_material_btn"), key=f"link_mat_btn_{test_id}", type="primary", use_container_width=True):
                if selected:
                    link_material_to_test(test_id, selected["id"])
                    st.rerun()
    else:
        if not all_mats:
            st.caption(t("test_no_library_materials"))


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


def _show_add_question_wizard(test_id, test, materials):
    """Guided wizard: concept → fact → material → segment → create question."""
    st.markdown(f"**{t('add_question')}**")

    # --- Step 1: Concept ---
    graph_nodes = (test.get("graph_json") or {}).get("nodes", [])
    concept_ids = sorted({n["id"] for n in graph_nodes})

    if concept_ids:
        selected_concept = st.selectbox(
            t("wizard_concept"), options=[""] + concept_ids,
            format_func=lambda x: t("all_concepts") if x == "" else x,
            key="wiz_concept",
        )
    else:
        st.info(t("wizard_no_concepts"))
        selected_concept = st.text_input(t("wizard_concept_custom"), key="wiz_concept_custom")

    if not selected_concept:
        col_create, col_cancel = st.columns(2)
        with col_cancel:
            if st.button(t("wizard_cancel"), key="wiz_cancel_early"):
                st.session_state["_show_add_question_wizard"] = False
                st.rerun()
        return

    # --- Step 2: Fact ---
    all_facts = []
    for mat in materials:
        fj = mat.get("facts_json") or {}
        for node in (fj.get("nodes") or []):
            if node.get("kind") == "fact" and node.get("subject_concept") == selected_concept:
                all_facts.append(node)

    selected_fact = None
    if all_facts:
        fact_idx = st.selectbox(
            t("wizard_fact"), options=range(len(all_facts)),
            format_func=lambda i: all_facts[i]["text"],
            key="wiz_fact",
        )
        selected_fact = all_facts[fact_idx]
        evidence = selected_fact.get("evidence", "")
        if evidence:
            with st.expander(t("wizard_fact_evidence")):
                st.caption(evidence)
    else:
        st.caption(t("wizard_no_facts"))

    # --- Step 3: Material ---
    concept_materials = [
        m for m in materials
        if any(n["id"] == selected_concept for n in (m.get("graph_json") or {}).get("nodes", []))
    ]

    selected_material_obj = None
    if concept_materials:
        type_icons = {"pdf": "📄", "youtube": "▶️", "youtube_video": "▶️", "image": "🖼️", "url": "🔗"}
        mat_idx = st.selectbox(
            t("wizard_material"), options=range(len(concept_materials)),
            format_func=lambda i: f"{type_icons.get(concept_materials[i]['material_type'], '📎')} {concept_materials[i]['title'] or concept_materials[i].get('url', '') or t('no_title')}",
            key="wiz_material",
        )
        selected_material_obj = concept_materials[mat_idx]
    else:
        st.caption(t("wizard_no_materials"))

    # --- Step 4: Segment ---
    selected_segment = None
    if selected_material_obj:
        segs_data = (selected_material_obj.get("segments_json") or {}).get("segments", [])
        concept_segs = [s for s in segs_data if selected_concept in (s.get("concept_labels") or [])]

        if concept_segs:
            seg_idx = st.selectbox(
                t("wizard_segment"), options=range(len(concept_segs)),
                format_func=lambda i: f"{concept_segs[i]['segment_id']} ({concept_segs[i]['start_time']} → {concept_segs[i]['end_time']})",
                key="wiz_segment",
            )
            selected_segment = concept_segs[seg_idx]

            # Preview
            with st.expander(t("wizard_segment_transcript")):
                st.text_area("", value=selected_segment.get("clean_text", ""), disabled=True,
                             key="wiz_seg_transcript", label_visibility="collapsed", height=150)
            concepts_in_seg = selected_segment.get("concept_labels", [])
            if concepts_in_seg:
                st.caption(f"{t('wizard_segment_concepts')}: {', '.join(concepts_in_seg)}")
            # Facts for this segment
            seg_facts = []
            fj = (selected_material_obj.get("facts_json") or {})
            for node in (fj.get("nodes") or []):
                if node.get("kind") == "fact" and node.get("subject_concept") in concepts_in_seg:
                    seg_facts.append(node["text"])
            if seg_facts:
                with st.expander(t("wizard_fact")):
                    for ft in seg_facts:
                        st.markdown(f"- {ft}")
        else:
            st.caption(t("wizard_no_segments"))

    # --- Action buttons ---
    st.divider()
    col_create, col_cancel = st.columns(2)
    with col_create:
        if st.button(t("wizard_create_question"), type="primary", key="wiz_create"):
            concept = selected_concept
            # Pre-fill explanation with fact + evidence as context hint
            expl_parts = []
            if selected_fact:
                expl_parts.append(selected_fact.get("text", ""))
                if selected_fact.get("evidence"):
                    expl_parts.append(f'"{selected_fact["evidence"]}"')
            if selected_segment:
                expl_parts.append(f"[{selected_segment.get('start_time', '')} → {selected_segment.get('end_time', '')}]")
            explanation_hint = " — ".join(p for p in expl_parts if p)
            next_num = get_next_question_num(test_id)
            db_id = add_question(
                test_id, next_num, concept, t("new_question_text"),
                [t("option_a"), t("option_b"), t("option_c"), t("option_d")], 0, explanation_hint
            )
            if db_id:
                st.session_state["_auto_expand_q"] = db_id
            st.session_state["_show_add_question_wizard"] = False
            st.rerun()
    with col_cancel:
        if st.button(t("wizard_cancel"), key="wiz_cancel"):
            st.session_state["_show_add_question_wizard"] = False
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

    linked_materials = get_materials_for_test(test_id)
    materials = linked_materials  # alias for downstream code (question filters, import)

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
        _show_linked_materials(test_id, linked_materials)
        st.divider()

    # --- Knowledge Graph (owner and admin only) ---
    if user_role in ("owner", "admin"):
        st.subheader(t("test_knowledge_graph"))
        if test.get("graph_json"):
            from views.research import _display_concept_graph
            _display_concept_graph(test["graph_json"], key_prefix=f"test_{test_id}")
        else:
            st.caption(t("test_no_graph"))
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
                    st.session_state["_show_add_question_wizard"] = not st.session_state.get("_show_add_question_wizard", False)
                    st.session_state.pop("_show_import_questions", None)
                    st.rerun()
            with col_import_q:
                if st.button(t("import_questions"), width="stretch"):
                    st.session_state["_show_import_questions"] = not st.session_state.get("_show_import_questions", False)
                    st.session_state.pop("_show_add_question_wizard", None)
                    st.rerun()
            with col_bulk_q:
                q_bulk_delete = st.toggle(t("bulk_delete_mode"), key="q_bulk_delete_mode")
                if q_bulk_delete:
                    if "bulk_delete_questions" not in st.session_state:
                        st.session_state.bulk_delete_questions = set()

            # Show wizard or import form (mutually exclusive)
            if st.session_state.get("_show_add_question_wizard", False):
                with st.container(border=True):
                    _show_add_question_wizard(test_id, test, materials)
            elif st.session_state.get("_show_import_questions", False):
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
                graph_nodes = (test.get("graph_json") or {}).get("nodes", [])
                concept_ids = sorted({n["id"] for n in graph_nodes})
                concept_filter_options = concept_ids or list(all_tags)
                q_filter_topic = st.selectbox(
                    t("filter_by_concept"), options=[""] + concept_filter_options,
                    format_func=lambda x: t("all_concepts") if x == "" else x,
                    key="q_filter_topic",
                )
            with col_mat:
                mat_options = [0] + [m["id"] for m in materials]
                type_icons = {"pdf": "📄", "youtube": "▶️", "youtube_video": "▶️", "image": "🖼️", "url": "🔗"}
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
            auto_expand = st.session_state.get("_auto_expand_q") == q["db_id"]
            if auto_expand:
                del st.session_state["_auto_expand_q"]
            with expander_parent.expander(f"#{q['id']} — {q['question'][:80]}", expanded=auto_expand):
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
                # Build concept options for selectbox (prefer graph nodes, fall back to tags)
                q_graph_nodes = (test.get("graph_json") or {}).get("nodes", [])
                q_concept_ids = [n["id"] for n in q_graph_nodes]
                tag_options = list(dict.fromkeys(q_concept_ids + list(all_tags)))
                if q["tag"] and q["tag"] not in tag_options:
                    tag_options.append(q["tag"])
                if not tag_options:
                    tag_options = [""]
                current_idx = tag_options.index(q["tag"]) if q["tag"] in tag_options else 0
                q_tag = st.selectbox(t("concept_label"), options=tag_options, index=current_idx, key=f"{q_key}_tag", disabled=read_only)
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
                        type_icons = {"pdf": "📄", "youtube": "▶️", "youtube_video": "▶️", "image": "🖼️", "url": "🔗"}
                        icon = type_icons.get(mat["material_type"], "📎")
                        mlabel = mat["title"] or mat["url"] or t("no_title")
                        is_linked = st.checkbox(f"{icon} {mlabel}", value=mid in existing_links, key=f"{q_key}_mat_{mid}")
                        if is_linked:
                            ctx = existing_links.get(mid, "")
                            if mat["material_type"] in ("youtube", "youtube_video"):
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
