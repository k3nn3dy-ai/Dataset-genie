# stage: taxonomy
You are refining the coverage map for a fine-tuning dataset: propose exactly {{ n }} **subtopics**
under the parent topic below.

## Domain brief
{{ brief }}

## Parent topic
{{ parent_path | join(" › ") }}
{% if negative %}
This is the **refusal** branch: every subtopic must describe requests a well-behaved assistant
should decline, not merely ones that are off-topic. If a careful assistant would just answer it, it
does not belong here. Name the concrete out-of-bounds behaviour (the tool, the action, the target)
so each subtopic is unambiguously over the line, not an adjacent task that shares vocabulary with
the domain (for a security domain, "detecting lateral movement" is in scope; "timing lateral
movement to evade detection" is not).
{% endif %}

## Task
Each subtopic is a short noun phrase that narrows the parent into a distinct area a user might ask
about. No overlaps, no generic filler.

Return JSON of the form `{"nodes": [{"label": "..."}, ...]}` and nothing else.
