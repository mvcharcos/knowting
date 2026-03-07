#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

from huggingface_hub import InferenceClient
from rapidfuzz import fuzz


# --------------------------------------------------
# Utilities
# --------------------------------------------------

TS_LINE_RE = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$")


def normalize_text(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def safe_json_load(text: str) -> Optional[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


def parse_time_to_seconds(ts: str) -> int:
    parts = [int(x) for x in ts.split(":")]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    raise ValueError(f"Invalid timestamp: {ts}")


def seconds_to_time(sec: int) -> str:
    if sec < 3600:
        m = sec // 60
        s = sec % 60
        return f"{m}:{s:02d}"
    h = sec // 3600
    rem = sec % 3600
    m = rem // 60
    s = rem % 60
    return f"{h}:{m:02d}:{s:02d}"


# --------------------------------------------------
# Transcript parsing
# --------------------------------------------------

@dataclass
class TranscriptBlock:
    time: str
    time_sec: int
    text: str


def parse_timestamped_transcript(raw: str) -> List[TranscriptBlock]:
    lines = raw.splitlines()
    blocks: List[TranscriptBlock] = []

    current_time = None
    current_lines = []

    for line in lines:
        m = TS_LINE_RE.match(line)
        if m:
            if current_time is not None and current_lines:
                text = normalize_text(" ".join(current_lines))
                if text:
                    blocks.append(
                        TranscriptBlock(
                            time=current_time,
                            time_sec=parse_time_to_seconds(current_time),
                            text=text,
                        )
                    )
            current_time = m.group(1)
            current_lines = []
        else:
            line = line.strip()
            if line:
                current_lines.append(line)

    if current_time is not None and current_lines:
        text = normalize_text(" ".join(current_lines))
        if text:
            blocks.append(
                TranscriptBlock(
                    time=current_time,
                    time_sec=parse_time_to_seconds(current_time),
                    text=text,
                )
            )

    return blocks


def build_initial_windows(
    blocks: List[TranscriptBlock],
    max_chars: int = 1200,
    max_gap_sec: int = 20,
) -> List[dict]:
    """
    Build moderate windows from nearby timestamp blocks.
    """
    windows = []
    if not blocks:
        return windows

    cur_blocks = [blocks[0]]
    cur_text = blocks[0].text

    for b in blocks[1:]:
        gap = b.time_sec - cur_blocks[-1].time_sec
        candidate_text = cur_text + " " + b.text

        if gap <= max_gap_sec and len(candidate_text) <= max_chars:
            cur_blocks.append(b)
            cur_text = candidate_text
        else:
            windows.append({
                "start_time": cur_blocks[0].time,
                "end_time": cur_blocks[-1].time,
                "start_sec": cur_blocks[0].time_sec,
                "end_sec": cur_blocks[-1].time_sec,
                "text": normalize_text(cur_text),
                "block_count": len(cur_blocks),
            })
            cur_blocks = [b]
            cur_text = b.text

    windows.append({
        "start_time": cur_blocks[0].time,
        "end_time": cur_blocks[-1].time,
        "start_sec": cur_blocks[0].time_sec,
        "end_sec": cur_blocks[-1].time_sec,
        "text": normalize_text(cur_text),
        "block_count": len(cur_blocks),
    })

    return windows


# --------------------------------------------------
# Concept loading
# --------------------------------------------------

def load_concepts(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    concepts = []

    if isinstance(data, list):
        if all(isinstance(x, str) for x in data):
            for i, label in enumerate(data, start=1):
                concepts.append({"id": f"C{i}", "label": label})
        elif all(isinstance(x, dict) for x in data):
            for i, item in enumerate(data, start=1):
                cid = item.get("id", f"C{i}")
                label = item.get("label")
                if label:
                    concepts.append({"id": cid, "label": label})
    elif isinstance(data, dict):
        if "concepts" in data and isinstance(data["concepts"], list):
            for i, item in enumerate(data["concepts"], start=1):
                if isinstance(item, dict):
                    cid = item.get("id", f"C{i}")
                    label = item.get("label")
                    if label:
                        concepts.append({"id": cid, "label": label})
        elif "nodes" in data and isinstance(data["nodes"], list):
            for i, item in enumerate(data["nodes"], start=1):
                if isinstance(item, dict):
                    cid = item.get("id", f"C{i}")
                    label = item.get("label", item.get("id"))
                    if label:
                        concepts.append({"id": cid, "label": label})

    if not concepts:
        raise ValueError("No concepts could be loaded from the provided file.")

    return concepts


# --------------------------------------------------
# LLM prompts
# --------------------------------------------------

def call_json_prompt(
    client: InferenceClient,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1200,
    temperature: float = 0.0,
) -> Optional[dict]:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return safe_json_load(resp.choices[0].message.content)


def prompt_clean_window(client: InferenceClient, model: str, text: str) -> str:
    system = "You clean noisy educational transcript segments. Return JSON only."
    user = f"""
Clean this transcript fragment to keep only educational content.

Rules:
- Remove filler words, repetitions, conversational artifacts, and ASR noise.
- Preserve the educational meaning.
- Do NOT add information.
- Keep the same language as the input.
- Keep terminology intact.

Return ONLY valid JSON:
{{
  "clean_text": "..."
}}

Fragment:
<<<{text}>>>
"""
    data = call_json_prompt(client, model, system, user, max_tokens=1000, temperature=0.0)
    if not data or "clean_text" not in data:
        return text
    return data["clean_text"].strip()


def prompt_identify_concepts(
    client: InferenceClient,
    model: str,
    clean_text: str,
    concepts: List[dict],
) -> dict:
    system = "You identify educational concepts explained in transcript segments. Return JSON only."
    user = f"""
Given the cleaned educational fragment and the concept list, identify which concepts are actually EXPLAINED in this fragment.

Rules:
- A concept is "explained" if the fragment gives a definition, description, relation, function, part, property, or pedagogically useful explanation.
- Do NOT mark a concept as explained if it is only mentioned in passing.
- Use ONLY the provided concept IDs.
- Do NOT use outside knowledge.
- Return a short summary of the fragment.
- Return a question_worthy score from 0 to 1.
- Return ONLY valid JSON.

Concept list:
{json.dumps(concepts, ensure_ascii=False, indent=2)}

Return this schema:
{{
  "summary": "...",
  "question_worthy": 0.0,
  "explained_concepts": [
    {{
      "id": "C1",
      "label": "...",
      "explanation_strength": "high|medium|low",
      "evidence": "short quote"
    }}
  ]
}}

Fragment:
<<<{clean_text}>>>
"""
    data = call_json_prompt(client, model, system, user, max_tokens=1400, temperature=0.0)
    if not data:
        return {"summary": "", "question_worthy": 0.0, "explained_concepts": []}
    if "explained_concepts" not in data:
        data["explained_concepts"] = []
    if "summary" not in data:
        data["summary"] = ""
    if "question_worthy" not in data:
        data["question_worthy"] = 0.0
    return data


# --------------------------------------------------
# Merge adjacent windows into larger segments
# --------------------------------------------------

def concept_id_set(window: dict) -> set:
    return {c["id"] for c in window.get("explained_concepts", []) if isinstance(c, dict) and "id" in c}


def merge_windows(
    windows: List[dict],
    min_question_worthy: float = 0.35,
    overlap_threshold: float = 0.4,
    max_gap_sec: int = 35,
    max_segment_chars: int = 2600,
) -> List[dict]:
    """
    Merge adjacent windows when they cover similar concepts and remain coherent.
    """
    filtered = [w for w in windows if w.get("question_worthy", 0.0) >= min_question_worthy and w.get("explained_concepts")]
    if not filtered:
        return []

    merged = [filtered[0]]

    for w in filtered[1:]:
        prev = merged[-1]

        prev_ids = concept_id_set(prev)
        curr_ids = concept_id_set(w)

        inter = len(prev_ids & curr_ids)
        union = len(prev_ids | curr_ids) if (prev_ids | curr_ids) else 1
        overlap = inter / union

        gap = w["start_sec"] - prev["end_sec"]

        can_merge = (
            gap <= max_gap_sec
            and overlap >= overlap_threshold
            and len(prev["clean_text"]) + len(w["clean_text"]) <= max_segment_chars
        )

        if can_merge:
            prev["end_time"] = w["end_time"]
            prev["end_sec"] = w["end_sec"]
            prev["raw_text"] = prev["raw_text"] + " " + w["raw_text"]
            prev["clean_text"] = prev["clean_text"] + " " + w["clean_text"]

            combined = {}
            for item in prev["explained_concepts"] + w["explained_concepts"]:
                cid = item["id"]
                existing = combined.get(cid)
                if existing is None:
                    combined[cid] = item
                else:
                    rank = {"high": 3, "medium": 2, "low": 1}
                    if rank.get(item.get("explanation_strength", "low"), 1) > rank.get(existing.get("explanation_strength", "low"), 1):
                        combined[cid] = item

            prev["explained_concepts"] = list(combined.values())
            prev["question_worthy"] = max(prev["question_worthy"], w["question_worthy"])
            if w.get("summary"):
                prev["summary"] = (prev.get("summary", "") + " " + w["summary"]).strip()
        else:
            merged.append(w)

    return merged


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", required=True, help="Timestamped transcript .txt")
    parser.add_argument("--concepts", required=True, help="Concepts JSON file")
    parser.add_argument("--out", default="segments_with_concepts.json")
    parser.add_argument("--model", default="Qwen/Qwen2.5-14B-Instruct")
    parser.add_argument("--window-max-chars", type=int, default=1200)
    parser.add_argument("--window-max-gap-sec", type=int, default=20)
    parser.add_argument("--merge-gap-sec", type=int, default=35)
    parser.add_argument("--merge-overlap-threshold", type=float, default=0.4)
    parser.add_argument("--min-question-worthy", type=float, default=0.35)
    args = parser.parse_args()

    token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN environment variable.")

    with open(args.transcript, "r", encoding="utf-8") as f:
        raw = f.read()

    concepts = load_concepts(args.concepts)
    blocks = parse_timestamped_transcript(raw)
    windows = build_initial_windows(
        blocks,
        max_chars=args.window_max_chars,
        max_gap_sec=args.window_max_gap_sec,
    )

    client = InferenceClient(token=token)

    analyzed_windows = []
    for i, w in enumerate(windows, start=1):
        clean_text = prompt_clean_window(client, args.model, w["text"])
        concept_result = prompt_identify_concepts(client, args.model, clean_text, concepts)

        analyzed = {
            "start_time": w["start_time"],
            "end_time": w["end_time"],
            "start_sec": w["start_sec"],
            "end_sec": w["end_sec"],
            "raw_text": w["text"],
            "clean_text": clean_text,
            "summary": concept_result.get("summary", ""),
            "question_worthy": float(concept_result.get("question_worthy", 0.0)),
            "explained_concepts": concept_result.get("explained_concepts", []),
        }
        analyzed_windows.append(analyzed)
        print(f"[{i}/{len(windows)}] {w['start_time']} - {w['end_time']} | concepts={len(analyzed['explained_concepts'])}")

    merged_segments = merge_windows(
        analyzed_windows,
        min_question_worthy=args.min_question_worthy,
        overlap_threshold=args.merge_overlap_threshold,
        max_gap_sec=args.merge_gap_sec,
    )

    final_segments = []
    for idx, seg in enumerate(merged_segments, start=1):
        concept_ids = [c["id"] for c in seg["explained_concepts"]]
        concept_labels = [c["label"] for c in seg["explained_concepts"]]

        final_segments.append({
            "segment_id": f"S{idx}",
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "duration_seconds": max(0, seg["end_sec"] - seg["start_sec"]),
            "question_worthy": seg["question_worthy"],
            "summary": seg["summary"],
            "concept_ids": concept_ids,
            "concept_labels": concept_labels,
            "concept_details": seg["explained_concepts"],
            "clean_text": normalize_text(seg["clean_text"]),
        })

    output = {
        "segments": final_segments,
        "meta": {
            "model": args.model,
            "initial_windows": len(windows),
            "final_segments": len(final_segments),
            "concept_count": len(concepts),
        }
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    with open("windows_debug.json", "w", encoding="utf-8") as f:
        json.dump({"windows": analyzed_windows}, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(f"Initial windows: {len(windows)}")
    print(f"Final segments: {len(final_segments)}")
    print(f"Wrote: {args.out}")
    print("Wrote: windows_debug.json")


if __name__ == "__main__":
    main()