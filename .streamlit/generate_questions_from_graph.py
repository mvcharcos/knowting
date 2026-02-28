#!/usr/bin/env python3

import argparse
import json
import os
import random
import re
from collections import defaultdict
from typing import List, Dict, Tuple

import networkx as nx
from huggingface_hub import InferenceClient


# ----------------------------
# CONFIG
# ----------------------------

QUESTION_TYPES = [
    "definition",
    "explanation",
    "application",
    "comparison",
    "dependency_reasoning",
    "causal_reasoning",
    "component_reasoning"
]

RELATION_MAP = {
    "depends_on": "dependency_reasoning",
    "causes": "causal_reasoning",
    "part_of": "component_reasoning",
    "is_a": "comparison",
}


# ----------------------------
# Utilities
# ----------------------------

def safe_json_load(text: str):
    text = text.strip()
    text = re.sub(r"^```(json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except:
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except:
                return None
    return None


def build_prompt(concept: str,
                 related: List[Tuple[str, str]],
                 question_type: str,
                 difficulty: str = "medium") -> List[Dict]:

    relations_text = ""
    for target, rel in related:
        relations_text += f"- {concept} {rel} {target}\n"

    return [
        {
            "role": "system",
            "content": (
                "You are an educational content generator. "
                "Generate ONE high-quality question in valid JSON only."
            )
        },
        {
            "role": "user",
            "content": f"""
Concept: "{concept}"

Known relations:
{relations_text if relations_text else "None"}

Question type: {question_type}
Difficulty: {difficulty}

Rules:
- Output ONLY valid JSON.
- Do NOT repeat the concept name excessively.
- Encourage deep thinking.
- Avoid trivial wording.

Format:
{{
  "question": "...",
  "type": "{question_type}",
  "difficulty": "{difficulty}",
  "concept": "{concept}"
}}
"""
        }
    ]


# ----------------------------
# Question Generation
# ----------------------------

def generate_question(client, model, concept, related, qtype, difficulty):
    messages = build_prompt(concept, related, qtype, difficulty)

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.6,
        max_tokens=400
    )

    content = response.choices[0].message.content
    data = safe_json_load(content)
    return data


# ----------------------------
# Concept Selection Strategy
# ----------------------------

def rank_concepts(G: nx.DiGraph):
    ranking = []

    for node in G.nodes():
        degree = G.degree(node)
        ranking.append((node, degree))

    ranking.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in ranking]


def get_related_concepts(G, concept):
    related = []

    for _, target, data in G.out_edges(concept, data=True):
        related.append((target, data.get("relation", "related_to")))

    for source, _, data in G.in_edges(concept, data=True):
        related.append((source, data.get("relation", "related_to")))

    return related


# ----------------------------
# Main Logic
# ----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("graph_json")
    parser.add_argument("--num-questions", type=int, required=True)
    parser.add_argument("--model", default="Qwen/Qwen2.5-14B-Instruct")
    args = parser.parse_args()

    token = os.getenv("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN environment variable")

    with open(args.graph_json, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    G = nx.DiGraph()

    for node in graph_data["nodes"]:
        G.add_node(node["id"])

    for edge in graph_data["edges"]:
        G.add_edge(edge["source"], edge["target"],
                   relation=edge.get("relation", "related_to"))

    client = InferenceClient(token=token)

    ranked_concepts = rank_concepts(G)

    questions = []
    used_pairs = set()
    concept_index = 0

    while len(questions) < args.num_questions:
        concept = ranked_concepts[concept_index % len(ranked_concepts)]
        concept_index += 1

        related = get_related_concepts(G, concept)

        if related:
            relation_types = [RELATION_MAP.get(rel, None) for _, rel in related]
            relation_types = [r for r in relation_types if r]
        else:
            relation_types = []

        qtype_candidates = list(set(QUESTION_TYPES + relation_types))
        qtype = random.choice(qtype_candidates)

        difficulty = random.choice(["easy", "medium", "hard"])

        if (concept, qtype, difficulty) in used_pairs:
            continue

        print(f"Generating question for {concept} ({qtype}, {difficulty})")

        data = generate_question(
            client,
            args.model,
            concept,
            related,
            qtype,
            difficulty
        )

        if not data or "question" not in data:
            continue

        question_text = data["question"]

        if question_text in [q["question"] for q in questions]:
            continue

        questions.append(data)
        used_pairs.add((concept, qtype, difficulty))

    with open("questions_output.json", "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)

    print(f"\nGenerated {len(questions)} questions.")
    print("Saved to questions_output.json")


if __name__ == "__main__":
    main()
