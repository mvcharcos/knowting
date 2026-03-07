#!/usr/bin/env python3
"""
Concept graph extractor v2 (EN/ES) for transcripts (PDF/TXT).

Key improvements vs naive noun-chunking:
- Chunking by timestamps / subtitle blocks (not sentence splitting)
- Definition cue extraction (se llama / se denomina / known as / called)
- Multi-word term preservation + aggressive junk filtering
- Alias extraction (X o Y / X también llamado Y / X (Y))
- Rule-based relation extraction for high-signal anatomy/lecture patterns
- Optional HF-hosted LLM refinement pass (label cleanup / dedup)

Outputs:
- concept_graph.json
- concept_mentions.json (debug/provenance)

Usage:
  python concept_graph_extractor_v2.py --input transcript.pdf --out concept_graph.json
  python concept_graph_extractor_v2.py --input transcript.txt --out concept_graph.json

Optional LLM refine:
  python concept_graph_extractor_v2.py --input transcript.pdf --out concept_graph.json \
    --llm-model Qwen/Qwen2.5-14B-Instruct --llm-refine
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Iterable

import networkx as nx
import pdfplumber
import spacy
from langdetect import detect, LangDetectException
from rapidfuzz import fuzz

try:
    from huggingface_hub import InferenceClient
except Exception:
    InferenceClient = None


# -----------------------------
# Config
# -----------------------------

REL_TYPES = {
    "is_a",
    "part_of",
    "has_part",
    "articulates_with",
    "divides_into",
    "has_movement",
    "related_to",
    "aka",  # alias edge (optional)
}

# Strong definition/labeling cues (ES/EN)
CUE_PATTERNS = [
    # Spanish
    (re.compile(r"\bse\s+llama\s+(?:la|el|los|las)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})", re.IGNORECASE), "define"),
    (re.compile(r"\bse\s+denomina\s+(?:la|el|los|las)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})", re.IGNORECASE), "define"),
    (re.compile(r"\bconocido\s+como\s+(?:la|el|los|las)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})", re.IGNORECASE), "define"),
    # English
    (re.compile(r"\bis\s+called\s+([A-Za-z0-9\- ]{3,80})", re.IGNORECASE), "define"),
    (re.compile(r"\bknown\s+as\s+([A-Za-z0-9\- ]{3,80})", re.IGNORECASE), "define"),
    (re.compile(r"\bwe\s+call\s+this\s+([A-Za-z0-9\- ]{3,80})", re.IGNORECASE), "define"),
]

# Alias cues: "X o Y", "X (Y)", "X también llamado Y"
ALIAS_PATTERNS = [
    re.compile(r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s*\(\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s*\)"),
    re.compile(r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s+(?:o|u)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\b", re.IGNORECASE),
    re.compile(r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s+(?:también\s+llamado|también\s+conocido\s+como)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})", re.IGNORECASE),
    re.compile(r"\b([A-Za-z0-9\- ]{3,80})\s+(?:also\s+called|also\s+known\s+as)\s+([A-Za-z0-9\- ]{3,80})", re.IGNORECASE),
]

# Relation patterns (rule-based) - high-signal
REL_PATTERNS = [
    # "X divide Y en A y B"
    ("divides_into", re.compile(
        r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s+divide\s+(?:a|al)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s+en\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s+y\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})",
        re.IGNORECASE
    )),
    # "X se articula con Y" / "X articulates with Y"
    ("articulates_with", re.compile(
        r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s+(?:se\s+articula\s+con|articula\s+con)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})",
        re.IGNORECASE
    )),
    ("articulates_with", re.compile(
        r"\b([A-Za-z0-9\- ]{3,80})\s+articulates\s+with\s+([A-Za-z0-9\- ]{3,80})",
        re.IGNORECASE
    )),
    # "X es parte de Y"
    ("part_of", re.compile(
        r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})\s+(?:es|forma\s+parte)\s+de\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9\- ]{3,80})",
        re.IGNORECASE
    )),
    # "movimientos ... son A, B, C"
    ("has_movement", re.compile(
        r"\bmovimientos?\b.*?\bson\b\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9,\- ]{6,200})",
        re.IGNORECASE
    )),
]

# Junk / generic words to avoid as standalone "concepts"
JUNK_SINGLE = {
    "parte", "zona", "superficie", "imagen", "hueso", "huesos", "articulación", "articulaciones",
    "movimiento", "movimientos", "cosa", "cosas", "tema", "punto", "lado", "forma",
    "part", "area", "surface", "bone", "bones", "joint", "joints", "movement", "movements", "thing",
    "a", "an", "the", "to", "of", "and", "or", "in", "on", "for", "with",
}

@dataclass
class ExtractConfig:
    max_concepts: int = 120
    min_freq: int = 2
    fuzzy_merge_threshold: int = 93
    chunk_max_chars: int = 1400
    keep_top_snippets_per_concept: int = 5


# -----------------------------
# IO: load transcript
# -----------------------------

def load_text(input_path: str) -> str:
    if input_path.lower().endswith(".pdf"):
        text_parts = []
        with pdfplumber.open(input_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text_parts.append(t)
        return "\n".join(text_parts)
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            return f.read()

def clean_transcript(text: str) -> str:
    # keep newlines; normalize spaces
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

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


# -----------------------------
# Chunking: timestamp blocks / subtitle-like blocks
# -----------------------------

TS_RE = re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b")

def chunk_by_timestamps(text: str, max_chars: int) -> List[str]:
    """
    Split into blocks around timestamps, then pack into <= max_chars chunks.
    Works with many transcript PDFs where each segment starts with 00:00:00.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blocks = []
    cur = []
    for ln in lines:
        if TS_RE.search(ln) and cur:
            blocks.append(" ".join(cur).strip())
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blocks.append(" ".join(cur).strip())

    # Pack blocks into size-limited chunks
    chunks = []
    buf = ""
    for b in blocks:
        if not buf:
            buf = b
        elif len(buf) + len(b) + 2 <= max_chars:
            buf += " " + b
        else:
            chunks.append(buf)
            buf = b
    if buf:
        chunks.append(buf)

    # fallback if no timestamps detected: split by paragraphs
    if len(chunks) == 1 and not TS_RE.search(chunks[0]):
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks = []
        buf = ""
        for p in paras:
            if not buf:
                buf = p
            elif len(buf) + len(p) + 2 <= max_chars:
                buf += " " + p
            else:
                chunks.append(buf)
                buf = p
        if buf:
            chunks.append(buf)

    return chunks


# -----------------------------
# Concept extraction
# -----------------------------

def norm_term(t: str) -> str:
    t = t.strip().strip("`'\"")
    t = re.sub(r"\s+", " ", t)
    t = t.lower()
    # drop leading articles ES/EN
    t = re.sub(r"^(el|la|los|las|un|una|unos|unas)\s+", "", t)
    t = re.sub(r"^(a|an|the)\s+", "", t)
    # strip punctuation at ends
    t = re.sub(r"^[^\wáéíóúüñ]+|[^\wáéíóúüñ]+$", "", t, flags=re.IGNORECASE)
    return t

def is_good_concept_label(t: str) -> bool:
    t = norm_term(t)
    if not t or len(t) < 3:
        return False
    if len(t.split()) > 6:
        return False
    if t in JUNK_SINGLE:
        return False
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return False
    # reject mostly stopword phrases
    toks = t.split()
    stopish = {"de","del","la","el","los","las","y","o","a","en","por","para","con","un","una",
               "of","the","and","or","to","in","on","for","with","a","an"}
    if len(toks) >= 2:
        ratio = sum(x in stopish for x in toks) / len(toks)
        if ratio > 0.45:
            return False
    return True

def extract_cue_terms(chunk: str) -> List[str]:
    out = []
    for pat, _kind in CUE_PATTERNS:
        for m in pat.finditer(chunk):
            term = m.group(1).strip()
            term = re.sub(r"[\,\;\:\.]+$", "", term)
            if is_good_concept_label(term):
                out.append(norm_term(term))
    return out

def extract_alias_pairs(chunk: str) -> List[Tuple[str,str]]:
    pairs = []
    for pat in ALIAS_PATTERNS:
        for m in pat.finditer(chunk):
            a = m.group(1).strip()
            b = m.group(2).strip()
            if is_good_concept_label(a) and is_good_concept_label(b):
                pairs.append((norm_term(a), norm_term(b)))
    return pairs

def extract_spacy_terms(nlp, chunk: str) -> List[str]:
    doc = nlp(chunk)
    terms = []
    # noun chunks (if DEP exists)
    if doc.has_annotation("DEP"):
        for ch in doc.noun_chunks:
            t = norm_term(ch.text)
            if is_good_concept_label(t):
                terms.append(t)
    # entities
    for ent in doc.ents:
        t = norm_term(ent.text)
        if is_good_concept_label(t):
            terms.append(t)
    return terms

def fuzzy_merge(terms: List[str], threshold: int) -> Dict[str,str]:
    uniq = sorted(set(terms), key=len, reverse=True)
    canon = []
    mp = {}
    for t in uniq:
        best = None
        best_score = 0
        for c in canon:
            s = fuzz.token_sort_ratio(t, c)
            if s > best_score:
                best_score = s
                best = c
        if best and best_score >= threshold:
            mp[t] = best
        else:
            canon.append(t)
            mp[t] = t
    return mp


# -----------------------------
# Relation extraction (rules)
# -----------------------------

def parse_list_items(text: str) -> List[str]:
    """
    Parse comma-separated items: "A, B y C" -> ["A","B","C"]
    """
    t = re.sub(r"\s+y\s+", ",", text, flags=re.IGNORECASE)
    parts = [p.strip() for p in t.split(",") if p.strip()]
    out = []
    for p in parts:
        p = re.sub(r"[\.]+$", "", p)
        if is_good_concept_label(p):
            out.append(norm_term(p))
    return out[:10]

def extract_relations(chunk: str) -> List[Tuple[str,str,str,str]]:
    """
    Return list of (src, rel, tgt, evidence_snippet)
    Evidence is kept short for later grounding.
    """
    rels = []
    short_ev = re.sub(r"\s+", " ", chunk).strip()
    if len(short_ev) > 260:
        short_ev = short_ev[:260] + "..."

    for rel_name, pat in REL_PATTERNS:
        for m in pat.finditer(chunk):
            if rel_name == "divides_into":
                x = norm_term(m.group(1))
                y = norm_term(m.group(2))
                a = norm_term(m.group(3))
                b = norm_term(m.group(4))
                if all(is_good_concept_label(z) for z in [x,y,a,b]):
                    # x divides y into a and b
                    rels.append((x, "divides_into", y, short_ev))
                    rels.append((y, "has_part", a, short_ev))
                    rels.append((y, "has_part", b, short_ev))

            elif rel_name == "articulates_with":
                x = norm_term(m.group(1))
                y = norm_term(m.group(2))
                if is_good_concept_label(x) and is_good_concept_label(y):
                    rels.append((x, "articulates_with", y, short_ev))
                    rels.append((y, "articulates_with", x, short_ev))

            elif rel_name == "part_of":
                x = norm_term(m.group(1))
                y = norm_term(m.group(2))
                if is_good_concept_label(x) and is_good_concept_label(y):
                    rels.append((x, "part_of", y, short_ev))

            elif rel_name == "has_movement":
                items = parse_list_items(m.group(1))
                # We don't always know the subject; leave as generic "scapula"/"escápula" if present
                # Try to infer subject from chunk keywords
                subj = "escápula" if "escápula" in norm_term(chunk) else "scapula" if "scapula" in norm_term(chunk) else None
                if subj:
                    for it in items:
                        rels.append((subj, "has_movement", it, short_ev))

    return rels


# -----------------------------
# Optional LLM refinement (labels)
# -----------------------------

def llm_refine_labels(client: InferenceClient, model: str, labels: List[str], lang: str) -> List[str]:
    """
    Ask the LLM to normalize/clean labels (no new concepts), dedupe synonyms lightly.
    Output JSON: {"labels":[...]}.
    """
    payload = json.dumps(labels[:200], ensure_ascii=False, indent=2)
    if lang == "es":
        system = "Eres un normalizador de términos. Devuelve SOLO JSON válido."
        user = f"""
Normaliza esta lista de conceptos (términos) para un grafo de aprendizaje.
Reglas:
- NO inventes conceptos nuevos.
- Corrige errores tipográficos leves.
- Mantén términos multi-palabra.
- Unifica duplicados obvios (p.ej. con guiones/acentos).
Devuelve SOLO JSON.

Entrada:
{payload}

Formato:
{{"labels":[ "...", "..." ]}}
"""
    else:
        system = "You normalize terminology. Return ONLY valid JSON."
        user = f"""
Normalize these concept labels for a learning concept graph.
Rules:
- Do NOT add new concepts.
- Fix minor typos.
- Keep multi-word terms.
- Merge obvious duplicates (hyphen/spacing).
Return ONLY JSON.

Input:
{payload}

Format:
{{"labels":[ "...", "..." ]}}
"""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.0,
        max_tokens=900,
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r"^```(json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        out = data.get("labels", [])
        if isinstance(out, list) and all(isinstance(x, str) for x in out):
            return [norm_term(x) for x in out if is_good_concept_label(x)]
    except Exception:
        pass
    return labels


# -----------------------------
# Build graph
# -----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Transcript file (.pdf or .txt)")
    ap.add_argument("--out", default="concept_graph.json")
    ap.add_argument("--max-concepts", type=int, default=120)
    ap.add_argument("--min-freq", type=int, default=2)
    ap.add_argument("--chunk-max-chars", type=int, default=1400)
    ap.add_argument("--fuzzy-threshold", type=int, default=93)

    ap.add_argument("--llm-refine", action="store_true", help="Use HF hosted LLM to refine labels")
    ap.add_argument("--llm-model", default="Qwen/Qwen2.5-14B-Instruct")
    args = ap.parse_args()

    cfg = ExtractConfig(
        max_concepts=args.max_concepts,
        min_freq=args.min_freq,
        fuzzy_merge_threshold=args.fuzzy_threshold,
        chunk_max_chars=args.chunk_max_chars,
    )

    raw = load_text(args.input)
    text = clean_transcript(raw)
    lang = detect_lang(text)
    nlp = get_nlp(lang)

    chunks = chunk_by_timestamps(text, max_chars=cfg.chunk_max_chars)

    # 1) gather terms + provenance
    term_counts = Counter()
    term_mentions = defaultdict(list)  # term -> list of evidence snippets
    alias_pairs_all = []

    # relations extracted per chunk (raw, pre-merge)
    raw_relations = []

    for ch in chunks:
        ch_norm = norm_term(ch)

        cue_terms = extract_cue_terms(ch)
        alias_pairs = extract_alias_pairs(ch)
        spacy_terms = extract_spacy_terms(nlp, ch)

        alias_pairs_all.extend(alias_pairs)

        # count & store mention snippets
        for t in cue_terms:
            term_counts[t] += 3  # strong boost for defined terms
            term_mentions[t].append(ch[:220])

        for t in spacy_terms:
            term_counts[t] += 1
            if len(term_mentions[t]) < cfg.keep_top_snippets_per_concept:
                term_mentions[t].append(ch[:220])

        # rule relations
        raw_relations.extend(extract_relations(ch))

    # 2) initial filtering by frequency
    candidates = [t for t, f in term_counts.items() if f >= cfg.min_freq and is_good_concept_label(t)]
    # keep more before merge
    candidates = sorted(candidates, key=lambda t: term_counts[t], reverse=True)[: cfg.max_concepts * 2]

    # 3) fuzzy merge
    mapping = fuzzy_merge(candidates, threshold=cfg.fuzzy_merge_threshold)

    # apply mapping to counts & mentions
    canon_counts = Counter()
    canon_mentions = defaultdict(list)
    for t, f in term_counts.items():
        if t in mapping:
            canon = mapping[t]
            canon_counts[canon] += f
            canon_mentions[canon].extend(term_mentions.get(t, []))

    # choose final canon set
    canon = [t for t, _ in canon_counts.most_common(cfg.max_concepts)]
    canon_set = set(canon)

    # 4) alias canonicalization (only keep if both sides in canon after mapping)
    alias_edges = []
    for a, b in alias_pairs_all:
        a2 = mapping.get(a, a)
        b2 = mapping.get(b, b)
        if a2 != b2 and a2 in canon_set and b2 in canon_set:
            alias_edges.append((a2, "aka", b2))

    # 5) apply mapping to relations; keep if nodes exist
    edges = []
    for src, rel, tgt, ev in raw_relations:
        src2 = mapping.get(src, src)
        tgt2 = mapping.get(tgt, tgt)
        if src2 in canon_set and tgt2 in canon_set and rel in REL_TYPES and src2 != tgt2:
            edges.append((src2, rel, tgt2, ev))

    # 6) optional LLM label refinement (post-merge, pre-export)
    if args.llm_refine:
        if InferenceClient is None:
            raise RuntimeError("huggingface_hub not installed. pip install huggingface_hub")
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
        if not token:
            raise RuntimeError("Set HF_TOKEN for --llm-refine")
        client = InferenceClient(token=token)
        refined = llm_refine_labels(client, args.llm_model, canon, lang=lang)

        # Re-map old canon -> refined by index (stable)
        # (This is a safe approach: we are not “merging” here, just cleaning strings.)
        if len(refined) == len(canon):
            ren = {canon[i]: refined[i] for i in range(len(canon))}
            canon = [ren[c] for c in canon]
            canon_set = set(canon)

            # apply rename to nodes, edges, alias
            canon_counts2 = Counter()
            canon_mentions2 = defaultdict(list)
            for old, val in canon_counts.items():
                if old in ren:
                    canon_counts2[ren[old]] += val
                    canon_mentions2[ren[old]].extend(canon_mentions.get(old, []))
            canon_counts = canon_counts2
            canon_mentions = canon_mentions2

            edges2 = []
            for s, r, t, ev in edges:
                s2 = ren.get(s, s)
                t2 = ren.get(t, t)
                if s2 in canon_set and t2 in canon_set and s2 != t2:
                    edges2.append((s2, r, t2, ev))
            edges = edges2

            alias_edges2 = []
            for s, r, t in alias_edges:
                s2 = ren.get(s, s)
                t2 = ren.get(t, t)
                if s2 in canon_set and t2 in canon_set and s2 != t2:
                    alias_edges2.append((s2, r, t2))
            alias_edges = alias_edges2

    # 7) Build graph object
    G = nx.DiGraph()
    for c in canon:
        G.add_node(c, frequency=int(canon_counts.get(c, 1)))

    # Add relation edges with weights
    edge_counter = Counter()
    edge_evidence = defaultdict(list)

    for s, r, t, ev in edges:
        edge_counter[(s, r, t)] += 1
        if len(edge_evidence[(s, r, t)]) < 3:
            edge_evidence[(s, r, t)].append(ev)

    for (s, r, t), w in edge_counter.items():
        G.add_edge(s, t, relation=r, weight=int(w), evidence=" | ".join(edge_evidence[(s, r, t)]))

    # Add alias edges (optional)
    for s, r, t in alias_edges:
        if not G.has_edge(s, t):
            G.add_edge(s, t, relation=r, weight=1)

    # 8) Export JSON in the same node/edge format you used before
    nodes_out = [{"id": n, **G.nodes[n], "mentions": dedup_list(canon_mentions.get(n, []), 5)} for n in G.nodes]
    edges_out = [{"source": u, "target": v, **G.edges[u, v]} for u, v in G.edges]

    out = {
        "nodes": nodes_out,
        "edges": edges_out,
        "meta": {
            "language": lang,
            "chunking": "timestamps_or_paragraphs",
            "max_concepts": cfg.max_concepts,
            "min_freq": cfg.min_freq,
            "fuzzy_merge_threshold": cfg.fuzzy_merge_threshold,
            "llm_refined": bool(args.llm_refine),
            "llm_model": args.llm_model if args.llm_refine else None,
        }
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # debug provenance
    with open("concept_mentions.json", "w", encoding="utf-8") as f:
        json.dump(
            {c: dedup_list(canon_mentions.get(c, []), 8) for c in canon},
            f, ensure_ascii=False, indent=2
        )

    print("Done.")
    print(f"Chunks: {len(chunks)}")
    print(f"Nodes: {len(nodes_out)}")
    print(f"Edges: {len(edges_out)}")
    print(f"Wrote: {args.out}")
    print("Wrote: concept_mentions.json")


def dedup_list(items: List[str], max_keep: int) -> List[str]:
    out = []
    for x in items:
        if not isinstance(x, str) or not x.strip():
            continue
        if all(fuzz.token_set_ratio(x, y) < 92 for y in out):
            out.append(x.strip())
        if len(out) >= max_keep:
            break
    return out


if __name__ == "__main__":
    main()