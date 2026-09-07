# stage: taxonomy
You are refining the coverage map for a fine-tuning dataset: propose exactly {{ n }} **subtopics**
under the parent topic below.

## Domain brief
{{ brief }}

## Parent topic
{{ parent_path | join(" › ") }}
{% if negative %}
This is an **out-of-scope** branch: every subtopic must describe requests the assistant should
decline or redirect (adjacent but not part of the domain, unsafe, or off-purpose).
{% endif %}

## Task
Each subtopic is a short noun phrase that narrows the parent into a distinct area a user might ask
about. No overlaps, no generic filler.

Return JSON of the form `{"nodes": [{"label": "..."}, ...]}` and nothing else.
