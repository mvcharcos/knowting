#!/usr/bin/env python3
"""
Ground (update) a concept graph using evidence from a transcript.

Updates:
- Node-level grounding:
  - mention_count
  - grounded (bool)
  - evidence_snippets: list[str] (sentence windows)
  - sentence_hits: list[int] (sentence indices)
  - language: "en" or "es" (detected on transcript)

- Edge-level grounding:
  - evidence_snippets: list[str] where source+target co-occur within a window
  - cooccur_count

Optionally:
- prune ungrounded nodes and edges.

Input graph format expected (from earlier scripts):
{
  "nodes": [{"id": "<concept_label>", ...}, ...],
  "edges": [{"source": "<concept_label>", "target": "<concept_label>", "relation": "...", ...}, ...]
}

Output:
- Writes updated graph JSON with added grounding fields.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from typing import Dict, List, Tuple

import spacy
from langdetect import detect, LangDetectException
from rapidfuzz import fuzz


# ----------------------------
# Language & NLP
# ----------------------------

_NLP_CACHE = {}

def detect_lang(text: str) -> str:
    try:
        lang = detect(text[:800])
    except LangDetectException:
        return "en"
    return "es" if lang.startswith("es") else "en"

def get_nlp(lang: str):
    key = "es" if lang == "es" else "en"
    if key in _NLP_CACHE:
        return _NLP_CACHE[key]
    model = "es_core_news_sm" if key == "es" else "en_core_web_sm"
    nlp = spacy.load(model)
    if "sentencizer" not in nlp.pipe_names:
        try:
            nlp.add_pipe("sentencizer", first=True)
        except Exception:
            pass
    _NLP_CACHE[key] = nlp
    return nlp


# ----------------------------
# Normalization / cleaning
# ----------------------------

def clean_transcript(text: str) -> str:
    # timestamps
    text = re.sub(r"\[?\b\d{1,2}:\d{2}(?::\d{2})?\b\]?", " ", text)
    # speaker labels
    text = re.sub(r"^\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_\- ]{1,30}:\s+", "", text, flags=re.MULTILINE)
    # whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text

def normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip("`'\"")
    return s

def split_sentences(text: str, lang: str) -> List[str]:
    nlp = get_nlp(lang)
    doc = nlp(text)
    return [s.text.strip() for s in doc.sents if s.text.strip()]


# ----------------------------
# Evidence extraction
# ----------------------------

def window_snippet(sentences: List[str], i: int, window: int) -> str:
    left = max(0, i - window)
    right = min(len(sentences), i + window + 1)
    return " ".join(sentences[left:right]).strip()

def dedup_snippets(snips: List[str], sim_threshold: int = 92) -> List[str]:
    out = []
    for s in snips:
        if not any(fuzz.token_set_ratio(s, x) >= sim_threshold for x in out):
            out.append(s)
    return out

def find_concept_hits(
    concept: str,
    norm_sentences: List[str]
) -> List[int]:
    """
    Return indices of sentences containing the concept (substring match).
    """
    c = normalize(concept)
    if len(c) < 2:
        return []
    hits = [i for i, s in enumerate(norm_sentences) if c in s]
    return hits

def build_node_grounding(
    concepts: List[str],
    sentences: List[str],
    window: int,
    max_snips: int
) -> Dict[str, dict]:
    """
    For each concept, collect mention_count, sentence hits, and evidence snippets.
    """
    norm_sentences = [normalize(s) for s in sentences]
    node_info: Dict[str, dict] = {}

    for c in concepts:
        hit_idx = find_concept_hits(c, norm_sentences)
        mention_count = len(hit_idx)

        snips = []
        for i in hit_idx:
            snips.append(window_snippet(sentences, i, window))
            if len(snips) >= max_snips * 2:
                break

        snips = dedup_snippets(snips)[:max_snips]

        node_info[c] = {
            "grounded": mention_count > 0,
            "mention_count": mention_count,
            "sentence_hits": hit_idx[:200],  # cap to keep JSON manageable
            "evidence_snippets": snips
        }

    return node_info

def build_edge_grounding(
    edges: List[dict],
    sentences: List[str],
    window: int,
    max_snips: int
) -> List[dict]:
    """
    For each edge, attach evidence snippets where source and target co-occur
    in the same window snippet. This is a grounding heuristic, not a proof.
    """
    norm_sentences = [normalize(s) for s in sentences]

    # Precompute sentence indices per concept for speed
    concept_to_sentidx = defaultdict(list)
    for i, s in enumerate(norm_sentences):
        # This would be too expensive to scan all concepts here; instead do edge-driven checks below.
        pass

    updated_edges = []
    for e in edges:
        src = e.get("source")
        tgt = e.get("target")
        if not src or not tgt:
            updated_edges.append(e)
            continue

        src_n = normalize(src)
        tgt_n = normalize(tgt)

        snips = []
        cooccur = 0

        # scan sentence windows: if both appear within same window snippet, accept
        for i in range(len(sentences)):
            snip = window_snippet(sentences, i, window)
            snip_n = normalize(snip)
            if src_n in snip_n and tgt_n in snip_n:
                cooccur += 1
                snips.append(snip)
                if len(snips) >= max_snips * 2:
                    break

        snips = dedup_snippets(snips)[:max_snips]

        e2 = dict(e)
        e2["grounded"] = cooccur > 0
        e2["cooccur_count"] = cooccur
        e2["evidence_snippets"] = snips
        updated_edges.append(e2)

    return updated_edges


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True, help="Input concept_graph.json")
    ap.add_argument("--transcript", required=True, help="Transcript .txt")
    ap.add_argument("--out", default="concept_graph_grounded.json", help="Output grounded graph JSON")
    ap.add_argument("--window-sentences", type=int, default=1, help="+/- sentence window size")
    ap.add_argument("--max-snippets", type=int, default=6, help="Max snippets per node/edge")
    ap.add_argument("--prune-ungrounded", action="store_true", help="Remove nodes/edges not grounded in transcript")
    args = ap.parse_args()

    # Load graph
    with open(args.graph, "r", encoding="utf-8") as f:
        g = json.load(f)

    nodes = g.get("nodes", [])
    edges = g.get("edges", [])

    # Extract concept labels
    # Expect node["id"] to be the concept label (as in earlier scripts)
    concepts = []
    for n in nodes:
        cid = n.get("id") or n.get("label")
        if isinstance(cid, str) and cid.strip():
            concepts.append(cid.strip())

    if not concepts:
        raise RuntimeError("No concepts found in graph nodes. Expected nodes with 'id' field as label.")

    # Load transcript
    with open(args.transcript, "r", encoding="utf-8") as f:
        transcript_raw = f.read()

    transcript = clean_transcript(transcript_raw)
    if not transcript:
        raise RuntimeError("Transcript became empty after cleaning.")

    lang = detect_lang(transcript)
    sentences = split_sentences(transcript, lang)
    if not sentences:
        # fallback: treat as one big sentence
        sentences = [transcript]

    # Build grounding
    node_ground = build_node_grounding(
        concepts=concepts,
        sentences=sentences,
        window=args.window_sentences,
        max_snips=args.max_snippets
    )

    grounded_nodes = []
    for n in nodes:
        cid = n.get("id") or n.get("label")
        if not isinstance(cid, str):
            continue
        cid = cid.strip()
        info = node_ground.get(cid, {})
        n2 = dict(n)
        n2["language"] = lang
        n2.update(info)
        grounded_nodes.append(n2)

    grounded_edges = build_edge_grounding(
        edges=edges,
        sentences=sentences,
        window=args.window_sentences,
        max_snips=args.max_snippets
    )

    # Optionally prune
    if args.prune_ungrounded:
        grounded_set = {n.get("id") for n in grounded_nodes if n.get("grounded")}
        grounded_nodes = [n for n in grounded_nodes if n.get("grounded")]
        grounded_edges = [
            e for e in grounded_edges
            if e.get("source") in grounded_set and e.get("target") in grounded_set and e.get("grounded")
        ]

    # Update graph object
    g2 = dict(g)
    g2["nodes"] = grounded_nodes
    g2["edges"] = grounded_edges
    g2["meta"] = {
        "grounding": {
            "transcript_language": lang,
            "window_sentences": args.window_sentences,
            "max_snippets": args.max_snippets,
            "pruned_ungrounded": bool(args.prune_ungrounded),
            "num_sentences": len(sentences),
        }
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(g2, f, ensure_ascii=False, indent=2)

    print("Done.")
    print(f"Transcript language: {lang}")
    print(f"Sentences: {len(sentences)}")
    print(f"Nodes: {len(g2['nodes'])}")
    print(f"Edges: {len(g2['edges'])}")
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()