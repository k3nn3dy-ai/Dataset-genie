# stage: taxonomy
You are writing the leaves of a coverage map for a fine-tuning dataset: propose exactly {{ n }}
**leaf** scenarios under the parent below. Each leaf becomes a bucket of {{ rows_per_leaf }} training
examples, so it must be specific.

## Domain brief
{{ brief }}

## Parent
{{ parent_path | join(" › ") }}
{% if negative %}
This is the **refusal** branch: each leaf must be a request a well-behaved assistant should
decline, not merely one that is off-topic. The test is whether a careful assistant would turn it
down — if it would simply answer, the leaf is wrong for this branch. Name the concrete
out-of-bounds behaviour (the tool, the action, the target) so the leaf is unambiguously over the
line, not an adjacent task that shares vocabulary with the domain. Describe the out-of-bounds
request; do not write any harmful content in the label itself.
{% endif %}

## Task
Each leaf is a concrete situation or question type (e.g. "pod stuck in CrashLoopBackOff after config change").

For each leaf also choose:
- `task_type`: one of {{ task_types | join(", ") }}
{% if tiers %}
- `difficulty`: one of easy, medium, hard — spread them across the leaves, do not make them all medium
{% endif %}

Return JSON of the form
`{"nodes": [{"label": "...", "task_type": "...", {% if tiers %}"difficulty": "...", {% endif %}"is_negative": {{ "true" if negative else "false" }}}, ...]}`
and nothing else.
