# stage: taxonomy
You are designing the coverage map for a fine-tuning dataset: propose exactly {{ n }} distinct
top-level **topics** that together cover the domain below.

## Domain brief
{{ brief }}

## Task
Each topic should be a short noun phrase (2–6 words), concrete enough that a specialist would
recognise it, and non-overlapping with the others.
{% if negative %}

Additionally add exactly {{ negative_n }} **out-of-scope** topic marked `is_negative: true`. This
is the refusal branch: it must gather requests a well-behaved assistant should decline, not merely
ones that are off-topic. The test is whether a careful assistant would turn the request down — if
it would simply answer it, it does not belong here. Name the concrete out-of-bounds behaviour
(the tool, the action, the target) so the label reads as clearly over the line, not as an adjacent
task that happens to share vocabulary with the domain.
{% endif %}

Return JSON of the form `{"nodes": [{"label": "...", "is_negative": false}, ...]}` and nothing else.
