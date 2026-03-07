#!/usr/bin/env python3
"""
Generate grounded questions from a concept graph + transcript.

Key idea:
- Concepts alone are too abstract => questions drift out-of-scope.
- We ground question generation by providing transcript evidence snippets
  (sentences/windows containing the concept and its neighbors).

Inputs:
- concept_graph.json (from your graph builder)
- transcript.txt

Outputs:
- questions_grounded.json  (list of Q/A items with evidence)
- question_cache.json      (hashes to avoid repeats)

Usage:
  python grounded_questions_from_graph.py --graph concept_graph.json --transcript transcript.txt --num-questions 30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import spacy
from huggingface_hub import InferenceClient
from langdetect import detect, LangDetectException
from rapidfuzz import fuzz


# ----------------------------
# Settings
# ----------------------------

RELATION_TYPES = ["is_a", "part_of", "depends_on", "causes", "used_for", "related_to"]

QUESTION_TEMPLATES = [
    "definition", "explanation", "application", "comparison",
    "dependency_reasoning", "causal_reasoning", "component_reasoning"
]

RELATION_TO_QTYPES = {
    "depends_on": "dependency_reasoning",
    "causes": "causal_reasoning",
    "part_of": "component_reasoning",
    "is_a": "comparison",
    "used_for": "application",
    "related_to": "explanation",
}

# transcript cleanup
def clean_transcript(text: str) -> str:
    text = re.sub(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?", " ", text)  # timestamps
    text = re.sub(r"^\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_\- ]{1,30}:\s+", "", text, flags=re.MULTILINE)  # speaker labels
    text = re.sub(r"\s+", " ", text).strip()
    return text

def detect_lang(text: str) -> str:
    try:
        lang = detect(text[:800])
    except LangDetectException:
        return "en"
    return "es" if lang.startswith("es") else "en"

_NLP = {}
def get_nlp(lang: str):
    key = "es" if lang == "es" else "en"
    if key in _NLP:
        return _NLP[key]
    model = "es_core_news_sm" if key == "es" else "en_core_web_sm"
    nlp = spacy.load(model)
    if "sentencizer" not in nlp.pipe_names:
        try:
            nlp.add_pipe("sentencizer", first=True)
        except Exception:
            pass
    _NLP[key] = nlp
    return nlp

def normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip("`'\"")
    return s


# ----------------------------
# Graph loading
# ----------------------------

def load_graph(graph_json_path: str):
    with open(graph_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Your concept_graph.json format from earlier:
    # nodes: [{"id": "<label>", "frequency": ... , ...}]  (in that script id was label)
    # edges: [{"source": "<label>", "target": "<label>", "relation": ... , ...}]
    nodes = [n["id"] if "id" in n else n.get("label") for n in data.get("nodes", [])]
    edges = data.get("edges", [])

    # Normalize nodes as labels
    node_labels = []
    for n in nodes:
        if isinstance(n, str) and n.strip():
            node_labels.append(n.strip())

    # frequency map if exists
    freq = {}
    for n in data.get("nodes", []):
        if "id" in n:
            freq[n["id"]] = int(n.get("frequency", 1))

    # adjacency
    out_edges = defaultdict(list)
    in_edges = defaultdict(list)
    for e in edges:
        s = e.get("source")
        t = e.get("target")
        r = e.get("relation", "related_to")
        if not s or not t:
            continue
        out_edges[s].append((t, r))
        in_edges[t].append((s, r))

    return node_labels, out_edges, in_edges, freq


# ----------------------------
# Evidence indexing (concept -> transcript snippets)
# ----------------------------

@dataclass
class EvidenceConfig:
    window_sentences: int = 1          # include +/- N sentences around the hit
    max_snippets_per_concept: int = 6  # limit context
    min_snippet_len: int = 30
    max_context_chars: int = 1800      # for LLM prompt budget

def split_sentences(text: str, lang: str) -> List[str]:
    nlp = get_nlp(lang)
    doc = nlp(text)
    sents = [s.text.strip() for s in doc.sents if s.text.strip()]
    return sents

def build_evidence_index(
    transcript_text: str,
    concepts: List[str],
    cfg: EvidenceConfig
) -> Dict[str, List[str]]:
    """
    For each concept label, find occurrences in transcript and store sentence windows.
    Uses normalized substring matching. Works surprisingly well for transcripts.

    If you want more robustness later:
    - add fuzzy token match
    - or index by lemmatized keywords
    """
    lang = detect_lang(transcript_text)
    sents = split_sentences(transcript_text, lang)

    # Pre-normalize sentences once
    norm_sents = [normalize(s) for s in sents]

    # Sort concepts by length desc to avoid tiny matches dominating
    concepts_sorted = sorted(concepts, key=lambda c: len(c), reverse=True)

    idx = defaultdict(list)

    for ci, concept in enumerate(concepts_sorted):
        c_norm = normalize(concept)
        if len(c_norm) < 3:
            continue

        for i, s_norm in enumerate(norm_sents):
            if c_norm in s_norm:
                left = max(0, i - cfg.window_sentences)
                right = min(len(sents), i + cfg.window_sentences + 1)
                snippet = " ".join(sents[left:right]).strip()

                if len(snippet) < cfg.min_snippet_len:
                    continue

                # Dedup similar snippets
                already = idx[concept]
                if any(fuzz.token_set_ratio(snippet, ex) > 92 for ex in already):
                    continue

                idx[concept].append(snippet)
                if len(idx[concept]) >= cfg.max_snippets_per_concept:
                    break

    return idx


# ----------------------------
# LLM question generation (grounded)
# ----------------------------

def safe_json_load(s: str) -> Optional[dict]:
    s = s.strip()
    s = re.sub(r"^```(json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\})", s, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None

def make_prompt(
    concept: str,
    qtype: str,
    difficulty: str,
    context: str,
    lang: str,
    related_facts: List[str]
) -> List[Dict[str, str]]:
    """
    Ask for 1 question grounded in transcript context.
    Require:
    - question
    - answer
    - evidence (exact quote from context)
    - concept
    - type, difficulty
    """
    facts_block = "\n".join(f"- {f}" for f in related_facts) if related_facts else "- (none)"

    if lang == "es":
        system = (
            "Eres un generador de preguntas de estudio. "
            "Debes basarte ÚNICAMENTE en el CONTEXTO proporcionado. "
            "Devuelve SOLO JSON válido."
        )
        user = f"""
CONCEPTO: "{concept}"
TIPO_DE_PREGUNTA: {qtype}
DIFICULTAD: {difficulty}

HECHOS_RELACIONADOS (pueden inspirar la pregunta, pero NO inventes nada):
{facts_block}

CONTEXTO (usa SOLO esto; no uses conocimiento externo):
\"\"\"{context}\"\"\"

Reglas:
- Genera UNA sola pregunta alineada con el contexto.
- Incluye una respuesta correcta basada en el contexto.
- Incluye "evidence" como una cita corta (5-25 palabras) COPIADA literalmente del contexto.
- NO añadas información que no aparezca en el contexto.
- Devuelve SOLO JSON válido, sin texto extra.

Formato:
{{
  "question": "...",
  "answer": "...",
  "evidence": "...",
  "concept": "{concept}",
  "type": "{qtype}",
  "difficulty": "{difficulty}",
  "language": "es"
}}
"""
    else:
        system = (
            "You generate study questions. "
            "You MUST rely ONLY on the provided CONTEXT. "
            "Return ONLY valid JSON."
        )
        user = f"""
CONCEPT: "{concept}"
QUESTION_TYPE: {qtype}
DIFFICULTY: {difficulty}

RELATED FACTS (may inspire the question, but do NOT invent anything):
{facts_block}

CONTEXT (use ONLY this; no outside knowledge):
\"\"\"{context}\"\"\"

Rules:
- Generate ONE question aligned with the context.
- Include a correct answer based on the context.
- Include "evidence" as a short quote (5-25 words) COPIED verbatim from the context.
- Do NOT add information not present in the context.
- Output ONLY valid JSON, no extra text.

Format:
{{
  "question": "...",
  "answer": "...",
  "evidence": "...",
  "concept": "{concept}",
  "type": "{qtype}",
  "difficulty": "{difficulty}",
  "language": "en"
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def call_llm_one(
    client: InferenceClient,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 450,
    temperature: float = 0.4
) -> Optional[dict]:
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.choices[0].message.content
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return None

    data = safe_json_load(text)
    if data and isinstance(data, dict) and "question" in data:
        return data

    # One repair attempt
    repair = [
        {"role": "system", "content": "Return ONLY valid JSON. No commentary."},
        {"role": "user", "content": f"Fix into the required JSON schema:\n{text}"},
    ]
    try:
        resp2 = client.chat.completions.create(
            model=model, messages=repair, max_tokens=max_tokens, temperature=0.0
        )
        data2 = safe_json_load(resp2.choices[0].message.content)
        if data2 and isinstance(data2, dict) and "question" in data2:
            return data2
    except Exception:
        return None
    return None

def validate_grounding(item: dict, context: str) -> bool:
    """
    Ensure evidence is actually from context.
    """
    ev = item.get("evidence", "")
    if not isinstance(ev, str) or len(ev.split()) < 4:
        return False
    # Evidence must be substring (case-insensitive-ish)
    if normalize(ev) not in normalize(context):
        return False
    # Answer must exist
    ans = item.get("answer", "")
    if not isinstance(ans, str) or len(ans.strip()) < 2:
        return False
    # Question must exist
    q = item.get("question", "")
    if not isinstance(q, str) or len(q.strip()) < 5:
        return False
    return True


# ----------------------------
# Coverage-driven selection + dedup
# ----------------------------

def concept_score(concept: str, freq: Dict[str, int], out_edges, in_edges, used_count: Counter) -> float:
    degree = len(out_edges.get(concept, [])) + len(in_edges.get(concept, []))
    f = freq.get(concept, 1)
    used = used_count.get(concept, 0)
    # Prefer high-degree / high-frequency nodes but penalize already-used ones
    return (0.6 * degree + 0.4 * (f ** 0.5)) / (1 + used)

def pick_qtype(concept: str, out_edges, in_edges) -> str:
    neighbors = out_edges.get(concept, []) + in_edges.get(concept, [])
    rels = [r for _, r in neighbors if r]
    random.shuffle(rels)
    for r in rels:
        if r in RELATION_TO_QTYPES:
            return RELATION_TO_QTYPES[r]
    return random.choice(QUESTION_TEMPLATES)

def build_context_for_concept(
    concept: str,
    evidence_index: Dict[str, List[str]],
    out_edges,
    in_edges,
    max_chars: int
) -> Tuple[str, List[str]]:
    """
    Context = snippets for concept + (optionally) one snippet for neighbor concepts
    Facts = simple relation lines included as "related facts"
    """
    snippets = []
    facts = []

    # facts from graph
    for tgt, rel in out_edges.get(concept, [])[:3]:
        facts.append(f"{concept} {rel} {tgt}")
    for src, rel in in_edges.get(concept, [])[:3]:
        facts.append(f"{src} {rel} {concept}")

    # primary evidence
    for snip in evidence_index.get(concept, []):
        snippets.append(snip)

    # add some neighbor evidence to allow relation-aware questions
    neighbors = [t for t, _ in out_edges.get(concept, [])] + [s for s, _ in in_edges.get(concept, [])]
    random.shuffle(neighbors)
    for nb in neighbors[:2]:
        for snip in evidence_index.get(nb, [])[:1]:
            snippets.append(snip)

    # join, respect max chars
    ctx = ""
    for sn in snippets:
        if not ctx:
            ctx = sn
        elif len(ctx) + len(sn) + 3 <= max_chars:
            ctx += "\n\n" + sn
        else:
            break

    return ctx.strip(), facts


def question_fingerprint(item: dict) -> str:
    key = (normalize(item.get("concept", "")) + "|" +
           normalize(item.get("type", "")) + "|" +
           normalize(item.get("difficulty", "")) + "|" +
           normalize(item.get("question", "")))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True, help="concept_graph.json")
    ap.add_argument("--transcript", required=True, help="transcript .txt")
    ap.add_argument("--num-questions", type=int, required=True)
    ap.add_argument("--model", default="Qwen/Qwen2.5-14B-Instruct")
    ap.add_argument("--min-evidence", type=int, default=1, help="minimum snippets required for a concept to be eligible")
    ap.add_argument("--window-sentences", type=int, default=1)
    ap.add_argument("--max-snippets", type=int, default=6)
    ap.add_argument("--max-context-chars", type=int, default=1800)
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    random.seed(args.seed)

    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if not token:
        print("ERROR: set HF_TOKEN environment variable", file=sys.stderr)
        sys.exit(1)

    concepts, out_edges, in_edges, freq = load_graph(args.graph)

    with open(args.transcript, "r", encoding="utf-8") as f:
        transcript = clean_transcript(f.read())
    if not transcript:
        print("ERROR: transcript empty after cleaning", file=sys.stderr)
        sys.exit(1)

    ev_cfg = EvidenceConfig(
        window_sentences=args.window_sentences,
        max_snippets_per_concept=args.max_snippets,
        max_context_chars=args.max_context_chars,
    )
    evidence_index = build_evidence_index(transcript, concepts, ev_cfg)

    # filter concepts to those that actually appear (have evidence)
    eligible = [c for c in concepts if len(evidence_index.get(c, [])) >= args.min_evidence]
    if not eligible:
        print("ERROR: no eligible concepts found in transcript evidence. "
              "Try --window-sentences 2 or lower --min-evidence.", file=sys.stderr)
        sys.exit(1)

    # Load cache to avoid repeats across runs
    cache_path = "question_cache.json"
    seen_hashes = set()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                seen_hashes = set(json.load(f))
        except Exception:
            seen_hashes = set()

    client = InferenceClient(token=token)

    used_count = Counter()
    questions = []
    attempts = 0
    max_attempts = max(200, args.num_questions * 10)

    while len(questions) < args.num_questions and attempts < max_attempts:
        attempts += 1

        # pick a concept to maximize coverage
        eligible.sort(key=lambda c: concept_score(c, freq, out_edges, in_edges, used_count), reverse=True)
        concept = eligible[0]

        # build grounded context
        context, facts = build_context_for_concept(concept, evidence_index, out_edges, in_edges, args.max_context_chars)
        if not context:
            # concept has evidence but couldn't build context (rare)
            used_count[concept] += 1
            continue

        lang = detect_lang(context)
        qtype = pick_qtype(concept, out_edges, in_edges)
        difficulty = random.choice(["easy", "medium", "hard"])

        messages = make_prompt(concept, qtype, difficulty, context, lang, facts)
        item = call_llm_one(client, args.model, messages, temperature=args.temperature)
        if not item:
            used_count[concept] += 1
            continue

        # Force canonical fields
        item["concept"] = concept
        item["type"] = qtype
        item["difficulty"] = difficulty
        item["language"] = "es" if lang == "es" else "en"
        item["context_id"] = f"{concept}|{qtype}|{difficulty}"

        if not validate_grounding(item, context):
            used_count[concept] += 1
            continue

        # Dedup across session + cache
        fp = question_fingerprint(item)
        if fp in seen_hashes:
            used_count[concept] += 1
            continue

        # In-session dedup by question text similarity
        qtext = item.get("question", "")
        if any(fuzz.token_set_ratio(qtext, q.get("question", "")) > 92 for q in questions):
            used_count[concept] += 1
            continue

        # Attach the context used (optional; helps debugging and later review)
        item["source_context"] = context

        questions.append(item)
        seen_hashes.add(fp)
        used_count[concept] += 1

        print(f"[{len(questions)}/{args.num_questions}] {concept} | {qtype} | {difficulty} | lang={item['language']}")

    with open("questions_grounded.json", "w", encoding="utf-8") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_hashes)), f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Generated: {len(questions)} questions (attempts={attempts})")
    print("Wrote: questions_grounded.json and question_cache.json")


if __name__ == "__main__":
    main()