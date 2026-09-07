# stage: simulated_user
You are role-playing the **user** in a conversation with an expert assistant. Write the user's
next follow-up message only — no narration, no quotes, no "User:" label.

Mood: **{{ mood }}** — {{ mood_guide }}

Keep the follow-up short (one to three sentences), grounded in what the assistant just said
(react to it, report a result, ask the natural next question), and consistent with the original
request.

## Conversation so far
{% for m in transcript %}
**{{ m.role }}:** {{ m.content }}

{% endfor %}
