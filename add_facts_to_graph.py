#!/usr/bin/env python3
"""
Extend a concept graph by extracting explicit, evidence-backed facts from transcript snippets using an HF-hosted LLM.

Inputs:
- concept_graph_grounded.json  (must include nodes with evidence_snippets OR you provide transcript to build snippets)
- OR concept_graph.json + transcript.txt (this script can also create snippets itself)

Outputs:
- concept_graph_with_facts.json

Fact extraction is:
- explicit only (no inference)
- evidence quote must be verbatim in the snippet
- bilingual EN/ES
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from huggingface_hub import InferenceClient
from langdetect import detect, LangDetectException
from rapidfuzz import fuzz


RELATION_TYPES = ["is_a", "part_of", "depends_on", "causes", "used_for", "related_to"]

def normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip("`'\"")
    return s

def detect_lang(text: str) -> str:
    try:
        lang = detect(text[:800])
    except LangDetectException:
        return "en"
    return "es" if lang.startswith("es") else "en"

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

def evidence_is_verbatim(evidence: str, snippet: str) -> bool:
    if not isinstance(evidence, str) or len(evidence.split()) < 4:
        return False
    return normalize(evidence) in normalize(snippet)

def dedup_texts(texts: List[str], thr: int = 92) -> List[str]:
    out = []
    for t in texts:
        if not any(fuzz.token_set_ratio(t, x) >= thr for x in out):
            out.append(t)
    return out


# ----------------------------
# LLM: fact extraction prompt
# ----------------------------

def build_fact_prompt(concept: str, snippet: str, lang: str) -> List[Dict[str, str]]:
    """
    Extract explicit facts about the concept from the snippet.
    Output strict JSON.
    """
    if lang == "es":
        system = (
            "Eres un extractor de hechos para aprendizaje. "
            "Extrae SOLO hechos EXPLÍCITOS del texto. Devuelve SOLO JSON válido."
        )
        user = f"""
CONCEPTO: "{concept}"

TEXTO:
\"\"\"{snippet}\"\"\"

Tarea:
- Extrae de 1 a 3 hechos atómicos y explícitos sobre el concepto, tal como aparecen en el texto.
- NO infieras nada. Si no hay hechos claros, devuelve una lista vacía.
- Cada hecho debe incluir una cita 'evidence' copiada literalmente del texto (5-25 palabras).
- Devuelve SOLO JSON válido.

Formato:
{{
  "facts": [
    {{
      "fact": "...",
      "evidence": "...",
      "confidence": 0.0
    }}
  ]
}}
"""
    else:
        system = (
            "You extract learning facts. "
            "Extract ONLY EXPLICIT facts from the text. Return ONLY valid JSON."
        )
        user = f"""
CONCEPT: "{concept}"

TEXT:
\"\"\"{snippet}\"\"\"

Task:
- Extract 1 to 3 atomic, explicit facts about the concept as stated in the text.
- Do NOT infer anything. If there are no clear facts, return an empty list.
- Each fact must include an 'evidence' quote copied verbatim from the text (5-25 words).
- Output ONLY valid JSON.

Format:
{{
  "facts": [
    {{
      "fact": "...",
      "evidence": "...",
      "confidence": 0.0
    }}
  ]
}}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_llm_extract_facts(
    client: InferenceClient,
    model: str,
    concept: str,
    snippet: str,
    lang: str,
    temperature: float = 0.0,
    max_tokens: int = 650,
) -> List[dict]:
    msgs = build_fact_prompt(concept, snippet, lang)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return []

    data = safe_json_load(text)
    if not data or "facts" not in data or not isinstance(data["facts"], list):
        # repair once
        repair = [
            {"role": "system", "content": "Return ONLY valid JSON with schema {'facts':[...]}."},
            {"role": "user", "content": f"Fix to valid JSON with the required schema:\n{text}"},
        ]
        try:
            resp2 = client.chat.completions.create(
                model=model, messages=repair, temperature=0.0, max_tokens=max_tokens
            )
            data2 = safe_json_load(resp2.choices[0].message.content)
            if data2 and isinstance(data2.get("facts"), list):
                data = data2
            else:
                return []
        except Exception:
            return []

    out = []
    for f in data["facts"]:
        if not isinstance(f, dict):
            continue
        fact = f.get("fact")
        ev = f.get("evidence")
        conf = f.get("confidence", 0.5)
        if not isinstance(fact, str) or len(fact.strip()) < 8:
            continue
        if not isinstance(ev, str):
            continue
        if not evidence_is_verbatim(ev, snippet):
            continue
        try:
            conf = float(conf)
        except Exception:
            conf = 0.5
        out.append({"fact": fact.strip(), "evidence": ev.strip(), "confidence": conf})
    return out


# ----------------------------
# Main graph update
# ----------------------------

def stable_fact_id(concept: str, fact_text: str, evidence: str) -> str:
    h = hashlib.sha256((normalize(concept) + "|" + normalize(fact_text) + "|" + normalize(evidence)).encode("utf-8")).hexdigest()
    return "F" + h[:10].upper()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True, help="Input graph JSON (should include node evidence_snippets)")
    ap.add_argument("--out", default="concept_graph_with_facts.json")
    ap.add_argument("--model", default="Qwen/Qwen2.5-14B-Instruct")
    ap.add_argument("--max-snippets-per-concept", type=int, default=4)
    ap.add_argument("--max-facts-per-snippet", type=int, default=3)
    ap.add_argument("--min-node-mentions", type=int, default=1, help="Only process nodes that appear at least this many times (if mention_count exists)")
    ap.add_argument("--temperature", type=float, default=0.0)
    args = ap.parse_args()

    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if not token:
        print("ERROR: set HF_TOKEN env var", file=sys.stderr)
        sys.exit(1)

    with open(args.graph, "r", encoding="utf-8") as f:
        g = json.load(f)

    nodes = g.get("nodes", [])
    edges = g.get("edges", [])

    client = InferenceClient(token=token)

    # existing facts (avoid duplicates if script re-run)
    existing_fact_ids = set()
    for n in nodes:
        if isinstance(n.get("kind"), str) and n["kind"] == "fact":
            existing_fact_ids.add(n.get("id"))

    new_fact_nodes = []
    new_fact_edges = []

    # For dedup across newly generated facts
    seen_fact_fp = set()

    for idx, n in enumerate(nodes, start=1):
        concept = n.get("id")
        if not isinstance(concept, str) or not concept.strip():
            continue
        concept = concept.strip()

        # Only concepts (skip already-fact nodes)
        if n.get("kind") == "fact":
            continue

        mention_count = int(n.get("mention_count", 1))
        if mention_count < args.min_node_mentions:
            continue

        snips = n.get("evidence_snippets") or []
        if not isinstance(snips, list) or not snips:
            continue

        snips = snips[: args.max_snippets_per_concept]

        for s_i, snippet in enumerate(snips, start=1):
            if not isinstance(snippet, str) or len(snippet.strip()) < 30:
                continue
            lang = detect_lang(snippet)

            facts = call_llm_extract_facts(
                client=client,
                model=args.model,
                concept=concept,
                snippet=snippet,
                lang=lang,
                temperature=args.temperature,
            )
            facts = facts[: args.max_facts_per_snippet]

            for fobj in facts:
                fid = stable_fact_id(concept, fobj["fact"], fobj["evidence"])
                fp = fid
                if fp in seen_fact_fp or fid in existing_fact_ids:
                    continue

                seen_fact_fp.add(fp)

                new_fact_nodes.append({
                    "id": fid,
                    "kind": "fact",
                    "text": fobj["fact"],
                    "subject_concept": concept,
                    "language": "es" if lang == "es" else "en",
                    "confidence": fobj["confidence"],
                    "evidence": fobj["evidence"],
                })

                new_fact_edges.append({
                    "source": concept,
                    "target": fid,
                    "relation": "has_fact",
                    "weight": 1,
                    "grounded": True,
                })

        if idx % 10 == 0:
            print(f"Processed {idx}/{len(nodes)} nodes; new facts so far: {len(new_fact_nodes)}", file=sys.stderr)

    # Dedup fact nodes by text similarity (optional final pass)
    # Keep first occurrence
    deduped = []
    for fn in new_fact_nodes:
        if not any(fuzz.token_set_ratio(fn["text"], x["text"]) > 94 and fn["subject_concept"] == x["subject_concept"] for x in deduped):
            deduped.append(fn)
    new_fact_nodes = deduped

    g2 = dict(g)
    g2["nodes"] = nodes + new_fact_nodes
    g2["edges"] = edges + new_fact_edges
    g2.setdefault("meta", {})
    g2["meta"]["facts_added"] = {
        "model": args.model,
        "new_fact_nodes": len(new_fact_nodes),
        "new_fact_edges": len(new_fact_edges),
        "max_snippets_per_concept": args.max_snippets_per_concept,
        "max_facts_per_snippet": args.max_facts_per_snippet,
        "explicit_only": True,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(g2, f, ensure_ascii=False, indent=2)

    print("Done.")
    print(f"New fact nodes: {len(new_fact_nodes)}")
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()