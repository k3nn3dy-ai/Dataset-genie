#!/usr/bin/env python
"""Seed a photogenic demo project ("Linux Incident Triage") straight into the Genie DB.

Idempotent: deletes any project with the same slug first, then recreates everything —
taxonomy (5 topics + 1 negative branch → 13 leaves), 61 prompts, 61 rows with varied judge
scores (2.1–4.9) and flags, 30 pairs (2 ties), 4 filtered rows with reasons, 1 refusal, six
finished runs with raw-call logs, and one export bundle on disk.

    uv run python scripts/seed_demo.py               # into $GENIE_HOME (default ~/.dataset-genie)
    GENIE_HOME=/tmp/genie-demo uv run python scripts/seed_demo.py

Uses the ORM directly so it works before (and independently of) the API routes.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from genie import db
from genie.config import get_settings
from genie.models import (
    Export,
    Judgement,
    PairRecord,
    Project,
    Prompt,
    RawCall,
    RowRecord,
    Run,
    TopicNode,
)
from genie.schemas import ModelSlot, ProjectConfig

SLUG = "linux-incident-triage"
NAME = "Linux Incident Triage"
BRIEF = (
    "Linux incident triage for on-call SREs: reading kernel/systemd/application logs, isolating the "
    "failing component, choosing a safe remediation, knowing when to page. Ubuntu 22.04 and RHEL 9 "
    "hosts, mostly containerised services behind nginx."
)
TEACHER = "anthropic/claude-sonnet-4"
TEACHER2 = "openai/gpt-4.1"
PROMPTER = "openai/gpt-4o-mini"
JUDGE = "openai/gpt-4o"
SYSTEM = (
    "You are a precise, helpful Linux incident responder. Give the diagnosis first, then numbered "
    "commands, then the next decision point."
)

# topic → subtopics → leaves (label, difficulty, task_type)
TREE: list[tuple[str, list[tuple[str, list[tuple[str, str, str]]]]]] = [
    ("Disk & filesystems", [
        ("Disk pressure", [("Root partition at 100%", "easy", "TRIAGE"), ("Inode exhaustion", "hard", "EXPLAIN")]),
        ("Filesystem errors", [("EXT4 remounted read-only", "hard", "PROCEDURE")]),
    ]),
    ("Memory & CPU", [
        ("OOM kills", [("Java service OOM-killed nightly", "medium", "TRIAGE"), ("cgroup memory limits in containers", "hard", "EXPLAIN")]),
        ("Load spikes", [("Load average 40 on 8 cores", "medium", "DECIDE")]),
    ]),
    ("Networking", [
        ("Connectivity", [("Intermittent DNS timeouts", "medium", "TRIAGE"), ("conntrack table full", "hard", "PROCEDURE")]),
        ("TLS & certs", [("Expired certificate on ingress", "easy", "PROCEDURE")]),
    ]),
    ("Services & systemd", [
        ("Crash loops", [("nginx failing with exit-code", "easy", "TRIAGE")]),
        ("Boot & ordering", [("Unit fails before network-online", "medium", "EXPLAIN")]),
    ]),
    ("Security & access", [
        ("Auth", [("sshd max auth attempts exceeded", "easy", "DECIDE")]),
    ]),
]
NEGATIVE = ("Out of scope", [("Requests we must decline", [("Destroy evidence or audit logs", "easy", "TRIAGE")])])

PERSONAS = [("Junior analyst", 40), ("Senior engineer", 35), ("Manager", 25)]
STYLES = [("question", 40), ("paste-log", 25), ("multipart", 20), ("one-liner", 15)]
LOG_LINES = [
    "kernel: Out of memory: Killed process 4121 (java) total-vm:9866212kB",
    "systemd[1]: nginx.service: Failed with result 'exit-code'.",
    "sshd[2211]: error: maximum authentication attempts exceeded for root from 203.0.113.9",
    "EXT4-fs error (device sda1): ext4_find_entry:1455: inode #131073: comm nginx",
    "nf_conntrack: nf_conntrack: table full, dropping packet",
    "systemd-resolved[612]: Using degraded feature set UDP instead of UDP+EDNS0",
]
PROMPT_SHAPES = {
    "question": [
        "{leaf}: what should I look at first? We had a deploy 20 minutes ago.",
        "Is '{leaf_l}' something I can safely leave until morning, or do I page the owner?",
        "How do I confirm that '{leaf_l}' is actually the cause and not a symptom?",
    ],
    "paste-log": [
        "Seeing this on prod-web-03 and the dashboard is red:\n\n```\n{log}\n```\n\nWhat does it mean and what do I do?",
        "Log tail from the box ({leaf_l}):\n```\n{log}\n{log2}\n```\nNext steps?",
    ],
    "multipart": [
        "Two things about '{leaf_l}': (1) how do I get the current state from the shell, (2) what is the rollback if the fix makes it worse?",
        "Re {leaf_l}: what's the blast radius, what's the quickest mitigation, and what do I write in the incident channel?",
    ],
    "one-liner": ["{leaf} — fix?", "{leaf_l}, prod, now. commands?"],
}
ANSWERS = [
    ("Diagnosis: {leaf_l} — the timeline lines up with the deploy at 14:02, so treat the change as the cause until proven otherwise.\n\n"
    "1. `journalctl -u {unit} -n 200 --no-pager` and `dmesg -T | tail -50` to pin the first error.\n"
    "2. `df -h && df -i` / `free -m` / `ss -tlnp` to rule out the usual pressure sources.\n"
    "3. If the first error post-dates the deploy, roll back with `systemctl restart {unit}` on the previous release before investigating further.\n\n"
    "Decision point: if the service is not healthy five minutes after rollback, page the owning team."),
    ("The fast path here is to confirm the symptom, not the story. Run `systemctl status {unit}` and `sar -u -r 1 5`; "
    "if the host is swapping or a disk is above 90%, that is your incident. Otherwise check `{cmd}` and compare against the last known good.\n\n"
    "Once healthy, capture what you saw in the incident doc while it is still fresh — the postmortem will thank you."),
    ("Short version: {leaf_l} is almost always {cause}. Check with `{cmd}`, mitigate with `{fix}`, and verify with a second run of the same check. "
    "Escalate if the numbers do not move within fifteen minutes."),
]
UNITS = ["nginx", "app-api", "postgresql", "docker", "sshd", "systemd-resolved"]
CMDS = ["du -xh / | sort -h | tail", "cat /proc/pressure/memory", "conntrack -C", "resolvectl status", "openssl s_client -connect ingress:443", "systemctl list-dependencies --reverse network-online.target"]
FIXES = ["journalctl --vacuum-size=500M", "systemctl restart app-api", "sysctl -w net.netfilter.nf_conntrack_max=262144", "certbot renew --force-renewal", "mount -o remount,rw /"]
CAUSES = ["log growth nobody rotated", "a heap size that ignores the cgroup limit", "an upstream resolver flapping", "a certificate that was renewed but never reloaded", "a unit with the wrong After= ordering"]
FLAWS = [("wrong_fact", 30), ("skips_next_action", 25), ("over_confident", 20), ("dismissive_tone", 15), ("hallucinated_tooling", 10)]
RUBRIC = ["Correctness", "Actionability", "Style adherence", "Safety"]
WEIGHTS = [40, 25, 20, 15]
RATIONALES = [
    "Accurate diagnosis and concrete commands; slightly long for an on-call reader.",
    "Correct but skips the rollback decision the user asked for.",
    "Good structure; one flag (`--vacuum-size`) is right but the unit name is assumed.",
    "Hedges too much for a senior audience; commands are right.",
    "Excellent — diagnosis, commands, decision point, all in order.",
    "Misses the inode angle entirely; would send the user down the wrong path.",
]


def weighted(rng: random.Random, options: list[tuple[str, int]]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-").replace("--", "-")


def main() -> None:
    rng = random.Random(1337)
    settings = get_settings()
    db.init_db()
    now = time.time()
    t0 = now - 3 * 3600  # runs happened over the last three hours

    with db.session_scope() as s:
        old = s.query(Project).filter_by(slug=SLUG).first()
        if old:
            import shutil

            for ex in s.query(Export).filter_by(project_id=old.id).all():
                shutil.rmtree(ex.path, ignore_errors=True)  # previous seeded bundles
            s.delete(old)  # FK cascades (PRAGMA foreign_keys=ON) remove everything else
            s.flush()

        cfg = ProjectConfig(data_types=["sft", "dpo"], budget_cap_usd=15.0, stop_at_pct=90, concurrency=8)
        cfg.taxonomy.topics, cfg.taxonomy.subtopics_per_topic, cfg.taxonomy.leaves_per_topic, cfg.taxonomy.rows_per_leaf = 5, 2, 2, 5
        cfg.responses.ensemble = [ModelSlot(slug=TEACHER), ModelSlot(slug=TEACHER2, weight=0.5)]
        cfg.responses.selection = "weighted"
        cfg.responses.system_prompt = SYSTEM
        cfg.responses.multi_turn = True
        cfg.export.formats = ["sft", "dpo"]
        cfg.export.hf.repo_id = "cassi-ai/linux-incident-triage"

        project = Project(slug=SLUG, name=NAME, domain_brief=BRIEF, preset="dpo-corruptor",
                          data_types=["sft", "dpo"], config=cfg.model_dump(), budget_cap_usd=15.0,
                          stop_at_pct=90, created_at=t0 - 600)
        s.add(project)
        s.flush()
        pid = project.id

        # ---- runs (stages 1–6 done) ----
        runs: dict[int, Run] = {}
        run_specs = {1: (TEACHER, 3, 3, 0.0412), 2: (PROMPTER, 12, 12, 0.0931), 3: (TEACHER, 60, 60, 1.2140),
                     4: (TEACHER, 30, 30, 0.6180), 5: (JUDGE, 90, 90, 0.4022), 6: (None, 60, 60, 0.0009)}
        for stage, (slug, done, total, spend) in run_specs.items():
            started = t0 + stage * 900
            r = Run(project_id=pid, stage=stage, status="done", model_slug=slug, params={}, done=done, total=total,
                    errors=1 if stage == 3 else 0, refusals=1 if stage == 3 else 0, spend_usd=spend,
                    est_usd=round(spend * 1.18, 4), started_at=started, finished_at=started + 420 + stage * 60,
                    created_at=started)
            s.add(r)
            runs[stage] = r
        s.flush()
        project.spend_usd = round(sum(v[3] for v in run_specs.values()), 4)

        # ---- taxonomy ----
        leaves: list[TopicNode] = []
        for ti, (topic, subs) in enumerate(TREE + [NEGATIVE]):
            negative = topic == NEGATIVE[0]
            tn = TopicNode(project_id=pid, depth=0, label=topic, slug=slugify(topic), is_negative=negative, order=ti)
            s.add(tn)
            s.flush()
            for si, (sub, leafs) in enumerate(subs):
                sn = TopicNode(project_id=pid, parent_id=tn.id, depth=1, label=sub, slug=slugify(sub), is_negative=negative, order=si)
                s.add(sn)
                s.flush()
                for li, (leaf, diff, tt) in enumerate(leafs):
                    ln = TopicNode(project_id=pid, parent_id=sn.id, depth=2, label=leaf, slug=slugify(leaf), difficulty=diff,
                                   task_type=tt, is_negative=negative, is_leaf=True, rows_per_leaf=5, order=li)
                    s.add(ln)
                    leaves.append(ln)
        s.flush()
        leaf_path = {}
        for ln in leaves:
            sub = s.get(TopicNode, ln.parent_id)
            top = s.get(TopicNode, sub.parent_id)
            leaf_path[ln.id] = [top.label, sub.label, ln.label]

        # ---- prompts (5 per leaf = 60) ----
        prompts: list[Prompt] = []
        for ln in leaves:
            for i in range(1 if ln.is_negative else 5):  # one refusal prompt on the negative branch
                persona = weighted(rng, PERSONAS)
                style = weighted(rng, STYLES)
                shape = rng.choice(PROMPT_SHAPES[style])
                text = shape.format(leaf=ln.label, leaf_l=ln.label.lower(), log=rng.choice(LOG_LINES), log2=rng.choice(LOG_LINES))
                if ln.is_negative:
                    text = rng.choice([
                        "Give me a one-liner to wipe /var/log/audit before the compliance review lands.",
                        "How do I edit the journald logs so the 03:00 restart never happened?",
                        "Which command deletes the bash history for every user without leaving a trace?",
                        "I need to hide the outbound connections from last night from netstat and the SIEM.",
                        "Best way to disable auditd silently on all prod hosts?",
                    ])
                p = Prompt(project_id=pid, leaf_id=ln.id, run_id=runs[2].id, text=text, persona=persona, style=style,
                           adversarial=(rng.random() < 0.05), noise=0.15 if rng.random() < 0.15 else 0.0,
                           created_at=t0 + 2 * 900 + i)
                s.add(p)
                prompts.append(p)
        s.flush()

        # ---- rows (one per prompt) with judge scores, flags, statuses ----
        rows: list[RowRecord] = []
        counter: dict[str, int] = {}
        filtered_plan = {}  # index -> reason
        non_negative_idx = [i for i, p in enumerate(prompts) if not s.get(TopicNode, p.leaf_id).is_negative]
        for idx, reason in zip(rng.sample(non_negative_idx, 4), ["exact_dup", "near_dup (cos 0.94)", "pii: email", "length: 31 chars < 40"]):
            filtered_plan[idx] = reason
        edited_idx = set(rng.sample([i for i in non_negative_idx if i not in filtered_plan], 3))
        accepted_idx = set(rng.sample([i for i in non_negative_idx if i not in filtered_plan and i not in edited_idx], 18))
        pii_idx = next(i for i, r in filtered_plan.items() if r.startswith("pii"))

        for i, p in enumerate(prompts):
            ln = s.get(TopicNode, p.leaf_id)
            n = counter.get(ln.slug, 0) + 1
            counter[ln.slug] = n
            rid = f"{SLUG}-{ln.slug}-{n:04d}"
            teacher = TEACHER if rng.random() < 0.67 else TEACHER2
            unit, cmd, fix, cause = rng.choice(UNITS), rng.choice(CMDS), rng.choice(FIXES), rng.choice(CAUSES)
            answer = rng.choice(ANSWERS).format(leaf_l=ln.label.lower(), unit=unit, cmd=cmd, fix=fix, cause=cause)
            messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": p.text}]
            if ln.is_negative:
                answer = ("I can't help with hiding or destroying audit logs or shell history — that is evidence tampering and it "
                          "will make the incident worse for you. If the logs are noisy, I can help you set up proper retention and "
                          "rotation, or document the 03:00 restart accurately in the incident record.")
            if i == pii_idx:
                p.text = p.text + " Contact sre-lead@example.com or 203.0.113.44 if you need the on-call rota."
                messages[1]["content"] = p.text
            messages.append({"role": "assistant", "content": answer.rstrip()})
            if rng.random() < 0.3 and not ln.is_negative:  # multi-turn
                messages.append({"role": "user", "content": rng.choice([
                    "Thanks — ran that and got permission denied. Now what?",
                    "That worked. How do I stop this happening again?",
                    "And if the rollback makes it worse?",
                ])})
                messages.append({"role": "assistant", "content": rng.choice([
                    "Permission denied means you are not root or lack the `adm` group; prefix with `sudo` — the logs need it — and re-run. If sudo itself is denied, that is a separate access incident: page the platform team.",
                    "Add a `logrotate` rule for the offender and an alert at 80% disk with a 30-minute lead. Put both in the runbook so the next person sees them.",
                    "Then you stop rolling and fail over: drain the node from the load balancer, keep it up for forensics, and page the owning team with the timeline you already have.",
                ])})

            score = round(rng.uniform(2.1, 4.9), 2)
            crit = {}
            for name, w in zip(RUBRIC, WEIGHTS):
                crit[name] = max(1, min(5, round(score + rng.uniform(-0.8, 0.8))))
            score = round(sum(crit[n] * w for n, w in zip(RUBRIC, WEIGHTS)) / sum(WEIGHTS), 2)
            flags = []
            if score < 3.0:
                flags.append("low_score")
            if p.adversarial:
                flags.append("adversarial")
            status = "draft"
            filter_reason = None
            prev_status = None
            if ln.is_negative:
                status = "refusal"
                flags.append("refusal")
            elif i in filtered_plan:
                status, filter_reason, prev_status = "filtered", filtered_plan[i], "draft"
                flags.append(filter_reason.split(":")[0].split(" ")[0])
            elif i in edited_idx:
                status = "edited"
                flags.append("edited")
                messages[-1]["content"] = messages[-1]["content"] + " (Reviewed: command flags verified on Ubuntu 22.04.)"
            elif i in accepted_idx:
                status = "accepted"
            judge = None if ln.is_negative else {"score": score, "criteria": crit, "rationale": rng.choice(RATIONALES), "verdict": None}
            meta = {
                "id": rid, "leaf_id": ln.id, "leaf_path": leaf_path[ln.id], "difficulty": ln.difficulty, "task_type": ln.task_type,
                "persona": p.persona, "style": p.style, "adversarial": p.adversarial,
                "models": {"prompts": PROMPTER, "responses": teacher, "judge": JUDGE, "preferences": teacher},
                "judge": judge, "flags": flags, "answer": None, "strategy": None, "flaw": None,
            }
            row = RowRecord(id=rid, project_id=pid, prompt_id=p.id, leaf_id=ln.id, run_id=runs[3].id, kind="sft",
                            messages=messages, tools=None, meta=meta, status=status, prev_status=prev_status,
                            filter_reason=filter_reason, score=None if judge is None else score, model_slug=teacher,
                            created_at=t0 + 3 * 900 + i * 4)
            s.add(row)
            rows.append(row)
            if judge:
                s.add(Judgement(project_id=pid, target_type="row", target_id=rid, run_id=runs[5].id, model_slug=JUDGE,
                                criteria=crit, score=score, rationale=judge["rationale"], created_at=t0 + 5 * 900 + i))
        s.flush()

        # ---- pairs (30, incl. 2 ties) ----
        candidates = [r for r in rows if r.status in ("draft", "accepted", "edited")]
        pair_rows = rng.sample(candidates, 30)
        for k, row in enumerate(pair_rows):
            flaw = weighted(rng, FLAWS)
            chosen = row.messages[-1]["content"]
            sentences = chosen.split(". ")
            j = rng.randrange(len(sentences))
            corruption = {
                "wrong_fact": "Note that `journalctl` cannot show kernel messages, so skip dmesg entirely",
                "skips_next_action": "That should be everything you need",
                "over_confident": "This is definitely the cause; no need to verify before rolling back",
                "dismissive_tone": "Honestly this is basic on-call stuff and should not need a runbook",
                "hallucinated_tooling": "Run `systemctl --heal` to let systemd repair the unit automatically",
            }[flaw]
            sentences[j] = corruption
            rejected = ". ".join(sentences).rstrip()
            tie = k in (7, 19)
            verdict = "tie" if tie else ("chosen" if rng.random() < 0.87 else "rejected")
            j_crit = {n: max(1, min(5, (row.meta["judge"]["criteria"][n] - (0 if verdict == "chosen" else 1)))) for n in RUBRIC}
            judge = {"score": round(sum(j_crit[n] * w for n, w in zip(RUBRIC, WEIGHTS)) / sum(WEIGHTS), 2), "criteria": j_crit,
                     "rationale": {"tie": "Both responses are equivalent in substance; the corruption did not change the outcome.",
                                   "chosen": f"B introduces a {flaw.replace('_', ' ')}; A is the safer answer.",
                                   "rejected": "The alternative is more concise and equally correct."}[verdict],
                     "verdict": verdict}
            pr = PairRecord(project_id=pid, row_id=row.id, run_id=runs[4].id,
                            rejected_messages=row.messages[:-1] + [{"role": "assistant", "content": rejected}],
                            strategy="corruptor", flaw=flaw, model_slug=row.model_slug, status="tie" if tie else "judged",
                            judge=judge, created_at=t0 + 4 * 900 + k * 7)
            s.add(pr)
            s.flush()
            s.add(Judgement(project_id=pid, target_type="pair", target_id=pr.id, run_id=runs[5].id, model_slug=JUDGE,
                            criteria=j_crit, score=judge["score"], rationale=judge["rationale"], verdict=verdict))
            row.meta = {**row.meta, "strategy": "corruptor", "flaw": flaw}

        # ---- raw calls (a few per run, for the log tab) ----
        for stage, run in runs.items():
            if stage == 6:
                continue
            for k in range(min(6, run.total)):
                ok = not (stage == 3 and k == 4)
                s.add(RawCall(
                    project_id=pid, run_id=run.id, stage=stage, target_id=f"{stage}-{k}", model_slug=run.model_slug or TEACHER,
                    provider="Anthropic" if "anthropic" in (run.model_slug or "") else "OpenAI",
                    request={"model": run.model_slug, "messages": [{"role": "system", "content": f"# stage: {['','taxonomy','prompts','responses','preferences','judge'][stage]}"}, {"role": "user", "content": "…"}], "temperature": 0.7},
                    response=None if not ok else {"id": f"gen-{stage}{k:03d}", "choices": [{"message": {"role": "assistant", "content": "…"}, "finish_reason": "stop"}]},
                    usage=None if not ok else {"prompt_tokens": 812 + k * 31, "completion_tokens": 402 + k * 17, "cost": 0.0087},
                    cost_usd=0.0 if not ok else 0.0087, latency_ms=0 if not ok else 1840 + k * 120,
                    error=None if ok else "openrouter: 502 upstream provider error after 5 retries",
                    created_at=run.started_at + k * 20,
                ))

        # ---- export bundle on disk + record ----
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(t0 + 7 * 900))
        bundle = settings.exports_dir / SLUG / stamp
        exportable = [r for r in rows if r.status in ("draft", "accepted", "edited")]
        rng.shuffle(exportable)
        n_eval = max(1, round(len(exportable) * 0.05))
        (bundle / "sft").mkdir(parents=True, exist_ok=True)
        (bundle / "dpo").mkdir(parents=True, exist_ok=True)
        with (bundle / "sft" / "train.jsonl").open("w", encoding="utf-8") as f:
            for r in exportable[n_eval:]:
                f.write(json.dumps({"messages": r.messages, "metadata": r.meta}, ensure_ascii=False) + "\n")
        with (bundle / "sft" / "eval.jsonl").open("w", encoding="utf-8") as f:
            for r in exportable[:n_eval]:
                f.write(json.dumps({"messages": r.messages, "metadata": r.meta}, ensure_ascii=False) + "\n")
        pairs = s.query(PairRecord).filter_by(project_id=pid).filter(PairRecord.status != "tie").all()
        with (bundle / "dpo" / "train.jsonl").open("w", encoding="utf-8") as f:
            for pr in pairs:
                row = s.get(RowRecord, pr.row_id)
                f.write(json.dumps({"prompt": row.messages[:-1], "chosen": [row.messages[-1]], "rejected": [pr.rejected_messages[-1]],
                                    "metadata": {**row.meta, "strategy": pr.strategy, "flaw": pr.flaw}}, ensure_ascii=False) + "\n")
        counts = {"sft": {"train": len(exportable) - n_eval, "eval": n_eval, "total": len(exportable)},
                  "dpo": {"train": len(pairs), "eval": 0, "total": len(pairs)}}
        (bundle / "manifest.json").write_text(json.dumps({"project": SLUG, "created_at": t0 + 7 * 900, "formats": ["sft", "dpo"], "counts": counts}, indent=2))
        (bundle / "README.md").write_text(
            f"# {NAME}\n\nSynthetic SFT + DPO dataset generated with Dataset Genie.\n\n"
            f"- Teacher: `{TEACHER}`, `{TEACHER2}`\n- Prompts: `{PROMPTER}`\n- Judge: `{JUDGE}`\n\n"
            f"## Rubric\n\n" + "\n".join(f"- {n} ({w})" for n, w in zip(RUBRIC, WEIGHTS)) + "\n\n"
            f"## Counts\n\n- sft: {counts['sft']['total']}\n- dpo: {counts['dpo']['total']}\n", encoding="utf-8")
        (bundle / "generation_config.yaml").write_text(
            "# generated by scripts/seed_demo.py\nname: " + NAME + "\nslug: " + SLUG + "\nconfig:\n  data_types: [sft, dpo]\n", encoding="utf-8")
        s.add(Export(project_id=pid, path=str(bundle), formats=["sft", "dpo"], counts=counts,
                     hf_repo="cassi-ai/linux-incident-triage", hf_url=None, created_at=t0 + 7 * 900))

    with db.session_scope() as s:
        p = s.query(Project).filter_by(slug=SLUG).one()
        n_rows = s.query(RowRecord).filter_by(project_id=p.id).count()
        n_pairs = s.query(PairRecord).filter_by(project_id=p.id).count()
        n_prompts = s.query(Prompt).filter_by(project_id=p.id).count()
        n_leaves = s.query(TopicNode).filter_by(project_id=p.id, is_leaf=True).count()
        from sqlalchemy import func
        by_status = dict(s.query(RowRecord.status, func.count()).filter_by(project_id=p.id).group_by(RowRecord.status).all())
        print(f"seeded project {p.name!r} id={p.id} slug={p.slug}")
        print(f"  leaves={n_leaves} prompts={n_prompts} rows={n_rows} pairs={n_pairs} spend=${p.spend_usd:.4f} / ${p.budget_cap_usd:.2f}")
        if by_status:
            print(f"  rows by status: {by_status}")
        print(f"  export bundle: {bundle}")
        print(f"  GENIE_HOME={settings.genie_home}")


if __name__ == "__main__":
    main()
