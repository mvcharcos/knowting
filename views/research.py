import streamlit as st
from translations import t
from helpers import _fetch_youtube_transcript, _extract_youtube_id
from db import get_concept_graph, save_concept_graph, get_research_extras, save_research_extras

DEFAULT_VIDEO_URL = st.secrets.get("DEFAULT_VIDEO_URL", "")


class _OpenRouterClient:
    """Drop-in replacement for InferenceClient that routes calls through OpenRouter."""

    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _OpenRouterClient._Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_OpenRouterClient._Choice(content)]

    class _Completions:
        def __init__(self, api_key):
            self._api_key = api_key

        def create(self, model, messages, temperature=0.0, max_tokens=2000, **kwargs):
            import requests
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                timeout=120,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _OpenRouterClient._Resp(content)

    class _Chat:
        def __init__(self, api_key):
            self.completions = _OpenRouterClient._Completions(api_key)

    def __init__(self, api_key):
        self.chat = self._Chat(api_key)


class _GeminiClient:
    """Drop-in replacement for InferenceClient using Google's OpenAI-compatible Gemini endpoint."""

    class _Msg:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _GeminiClient._Msg(content)

    class _Resp:
        def __init__(self, content):
            self.choices = [_GeminiClient._Choice(content)]

    class _Completions:
        def __init__(self, api_key):
            self._api_key = api_key

        def create(self, model, messages, temperature=0.0, max_tokens=2000, **kwargs):
            import requests, time
            delay = 10
            for attempt in range(5):
                resp = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                    timeout=120,
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", delay))
                    time.sleep(retry_after)
                    delay *= 2
                    continue
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return _GeminiClient._Resp(content)
            resp.raise_for_status()  # raise after exhausting retries

    class _Chat:
        def __init__(self, api_key):
            self.completions = _GeminiClient._Completions(api_key)

    def __init__(self, api_key):
        self.chat = self._Chat(api_key)


def _make_llm_client(provider, api_key):
    """Return a chat-completions client for the given provider."""
    if provider == "openrouter":
        return _OpenRouterClient(api_key)
    if provider == "gemini":
        return _GeminiClient(api_key)
    from huggingface_hub import InferenceClient
    return InferenceClient(token=api_key)


def show_research():
    """Show the research page for testing concept graph generation from transcripts."""
    st.header(f"🔬 {t('research')}")
    st.write(t("research_desc"))

    st.divider()

    # --- Concept Graph from Transcript ---
    st.subheader(t("research_concept_graph"))
    st.caption(t("research_concept_graph_desc"))

    approach = st.selectbox(
        t("research_approach"),
        options=["llm", "nlp_v2", "nlp_v2_llm", "nlp", "ts", "prompt_seq"],
        format_func=lambda x: t(f"research_approach_{x}"),
        key="research_approach",
    )

    default_token = st.secrets.get("HF_API_KEY", "")
    default_model = st.secrets.get("HF_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    default_or_key = st.secrets.get("OPENROUTE_API_KEY", "")
    default_or_model = st.secrets.get("OR_MODEL", "qwen/qwen-2.5-72b-instruct")
    default_gemini_key = st.secrets.get("GEMIMI_API_KEY", "")
    default_gemini_model = st.secrets.get("GEMINI_MODEL", "gemini-2.0-flash")

    hf_token = None
    hf_model = default_model
    subject_matter = ""
    provider = "huggingface"
    if approach in ("llm", "nlp_v2_llm", "prompt_seq"):
        provider = st.radio(
            t("research_provider"),
            options=["huggingface", "openrouter", "gemini"],
            format_func=lambda x: t(f"research_provider_{x}"),
            horizontal=True,
            key="research_provider",
        )
        if provider == "huggingface":
            hf_token = st.text_input(
                t("research_hf_token"),
                value=default_token,
                type="password",
                key="research_hf_token",
            )
            hf_model = st.text_input(
                t("research_hf_model"),
                value=default_model,
                key="research_hf_model",
            )
        elif provider == "openrouter":
            hf_token = st.text_input(
                t("research_or_key"),
                value=default_or_key,
                type="password",
                key="research_or_key",
            )
            hf_model = st.text_input(
                t("research_or_model"),
                value=default_or_model,
                key="research_or_model",
            )
        else:  # gemini
            hf_token = st.text_input(
                t("research_gemini_key"),
                value=default_gemini_key,
                type="password",
                key="research_gemini_key",
            )
            hf_model = st.text_input(
                t("research_gemini_model"),
                value=default_gemini_model,
                key="research_gemini_model",
            )
    if approach == "prompt_seq":
        subject_matter = st.text_input(
            t("research_subject_matter"),
            value="",
            key="research_subject_matter",
        )

    # Resolve the active API key (fallback to secret if the input was cleared)
    _fallback_keys = {"huggingface": default_token, "openrouter": default_or_key, "gemini": default_gemini_key}
    active_key = hf_token or _fallback_keys.get(provider, "")

    url = st.text_input(
        t("research_video_url"),
        value=DEFAULT_VIDEO_URL,
        key="research_url",
    )

    # --- Cache check ---
    video_id = _extract_youtube_id(url.strip()) if url.strip() else None
    cached = get_concept_graph(video_id, approach) if video_id else None

    # Auto-load cached graph into session state when session is fresh
    if cached and "research_graph_data" not in st.session_state:
        st.session_state.research_graph_data = cached["graph_json"]
        st.session_state.research_saved_token = active_key
        st.session_state.research_saved_model = hf_model
        st.session_state.research_saved_provider = provider

    # Auto-load extras whenever they're absent and we have a known video (independent of graph load)
    if video_id and "research_graph_data" in st.session_state:
        extras = get_research_extras(video_id, approach)
        if extras.get("facts_json") and "research_grounded_data" not in st.session_state:
            st.session_state.research_grounded_data = extras["facts_json"]
        if extras.get("questions_json") and "research_questions" not in st.session_state:
            st.session_state.research_questions = extras["questions_json"]
        if extras.get("segments_json") and "research_segments_data" not in st.session_state:
            st.session_state.research_segments_data = extras["segments_json"]

    # --- Generate / Regenerate button ---
    needs_token = approach in ("llm", "nlp_v2_llm", "prompt_seq")
    ready = bool(url.strip()) and (not needs_token or bool(active_key))

    if cached:
        display_date = (cached.get("updated_at") or cached.get("created_at", ""))[:10]
        st.info(t("research_graph_cached", date=display_date))
        do_generate = st.button(t("research_regenerate"), type="primary", disabled=not ready, key="research_regenerate_btn")
    else:
        do_generate = st.button(t("research_generate"), type="primary", disabled=not ready, key="research_generate_btn")

    if do_generate:
        if needs_token and not active_key:
            st.error(t("research_no_hf_token"))
            return

        vid = _extract_youtube_id(url.strip())
        if not vid:
            st.error(t("research_invalid_url"))
        else:
            with st.spinner(t("research_fetching_transcript")):
                transcript = _fetch_youtube_transcript(url.strip())

            if not transcript:
                st.error(t("research_no_transcript"))
            else:
                st.success(t("research_transcript_fetched", n=len(transcript)))

                with st.expander(t("research_transcript_preview"), expanded=False):
                    st.text_area("", transcript, height=300, disabled=True, label_visibility="collapsed", key="research_transcript_generate_preview")

                with st.spinner(t("research_building_graph")):
                    if approach == "llm":
                        graph_data = _build_concept_graph_llm(transcript, active_key, hf_model, provider=provider)
                    elif approach == "nlp_v2":
                        graph_data = _build_concept_graph_nlp_v2(transcript)
                    elif approach == "nlp_v2_llm":
                        graph_data = _build_concept_graph_nlp_v2(transcript, active_key, hf_model, provider=provider)
                    elif approach == "ts":
                        graph_data = _build_concept_graph_timestamp(transcript)
                    elif approach == "prompt_seq":
                        graph_data = _build_concept_graph_prompt_seq(transcript, active_key, hf_model, subject_matter, provider=provider)
                    else:
                        graph_data = _build_concept_graph_nlp(transcript)

                if graph_data:
                    st.session_state.research_graph_data = graph_data
                    st.session_state.research_saved_token = active_key
                    st.session_state.research_saved_model = hf_model
                    st.session_state.research_saved_provider = provider
                    st.session_state.research_transcript_lang = _detect_transcript_lang(transcript)
                    st.session_state.research_transcript = transcript
                    # Reset concept enable/disable state for the new graph
                    new_concept_ids = {
                        n["id"] for n in graph_data.get("nodes", [])
                        if isinstance(n.get("id"), str)
                    }
                    st.session_state.research_enabled_concepts = new_concept_ids
                    for key in list(st.session_state.keys()):
                        if key.startswith("research_concept_"):
                            del st.session_state[key]
                    save_concept_graph(vid, approach, graph_data)
                    st.success(t("research_graph_saved"))
                else:
                    st.session_state.pop("research_graph_data", None)
                    st.warning(t("research_no_concepts"))

    # Show graph results + question generation if graph is available
    if "research_graph_data" in st.session_state:
        graph_data = st.session_state.research_graph_data

        # --- Transcript section ---
        st.subheader(t("research_transcript_section"))
        transcript_in_state = st.session_state.get("research_transcript")
        if transcript_in_state:
            with st.expander(t("research_transcript_preview"), expanded=False):
                st.text_area("", transcript_in_state, height=300, disabled=True, label_visibility="collapsed", key="research_transcript_section_preview")
        else:
            if st.button(t("research_load_transcript"), key="research_load_transcript_btn"):
                with st.spinner(t("research_fetching_transcript")):
                    loaded = _fetch_youtube_transcript(url.strip())
                if loaded:
                    st.session_state.research_transcript = loaded
                    st.rerun()
                else:
                    st.error(t("research_no_transcript"))

        st.divider()

        _display_concept_graph(graph_data)

        st.divider()

        # --- Extract facts from transcript ---
        st.subheader(t("research_enrich_graph_title"))
        st.caption(t("research_enrich_graph_desc"))

        if st.button(t("research_enrich_btn"), type="secondary", key="research_enrich_btn"):
            if not active_key:
                st.error(t("research_no_hf_token"))
            else:
                transcript = st.session_state.get("research_transcript")
                if not transcript:
                    with st.spinner(t("research_fetching_transcript")):
                        transcript = _fetch_youtube_transcript(url.strip())
                    if transcript:
                        st.session_state.research_transcript = transcript
                if not transcript:
                    st.error(t("research_no_transcript"))
                else:
                    with st.spinner(t("research_enriching_graph")):
                        grounded = _ground_graph(graph_data, transcript)
                    if grounded:
                        with st.spinner(t("research_extracting_facts")):
                            with_facts = _add_facts(grounded, active_key, hf_model, provider=provider)
                        if with_facts:
                            st.session_state.research_grounded_data = with_facts
                            save_research_extras(video_id, approach, facts_json=with_facts)

        if "research_grounded_data" in st.session_state:
            _display_grounded_graph(st.session_state.research_grounded_data)

        st.divider()

        # --- Question generation ---
        st.subheader(t("research_generate_questions_title"))
        num_questions = st.number_input(
            t("research_num_questions"),
            min_value=1,
            max_value=50,
            value=10,
            step=1,
            key="research_num_questions",
        )

        if st.button(t("research_generate_questions"), type="secondary", key="research_gen_q_btn"):
            if not active_key:
                st.error(t("research_no_hf_token"))
            else:
                transcript = st.session_state.get("research_transcript")
                if not transcript:
                    with st.spinner(t("research_fetching_transcript")):
                        transcript = _fetch_youtube_transcript(url.strip())
                    if transcript:
                        st.session_state.research_transcript = transcript
                if not transcript:
                    st.error(t("research_no_transcript"))
                else:
                    enabled = st.session_state.get("research_enabled_concepts")
                    questions = _generate_questions(graph_data, transcript, int(num_questions), active_key, hf_model, enabled_concepts=enabled, provider=provider)
                    if questions:
                        st.session_state.research_questions = questions
                        save_research_extras(video_id, approach, questions_json=questions)

        if "research_questions" in st.session_state:
            _display_questions(st.session_state.research_questions)

        st.divider()

        # --- Video segment analysis ---
        st.subheader(t("research_segments_title"))
        st.caption(t("research_segments_desc"))

        if st.button(t("research_segment_btn"), type="secondary", key="research_segment_btn"):
            if not active_key:
                st.error(t("research_no_hf_token"))
            else:
                transcript = st.session_state.get("research_transcript")
                if not transcript:
                    with st.spinner(t("research_fetching_transcript")):
                        transcript = _fetch_youtube_transcript(url.strip())
                    if transcript:
                        st.session_state.research_transcript = transcript
                if not transcript:
                    st.error(t("research_no_transcript"))
                else:
                    segments_data = _compute_segments(
                        transcript, graph_data, active_key, hf_model, provider
                    )
                    if segments_data:
                        st.session_state.research_segments_data = segments_data
                        save_research_extras(video_id, approach, segments_json=segments_data)

        if "research_segments_data" in st.session_state:
            _display_segments(
                st.session_state.research_segments_data,
                grounded_data=st.session_state.get("research_grounded_data"),
                questions=st.session_state.get("research_questions"),
            )


def _compute_segments(transcript, graph_data, api_key, model, provider):
    """Segment the transcript by concept coverage using the LLM pipeline."""
    try:
        import sys
        import os
        import re
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)

        from segment_transcript_by_concepts import (
            parse_timestamped_transcript, build_initial_windows,
            merge_windows, prompt_clean_window, prompt_identify_concepts,
            normalize_text,
        )

        nodes = graph_data.get("nodes", [])
        concept_nodes = [n for n in nodes if n.get("kind") != "fact" and isinstance(n.get("id"), str)]
        if not concept_nodes:
            st.warning(t("research_no_concepts"))
            return None

        # Use node IDs as both id and label so results map directly back to the graph
        concepts_for_llm = [{"id": n["id"], "label": n["id"]} for n in concept_nodes]

        # _fetch_youtube_transcript returns "[m:ss] text" per line.
        # parse_timestamped_transcript needs standalone "m:ss" lines, so reformat first.
        bracket_ts_re = re.compile(r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$')
        reformatted_lines = []
        for line in transcript.splitlines():
            m = bracket_ts_re.match(line.strip())
            if m:
                reformatted_lines.append(m.group(1))
                if m.group(2).strip():
                    reformatted_lines.append(m.group(2).strip())
            else:
                reformatted_lines.append(line)
        transcript_for_parse = "\n".join(reformatted_lines)

        blocks = parse_timestamped_transcript(transcript_for_parse)
        if not blocks:
            st.warning(t("research_no_timestamps"))
            return None

        windows = build_initial_windows(blocks, max_chars=1200, max_gap_sec=20)
        if not windows:
            st.warning(t("research_no_timestamps"))
            return None

        client = _make_llm_client(provider, api_key)

        analyzed_windows = []
        progress = st.progress(0, text=t("research_computing_segments"))
        for i, w in enumerate(windows):
            clean_text = prompt_clean_window(client, model, w["text"])
            concept_result = prompt_identify_concepts(client, model, clean_text, concepts_for_llm)
            analyzed_windows.append({
                "start_time": w["start_time"],
                "end_time": w["end_time"],
                "start_sec": w["start_sec"],
                "end_sec": w["end_sec"],
                "raw_text": w["text"],
                "clean_text": clean_text,
                "summary": concept_result.get("summary", ""),
                "question_worthy": float(concept_result.get("question_worthy", 0.0)),
                "explained_concepts": concept_result.get("explained_concepts", []),
            })
            progress.progress((i + 1) / len(windows))
        progress.empty()

        merged = merge_windows(analyzed_windows)

        final_segments = []
        for idx, seg in enumerate(merged, start=1):
            final_segments.append({
                "segment_id": f"S{idx}",
                "start_time": seg["start_time"],
                "end_time": seg["end_time"],
                "duration_seconds": max(0, seg["end_sec"] - seg["start_sec"]),
                "question_worthy": seg["question_worthy"],
                "summary": seg["summary"],
                "concept_labels": [c.get("label", c.get("id", "")) for c in seg["explained_concepts"]],
                "concept_details": seg["explained_concepts"],
                "clean_text": normalize_text(seg["clean_text"]),
            })

        return {
            "segments": final_segments,
            "meta": {
                "model": model,
                "initial_windows": len(windows),
                "final_segments": len(final_segments),
                "concept_count": len(concept_nodes),
            },
        }

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error computing segments: {e}")
        return None


def _display_segments(segments_data, grounded_data=None, questions=None):
    """Display video segments with their concepts, facts, and questions."""
    import json
    from collections import defaultdict

    segments = segments_data.get("segments", [])
    meta = segments_data.get("meta", {})

    col1, col2, col3 = st.columns(3)
    col1.metric(t("research_segment_count"), len(segments))
    col2.metric(t("research_segment_windows"), meta.get("initial_windows", "—"))
    col3.metric(t("research_concepts"), meta.get("concept_count", "—"))

    # Index facts and questions by concept label for quick lookup
    facts_by_concept = defaultdict(list)
    if grounded_data:
        for n in grounded_data.get("nodes", []):
            if n.get("kind") == "fact":
                facts_by_concept[n.get("subject_concept", "")].append(n)

    questions_by_concept = defaultdict(list)
    if questions:
        for q in questions:
            questions_by_concept[q.get("concept", "")].append(q)

    strength_icons = {"high": "🟢", "medium": "🟡", "low": "🔴"}

    for seg in segments:
        start = seg.get("start_time", "")
        end = seg.get("end_time", "")
        duration = seg.get("duration_seconds", 0)
        qw = seg.get("question_worthy", 0.0)
        summary = seg.get("summary", "")
        concept_details = seg.get("concept_details", [])
        concept_labels = seg.get("concept_labels", [])

        expander_label = f"🕐 {start} → {end}  •  {duration}s  •  {len(concept_details)} {t('research_segment_concepts_covered')}  •  ⭐ {qw:.2f}"
        with st.expander(expander_label, expanded=False):
            if summary:
                st.markdown(f"**{t('research_segment_summary')}:** {summary}")

            if concept_details:
                st.markdown(f"**{t('research_segment_concepts_covered').capitalize()}:**")
                for c in concept_details:
                    icon = strength_icons.get(c.get("explanation_strength", "low"), "⚪")
                    ev = c.get("evidence", "")
                    clabel = c.get("label", c.get("id", ""))
                    st.markdown(f"{icon} **{clabel}**")
                    if ev:
                        st.caption(f'*"{ev}"*')

            seg_facts = [f for clabel in concept_labels for f in facts_by_concept.get(clabel, [])]
            if seg_facts:
                st.markdown(f"**{t('research_segment_facts_title')}:**")
                for fn in seg_facts:
                    st.markdown(f"- {fn['text']}")
                    st.caption(f'*"{fn.get("evidence", "")}"*')

            seg_questions = [q for clabel in concept_labels for q in questions_by_concept.get(clabel, [])]
            if seg_questions:
                st.markdown(f"**{t('research_segment_questions_title')}:**")
                for q in seg_questions:
                    st.markdown(f"- {q.get('question', '')}")

            clean_text = seg.get("clean_text", "")
            if clean_text:
                with st.expander(f"📄 {t('research_segment_transcript')}", expanded=False):
                    st.text(clean_text)

    st.download_button(
        t("research_download_segments"),
        data=json.dumps(segments_data, ensure_ascii=False, indent=2),
        file_name="video_segments.json",
        mime="application/json",
        key="research_download_segments_btn",
    )


def _build_concept_graph_nlp(transcript_text):
    """Build a concept graph using the rule-based NLP approach."""
    try:
        import spacy
        from concept_graph_from_transcript import (
            ExtractionConfig, extract_concepts, extract_relations,
            build_graph, graph_to_json, is_good_concept,
        )

        nlp = spacy.load("en_core_web_sm")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer", first=True)

        cfg = ExtractionConfig()
        concepts = extract_concepts(nlp, transcript_text)
        shortlist = [c for c, _ in concepts.most_common(cfg.max_concepts * 2)]
        shortlist = [c for c in shortlist if is_good_concept(c)]
        concept_set = set(shortlist)

        triples = extract_relations(nlp, transcript_text, concept_set)
        G = build_graph(concepts, triples, cfg)

        return graph_to_json(G)
    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error building concept graph: {e}")
        return None


def _build_concept_graph_nlp_v2(transcript_text, hf_token=None, hf_model=None, provider="huggingface"):
    """Build a concept graph using the improved v2 rule-based approach (timestamp chunking, cue patterns, aliases)."""
    try:
        import sys
        import os
        import networkx as nx
        from collections import Counter, defaultdict
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)

        from concept_graph_extractor_v2 import (
            clean_transcript, detect_lang, get_nlp, ExtractConfig,
            chunk_by_timestamps, extract_cue_terms, extract_alias_pairs,
            extract_spacy_terms, extract_relations, fuzzy_merge, dedup_list,
            llm_refine_labels, REL_TYPES, is_good_concept_label,
        )

        cfg = ExtractConfig()
        text = clean_transcript(transcript_text)
        lang = detect_lang(text)
        nlp = get_nlp(lang)
        chunks = chunk_by_timestamps(text, max_chars=cfg.chunk_max_chars)

        term_counts = Counter()
        term_mentions = defaultdict(list)
        alias_pairs_all = []
        raw_relations = []

        progress = st.progress(0, text=t("research_building_graph"))
        for idx, ch in enumerate(chunks):
            alias_pairs_all.extend(extract_alias_pairs(ch))
            for term in extract_cue_terms(ch):
                term_counts[term] += 3
                term_mentions[term].append(ch[:220])
            for term in extract_spacy_terms(nlp, ch):
                term_counts[term] += 1
                if len(term_mentions[term]) < cfg.keep_top_snippets_per_concept:
                    term_mentions[term].append(ch[:220])
            raw_relations.extend(extract_relations(ch))
            progress.progress((idx + 1) / len(chunks))
        progress.empty()

        candidates = [
            term for term, f in term_counts.items()
            if f >= cfg.min_freq and is_good_concept_label(term)
        ]
        candidates = sorted(candidates, key=lambda term: term_counts[term], reverse=True)[: cfg.max_concepts * 2]
        mapping = fuzzy_merge(candidates, threshold=cfg.fuzzy_merge_threshold)

        canon_counts = Counter()
        canon_mentions = defaultdict(list)
        for term, f in term_counts.items():
            if term in mapping:
                c = mapping[term]
                canon_counts[c] += f
                canon_mentions[c].extend(term_mentions.get(term, []))

        canon = [term for term, _ in canon_counts.most_common(cfg.max_concepts)]
        canon_set = set(canon)

        alias_edges = [
            (mapping.get(a, a), "aka", mapping.get(b, b))
            for a, b in alias_pairs_all
            if mapping.get(a, a) != mapping.get(b, b)
            and mapping.get(a, a) in canon_set
            and mapping.get(b, b) in canon_set
        ]

        edges = [
            (mapping.get(src, src), rel, mapping.get(tgt, tgt), ev)
            for src, rel, tgt, ev in raw_relations
            if mapping.get(src, src) in canon_set
            and mapping.get(tgt, tgt) in canon_set
            and rel in REL_TYPES
            and mapping.get(src, src) != mapping.get(tgt, tgt)
        ]

        # Optional LLM label refinement
        if hf_token and hf_model and canon:
            try:
                client = _make_llm_client(provider, hf_token)
                with st.spinner(t("research_refining_labels")):
                    refined = llm_refine_labels(client, hf_model, canon, lang=lang)
                if len(refined) == len(canon):
                    ren = {canon[i]: refined[i] for i in range(len(canon))}
                    canon = [ren[c] for c in canon]
                    canon_set = set(canon)
                    cc2, cm2 = Counter(), defaultdict(list)
                    for old, val in canon_counts.items():
                        if old in ren:
                            cc2[ren[old]] += val
                            cm2[ren[old]].extend(canon_mentions.get(old, []))
                    canon_counts, canon_mentions = cc2, cm2
                    edges = [
                        (ren.get(s, s), r, ren.get(tg, tg), ev) for s, r, tg, ev in edges
                        if ren.get(s, s) in canon_set and ren.get(tg, tg) in canon_set and ren.get(s, s) != ren.get(tg, tg)
                    ]
                    alias_edges = [
                        (ren.get(s, s), r, ren.get(tg, tg)) for s, r, tg in alias_edges
                        if ren.get(s, s) in canon_set and ren.get(tg, tg) in canon_set and ren.get(s, s) != ren.get(tg, tg)
                    ]
            except Exception as e:
                st.warning(f"LLM refinement failed (graph still built): {e}")

        G = nx.DiGraph()
        for c in canon:
            G.add_node(c, frequency=int(canon_counts.get(c, 1)))

        edge_counter = Counter()
        edge_evidence = defaultdict(list)
        for s, r, tg, ev in edges:
            edge_counter[(s, r, tg)] += 1
            if len(edge_evidence[(s, r, tg)]) < 3:
                edge_evidence[(s, r, tg)].append(ev)
        for (s, r, tg), w in edge_counter.items():
            G.add_edge(s, tg, relation=r, weight=int(w), evidence=" | ".join(edge_evidence[(s, r, tg)]))
        for s, r, tg in alias_edges:
            if not G.has_edge(s, tg):
                G.add_edge(s, tg, relation=r, weight=1)

        return {
            "nodes": [{"id": n, **G.nodes[n], "mentions": dedup_list(canon_mentions.get(n, []), 5)} for n in G.nodes],
            "edges": [{"source": u, "target": v, **G.edges[u, v]} for u, v in G.edges],
        }

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error building concept graph (v2): {e}")
        return None


def _build_concept_graph_timestamp(transcript_text):
    """Build a concept graph using the timestamp-chunked rule-based approach (Spanish-focused patterns)."""
    try:
        import sys
        import os
        import networkx as nx
        from collections import Counter, defaultdict
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)

        from concept_graph_from_timestamp_transcript import (
            chunk_timestamp_transcript, extract_from_chunk,
            fuzzy_merge, dedup_list, is_good_label,
        )

        REL_TYPES = {"has_part", "part_of", "divides_into", "articulates_with", "aka"}
        MAX_CONCEPTS = 120
        MIN_FREQ = 2
        CHUNK_MAX_CHARS = 1400
        FUZZY_THRESHOLD = 93

        chunks = chunk_timestamp_transcript(transcript_text, max_chars=CHUNK_MAX_CHARS)

        term_counts = Counter()
        mentions = defaultdict(list)
        raw_edges = []
        raw_aliases = []

        progress = st.progress(0, text=t("research_building_graph"))
        for idx, ch in enumerate(chunks):
            txt = ch["text"]
            concepts, rels, aliases = extract_from_chunk(txt)

            for c in concepts:
                if is_good_label(c):
                    term_counts[c] += 2
                    if len(mentions[c]) < 6:
                        ts_prefix = f"{ch['t0']}-{ch['t1']}: " if ch.get("t0") else ""
                        mentions[c].append(ts_prefix + (txt[:220] + ("..." if len(txt) > 220 else "")))

            for (src, rel, tgt, ev) in rels:
                term_counts[src] += 2
                term_counts[tgt] += 2
                raw_edges.append((src, rel, tgt, ev))

            raw_aliases.extend(aliases)
            progress.progress((idx + 1) / len(chunks))

        progress.empty()

        candidates = [
            term for term, freq in term_counts.items()
            if freq >= MIN_FREQ and is_good_label(term)
        ]
        candidates = sorted(candidates, key=lambda term: term_counts[term], reverse=True)[: MAX_CONCEPTS * 2]

        mp = fuzzy_merge(candidates, threshold=FUZZY_THRESHOLD)

        canon_counts = Counter()
        canon_mentions = defaultdict(list)
        for term, freq in term_counts.items():
            if term in mp:
                c = mp[term]
                canon_counts[c] += freq
                canon_mentions[c].extend(mentions.get(term, []))

        canon = [term for term, _ in canon_counts.most_common(MAX_CONCEPTS)]
        canon_set = set(canon)

        G = nx.DiGraph()
        for c in canon:
            G.add_node(c, frequency=int(canon_counts[c]))

        edge_counter = Counter()
        edge_evidence = defaultdict(list)
        for src, rel, tgt, ev in raw_edges:
            src2 = mp.get(src, src)
            tgt2 = mp.get(tgt, tgt)
            if src2 in canon_set and tgt2 in canon_set and src2 != tgt2 and rel in REL_TYPES:
                edge_counter[(src2, rel, tgt2)] += 1
                if len(edge_evidence[(src2, rel, tgt2)]) < 3:
                    edge_evidence[(src2, rel, tgt2)].append(ev)

        for (src, rel, tgt), w in edge_counter.items():
            G.add_edge(src, tgt, relation=rel, weight=int(w), evidence=" | ".join(edge_evidence[(src, rel, tgt)]))

        for a, b in raw_aliases:
            a2 = mp.get(a, a)
            b2 = mp.get(b, b)
            if a2 in canon_set and b2 in canon_set and a2 != b2:
                if not G.has_edge(a2, b2):
                    G.add_edge(a2, b2, relation="aka", weight=1)

        return {
            "nodes": [
                {"id": n, **G.nodes[n], "mentions": dedup_list(canon_mentions.get(n, []), max_keep=6)}
                for n in G.nodes
            ],
            "edges": [
                {"source": u, "target": v, **G.edges[u, v]}
                for u, v in G.edges
            ],
        }

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error building concept graph (timestamp): {e}")
        return None


def _build_concept_graph_prompt_seq(transcript_text, hf_token, hf_model, subject_matter="", provider="huggingface"):
    """Build a concept graph via multi-step LLM prompt sequence: clean → scope → concepts → relations."""
    try:
        import json
        import re

        client = _make_llm_client(provider, hf_token)

        def _strip_fences(raw):
            raw = raw.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return raw

        def _try_parse(raw):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r"(\{.*\})", raw, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
            return None

        def call_json_prompt(system_prompt, user_prompt, max_tokens=2000):
            resp = client.chat.completions.create(
                model=hf_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
            )
            text = _strip_fences(resp.choices[0].message.content)
            result = _try_parse(text)
            if result is not None:
                return result
            # Repair: ask the model to fix its own malformed JSON
            repair_resp = client.chat.completions.create(
                model=hf_model,
                messages=[
                    {"role": "system", "content": "Fix the malformed JSON below. Return ONLY valid JSON, nothing else."},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
            )
            repaired = _strip_fences(repair_resp.choices[0].message.content)
            result = _try_parse(repaired)
            if result is not None:
                return result
            raise ValueError(f"Could not parse LLM response as JSON: {text[:200]}")

        # Step 1: Clean transcript
        with st.spinner(t("research_ps_cleaning")):
            cleaned = call_json_prompt(
                "You clean noisy educational transcripts. Return JSON only.",
                f"""You are cleaning an automatic transcript from an educational video.

Task: Rewrite the transcript into a clean educational version.

Rules:
- Keep ONLY educational content.
- Remove filler words, hesitations, repeated fragments, transcription noise, and conversational artifacts.
- Preserve the original meaning and terminology exactly.
- Do NOT add information that is not present in the text.
- Keep the output in the same language as the input.
- Organize the result into clear paragraphs.

Output:
{{
  "clean_text": "..."
}}

Transcript:
<<<{transcript_text}>>>""",
                max_tokens=4000,
            )
        clean_text = cleaned.get("clean_text", transcript_text)

        # Step 2: Scope to subject matter (only if provided)
        educational_content = clean_text
        if subject_matter.strip():
            with st.spinner(t("research_ps_scoping")):
                scoped = call_json_prompt(
                    "You extract subject-relevant educational content. Return JSON only.",
                    f"""From the cleaned transcript, identify only the content that belongs to the subject matter: "{subject_matter}".

Rules:
- Keep only material relevant to the requested subject matter.
- Remove examples or side remarks not central to the subject.
- Do NOT add outside knowledge.
- Keep the output in the same language as the input.

Output:
{{
  "educational_content": "...",
  "excluded_content_summary": ["...", "..."]
}}

Cleaned transcript:
<<<{clean_text}>>>""",
                    max_tokens=4000,
                )
            educational_content = scoped.get("educational_content", clean_text)

        # Step 3: Extract concepts
        with st.spinner(t("research_ps_concepts")):
            concepts_result = call_json_prompt(
                "You extract concepts from educational material. Return JSON only.",
                f"""Identify the main concepts explicitly present in the text.

Rules:
- Extract only concepts explicitly mentioned or clearly defined in the text.
- Prefer domain concepts over generic words.
- Keep multi-word concepts when needed.
- Include aliases only if the text clearly suggests they refer to the same concept.
- Do NOT use outside knowledge or infer missing concepts.

Output:
{{
  "concepts": [
    {{
      "id": "C1",
      "label": "...",
      "aliases": ["..."]
    }}
  ]
}}

Text:
<<<{educational_content}>>>""",
                max_tokens=3000,
            )
        concepts_data = concepts_result.get("concepts", [])
        if not concepts_data:
            st.warning(t("research_no_concepts"))
            return None

        # Step 4: Extract relations
        with st.spinner(t("research_ps_relations")):
            relations_result = call_json_prompt(
                "You extract concept graph relations from educational material. Return JSON only.",
                f"""Using only the concepts provided, extract directed relations explicitly supported by the text.

Allowed relation types: is_a, part_of, has_part, depends_on, causes, used_for, related_to

Rules:
- Use ONLY the provided concept IDs.
- Create a relation only if the text supports it.
- Do NOT use outside knowledge. Prefer specific relations over related_to.

Output:
{{
  "relations": [
    {{
      "source": "C1",
      "relation": "has_part",
      "target": "C2"
    }}
  ]
}}

Concepts:
<<<{json.dumps(concepts_data, ensure_ascii=False)}>>>

Text:
<<<{educational_content}>>>""",
                max_tokens=3000,
            )
        relations_data = relations_result.get("relations", [])

        # Convert to standard graph format
        id_to_label = {c["id"]: c["label"] for c in concepts_data if c.get("id") and c.get("label")}

        seen_labels = set()
        nodes = []
        for c in concepts_data:
            label = c.get("label", "").strip()
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            node = {"id": label, "frequency": 1}
            if c.get("description"):
                node["description"] = c["description"]
            if c.get("aliases"):
                node["aliases"] = c["aliases"]
            if c.get("evidence"):
                node["evidence_snippets"] = [c["evidence"]]
            nodes.append(node)

        edges = []
        for r in relations_data:
            src_label = id_to_label.get(r.get("source"))
            tgt_label = id_to_label.get(r.get("target"))
            if not src_label or not tgt_label or src_label == tgt_label:
                continue
            edges.append({
                "source": src_label,
                "target": tgt_label,
                "relation": r.get("relation", "related_to"),
                "weight": 1,
                "evidence": r.get("evidence", ""),
            })

        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "approach": "prompt_seq",
                "subject_matter": subject_matter,
            },
        }

    except Exception as e:
        st.error(f"Error building concept graph (prompt sequence): {e}")
        return None


def _build_concept_graph_llm(transcript_text, hf_token, hf_model, provider="huggingface"):
    """Build a concept graph using the LLM-based HuggingFace approach."""
    try:
        import sys
        import os
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _streamlit_dir = os.path.join(_root, ".streamlit")
        if _streamlit_dir not in sys.path:
            sys.path.insert(0, _streamlit_dir)

        from concept_graph_hf_llm import (
            clean_transcript, detect_lang, chunk_text, get_nlp,
            extract_concepts_from_chunk, choose_top_concepts,
            llm_extract_edges, build_graph, graph_to_json,
            LLMConfig, normalize_term,
        )

        text = clean_transcript(transcript_text)
        chunks = chunk_text(text)

        all_terms = []
        for ch in chunks:
            lang = detect_lang(ch)
            nlp = get_nlp(lang)
            all_terms.extend(extract_concepts_from_chunk(nlp, ch))

        top_concepts, mapping, canon_counts = choose_top_concepts(all_terms)
        if not top_concepts:
            return None

        concept_id_to_label = {f"C{i+1:03d}": c for i, c in enumerate(top_concepts)}
        label_to_concept_id = {v: k for k, v in concept_id_to_label.items()}

        client = _make_llm_client(provider, hf_token)
        llm_cfg = LLMConfig(model=hf_model)

        all_edges = []
        progress = st.progress(0, text=t("research_building_graph"))
        for idx, ch in enumerate(chunks):
            lang = detect_lang(ch)
            ch_norm = ch.lower()
            present = [c for c in top_concepts if c in ch_norm]
            if len(present) < 2:
                local_terms = [normalize_term(t_) for t_ in extract_concepts_from_chunk(get_nlp(lang), ch)]
                local_terms = [mapping.get(t_, t_) for t_ in local_terms]
                present = list({t_ for t_ in local_terms if t_ in label_to_concept_id})
            if len(present) < 2:
                progress.progress((idx + 1) / len(chunks))
                continue
            present.sort(key=lambda c: canon_counts.get(c, 0), reverse=True)
            present = present[:18]
            try:
                edges = llm_extract_edges(client, llm_cfg, ch, present, lang)
            except RuntimeError as e:
                st.warning(str(e))
                edges = []
            local_id_to_global_id = {f"C{i+1:03d}": label_to_concept_id[present[i]] for i in range(len(present))}
            for e in edges:
                all_edges.append({
                    "source": local_id_to_global_id.get(e["source"]),
                    "target": local_id_to_global_id.get(e["target"]),
                    "relation": e["relation"],
                    "evidence": e["evidence"],
                })
            progress.progress((idx + 1) / len(chunks))

        progress.empty()
        all_edges = [e for e in all_edges if e["source"] and e["target"]]
        G = build_graph(top_concepts, canon_counts, all_edges, concept_id_to_label)
        result = graph_to_json(G)
        # graph_to_json does {"id": n, **G.nodes[n]} but G.nodes[n] has id=C-code
        # which overwrites the concept label — rebuild nodes using the graph keys directly
        result["nodes"] = [
            {"id": n, "frequency": G.nodes[n].get("frequency", 1)}
            for n in G.nodes
        ]
        return result

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error building concept graph: {e}")
        return None


def _detect_transcript_lang(transcript):
    """Detect the dominant language of the transcript."""
    try:
        from langdetect import detect
        lang = detect(transcript[:800])
        return "es" if lang.startswith("es") else "en"
    except Exception:
        return "en"


def _generate_questions(graph_data, transcript, num_questions, hf_token, hf_model, enabled_concepts=None, provider="huggingface"):
    """Generate grounded questions from the concept graph + transcript using the LLM."""
    try:
        import re
        import json
        import random
        import hashlib
        from collections import Counter, defaultdict
        from langdetect import detect, LangDetectException
        from rapidfuzz import fuzz

        # --- helpers (inline from grounded_questions_from_graph.py) ---

        RELATION_TO_QTYPES = {
            "depends_on": "dependency_reasoning", "causes": "causal_reasoning",
            "part_of": "component_reasoning", "is_a": "comparison",
            "used_for": "application", "related_to": "explanation",
        }
        QUESTION_TEMPLATES = [
            "definition", "explanation", "application", "comparison",
            "dependency_reasoning", "causal_reasoning", "component_reasoning",
        ]

        def _norm(s):
            s = s.lower().strip()
            s = re.sub(r"\s+", " ", s)
            return s.strip("`'\"")

        def _detect_lang(text):
            try:
                lang = detect(text[:800])
                return "es" if lang.startswith("es") else "en"
            except LangDetectException:
                return "en"

        def _clean_transcript(text):
            text = re.sub(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?", " ", text)
            text = re.sub(r"^\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_\- ]{1,30}:\s+", "", text, flags=re.MULTILINE)
            return re.sub(r"\s+", " ", text).strip()

        def _split_sentences(text, lang):
            import spacy
            _nlp_cache = {}
            key = "es" if lang == "es" else "en"
            if key not in _nlp_cache:
                model = "es_core_news_sm" if key == "es" else "en_core_web_sm"
                nlp = spacy.load(model)
                if "sentencizer" not in nlp.pipe_names:
                    try:
                        nlp.add_pipe("sentencizer", first=True)
                    except Exception:
                        pass
                _nlp_cache[key] = nlp
            doc = _nlp_cache[key](text)
            return [s.text.strip() for s in doc.sents if s.text.strip()]

        def _build_evidence_index(text, concepts, lang, window=1, max_snip=6, min_len=30):
            sents = _split_sentences(text, lang)
            norm_sents = [_norm(s) for s in sents]
            idx = defaultdict(list)
            for concept in sorted(concepts, key=len, reverse=True):
                c_norm = _norm(concept)
                if len(c_norm) < 3:
                    continue
                for i, s_norm in enumerate(norm_sents):
                    if c_norm in s_norm:
                        left = max(0, i - window)
                        right = min(len(sents), i + window + 1)
                        snippet = " ".join(sents[left:right]).strip()
                        if len(snippet) < min_len:
                            continue
                        if any(fuzz.token_set_ratio(snippet, ex) > 92 for ex in idx[concept]):
                            continue
                        idx[concept].append(snippet)
                        if len(idx[concept]) >= max_snip:
                            break
            return idx

        def _build_context(concept, ev_idx, out_edges, in_edges, max_chars=1800):
            facts = ([f"{concept} {r} {tgt}" for tgt, r in out_edges.get(concept, [])[:3]] +
                     [f"{src} {r} {concept}" for src, r in in_edges.get(concept, [])[:3]])
            snippets = list(ev_idx.get(concept, []))
            neighbors = [t for t, _ in out_edges.get(concept, [])] + [s for s, _ in in_edges.get(concept, [])]
            random.shuffle(neighbors)
            for nb in neighbors[:2]:
                snippets += ev_idx.get(nb, [])[:1]
            ctx = ""
            for sn in snippets:
                if not ctx:
                    ctx = sn
                elif len(ctx) + len(sn) + 3 <= max_chars:
                    ctx += "\n\n" + sn
                else:
                    break
            return ctx.strip(), facts

        def _make_prompt(concept, qtype, difficulty, context, lang, facts):
            facts_block = "\n".join(f"- {f}" for f in facts) if facts else "- (none)"
            if lang == "es":
                system = ("Eres un generador de preguntas de estudio. "
                          "Debes basarte ÚNICAMENTE en el CONTEXTO proporcionado. "
                          "Devuelve SOLO JSON válido.")
                user = (f'CONCEPTO: "{concept}"\nTIPO: {qtype}\nDIFICULTAD: {difficulty}\n\n'
                        f'HECHOS RELACIONADOS:\n{facts_block}\n\n'
                        f'CONTEXTO:\n"""{context}"""\n\n'
                        f'Genera UNA pregunta basada solo en el contexto. Devuelve SOLO JSON:\n'
                        f'{{"question":"...","answer":"...","evidence":"...","concept":"{concept}",'
                        f'"type":"{qtype}","difficulty":"{difficulty}","language":"es"}}')
            else:
                system = ("You generate study questions. "
                          "You MUST rely ONLY on the provided CONTEXT. "
                          "Return ONLY valid JSON.")
                user = (f'CONCEPT: "{concept}"\nTYPE: {qtype}\nDIFFICULTY: {difficulty}\n\n'
                        f'RELATED FACTS:\n{facts_block}\n\n'
                        f'CONTEXT:\n"""{context}"""\n\n'
                        f'Generate ONE question grounded in the context. Return ONLY JSON:\n'
                        f'{{"question":"...","answer":"...","evidence":"...","concept":"{concept}",'
                        f'"type":"{qtype}","difficulty":"{difficulty}","language":"en"}}')
            return [{"role": "system", "content": system}, {"role": "user", "content": user}]

        def _safe_json(text):
            text = text.strip()
            text = re.sub(r"^```(json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            try:
                return json.loads(text)
            except Exception:
                m = re.search(r"(\{.*\})", text, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except Exception:
                        return None
            return None

        def _validate(item, context):
            ev = item.get("evidence", "")
            if not isinstance(ev, str) or len(ev.split()) < 4:
                return False
            if _norm(ev) not in _norm(context):
                return False
            return bool(item.get("answer", "").strip()) and bool(item.get("question", "").strip())

        def _fingerprint(item):
            key = "|".join([_norm(item.get(k, "")) for k in ("concept", "type", "difficulty", "question")])
            return hashlib.sha256(key.encode()).hexdigest()

        def _concept_score(c, freq, out_edges, in_edges, used):
            degree = len(out_edges.get(c, [])) + len(in_edges.get(c, []))
            f = freq.get(c, 1)
            return (0.6 * degree + 0.4 * (f ** 0.5)) / (1 + used.get(c, 0))

        # --- build graph structures ---
        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        concepts = [n["id"] for n in nodes if "id" in n]
        if enabled_concepts is not None:
            concepts = [c for c in concepts if c in enabled_concepts]
        freq = {n["id"]: int(n.get("frequency", 1)) for n in nodes if "id" in n}
        out_edges = defaultdict(list)
        in_edges = defaultdict(list)
        for e in edges:
            s, tgt, r = e.get("source"), e.get("target"), e.get("relation", "related_to")
            if s and tgt:
                out_edges[s].append((tgt, r))
                in_edges[tgt].append((s, r))

        # --- build evidence index ---
        clean_text = _clean_transcript(transcript)
        lang = _detect_lang(clean_text)
        ev_idx = _build_evidence_index(clean_text, concepts, lang)

        eligible = [c for c in concepts if ev_idx.get(c)]
        if not eligible:
            st.warning(t("research_no_concepts"))
            return None

        client = _make_llm_client(provider, hf_token)
        questions = []
        seen = set()
        used_count = Counter()
        attempts = 0
        max_attempts = max(200, num_questions * 10)
        progress = st.progress(0, text=t("research_generating_questions"))

        while len(questions) < num_questions and attempts < max_attempts:
            attempts += 1
            eligible.sort(key=lambda c: _concept_score(c, freq, out_edges, in_edges, used_count), reverse=True)
            concept = eligible[0]

            context, facts = _build_context(concept, ev_idx, out_edges, in_edges)
            if not context:
                used_count[concept] += 1
                continue

            chunk_lang = _detect_lang(context)
            rels = [r for _, r in out_edges.get(concept, [])] + [r for _, r in in_edges.get(concept, [])]
            qtype = next((RELATION_TO_QTYPES[r] for r in rels if r in RELATION_TO_QTYPES), random.choice(QUESTION_TEMPLATES))
            difficulty = random.choice(["easy", "medium", "hard"])

            messages = _make_prompt(concept, qtype, difficulty, context, chunk_lang, facts)
            try:
                resp = client.chat.completions.create(
                    model=hf_model, messages=messages, temperature=0.4, max_tokens=450,
                )
                item = _safe_json(resp.choices[0].message.content)
            except Exception as e:
                st.warning(f"LLM error for '{concept}': {e}")
                used_count[concept] += 1
                continue

            if not item or not _validate(item, context):
                used_count[concept] += 1
                continue

            item["concept"] = concept
            item["type"] = qtype
            item["difficulty"] = difficulty

            fp = _fingerprint(item)
            if fp in seen:
                used_count[concept] += 1
                continue
            if any(fuzz.token_set_ratio(item.get("question", ""), q.get("question", "")) > 92 for q in questions):
                used_count[concept] += 1
                continue

            questions.append(item)
            seen.add(fp)
            used_count[concept] += 1
            progress.progress(len(questions) / num_questions)

        progress.empty()
        return questions if questions else None

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error generating questions: {e}")
        return None


def _ground_graph(graph_data, transcript):
    """Enrich the concept graph with grounding evidence from the transcript."""
    try:
        import sys
        import os
        import copy
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)

        from ground_graph_with_transcript import (
            clean_transcript, detect_lang, split_sentences,
            build_node_grounding, build_edge_grounding,
        )

        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])

        concepts = [
            n["id"].strip() for n in nodes
            if isinstance(n.get("id"), str) and n["id"].strip()
        ]
        if not concepts:
            st.warning(t("research_no_concepts"))
            return None

        transcript_clean = clean_transcript(transcript)
        lang = detect_lang(transcript_clean)
        sentences = split_sentences(transcript_clean, lang)
        if not sentences:
            sentences = [transcript_clean]

        node_ground = build_node_grounding(
            concepts=concepts, sentences=sentences, window=1, max_snips=4
        )

        grounded_nodes = []
        for n in nodes:
            cid = n.get("id", "")
            if not isinstance(cid, str):
                continue
            cid = cid.strip()
            n2 = dict(n)
            n2["language"] = lang
            n2.update(node_ground.get(cid, {}))
            grounded_nodes.append(n2)

        grounded_edges = build_edge_grounding(
            edges=edges, sentences=sentences, window=1, max_snips=4
        )

        result = copy.deepcopy(graph_data)
        result["nodes"] = grounded_nodes
        result["edges"] = grounded_edges
        result["meta"] = {
            "grounding": {
                "transcript_language": lang,
                "window_sentences": 1,
                "max_snippets": 4,
                "num_sentences": len(sentences),
            }
        }
        return result

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error enriching graph: {e}")
        return None


def _add_facts(grounded_data, hf_token, hf_model, provider="huggingface"):
    """Extract explicit facts from grounded node snippets using the LLM."""
    try:
        import sys
        import os
        import copy
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)

        from add_facts_to_graph import (
            call_llm_extract_facts, stable_fact_id, detect_lang,
        )
        from rapidfuzz import fuzz

        nodes = grounded_data.get("nodes", [])
        edges = grounded_data.get("edges", [])

        client = _make_llm_client(provider, hf_token)

        existing_fact_ids = {n.get("id") for n in nodes if n.get("kind") == "fact"}
        concept_nodes = [n for n in nodes if n.get("kind") != "fact" and isinstance(n.get("id"), str)]

        new_fact_nodes = []
        new_fact_edges = []
        seen_fact_fp = set()

        progress = st.progress(0, text=t("research_extracting_facts"))
        for idx, n in enumerate(concept_nodes):
            concept = n.get("id", "").strip()
            if not concept:
                progress.progress((idx + 1) / len(concept_nodes))
                continue

            snips = n.get("evidence_snippets") or []
            for snippet in snips[:4]:
                if not isinstance(snippet, str) or len(snippet.strip()) < 30:
                    continue
                lang = detect_lang(snippet)
                facts = call_llm_extract_facts(
                    client=client, model=hf_model,
                    concept=concept, snippet=snippet, lang=lang, temperature=0.0,
                )
                for fobj in facts[:3]:
                    fid = stable_fact_id(concept, fobj["fact"], fobj["evidence"])
                    if fid in seen_fact_fp or fid in existing_fact_ids:
                        continue
                    seen_fact_fp.add(fid)
                    new_fact_nodes.append({
                        "id": fid, "kind": "fact",
                        "text": fobj["fact"],
                        "subject_concept": concept,
                        "language": lang,
                        "confidence": fobj["confidence"],
                        "evidence": fobj["evidence"],
                    })
                    new_fact_edges.append({
                        "source": concept, "target": fid,
                        "relation": "has_fact", "weight": 1, "grounded": True,
                    })

            progress.progress((idx + 1) / len(concept_nodes))

        progress.empty()

        # Dedup by text similarity
        deduped = []
        for fn in new_fact_nodes:
            if not any(
                fuzz.token_set_ratio(fn["text"], x["text"]) > 94
                and fn["subject_concept"] == x["subject_concept"]
                for x in deduped
            ):
                deduped.append(fn)

        result = copy.deepcopy(grounded_data)
        result["nodes"] = nodes + deduped
        result["edges"] = edges + new_fact_edges
        result.setdefault("meta", {})
        result["meta"]["facts_added"] = {
            "model": hf_model,
            "new_fact_nodes": len(deduped),
            "new_fact_edges": len(new_fact_edges),
        }
        return result

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error extracting facts: {e}")
        return None


def _display_grounded_graph(grounded_data):
    """Display the fact nodes extracted from the concept graph."""
    import json
    from collections import defaultdict

    nodes = grounded_data.get("nodes", [])
    facts_meta = grounded_data.get("meta", {}).get("facts_added", {})

    fact_nodes = [n for n in nodes if n.get("kind") == "fact"]
    concepts_with_facts = {n["subject_concept"] for n in fact_nodes}
    concept_nodes = [n for n in nodes if n.get("kind") != "fact"]
    concepts_without_facts = len(concept_nodes) - len(concepts_with_facts)

    col1, col2, col3 = st.columns(3)
    col1.metric(t("research_fact_nodes"), len(fact_nodes))
    col2.metric(t("research_concepts_with_facts"), len(concepts_with_facts))
    col3.metric(t("research_concepts_without_facts"), concepts_without_facts)

    if facts_meta.get("model"):
        st.caption(f"Model: {facts_meta['model']}")

    if fact_nodes:
        st.subheader(t("research_facts_by_concept"))
        facts_by_concept = defaultdict(list)
        for fn in fact_nodes:
            facts_by_concept[fn["subject_concept"]].append(fn)

        for concept, facts in sorted(facts_by_concept.items(), key=lambda x: -len(x[1])):
            with st.expander(f"{concept}  ({len(facts)} {t('research_facts_label')})", expanded=False):
                for fn in facts:
                    conf = fn.get("confidence", 0)
                    st.markdown(f"**{fn['text']}**")
                    st.caption(f'📄 *"{fn.get("evidence", "")}"*  — confidence: {conf:.2f}')
                    st.write("")

    st.download_button(
        t("research_download_grounded"),
        data=json.dumps(grounded_data, ensure_ascii=False, indent=2),
        file_name="concept_graph_with_facts.json",
        mime="application/json",
    )


def _display_concept_graph(graph_data):
    """Display the concept graph results with per-concept enable/disable controls."""
    import json

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    all_concept_ids = {n["id"] for n in nodes if isinstance(n.get("id"), str)}

    # Lazy-initialise enabled set (also handles first load)
    if "research_enabled_concepts" not in st.session_state:
        st.session_state.research_enabled_concepts = set(all_concept_ids)

    st.subheader(t("research_results"))

    # --- Concept management expander ---
    with st.expander(t("research_manage_concepts"), expanded=False):
        col_a, col_b = st.columns(2)
        if col_a.button(t("research_enable_all"), key="research_enable_all_btn"):
            for cid in all_concept_ids:
                st.session_state[f"research_concept_{cid}"] = True
            st.session_state.research_enabled_concepts = set(all_concept_ids)
            st.rerun()
        if col_b.button(t("research_disable_all"), key="research_disable_all_btn"):
            for cid in all_concept_ids:
                st.session_state[f"research_concept_{cid}"] = False
            st.session_state.research_enabled_concepts = set()
            st.rerun()

        sorted_all = sorted(nodes, key=lambda n: n.get("frequency", 0), reverse=True)
        new_enabled = set()
        for node in sorted_all:
            cid = node.get("id", "")
            freq = node.get("frequency", 0)
            checked = st.checkbox(
                f"{cid}  (×{freq})",
                value=cid in st.session_state.research_enabled_concepts,
                key=f"research_concept_{cid}",
            )
            if checked:
                new_enabled.add(cid)
        st.session_state.research_enabled_concepts = new_enabled

        disabled_count = len(all_concept_ids) - len(new_enabled)
        if disabled_count > 0:
            hidden_edges = sum(
                1 for e in edges
                if e.get("source") not in new_enabled or e.get("target") not in new_enabled
            )
            st.caption(t("research_disabled_info", concepts=disabled_count, relations=hidden_edges))

    # --- Show-enabled-only toggle ---
    show_enabled_only = st.toggle(
        t("research_show_enabled_only"), key="research_show_enabled_only_toggle"
    )

    enabled = st.session_state.research_enabled_concepts
    if show_enabled_only:
        display_nodes = [n for n in nodes if n.get("id") in enabled]
        display_edges = [
            e for e in edges
            if e.get("source") in enabled and e.get("target") in enabled
        ]
    else:
        display_nodes = nodes
        display_edges = edges

    col1, col2 = st.columns(2)
    col1.metric(t("research_concepts"), len(display_nodes))
    col2.metric(t("research_relations"), len(display_edges))

    if display_nodes:
        st.subheader(t("research_top_concepts"))
        sorted_nodes = sorted(display_nodes, key=lambda n: n.get("frequency", 0), reverse=True)
        for node in sorted_nodes[:20]:
            cid = node.get("id", "")
            freq = node.get("frequency", 0)
            bar = "█" * min(freq, 30)
            prefix = "  " if cid in enabled else "✗ "
            st.text(f"{prefix}{freq:3d}  {bar}  {cid}")

    if display_edges:
        st.subheader(t("research_relations_found"))
        sorted_edges = sorted(display_edges, key=lambda e: e.get("weight", 0), reverse=True)
        for edge in sorted_edges[:30]:
            rel = edge.get("relation", "related_to")
            w = edge.get("weight", 1)
            ev = edge.get("evidence", "")
            line = f"  {edge['source']}  —[{rel}]→  {edge['target']}  (x{w})"
            if ev:
                line += f"\n    ↳ {ev}"
            st.text(line)

    st.download_button(
        t("research_download_json"),
        data=json.dumps(graph_data, ensure_ascii=False, indent=2),
        file_name="concept_graph.json",
        mime="application/json",
    )


def _display_questions(questions):
    """Display generated grounded questions."""
    import json

    st.subheader(t("research_questions_title", n=len(questions)))
    for i, q in enumerate(questions, 1):
        with st.expander(f"{i}. {q.get('question', '')}", expanded=False):
            answer = q.get("answer", "")
            if answer:
                st.markdown(f"**✅ {t('research_question_answer')}:** {answer}")
            evidence = q.get("evidence", "")
            if evidence:
                st.caption(f"📄 *\"{evidence}\"*")
            col1, col2, col3 = st.columns(3)
            col1.caption(f"**{t('research_question_type')}:** {q.get('type', '')}")
            col2.caption(f"**{t('research_question_difficulty')}:** {q.get('difficulty', '')}")
            col3.caption(f"**{t('research_question_concept')}:** {q.get('concept', '')}")

    st.download_button(
        t("research_download_questions"),
        data=json.dumps(questions, ensure_ascii=False, indent=2),
        file_name="questions.json",
        mime="application/json",
    )
