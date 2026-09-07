# stage: tool_simulator
You simulate the backend of the tool `{{ name }}`. The assistant has just called it; produce the
JSON result the real tool would return — plausible, internally consistent with the conversation,
and shaped like the tool's declared output. Return **only** a JSON object, no prose.

## Tool declaration
```json
{{ schema_json }}
```

## Call arguments
```json
{{ arguments }}
```

## Conversation context
{% for m in transcript %}
**{{ m.role }}:** {{ m.content }}
{% endfor %}
