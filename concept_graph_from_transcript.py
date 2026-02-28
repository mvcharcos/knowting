#!/usr/bin/env python3
"""
Build a concept graph from a transcript.

Input:  a .txt transcript (English)
Output: concept_graph.graphml, concept_graph.json

Baseline approach:
- Concepts: frequent noun chunks + named entities
- Relations: dependency-based patterns (X is Y, X has Y, X causes Y, X leads to Y, etc.)
- Merging: string normalization + fuzzy match (+ optional embedding clustering)

This is a good starting point; quality improves with better models/patterns.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import networkx as nx
from rapidfuzz import fuzz

import spacy


# ----------------------------
# Utilities
# ----------------------------

STOP_TERMS = {
    "thing", "stuff", "something", "anything", "everything",
    "someone", "somebody", "people", "person", "example",
    "video", "today", "time", "way", "kind", "lot"
}

RELATION_LEMMAS = {
    "be": "is_a",
    "have": "has",
    "include": "includes",
    "contain": "includes",
    "consist": "includes",
    "cause": "causes",
    "lead": "leads_to",
    "result": "leads_to",
    "allow": "enables",
    "enable": "enables",
    "use": "uses",
    "require": "requires",
    "depend": "depends_on",
    "improve": "improves",
    "reduce": "reduces",
    "increase": "increases",
}

def normalize_term(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^[^\w]+|[^\w]+$", "", t)
    # Drop leading articles
    t = re.sub(r"^(a|an|the)\s+", "", t)
    return t

def is_good_concept(t: str) -> bool:
    if not t or len(t) < 3:
        return False
    if t in STOP_TERMS:
        return False
    # too numeric
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return False
    # single stopword-ish token
    if t in {"this", "that", "these", "those"}:
        return False
    return True

def fuzzy_merge_terms(terms: List[str], threshold: int = 92) -> Dict[str, str]:
    """
    Map each term to a canonical term using fuzzy string similarity.
    Works okay for near-duplicates like:
      "row level security" vs "row-level security"
    """
    canon: List[str] = []
    mapping: Dict[str, str] = {}
    for t in sorted(set(terms), key=len, reverse=True):
        if t in mapping:
            continue
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

@dataclass
class ExtractionConfig:
    min_concept_freq: int = 2
    max_concepts: int = 80
    fuzzy_merge_threshold: int = 92
    max_edges: int = 300


# ----------------------------
# Concept extraction
# ----------------------------

def extract_concepts(nlp, text: str) -> Counter:
    doc = nlp(text)

    terms: List[str] = []

    # 1) Noun chunks
    for chunk in doc.noun_chunks:
        t = normalize_term(chunk.text)
        # Filter pronoun chunks and extremely long chunks
        if len(t.split()) > 6:
            continue
        if is_good_concept(t):
            terms.append(t)

    # 2) Named entities (often key concepts)
    for ent in doc.ents:
        t = normalize_term(ent.text)
        if len(t.split()) > 6:
            continue
        if is_good_concept(t):
            terms.append(t)

    return Counter(terms)


# ----------------------------
# Relation extraction (rule-based)
# ----------------------------

def extract_relations(nlp, text: str, concept_set: set) -> Counter:
    """
    Extract (head_concept, relation, tail_concept) triples using dependency patterns.

    Patterns:
    - X is/are Y
    - X has Y
    - X causes/leads_to/enables Y
    - X depends_on Y
    - X uses Y
    """
    doc = nlp(text)
    triples = Counter()

    for sent in doc.sents:
        # Collect candidate spans from sentence by matching concepts in text
        sent_text = normalize_term(sent.text)
        present = [c for c in concept_set if c in sent_text]
        if len(present) < 2:
            continue

        # Dependency-based: find verbs and their subjects/objects
        for token in sent:
            if token.pos_ != "VERB":
                continue

            lemma = token.lemma_.lower()
            if lemma not in RELATION_LEMMAS:
                continue
            rel = RELATION_LEMMAS[lemma]

            # nominal subject (X)
            subj = None
            for child in token.children:
                if child.dep_ in ("nsubj", "nsubjpass"):
                    subj = child
                    break

            # direct object / attribute / prepositional object (Y)
            obj = None
            for child in token.children:
                if child.dep_ in ("dobj", "attr", "oprd"):
                    obj = child
                    break
                if child.dep_ == "prep":
                    # object of preposition
                    for gc in child.children:
                        if gc.dep_ == "pobj":
                            obj = gc
                            break
                if obj:
                    break

            if not subj or not obj:
                continue

            subj_span = normalize_term(subj.subtree.__iter__().__next__().doc[subj.left_edge.i:subj.right_edge.i+1].text)
            obj_span = normalize_term(obj.subtree.__iter__().__next__().doc[obj.left_edge.i:obj.right_edge.i+1].text)

            # Snap spans to closest concept string present in sentence
            head = best_match_concept(subj_span, present)
            tail = best_match_concept(obj_span, present)
            if not head or not tail or head == tail:
                continue

            triples[(head, rel, tail)] += 1

    return triples

def best_match_concept(span: str, candidates: List[str]) -> Optional[str]:
    span = normalize_term(span)
    if not span:
        return None
    # Exact containment preference
    for c in candidates:
        if span == c:
            return c
    # Fuzzy
    best = None
    best_score = 0
    for c in candidates:
        s = fuzz.token_sort_ratio(span, c)
        if s > best_score:
            best_score = s
            best = c
    return best if best_score >= 80 else None


# ----------------------------
# Graph building
# ----------------------------

def build_graph(concepts: Counter, triples: Counter, cfg: ExtractionConfig) -> nx.DiGraph:
    # Keep top concepts by frequency
    kept = [(c, f) for c, f in concepts.items() if f >= cfg.min_concept_freq and is_good_concept(c)]
    kept.sort(key=lambda x: x[1], reverse=True)
    kept = kept[: cfg.max_concepts]
    kept_concepts = [c for c, _ in kept]

    # Merge near-duplicates (fuzzy)
    mapping = fuzzy_merge_terms(kept_concepts, threshold=cfg.fuzzy_merge_threshold)

    # Canonical concept frequencies
    canon_freq = Counter()
    for c, f in kept:
        canon_freq[mapping[c]] += f

    # Build graph
    G = nx.DiGraph()

    for c, f in canon_freq.items():
        G.add_node(c, frequency=int(f))

    # Add edges from triples
    edge_items = []
    for (h, rel, t), w in triples.items():
        h2 = mapping.get(h, h)
        t2 = mapping.get(t, t)
        if h2 == t2:
            continue
        if h2 not in G or t2 not in G:
            continue
        edge_items.append((h2, rel, t2, int(w)))

    # Limit edges to strongest
    edge_items.sort(key=lambda x: x[3], reverse=True)
    edge_items = edge_items[: cfg.max_edges]

    for h, rel, t, w in edge_items:
        # combine weights for same edge+relation
        if G.has_edge(h, t) and G[h][t].get("relation") == rel:
            G[h][t]["weight"] += w
        else:
            G.add_edge(h, t, relation=rel, weight=w)

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
    ap.add_argument("transcript_path", help="Path to transcript .txt file")
    ap.add_argument("--min-freq", type=int, default=2)
    ap.add_argument("--max-concepts", type=int, default=80)
    ap.add_argument("--fuzzy-threshold", type=int, default=92)
    ap.add_argument("--max-edges", type=int, default=300)
    ap.add_argument("--model", default="en_core_web_sm", help="spaCy model")
    args = ap.parse_args()

    cfg = ExtractionConfig(
        min_concept_freq=args.min_freq,
        max_concepts=args.max_concepts,
        fuzzy_merge_threshold=args.fuzzy_threshold,
        max_edges=args.max_edges,
    )

    with open(args.transcript_path, "r", encoding="utf-8") as f:
        text = f.read()

    nlp = spacy.load(args.model)
    nlp.add_pipe("sentencizer", first=True) if "sentencizer" not in nlp.pipe_names else None

    concepts = extract_concepts(nlp, text)
    # initial shortlist for relation extraction: take more than max_concepts to catch relations
    shortlist = [c for c, _ in concepts.most_common(cfg.max_concepts * 2)]
    shortlist = [c for c in shortlist if is_good_concept(c)]
    concept_set = set(shortlist)

    triples = extract_relations(nlp, text, concept_set)
    G = build_graph(concepts, triples, cfg)

    nx.write_graphml(G, "concept_graph.graphml")
    with open("concept_graph.json", "w", encoding="utf-8") as f:
        json.dump(graph_to_json(G), f, ensure_ascii=False, indent=2)

    print(f"Wrote concept_graph.graphml and concept_graph.json")
    print(f"Nodes: {G.number_of_nodes()}  Edges: {G.number_of_edges()}")


if __name__ == "__main__":
    main()
