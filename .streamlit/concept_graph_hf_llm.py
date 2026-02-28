#!/usr/bin/env python3
"""
Concept graph extraction from bilingual (English/Spanish) transcripts using:
- Rule-based concept candidate extraction (spaCy EN/ES)
- LLM-based relation extraction via Hugging Face hosted Inference API (chat)

Outputs:
- concept_graph.json
- concept_graph.graphml

Usage:
  python concept_graph_hf_llm.py transcript.txt --model Qwen/Qwen2.5-14B-Instruct

Notes:
- Requires HF hosted inference access to the chosen model.
- If your HF tier doesn't support some model sizes, switch to a smaller one
  (e.g., Qwen/Qwen2.5-7B-Instruct).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import networkx as nx
import spacy
from huggingface_hub import InferenceClient
from langdetect import detect, LangDetectException
from rapidfuzz import fuzz


# ----------------------------
# Config
# ----------------------------

RELATION_TYPES = [
    "is_a",        # X is a type of Y
    "part_of",     # X is part of Y
    "depends_on",  # X requires/prerequisite Y
    "causes",      # X causes Y
    "used_for",    # X is used for Y
    "related_to",  # fallback weak relation
]

# Common transcript junk / filler (EN+ES)
JUNK_TERMS = {
    "part", "thing", "stuff", "someone", "somebody", "people", "person", "example",
    "video", "today", "time", "way", "kind", "lot", "ok", "okay", "right",
    "parte", "cosa", "cosas", "alguien", "gente", "persona", "ejemplo",
    "vídeo", "video", "hoy", "tiempo", "manera", "forma", "tipo", "vale", "bueno",
}

# Phrases frequently meaningless in transcripts
FILLER_PHRASES = {
    "this part", "that part", "the part", "this thing", "that thing", "the thing",
    "kind of", "sort of", "a lot",
    "esta parte", "esa parte", "la parte", "esta cosa", "esa cosa", "la cosa",
    "más o menos", "un poco", "mucho",
}

def normalize_term(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^[^\wáéíóúüñ]+|[^\wáéíóúüñ]+$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(a|an|the)\s+", "", t)
    t = re.sub(r"^(un|una|unos|unas|el|la|los|las)\s+", "", t)
    # remove leftover quotes/backticks
    t = t.strip("`'\"")
    return t

def looks_like_junk(text: str) -> bool:
    t = normalize_term(text)
    if not t or len(t) < 3:
        return True
    if t in JUNK_TERMS:
        return True
    if t in FILLER_PHRASES:
        return True
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return True
    # single short token often junk unless proper noun (handled later)
    if len(t.split()) == 1 and len(t) <= 3:
        return True
    return False


# ----------------------------
# spaCy loading (EN/ES)
# ----------------------------

_NLP_CACHE: Dict[str, spacy.language.Language] = {}

def get_nlp(lang: str) -> spacy.language.Language:
    """
    lang: 'en' or 'es' (fallback en)
    """
    key = "en" if lang == "en" else "es" if lang == "es" else "en"
    if key in _NLP_CACHE:
        return _NLP_CACHE[key]

    model = "en_core_web_sm" if key == "en" else "es_core_news_sm"
    try:
        nlp = spacy.load(model)
    except OSError as e:
        raise RuntimeError(
            f"spaCy model '{model}' not found. Install with:\n"
            f"  python -m spacy download {model}"
        ) from e

    # Ensure sentence boundaries
    if "sentencizer" not in nlp.pipe_names:
        try:
            nlp.add_pipe("sentencizer", first=True)
        except Exception:
            pass

    _NLP_CACHE[key] = nlp
    return nlp


# ----------------------------
# Transcript preprocessing + chunking
# ----------------------------

def clean_transcript(text: str) -> str:
    # remove timestamps like 00:01:23 or [00:01:23]
    text = re.sub(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?", " ", text)
    # remove speaker labels like "Speaker 1:" / "John:"
    text = re.sub(r"^\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_\- ]{1,30}:\s+", "", text, flags=re.MULTILINE)
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text

def detect_lang(text: str) -> str:
    """
    Returns 'en' or 'es' (fallback 'en').
    """
    sample = text[:800]
    try:
        lang = detect(sample)
    except LangDetectException:
        return "en"
    if lang.startswith("es"):
        return "es"
    return "en"

def chunk_text(text: str, max_chars: int = 1800) -> List[str]:
    """
    Chunk by sentence-ish separators while keeping <= max_chars.
    Works reasonably for transcripts.
    """
    # Split on sentence boundaries (simple)
    parts = re.split(r"(?<=[\.\!\?\。\¿\¡])\s+", text)
    chunks = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) + 1 <= max_chars:
            buf = (buf + " " + p).strip()
        else:
            if buf:
                chunks.append(buf)
            buf = p.strip()
    if buf:
        chunks.append(buf)
    return chunks


# ----------------------------
# Concept extraction (rule-based, strict)
# ----------------------------

def is_good_np(span) -> bool:
    # reject obvious junk
    t = normalize_term(span.text)
    if looks_like_junk(t):
        return False

    # head must be noun/proper noun
    if span.root.pos_ not in ("NOUN", "PROPN"):
        return False

    toks = [tok for tok in span if not tok.is_space and not tok.is_punct]
    if not (1 <= len(toks) <= 5):
        return False

    # stopword ratio
    stop_ratio = sum(tok.is_stop for tok in toks) / len(toks)
    if stop_ratio > 0.35:
        return False

    # POS ratio mostly NOUN/PROPN/ADJ
    good_pos = {"NOUN", "PROPN", "ADJ"}
    good_ratio = sum(tok.pos_ in good_pos for tok in toks) / len(toks)
    if good_ratio < 0.6:
        return False

    # avoid pronoun-led spans
    if toks[0].pos_ == "PRON":
        return False

    # avoid generic demonstratives like "this" "that" at start
    first = toks[0].lemma_.lower()
    if first in {"this", "that", "these", "those", "esto", "esa", "ese", "esta", "este", "estos", "estas"}:
        return False

    return True

def extract_concepts_from_chunk(nlp, chunk: str) -> List[str]:
    doc = nlp(chunk)
    terms: List[str] = []

    # noun chunks
    if doc.has_annotation("DEP"):
        for chunk_span in doc.noun_chunks:
            if is_good_np(chunk_span):
                terms.append(normalize_term(chunk_span.text))

    # named entities (often key)
    for ent in doc.ents:
        t = normalize_term(ent.text)
        if not looks_like_junk(t) and 1 <= len(t.split()) <= 6:
            terms.append(t)

    # additional: standalone PROPN/NOUN tokens with capital / technical look
    for tok in doc:
        if tok.pos_ in ("PROPN", "NOUN") and not tok.is_stop and not tok.is_punct:
            t = normalize_term(tok.text)
            if not looks_like_junk(t) and len(t) >= 4:
                terms.append(t)

    return terms

def fuzzy_merge_terms(terms: List[str], threshold: int = 92) -> Dict[str, str]:
    """
    Maps each term -> canonical term using token_sort fuzzy similarity.
    """
    uniq = sorted(set(terms), key=len, reverse=True)
    canon: List[str] = []
    mapping: Dict[str, str] = {}
    for t in uniq:
        best = None
        best_score = 0
        for c in canon:
            s = fuzz.token_sort_ratio(t, c)
            if s > best_score:
                best_score = s
                best = c
        if best and best_score >= threshold:
            mapping[t] = best
        else:
            canon.append(t)
            mapping[t] = t
    return mapping

def choose_top_concepts(all_terms: List[str], min_freq: int = 2, max_concepts: int = 80,
                        fuzzy_threshold: int = 92) -> Tuple[List[str], Dict[str, str], Counter]:
    counts = Counter([normalize_term(t) for t in all_terms if not looks_like_junk(t)])
    # Keep reasonably frequent terms
    kept = [(t, f) for t, f in counts.items() if f >= min_freq and 1 <= len(t.split()) <= 5]
    kept.sort(key=lambda x: x[1], reverse=True)
    kept_terms = [t for t, _ in kept][: max_concepts * 2]  # take more pre-merge

    mapping = fuzzy_merge_terms(kept_terms, threshold=fuzzy_threshold)

    canon_counts = Counter()
    for t, f in kept:
        if t in mapping:
            canon_counts[mapping[t]] += f

    top = [t for t, _ in canon_counts.most_common(max_concepts)]
    return top, mapping, canon_counts


# ----------------------------
# LLM relation extraction (HF hosted)
# ----------------------------

@dataclass
class LLMConfig:
    model: str
    temperature: float = 0.0
    max_new_tokens: int = 700
    timeout: int = 60

def build_relation_prompt(chunk: str, concepts: List[str], lang: str) -> List[Dict[str, str]]:
    """
    Chat messages for instruction-tuned models.
    We keep it bilingual-friendly by giving instructions in the chunk language.
    """
    # Keep concepts list compact to reduce tokens
    concept_items = [{"id": f"C{i+1:03d}", "label": c} for i, c in enumerate(concepts)]
    concept_json = json.dumps(concept_items, ensure_ascii=False, indent=2)

    if lang == "es":
        system = (
            "Eres un extractor de información. Tu tarea es extraer relaciones entre conceptos "
            "de un fragmento de transcripción. Debes ser estricto y devolver SOLO JSON válido."
        )
        user = f"""
Fragmento:
\"\"\"{chunk}\"\"\"

Lista de conceptos (usa SOLO estos IDs; no inventes nuevos conceptos):
{concept_json}

Extrae relaciones dirigidas entre conceptos presentes en el fragmento.
Usa SOLO estos tipos de relación:
{RELATION_TYPES}

Reglas:
- Devuelve SOLO JSON válido, sin texto extra.
- Si no hay relaciones claras, devuelve {{ "edges": [] }}.
- Cada edge debe tener: source, relation, target, evidence.
- evidence debe ser una cita corta (5-25 palabras) copiada del fragmento.

Formato:
{{
  "edges": [
    {{"source":"C001","relation":"depends_on","target":"C004","evidence":"..."}}
  ]
}}
"""
    else:
        system = (
            "You are an information extraction system. Extract relationships between concepts "
            "from a transcript chunk. Be strict and output ONLY valid JSON."
        )
        user = f"""
Chunk:
\"\"\"{chunk}\"\"\"

Concept list (use ONLY these IDs; do not invent new concepts):
{concept_json}

Extract directed relations between concepts that are supported by the chunk.
Use ONLY these relation types:
{RELATION_TYPES}

Rules:
- Output ONLY valid JSON, no extra text.
- If no clear relations, return {{ "edges": [] }}.
- Each edge must have: source, relation, target, evidence.
- evidence must be a short quote (5-25 words) copied from the chunk.

Format:
{{
  "edges": [
    {{"source":"C001","relation":"depends_on","target":"C004","evidence":"..."}}
  ]
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]

def safe_json_load(s: str) -> Optional[dict]:
    """
    Try to parse JSON even if model returns code fences or leading text.
    """
    s = s.strip()
    # strip code fences
    s = re.sub(r"^```(json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # try direct
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # try to extract first {...} block
    m = re.search(r"(\{.*\})", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None

def llm_extract_edges(client: InferenceClient, cfg: LLMConfig, chunk: str,
                      concepts: List[str], lang: str) -> List[dict]:
    """
    Calls HF Inference chat completion. Returns list of edges.
    """
    messages = build_relation_prompt(chunk, concepts, lang)

    # Hugging Face InferenceClient supports chat for many instruction models.
    # If your tier/model doesn't support chat.completions, you may need to switch to text_generation.
    try:
        resp = client.chat.completions.create(
            model=cfg.model,
            messages=messages,
            temperature=cfg.temperature,
            max_tokens=cfg.max_new_tokens,
        )
        text = resp.choices[0].message.content
    except Exception as e:
        raise RuntimeError(
            f"HF chat completion failed for model '{cfg.model}'. "
            f"Try a smaller model or one known to support chat on HF Inference.\nError: {e}"
        )

    data = safe_json_load(text)
    if not data or "edges" not in data or not isinstance(data["edges"], list):
        # one repair attempt
        repair_messages = [
            {"role": "system", "content": "Return ONLY valid JSON. No commentary."},
            {"role": "user", "content": f"Fix this to valid JSON with schema {{'edges':[...]}}:\n{text}"},
        ]
        try:
            resp2 = client.chat.completions.create(
                model=cfg.model,
                messages=repair_messages,
                temperature=0.0,
                max_tokens=cfg.max_new_tokens,
            )
            text2 = resp2.choices[0].message.content
            data2 = safe_json_load(text2)
            if data2 and "edges" in data2 and isinstance(data2["edges"], list):
                data = data2
            else:
                return []
        except Exception:
            return []

    # Validate edges
    valid = []
    concept_ids = {f"C{i+1:03d}" for i in range(len(concepts))}
    for e in data["edges"]:
        if not isinstance(e, dict):
            continue
        s = e.get("source")
        r = e.get("relation")
        t = e.get("target")
        ev = e.get("evidence", "")
        if s not in concept_ids or t not in concept_ids:
            continue
        if r not in RELATION_TYPES:
            continue
        if s == t:
            continue
        # evidence sanity (optional)
        if not isinstance(ev, str) or len(ev.split()) < 4:
            continue
        valid.append({"source": s, "relation": r, "target": t, "evidence": ev.strip()})
    return valid


# ----------------------------
# Graph building
# ----------------------------

def build_graph(canon_concepts: List[str], canon_counts: Counter,
                all_edges: List[dict], concept_id_to_label: Dict[str, str]) -> nx.DiGraph:
    G = nx.DiGraph()

    for cid, label in concept_id_to_label.items():
        G.add_node(label, id=cid, frequency=int(canon_counts.get(label, 1)))

    # Accumulate edge weights
    edge_counter = Counter()
    evidence_map = defaultdict(list)

    for e in all_edges:
        src = concept_id_to_label.get(e["source"])
        tgt = concept_id_to_label.get(e["target"])
        rel = e["relation"]
        if not src or not tgt:
            continue
        edge_counter[(src, rel, tgt)] += 1
        if len(evidence_map[(src, rel, tgt)]) < 3:
            evidence_map[(src, rel, tgt)].append(e.get("evidence", ""))

    for (src, rel, tgt), w in edge_counter.items():
        G.add_edge(src, tgt, relation=rel, weight=int(w), evidence=" | ".join(evidence_map[(src, rel, tgt)]))

    return G

def graph_to_json(G: nx.DiGraph) -> dict:
    nodes = [{"id": n, **G.nodes[n]} for n in G.nodes]
    edges = [{"source": u, "target": v, **G.edges[u, v]} for u, v in G.edges]
    return {"nodes": nodes, "edges": edges}


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript_path", help="Path to transcript .txt")
    ap.add_argument("--model", default="Qwen/Qwen2.5-14B-Instruct",
                    help="HF model repo id (must be available on hosted inference tier)")
    ap.add_argument("--min-freq", type=int, default=2, help="Min frequency for concept candidates")
    ap.add_argument("--max-concepts", type=int, default=60, help="Max canonical concepts in graph")
    ap.add_argument("--fuzzy-threshold", type=int, default=92, help="Fuzzy merge threshold (0-100)")
    ap.add_argument("--chunk-chars", type=int, default=1800, help="Chunk size for LLM calls")
    ap.add_argument("--concepts-per-chunk", type=int, default=18, help="Concept IDs passed per chunk to LLM")
    ap.add_argument("--max-chunks", type=int, default=0, help="Limit chunks (0=all) for quick tests")
    args = ap.parse_args()

    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if not token:
        print("ERROR: set HF_TOKEN environment variable.", file=sys.stderr)
        sys.exit(1)

    with open(args.transcript_path, "r", encoding="utf-8") as f:
        raw = f.read()

    text = clean_transcript(raw)
    if not text:
        print("ERROR: Empty transcript after cleaning.", file=sys.stderr)
        sys.exit(1)

    chunks = chunk_text(text, max_chars=args.chunk_chars)
    if args.max_chunks and args.max_chunks > 0:
        chunks = chunks[: args.max_chunks]

    # 1) Extract concept candidates from all chunks (rule-based)
    all_terms: List[str] = []
    for ch in chunks:
        lang = detect_lang(ch)
        nlp = get_nlp(lang)
        all_terms.extend(extract_concepts_from_chunk(nlp, ch))

    top_concepts, mapping, canon_counts = choose_top_concepts(
        all_terms,
        min_freq=args.min_freq,
        max_concepts=args.max_concepts,
        fuzzy_threshold=args.fuzzy_threshold,
    )

    if not top_concepts:
        print("No concepts extracted. Try lowering --min-freq or --fuzzy-threshold.", file=sys.stderr)
        sys.exit(1)

    # Canonical list and ID mapping (global)
    canon_concepts = top_concepts
    concept_id_to_label = {f"C{i+1:03d}": c for i, c in enumerate(canon_concepts)}
    label_to_concept_id = {v: k for k, v in concept_id_to_label.items()}

    # 2) LLM relations per chunk (only concepts present in chunk, up to concepts-per-chunk)
    client = InferenceClient(token=token)
    llm_cfg = LLMConfig(model=args.model)

    all_edges: List[dict] = []

    for idx, ch in enumerate(chunks, start=1):
        lang = detect_lang(ch)
        nlp = get_nlp(lang)

        # Concepts present in this chunk (by substring match on canonical labels)
        ch_norm = normalize_term(ch)
        present = [c for c in canon_concepts if c in ch_norm]

        # If too few, also consider chunk-level candidates snapped through mapping
        if len(present) < 2:
            local_terms = [normalize_term(t) for t in extract_concepts_from_chunk(nlp, ch)]
            local_terms = [mapping.get(t, t) for t in local_terms]
            present = list({t for t in local_terms if t in label_to_concept_id})

        if len(present) < 2:
            continue

        # Rank by global frequency; keep top N for token budget
        present.sort(key=lambda c: canon_counts.get(c, 0), reverse=True)
        present = present[: args.concepts_per_chunk]

        # Call LLM to extract edges among present concepts
        try:
            edges = llm_extract_edges(client, llm_cfg, ch, present, lang)
        except RuntimeError as e:
            print(f"[chunk {idx}/{len(chunks)}] LLM error: {e}", file=sys.stderr)
            edges = []

        # Translate local chunk concept IDs to global concept IDs
        # In the prompt, IDs are assigned in the order of `present`.
        local_id_to_global_id = {f"C{i+1:03d}": label_to_concept_id[present[i]] for i in range(len(present))}

        for e in edges:
            all_edges.append({
                "source": local_id_to_global_id.get(e["source"]),
                "target": local_id_to_global_id.get(e["target"]),
                "relation": e["relation"],
                "evidence": e["evidence"],
                "chunk_index": idx,
            })

        print(f"[chunk {idx}/{len(chunks)}] concepts={len(present)} edges={len(edges)}")

    # Filter any None IDs (paranoia)
    all_edges = [e for e in all_edges if e["source"] and e["target"]]

    # 3) Build graph
    G = build_graph(canon_concepts, canon_counts, all_edges, concept_id_to_label)

    # 4) Save outputs
    out_json = graph_to_json(G)
    with open("concept_graph.json", "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)

    nx.write_graphml(G, "concept_graph.graphml")

    # Also save raw extracted edges for debugging
    with open("edges_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_edges, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Concepts (nodes): {G.number_of_nodes()}")
    print(f"Relations (edges): {G.number_of_edges()}")
    print("Wrote: concept_graph.json, concept_graph.graphml, edges_raw.json")


if __name__ == "__main__":
    main()
