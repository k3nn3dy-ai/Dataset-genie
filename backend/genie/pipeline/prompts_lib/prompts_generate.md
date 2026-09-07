# stage: prompts
You write realistic user messages for a fine-tuning dataset. Write exactly {{ specs | length }}
messages. Each message is what a real person would type to an expert assistant — with their own
vocabulary, goals and level of detail.

## Domain brief
{{ brief }}

## Scenario
Leaf: {{ leaf_path[-1] }}
Path: {{ leaf_path | join(" › ") }}
{% if difficulty %}Difficulty: {{ difficulty }}{% endif %}
{% if task_type %}Task type: {{ task_type }}{% endif %}
{% if negative %}
This scenario is **out of scope** for the assistant: write requests that a user might plausibly
send anyway (adjacent, tempting, or off-purpose), so the assistant can learn to redirect.
{% endif %}

## The {{ specs | length }} messages, one per spec, in this order
{% for s in specs %}
{{ loop.index }}. Persona: **{{ s.persona }}** ({{ s.persona_style }}). Form: **{{ s.style }}** — {{ s.style_guide }}.
{% if s.adversarial %}   Adversarial: this user tries to push the assistant off its guidelines (asks for something
   it should not do, embeds an instruction to ignore the system prompt, or pressures for certainty
   where none exists). Keep it natural, not cartoonish.
{% endif %}
{% endfor %}

Rules:
- Each message must be self-contained and concrete (real-sounding names, values, error text).
- No two messages may ask essentially the same thing.
- Do not answer the messages. Do not add labels, numbering or quotes around them.

Return JSON of the form `{"prompts": ["message 1", "message 2", ...]}` and nothing else.
