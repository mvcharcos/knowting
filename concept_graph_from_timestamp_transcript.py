#!/usr/bin/env python3
"""
Concept graph extraction (ES/EN) tuned for timestamped transcripts like:

0:11
... text ...
0:14
... text ...

Key features:
- Chunk by timestamps (m:ss or h:mm:ss)
- Definition cues: "se llama", "lo que se conoce como", "se denomina", "vendría a ser"
- Composition cues: "está conformada por", "conformado por", "formada por"
- Division cue: "divide X en A y B"
- Strong filtering of junk terms
- Multi-word term preservation

Outputs:
- concept_graph.json   (nodes/edges)
- chunks_debug.json    (chunks used, for inspection)

Usage:
  python concept_graph_from_timestamp_transcript.py --input transcript.txt --out concept_graph.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from rapidfuzz import fuzz


# ----------------------------
# Normalization & filters
# ----------------------------

ES_ARTICLES = {"el", "la", "los", "las", "un", "una", "unos", "unas"}
CONNECTOR_WORDS = {
    "de", "del", "la", "el", "los", "las", "y", "o", "u", "en", "por", "para", "con", "al", "a",
    "of", "the", "and", "or", "in", "on", "for", "with", "to"
}

JUNK_SINGLE = {
    "parte", "zona", "superficie", "imagen", "hueso", "huesos", "articulación", "articulaciones",
    "movimiento", "movimientos", "cosa", "cosas", "tema", "punto", "lado", "forma", "línea",
    "this", "that", "thing", "part", "area", "surface", "bone", "bones", "joint", "joints",
}

def norm(s: str) -> str:
    s = s.strip().strip("`'\"")
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    # strip leading articles
    s = re.sub(r"^(el|la|los|las|un|una|unos|unas)\s+", "", s)
    s = re.sub(r"^(a|an|the)\s+", "", s)
    # trim punctuation edges
    s = re.sub(r"^[^\wáéíóúüñ]+|[^\wáéíóúüñ]+$", "", s, flags=re.IGNORECASE)
    return s

def is_good_label(label: str) -> bool:
    t = norm(label)
    if not t or len(t) < 3:
        return False
    # too long phrase
    if len(t.split()) > 7:
        return False
    if t in JUNK_SINGLE:
        return False
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return False
    toks = t.split()
    # reject mostly connector words
    if len(toks) >= 2:
        ratio = sum(x in CONNECTOR_WORDS for x in toks) / len(toks)
        if ratio > 0.5:
            return False
    # reject 1-word very generic
    if len(toks) == 1 and t in JUNK_SINGLE:
        return False
    return True

def dedup_list(items: List[str], thr: int = 92, max_keep: int = 8) -> List[str]:
    out = []
    for x in items:
        x = x.strip()
        if not x:
            continue
        if all(fuzz.token_set_ratio(x, y) < thr for y in out):
            out.append(x)
        if len(out) >= max_keep:
            break
    return out


# ----------------------------
# Chunking by timestamps
# ----------------------------

# matches lines like: "0:11" or "12:03" or "1:02:15"
TS_LINE_RE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$")

def chunk_timestamp_transcript(text: str, max_chars: int = 1400) -> List[Dict]:
    """
    Returns list of dict chunks:
    {"t0": "0:11", "t1": "0:26", "text": "..."}
    Packs consecutive timestamp blocks into <= max_chars chunks.
    """
    lines = text.splitlines()
    blocks = []
    cur_t = None
    cur_lines = []

    for ln in lines:
        m = TS_LINE_RE.match(ln)
        if m:
            # flush previous block
            if cur_t is not None and cur_lines:
                blocks.append({"t": cur_t, "text": " ".join(cur_lines).strip()})
            cur_t = m.group(1)
            cur_lines = []
        else:
            ln2 = ln.strip()
            if ln2:
                cur_lines.append(ln2)

    if cur_t is not None and cur_lines:
        blocks.append({"t": cur_t, "text": " ".join(cur_lines).strip()})

    # pack blocks
    chunks = []
    buf_text = ""
    buf_t0 = None
    buf_t1 = None

    for b in blocks:
        if not buf_text:
            buf_text = b["text"]
            buf_t0 = b["t"]
            buf_t1 = b["t"]
        elif len(buf_text) + len(b["text"]) + 1 <= max_chars:
            buf_text += " " + b["text"]
            buf_t1 = b["t"]
        else:
            chunks.append({"t0": buf_t0, "t1": buf_t1, "text": buf_text})
            buf_text = b["text"]
            buf_t0 = b["t"]
            buf_t1 = b["t"]

    if buf_text:
        chunks.append({"t0": buf_t0, "t1": buf_t1, "text": buf_text})

    # fallback: if no timestamps found, make one chunk
    if not chunks and text.strip():
        chunks = [{"t0": None, "t1": None, "text": re.sub(r"\s+", " ", text).strip()}]

    return chunks


# ----------------------------
# Rule-based concept + relation extraction
# ----------------------------

# Definition/name cues (as seen in your snippet)
DEF_CUES = [
    re.compile(r"\blo\s+que\s+se\s+conoce\s+como\s+(?:la|el|los|las)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})", re.IGNORECASE),
    re.compile(r"\bse\s+llama\s+(?:la|el|los|las)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})", re.IGNORECASE),
    re.compile(r"\bse\s+denomina\s+(?:la|el|los|las)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})", re.IGNORECASE),
]

# Alias pattern: "escápulas omóplatos" (two consecutive nouns) is tricky;
# We'll handle common explicit "X o Y" and "X (Y)" plus "X ... Y" with heuristics.
ALIAS_PATTERNS = [
    re.compile(r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s*\(\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s*\)"),
    re.compile(r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s+(?:o|u)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\b", re.IGNORECASE),
]

# Composition cues: "X está conformada por A, B y C"
COMP_PATTERNS = [
    re.compile(
        r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s+está\s+(?:conformad[ao]s?|formad[ao]s?)\s+por\s+(.{10,220})",
        re.IGNORECASE
    ),
    re.compile(
        r"\bconformad[ao]s?\s+por\s+(.{10,220})",
        re.IGNORECASE
    ),
]

# Division cue: "la espina ... divide la escápula en dos partes ... A y B"
DIV_PATTERN = re.compile(
    r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s+divide\s+(?:a|al|la|el)?\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s+en\s+dos\s+partes.*?\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s+y\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})",
    re.IGNORECASE
)

# Articulation: "X articulan con Y" / "X se articula con Y"
ART_PATTERN = re.compile(
    r"\b([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})\s+(?:se\s+articula[n]?\s+con|articula[n]?\s+con)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ\- ]{3,80})",
    re.IGNORECASE
)

def parse_enumeration(text: str) -> List[str]:
    """
    Parse A, B y C style lists. Keep only plausible labels.
    """
    t = re.sub(r"\s+y\s+", ",", text, flags=re.IGNORECASE)
    t = re.sub(r"\s+e\s+", ",", t, flags=re.IGNORECASE)
    parts = [p.strip() for p in t.split(",") if p.strip()]
    out = []
    for p in parts:
        p = re.sub(r"[\.:\;\)\]]+$", "", p).strip()
        p = re.sub(r"^(como\s+veis\s+aquí\s+)?", "", p, flags=re.IGNORECASE).strip()
        if is_good_label(p):
            out.append(norm(p))
    return out[:10]

def extract_from_chunk(chunk_text: str) -> Tuple[List[str], List[Tuple[str,str,str,str]], List[Tuple[str,str]]]:
    """
    Returns:
      concepts: [label...]
      relations: [(src, rel, tgt, evidence)...]
      aliases: [(a,b)...]
    """
    concepts = []
    relations = []
    aliases = []

    evidence = re.sub(r"\s+", " ", chunk_text).strip()
    evidence_short = evidence if len(evidence) <= 260 else evidence[:260] + "..."

    # definition cues -> concept terms
    for pat in DEF_CUES:
        for m in pat.finditer(chunk_text):
            term = m.group(1).strip()
            term = re.sub(r"[\,\;\:\.]+$", "", term)
            if is_good_label(term):
                concepts.append(norm(term))

    # alias cues
    for pat in ALIAS_PATTERNS:
        for m in pat.finditer(chunk_text):
            a = m.group(1).strip()
            b = m.group(2).strip()
            if is_good_label(a) and is_good_label(b):
                aliases.append((norm(a), norm(b)))
                concepts.extend([norm(a), norm(b)])

    # composition cue -> part_of/has_part relations
    # We'll try to infer "X" if pattern captures it; else if no X, skip
    for m in COMP_PATTERNS[0].finditer(chunk_text):
        whole = m.group(1).strip()
        items = m.group(2).strip()
        if is_good_label(whole):
            whole_n = norm(whole)
            concepts.append(whole_n)
            parts = parse_enumeration(items)
            for p in parts:
                concepts.append(p)
                relations.append((whole_n, "has_part", p, evidence_short))
                relations.append((p, "part_of", whole_n, evidence_short))

    # division
    for m in DIV_PATTERN.finditer(chunk_text):
        divider = m.group(1).strip()
        whole = m.group(2).strip()
        a = m.group(3).strip()
        b = m.group(4).strip()
        if all(is_good_label(x) for x in [divider, whole, a, b]):
            divider_n = norm(divider)
            whole_n = norm(whole)
            a_n = norm(a)
            b_n = norm(b)
            concepts.extend([divider_n, whole_n, a_n, b_n])
            relations.append((divider_n, "divides_into", whole_n, evidence_short))
            relations.append((whole_n, "has_part", a_n, evidence_short))
            relations.append((whole_n, "has_part", b_n, evidence_short))

    # articulation
    for m in ART_PATTERN.finditer(chunk_text):
        x = m.group(1).strip()
        y = m.group(2).strip()
        if is_good_label(x) and is_good_label(y):
            x_n = norm(x)
            y_n = norm(y)
            concepts.extend([x_n, y_n])
            relations.append((x_n, "articulates_with", y_n, evidence_short))
            relations.append((y_n, "articulates_with", x_n, evidence_short))

    return concepts, relations, aliases


# ----------------------------
# Canonicalization
# ----------------------------

def fuzzy_merge(terms: List[str], threshold: int) -> Dict[str, str]:
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


# ----------------------------
# Main
# ----------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Transcript text file with timestamp lines")
    ap.add_argument("--out", default="concept_graph.json")
    ap.add_argument("--max-concepts", type=int, default=120)
    ap.add_argument("--min-freq", type=int, default=2)
    ap.add_argument("--chunk-max-chars", type=int, default=1400)
    ap.add_argument("--fuzzy-threshold", type=int, default=93)
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        raw = f.read()

    chunks = chunk_timestamp_transcript(raw, max_chars=args.chunk_max_chars)

    term_counts = Counter()
    mentions = defaultdict(list)
    raw_edges = []
    raw_aliases = []

    for ch in chunks:
        txt = ch["text"]
        concepts, rels, aliases = extract_from_chunk(txt)

        # Boost terms that appear via cues/relations
        for c in concepts:
            if is_good_label(c):
                term_counts[c] += 2
                if len(mentions[c]) < 6:
                    mentions[c].append(f"{ch['t0']}-{ch['t1']}: " + (txt[:220] + ("..." if len(txt) > 220 else "")))

        for (s, r, t, ev) in rels:
            # relations imply concepts too
            term_counts[s] += 2
            term_counts[t] += 2
            raw_edges.append((s, r, t, ev))

        raw_aliases.extend(aliases)

    # Filter by freq and cap
    candidates = [t for t, f in term_counts.items() if f >= args.min_freq and is_good_label(t)]
    candidates = sorted(candidates, key=lambda t: term_counts[t], reverse=True)[: args.max_concepts * 2]

    mp = fuzzy_merge(candidates, threshold=args.fuzzy_threshold)

    canon_counts = Counter()
    canon_mentions = defaultdict(list)

    for t, f in term_counts.items():
        if t in mp:
            canon = mp[t]
            canon_counts[canon] += f
            canon_mentions[canon].extend(mentions.get(t, []))

    canon = [t for t, _ in canon_counts.most_common(args.max_concepts)]
    canon_set = set(canon)

    # Build graph
    G = nx.DiGraph()
    for c in canon:
        G.add_node(c, frequency=int(canon_counts[c]), mentions=dedup_list(canon_mentions[c], max_keep=6))

    edge_counter = Counter()
    edge_evidence = defaultdict(list)

    for s, r, t, ev in raw_edges:
        s2 = mp.get(s, s)
        t2 = mp.get(t, t)
        if s2 in canon_set and t2 in canon_set and s2 != t2 and r in REL_TYPES:
            edge_counter[(s2, r, t2)] += 1
            if len(edge_evidence[(s2, r, t2)]) < 3:
                edge_evidence[(s2, r, t2)].append(ev)

    for (s, r, t), w in edge_counter.items():
        G.add_edge(s, t, relation=r, weight=int(w), evidence=" | ".join(edge_evidence[(s, r, t)]))

    # Alias edges (optional)
    for a, b in raw_aliases:
        a2 = mp.get(a, a)
        b2 = mp.get(b, b)
        if a2 in canon_set and b2 in canon_set and a2 != b2:
            if not G.has_edge(a2, b2):
                G.add_edge(a2, b2, relation="aka", weight=1)

    out = {
        "nodes": [{"id": n, **G.nodes[n]} for n in G.nodes],
        "edges": [{"source": u, "target": v, **G.edges[u, v]} for u, v in G.edges],
        "meta": {
            "chunking": "timestamp_blocks",
            "min_freq": args.min_freq,
            "max_concepts": args.max_concepts,
            "fuzzy_threshold": args.fuzzy_threshold,
        }
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    with open("chunks_debug.json", "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    print("Done.")
    print(f"Chunks: {len(chunks)}")
    print(f"Nodes: {len(out['nodes'])}")
    print(f"Edges: {len(out['edges'])}")
    print(f"Wrote: {args.out}")
    print("Wrote: chunks_debug.json")


if __name__ == "__main__":
    main()