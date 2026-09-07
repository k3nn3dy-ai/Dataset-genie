# stage: taxonomy
You are designing the coverage map for a fine-tuning dataset: propose exactly {{ n }} distinct
top-level **topics** that together cover the domain below.

## Domain brief
{{ brief }}

## Task
Each topic should be a short noun phrase (2–6 words), concrete enough that a specialist would
recognise it, and non-overlapping with the others.
{% if negative %}

Additionally add exactly {{ negative_n }} **out-of-scope** topic marked `is_negative: true`. It should
describe plausible requests that fall outside the domain (adjacent, tempting, or unsafe asks)
so the model learns to redirect rather than comply.
{% endif %}

Return JSON of the form `{"nodes": [{"label": "...", "is_negative": false}, ...]}` and nothing else.
