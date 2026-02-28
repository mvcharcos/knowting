import streamlit as st
import random
import json
import re
import os
import base64
import streamlit.components.v1 as components

from translations import t
from auth import _is_logged_in, _is_global_admin, _can_create_tests
from db import (
    get_test,
    get_test_questions,
    get_test_materials,
    get_question_material_links,
    get_question_material_links_bulk,
    get_effective_visibility,
    get_user_role_for_test,
    toggle_favorite,
    delete_test,
    get_collaborators,
    get_test_tags,
    import_test_from_json,
    get_next_question_num,
    add_question,
    set_question_material_links,
    get_program,
    get_program_tests,
    get_program_collaborators,
)


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
def _show_study_dialog(mat, label, questions, is_reviewer=False):
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
            "num": q["id"],
        })
    q_json = _json.dumps(q_data, ensure_ascii=False)
    is_reviewer_js = "true" if is_reviewer else "false"

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
var IS_REVIEWER = {is_reviewer_js};
var QUESTION_NUM_LABEL = {_json.dumps(t("question_number", n="__N__"))};
var MISSING_Q_LABEL = {_json.dumps(t("missing_questions", n="__N__"))};

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
      var requested = (useConfigured && stopIdx < CONFIGURED_STOPS.length) ? CONFIGURED_STOPS[stopIdx].n : 1;
      // Cap to unseen questions available for this segment (no duplicates)
      var segAvailable = getQuestionsForSegment();
      var segGlobal = segAvailable.map(function(q) {{ return QUESTIONS.indexOf(q); }});
      var unseenCount = 0;
      for (var i = 0; i < segGlobal.length; i++) {{
        if (shownGlobalIds.indexOf(segGlobal[i]) === -1) unseenCount++;
      }}
      questionsRemaining = Math.min(requested, Math.max(unseenCount, 0));
      var missingCount = requested - questionsRemaining;
      if (questionsRemaining > 0) {{
        showQuestion(missingCount);
      }} else if (IS_REVIEWER && missingCount > 0) {{
        // Show missing questions warning to reviewer then resume
        var contentArea = document.getElementById('quiz-content');
        var btnArea = document.getElementById('btn-area');
        contentArea.innerHTML = '<div style="color:#e67e22;font-weight:bold;padding:1rem 0;">' + MISSING_Q_LABEL.replace('__N__', missingCount) + '</div>';
        btnArea.innerHTML = '';
        overlay.classList.add('active');
        var continueBtn = document.createElement('button');
        continueBtn.className = 'continue-btn';
        continueBtn.textContent = CONTINUE_LABEL;
        continueBtn.addEventListener('click', function() {{ resumeVideo(); }});
        btnArea.appendChild(continueBtn);
      }} else {{
        // No unseen questions left — skip quiz and resume
        advanceStop();
        player.playVideo();
      }}
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

var currentMissingCount = 0;
function showQuestion(missingCount) {{
  if (typeof missingCount !== 'undefined') currentMissingCount = missingCount;
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
  // If all segment questions were shown, skip (no duplicates)
  if (unseenIndices.length === 0) {{
    questionsRemaining = 0;
    resumeVideo();
    return;
  }}
  // Pick a random unseen question
  var pick = unseenIndices[Math.floor(Math.random() * unseenIndices.length)];
  var q = available[pick];
  shownGlobalIds.push(availableGlobal[pick]);

  // Shuffle options while tracking the correct answer
  var indices = [];
  for (var i = 0; i < q.options.length; i++) indices.push(i);
  for (var i = indices.length - 1; i > 0; i--) {{
    var j = Math.floor(Math.random() * (i + 1));
    var tmp = indices[i]; indices[i] = indices[j]; indices[j] = tmp;
  }}
  var shuffledCorrect = indices.indexOf(q.answer_index);

  var html = '';
  if (IS_REVIEWER && q.num) {{
    html += '<div class="tag" style="font-weight:bold;margin-bottom:4px">' + QUESTION_NUM_LABEL.replace('__N__', q.num) + '</div>';
  }}
  html += '<h3>' + escHtml(q.question) + '</h3>';
  html += '<div class="tag">' + escHtml(q.tag.replace(/_/g, ' ')) + '</div>';
  for (var i = 0; i < indices.length; i++) {{
    html += '<button class="opt-btn" data-idx="' + i + '" data-correct="' + shuffledCorrect + '">'
          + escHtml(q.options[indices[i]]) + '</button>';
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
        // Show missing questions warning before resume button (reviewer only)
        if (IS_REVIEWER && currentMissingCount > 0) {{
          var missingDiv = document.createElement('div');
          missingDiv.style.cssText = 'color:#e67e22;font-weight:bold;padding:0.5rem 0;';
          missingDiv.textContent = MISSING_Q_LABEL.replace('__N__', currentMissingCount);
          btnArea.appendChild(missingDiv);
        }}
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
            # Show buttons in a row: Select, Edit (if can_edit), Export (if can_edit), Delete (if can_edit)
            btn_cols = st.columns(4 if can_edit else 1)
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
                col_idx += 1
                with btn_cols[col_idx]:
                    delete_key = f"{prefix}confirm_del_test_{test_id}"
                    if st.button("🗑️", key=f"{prefix}del_{test_id}", width="stretch", help=t("delete")):
                        st.session_state[delete_key] = True
        # Inline delete confirmation (outside button columns, inside the card container)
        if can_edit:
            delete_key = f"{prefix}confirm_del_test_{test_id}"
            if st.session_state.get(delete_key):
                with st.container(border=True):
                    st.warning(t("confirm_delete"))
                    st.write(t("type_name_to_confirm", name=test["title"]))
                    typed = st.text_input("", key=f"{prefix}del_confirm_input_{test_id}", label_visibility="collapsed")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(t("yes_delete"), key=f"{prefix}del_confirm_btn_{test_id}", type="primary"):
                            if typed.strip() == test["title"].strip():
                                delete_test(test_id)
                                st.session_state.pop(delete_key, None)
                                st.success(t("test_deleted"))
                                st.rerun()
                            else:
                                st.error(t("type_name_to_confirm", name=test["title"]))
                    with c2:
                        if st.button(t("cancel"), key=f"{prefix}del_cancel_btn_{test_id}"):
                            st.session_state.pop(delete_key, None)
                            st.rerun()


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
