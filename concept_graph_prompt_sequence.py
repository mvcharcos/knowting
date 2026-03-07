import json
import os
import re
from huggingface_hub import InferenceClient

HF_TOKEN = os.environ["HF_TOKEN"]
MODEL = "Qwen/Qwen2.5-14B-Instruct"

client = InferenceClient(token=HF_TOKEN)

def call_json_prompt(system_prompt: str, user_prompt: str, model: str = MODEL, max_tokens: int = 2000):
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)

def clean_transcript(transcript: str):
    system = "You clean noisy educational transcripts. Return JSON only."
    user = f"""
You are cleaning an automatic transcript from an educational video.

Task:
Rewrite the transcript into a clean educational version.

Rules:
- Keep ONLY educational content.
- Remove filler words, hesitations, repeated fragments, transcription noise, and conversational artifacts.
- Preserve the original meaning.
- Do NOT add information that is not present in the text.
- Keep terminology exactly when possible.
- Keep the output in the same language as the input.
- Organize the result into clear paragraphs.

Output:
{{
  "clean_text": "..."
}}

Transcript:
<<<{transcript}>>>
"""
    return call_json_prompt(system, user)

def extract_scope(clean_text: str, subject_matter: str):
    system = "You extract subject-relevant educational content. Return JSON only."
    user = f"""
From the cleaned transcript, identify only the content that belongs to the subject matter: "{subject_matter}".

Rules:
- Keep only material relevant to the requested subject matter.
- Remove examples, comments, or side remarks not central to the subject.
- Do NOT add outside knowledge.
- Keep the output in the same language as the input.

Output:
{{
  "educational_content": "...",
  "excluded_content_summary": ["...", "..."]
}}

Cleaned transcript:
<<<{clean_text}>>>
"""
    return call_json_prompt(system, user)

def extract_concepts(educational_content: str):
    system = "You extract concepts from educational material. Return JSON only."
    user = f"""
Identify the main concepts explicitly present in the text.

Rules:
- Extract only concepts explicitly mentioned or clearly defined in the text.
- Prefer domain concepts over generic words.
- Keep multi-word concepts when needed.
- Include aliases only if the text clearly suggests they refer to the same concept.
- Do NOT use outside knowledge.
- Do NOT infer missing concepts.

Output:
{{
  "concepts": [
    {{
      "id": "C1",
      "label": "...",
      "aliases": ["...", "..."],
      "description": "...",
      "evidence": "short quote from text"
    }}
  ]
}}

Text:
<<<{educational_content}>>>
"""
    return call_json_prompt(system, user)

def extract_relations(educational_content: str, concepts_json: dict):
    system = "You extract concept graph relations from educational material. Return JSON only."
    user = f"""
Using only the concepts provided, extract directed relations that are explicitly supported by the text.

Allowed relation types:
- is_a
- part_of
- has_part
- depends_on
- causes
- used_for
- related_to

Rules:
- Use ONLY the provided concept IDs.
- Create a relation only if the text supports it.
- Do NOT use outside knowledge.
- Prefer specific relations over related_to.
- Include a short evidence quote copied from the text.

Output:
{{
  "relations": [
    {{
      "source": "C1",
      "relation": "has_part",
      "target": "C2",
      "evidence": "..."
    }}
  ]
}}

Concepts:
<<<{json.dumps(concepts_json, ensure_ascii=False)}>>>

Text:
<<<{educational_content}>>>
"""
    return call_json_prompt(system, user)

def extract_facts(educational_content: str, concepts_json: dict):
    system = "You extract explicit educational facts. Return JSON only."
    user = f"""
Extract atomic facts explicitly stated in the text and link them to the provided concepts.

Rules:
- Extract only explicit facts, not inferred ones.
- Each fact must be atomic and useful for learning.
- Use only the provided concept IDs.
- Include a short evidence quote copied from the text.
- Do NOT use outside knowledge.

Output:
{{
  "facts": [
    {{
      "id": "F1",
      "concept_id": "C1",
      "fact": "...",
      "evidence": "..."
    }}
  ]
}}

Concepts:
<<<{json.dumps(concepts_json, ensure_ascii=False)}>>>

Text:
<<<{educational_content}>>>
"""
    return call_json_prompt(system, user)

def build_graph(transcript: str, subject_matter: str):
    cleaned = clean_transcript(transcript)
    scoped = extract_scope(cleaned["clean_text"], subject_matter)
    concepts = extract_concepts(scoped["educational_content"])
    relations = extract_relations(scoped["educational_content"], concepts)
    facts = extract_facts(scoped["educational_content"], concepts)

    return {
        "clean_text": cleaned["clean_text"],
        "educational_content": scoped["educational_content"],
        "concepts": concepts["concepts"],
        "relations": relations["relations"],
        "facts": facts["facts"],
    }