import streamlit as st
from translations import t
from helpers import _fetch_youtube_transcript, _extract_youtube_id
from db import get_concept_graph, save_concept_graph

DEFAULT_VIDEO_URL = ""


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
        options=["llm", "nlp"],
        format_func=lambda x: t(f"research_approach_{x}"),
        key="research_approach",
    )

    default_token = st.secrets.get("HF_API_KEY", "")
    default_model = st.secrets.get("HF_MODEL", "Qwen/Qwen2.5-72B-Instruct")

    hf_token = None
    hf_model = default_model
    if approach == "llm":
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
        st.session_state.research_saved_token = hf_token or default_token
        st.session_state.research_saved_model = hf_model

    regenerate = False
    if cached:
        display_date = (cached.get("updated_at") or cached.get("created_at", ""))[:10]
        st.info(t("research_graph_cached", date=display_date))
        regenerate = st.button(t("research_regenerate"), type="secondary", key="research_regenerate_btn")

    # --- Generate button (always shown; also triggered by Regenerate) ---
    ready = bool(url.strip()) and (approach == "nlp" or bool(hf_token or default_token))
    do_generate = st.button(t("research_generate"), type="primary", disabled=not ready, key="research_generate_btn") or regenerate

    if do_generate:
        if approach == "llm" and not (hf_token or default_token):
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
                    st.text_area("", transcript, height=300, disabled=True, label_visibility="collapsed")

                with st.spinner(t("research_building_graph")):
                    if approach == "llm":
                        graph_data = _build_concept_graph_llm(transcript, hf_token or default_token, hf_model)
                    else:
                        graph_data = _build_concept_graph_nlp(transcript)

                if graph_data:
                    st.session_state.research_graph_data = graph_data
                    st.session_state.research_saved_token = hf_token or default_token
                    st.session_state.research_saved_model = hf_model
                    st.session_state.research_transcript_lang = _detect_transcript_lang(transcript)
                    save_concept_graph(vid, approach, graph_data)
                    st.success(t("research_graph_saved"))
                else:
                    st.session_state.pop("research_graph_data", None)
                    st.warning(t("research_no_concepts"))

    # Show graph results + question generation if graph is available
    if "research_graph_data" in st.session_state:
        graph_data = st.session_state.research_graph_data
        _display_concept_graph(graph_data)

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

        saved_token = st.session_state.get("research_saved_token") or default_token
        saved_model = st.session_state.get("research_saved_model") or default_model

        if st.button(t("research_generate_questions"), type="secondary", key="research_gen_q_btn"):
            if not saved_token:
                st.error(t("research_no_hf_token"))
            else:
                lang = st.session_state.get("research_transcript_lang", "en")
                questions = _generate_questions(graph_data, int(num_questions), saved_token, saved_model, lang)
                if questions:
                    st.session_state.research_questions = questions

        if "research_questions" in st.session_state:
            _display_questions(st.session_state.research_questions)


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


def _build_concept_graph_llm(transcript_text, hf_token, hf_model):
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
        from huggingface_hub import InferenceClient

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

        client = InferenceClient(token=hf_token)
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
        from langdetect import detect, LangDetectException
        sample = transcript[:800]
        lang = detect(sample)
        return "es" if lang.startswith("es") else lang
    except Exception:
        return "en"


def _mc_prompt(concept, related, difficulty, lang):
    """Build a chat prompt for a multiple-choice question in the given language."""
    relations_text = "\n".join(f"- {concept} {rel} {target}" for target, rel in related) or "None"

    if lang == "es":
        system = "Eres un generador de contenido educativo. Genera UNA pregunta de opción múltiple en JSON válido SOLAMENTE."
        user = f"""Concepto: "{concept}"
Relaciones conocidas:
{relations_text}
Dificultad: {difficulty}

Genera una pregunta de opción múltiple con exactamente 4 opciones en ESPAÑOL.
Reglas:
- Solo JSON válido, sin texto adicional.
- La pregunta y todas las opciones deben estar en ESPAÑOL.
- Una sola respuesta correcta.
- Usa índice 0-3 para answer_index.

Formato:
{{
  "question": "...",
  "options": ["opción A", "opción B", "opción C", "opción D"],
  "answer_index": 0,
  "explanation": "...",
  "concept": "{concept}",
  "difficulty": "{difficulty}"
}}"""
    else:
        system = "You are an educational content generator. Output ONE multiple-choice question as valid JSON ONLY."
        user = f"""Concept: "{concept}"
Known relations:
{relations_text}
Difficulty: {difficulty}

Generate a multiple-choice question with exactly 4 options in ENGLISH.
Rules:
- Output ONLY valid JSON, no extra text.
- The question and all options must be in ENGLISH.
- Exactly one correct answer.
- Use index 0-3 for answer_index.

Format:
{{
  "question": "...",
  "options": ["option A", "option B", "option C", "option D"],
  "answer_index": 0,
  "explanation": "...",
  "concept": "{concept}",
  "difficulty": "{difficulty}"
}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _generate_questions(graph_data, num_questions, hf_token, hf_model, lang="en"):
    """Generate multiple-choice questions from the concept graph using the LLM."""
    try:
        import re
        import json
        import random
        import networkx as nx
        from huggingface_hub import InferenceClient

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

        G = nx.DiGraph()
        for node in graph_data["nodes"]:
            G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})
        for edge in graph_data["edges"]:
            G.add_edge(edge["source"], edge["target"], relation=edge.get("relation", "related_to"))

        # Rank concepts by degree (most connected first)
        ranked = sorted(G.nodes(), key=lambda n: G.degree(n), reverse=True)

        client = InferenceClient(token=hf_token)
        questions = []
        used_pairs = set()
        concept_index = 0
        progress = st.progress(0, text=t("research_generating_questions"))

        while len(questions) < num_questions:
            if concept_index >= len(ranked) * 10:
                break

            concept = ranked[concept_index % len(ranked)]
            concept_index += 1
            difficulty = random.choice(["easy", "medium", "hard"])

            if (concept, difficulty) in used_pairs:
                continue

            related = (
                [(tgt, d.get("relation", "")) for _, tgt, d in G.out_edges(concept, data=True)] +
                [(src, d.get("relation", "")) for src, _, d in G.in_edges(concept, data=True)]
            )

            messages = _mc_prompt(concept, related, difficulty, lang)
            try:
                resp = client.chat.completions.create(
                    model=hf_model,
                    messages=messages,
                    temperature=0.6,
                    max_tokens=500,
                )
                data = _safe_json(resp.choices[0].message.content)
            except Exception as e:
                st.warning(f"LLM error for '{concept}': {e}")
                continue

            if not data or "question" not in data or "options" not in data:
                continue
            if not isinstance(data.get("options"), list) or len(data["options"]) != 4:
                continue
            if data["question"] in [q["question"] for q in questions]:
                continue

            questions.append(data)
            used_pairs.add((concept, difficulty))
            progress.progress(len(questions) / num_questions)

        progress.empty()
        return questions

    except ImportError as e:
        st.error(f"Missing dependency: {e}")
        return None
    except Exception as e:
        st.error(f"Error generating questions: {e}")
        return None


def _display_concept_graph(graph_data):
    """Display the concept graph results."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    st.subheader(t("research_results"))

    col1, col2 = st.columns(2)
    col1.metric(t("research_concepts"), len(nodes))
    col2.metric(t("research_relations"), len(edges))

    if nodes:
        st.subheader(t("research_top_concepts"))
        sorted_nodes = sorted(nodes, key=lambda n: n.get("frequency", 0), reverse=True)
        for node in sorted_nodes[:20]:
            freq = node.get("frequency", 0)
            bar = "█" * min(freq, 30)
            st.text(f"{freq:3d}  {bar}  {node['id']}")

    if edges:
        st.subheader(t("research_relations_found"))
        sorted_edges = sorted(edges, key=lambda e: e.get("weight", 0), reverse=True)
        for edge in sorted_edges[:30]:
            rel = edge.get("relation", "related_to")
            w = edge.get("weight", 1)
            ev = edge.get("evidence", "")
            line = f"  {edge['source']}  —[{rel}]→  {edge['target']}  (x{w})"
            if ev:
                line += f"\n    ↳ {ev}"
            st.text(line)

    import json
    st.download_button(
        t("research_download_json"),
        data=json.dumps(graph_data, ensure_ascii=False, indent=2),
        file_name="concept_graph.json",
        mime="application/json",
    )


def _display_questions(questions):
    """Display generated multiple-choice questions."""
    import json

    st.subheader(t("research_questions_title", n=len(questions)))
    letters = ["A", "B", "C", "D"]
    for i, q in enumerate(questions, 1):
        with st.expander(f"{i}. {q.get('question', '')}", expanded=False):
            options = q.get("options", [])
            answer_index = q.get("answer_index", -1)
            for j, opt in enumerate(options):
                prefix = "✅" if j == answer_index else f"{letters[j]}."
                st.write(f"{prefix} {opt}")
            explanation = q.get("explanation", "")
            if explanation:
                st.caption(f"💡 {explanation}")
            col1, col2 = st.columns(2)
            col1.caption(f"**{t('research_question_difficulty')}:** {q.get('difficulty', '')}")
            col2.caption(f"**{t('research_question_concept')}:** {q.get('concept', '')}")

    st.download_button(
        t("research_download_questions"),
        data=json.dumps(questions, ensure_ascii=False, indent=2),
        file_name="questions.json",
        mime="application/json",
    )
