"""`genie` CLI (typer).

    genie serve [--port 8765] [--reload]
    genie run CONFIG.yaml [--stages 1-8|1,2,3] [--name NAME]
    genie export SLUG --formats sft,dpo [--split 0.05] [--template llama-3.1]
                 [--push --repo user/name --private/--public --license cc-by-4.0 --tag v0.1.0]
    genie models [--search q]
    genie secrets set openrouter|huggingface

`run` reuses the same stage dispatcher as the API (`genie.pipeline.dispatch` + `genie.jobs.runner`)
with a Rich progress bar subscribed to `RunEvents.for_run(run_id)`; stage 8 builds the export bundle.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .schemas import STAGE_NAMES, DoneEvent, LogEvent, ProgressEvent, ProjectConfig

app = typer.Typer(
    help="Dataset Genie — generate Unsloth fine-tuning datasets via OpenRouter.",
    no_args_is_help=True,
    add_completion=False,
)
secrets_app = typer.Typer(help="Manage API tokens in the OS keychain (never written to disk).")
app.add_typer(secrets_app, name="secrets")

console = Console(soft_wrap=True)
err_console = Console(stderr=True, soft_wrap=True)

SECRET_NAMES = {"openrouter": "openrouter", "huggingface": "huggingface", "hf": "huggingface"}


# ---------------------------------------------------------------- serve
@app.command()
def serve(port: int = 8765, reload: bool = False) -> None:
    """Start the web app (API + built frontend)."""
    import uvicorn

    uvicorn.run("genie.main:app", port=port, reload=reload)


# ---------------------------------------------------------------- helpers
def parse_stages(spec: str) -> list[int]:
    """'1-8' → [1..8]; '1,2,3' → [1,2,3]; '3-5,8' → [3,4,5,8]. Validates against STAGE_NAMES."""
    out: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                raise typer.BadParameter(f"bad stage range {part!r}")
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part))
    bad = [n for n in out if n not in STAGE_NAMES]
    if bad:
        raise typer.BadParameter(f"unknown stage(s) {bad}; valid: {sorted(STAGE_NAMES)}")
    return sorted(dict.fromkeys(out))


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def upsert_project(session, fields: dict[str, Any], cfg: ProjectConfig, name: str | None = None):
    """Create or update (by slug) the project described by a generation config."""
    from sqlalchemy import select

    from .models import Project

    resolved_name = name or fields.get("name") or fields.get("slug") or "Untitled project"
    slug = fields.get("slug") or slugify(resolved_name)
    if name and not fields.get("slug"):
        slug = slugify(name)
    project = session.scalars(select(Project).where(Project.slug == slug)).first()
    if project is None:
        project = Project(slug=slug, name=resolved_name)
        session.add(project)
    project.name = resolved_name
    project.domain_brief = fields.get("domain_brief") or project.domain_brief or ""
    project.data_types = list(fields.get("data_types") or cfg.data_types)
    project.config = cfg.model_dump(mode="json")
    project.budget_cap_usd = cfg.budget_cap_usd
    project.stop_at_pct = cfg.stop_at_pct
    session.flush()
    return project


def _import_pipeline():
    """Lazy import of the stage dispatcher + job runner (other tracks). Clear error when absent."""
    try:
        from .jobs import runner as runner_mod
        from .pipeline import dispatch
    except ImportError as exc:
        raise RuntimeError(
            "the pipeline stages / job runner are not available in this build "
            f"({exc}). `genie run` needs genie.pipeline.dispatch and genie.jobs.runner; "
            "use the web app (`genie serve`) or `genie export` for stage 8 only."
        ) from exc
    return dispatch, runner_mod


async def _watch_run(run_id: str, label: str) -> str:
    """Rich progress bar fed by RunEvents until the DoneEvent; returns the final status."""
    from .jobs.events import RunEvents

    events = RunEvents.for_run(run_id)
    status = "done"
    with Progress(
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[extra]}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(label, total=None, extra="")
        async for _seq, ev in events.subscribe():
            if isinstance(ev, ProgressEvent):
                progress.update(
                    task,
                    completed=ev.done,
                    total=ev.total or None,
                    extra=f"${ev.spend_usd:.2f}/${ev.cap_usd:.2f}  refusals {ev.refusals}  errors {ev.errors}",
                )
            elif isinstance(ev, LogEvent) and ev.level in ("warn", "error"):
                progress.console.print(f"[yellow]{ev.level}[/] {ev.msg}")
            elif isinstance(ev, DoneEvent):
                status = ev.status
                break
    return status


async def _run_stage(project_id: str, stage: int, cfg: ProjectConfig) -> str:
    """Run one stage exactly like the API does (`pipeline.dispatch.start_stage`) and watch it."""
    from .db import session_scope
    from .models import Project

    dispatch, runner_mod = _import_pipeline()
    try:
        with session_scope() as s:
            project = s.get(Project, project_id)
            started = await dispatch.start_stage(project, stage, {}, s)
    except dispatch.StageError as exc:
        raise RuntimeError(f"stage {stage} {STAGE_NAMES[stage]}: {exc.detail}") from exc
    run_id = started["run_id"]
    console.print(f"stage {stage} {STAGE_NAMES[stage]}: run {run_id} · {started.get('items', '?')} items")
    status = await _watch_run(run_id, f"stage {stage} {STAGE_NAMES[stage]}")
    # `genie.jobs.runner` may be the module (with a `runner` singleton) or the singleton itself
    instance = runner_mod if hasattr(runner_mod, "wait") else getattr(runner_mod, "runner", None)
    waiter = getattr(instance, "wait", None)
    if waiter is not None:
        await waiter(run_id, timeout=None)
    return status


def _print_bundle(res, slug: str) -> None:
    table = Table(title=f"export · {slug}", show_lines=False)
    table.add_column("format")
    table.add_column("train", justify="right")
    table.add_column("eval", justify="right")
    for fmt, c in res.counts.items():
        table.add_row(fmt, str(c["train"]), str(c["eval"]))
    console.print(table)
    console.print(f"bundle: {res.path}")
    if res.gated_out:
        console.print(f"gated out (score below threshold): {res.gated_out}")
    for w in res.warnings:
        console.print(f"[yellow]warning[/] {w}")


# ---------------------------------------------------------------- run
@app.command()
def run(
    config: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="generation_config.yaml")
    ],
    stages: str = typer.Option("1-8", "--stages", help="e.g. 1-8, 1,2,3 or 3-5,8"),
    name: str | None = typer.Option(None, "--name", help="Override the project name (and slug)"),
) -> None:
    """Create/update a project from a YAML config and run the pipeline stages headlessly."""
    from .db import init_db, session_scope
    from .export import ExportRequest, build_bundle, load_generation_config, record_export
    from .formats.validate import ExportValidationError

    stage_list = parse_stages(stages)
    fields, cfg = load_generation_config(config)
    init_db()
    with session_scope() as s:
        project = upsert_project(s, fields, cfg, name)
        project_id, slug = project.id, project.slug
    console.print(f"project [bold]{slug}[/] · stages {stage_list}")

    for stage in stage_list:
        label = STAGE_NAMES[stage]
        if stage == 7:
            console.print("stage 7 review: human step — skipped (use the web app)")
            continue
        if stage == 8:
            req = ExportRequest.from_config(cfg.export)
            try:
                with session_scope() as s:
                    res = build_bundle(project_id, req, s)
                    record_export(s, project_id, res, req)
            except ExportValidationError as exc:
                _print_validation_error(exc)
                raise typer.Exit(code=1) from exc
            _print_bundle(res, slug)
            continue
        try:
            status = asyncio.run(_run_stage(project_id, stage, cfg))
        except RuntimeError as exc:
            err_console.print(f"[red]error[/] {exc}")
            raise typer.Exit(code=2) from exc
        console.print(f"stage {stage} {label}: {status}")
        if status not in ("done",):
            err_console.print(f"[red]stage {stage} ended with status {status!r}; stopping[/]")
            raise typer.Exit(code=1)


def _print_validation_error(exc) -> None:
    err_console.print(f"[red]export validation failed[/] ({exc.total} issue(s)); nothing was written")
    for issue in exc.issues:
        err_console.print(f"  {issue.id}: {issue.reason}")


# ---------------------------------------------------------------- export
@app.command()
def export(
    slug: Annotated[str, typer.Argument(help="project slug")],
    formats: str = typer.Option("sft", "--formats", help="comma-separated: sft,alpaca,dpo,tools,grpo"),
    split: float = typer.Option(0.05, "--split", min=0.0, max=0.99, help="eval fraction"),
    template: str | None = typer.Option("llama-3.1", "--template", help="llama-3.1 | chatml | gemma | none"),
    stratify_by: str = typer.Option("leaf", "--stratify-by", help="leaf | topic | difficulty | none"),
    gate: bool = typer.Option(False, "--gate/--no-gate", help="drop rows below the judge threshold"),
    no_scores: bool = typer.Option(False, "--no-scores", help="omit the metadata/judge object"),
    seed: int = typer.Option(42, "--seed"),
    push: bool = typer.Option(False, "--push", help="push the bundle to the Hugging Face Hub"),
    repo: str | None = typer.Option(None, "--repo", help="user/name (with --push)"),
    private: bool = typer.Option(True, "--private/--public"),
    license: str = typer.Option("cc-by-4.0", "--license"),
    tag: str = typer.Option("v0.1.0", "--tag"),
) -> None:
    """Build an export bundle for a project (and optionally push it)."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .export import ExportRequest, build_bundle, get_hf_token, push_bundle, record_export
    from .formats.validate import ExportValidationError
    from .models import Project
    from .schemas import HFPushConfig

    fmt_list = [f.strip() for f in formats.split(",") if f.strip()]
    push_cfg = None
    token = None
    if push:
        if not repo:
            raise typer.BadParameter("--push requires --repo user/name")
        push_cfg = HFPushConfig(repo_id=repo, private=private, license=license, version_tag=tag)
        token = get_hf_token()
        if not token:
            err_console.print("[red]no Hugging Face token[/]; run `genie secrets set huggingface` first")
            raise typer.Exit(code=1)
    try:
        req = ExportRequest(
            formats=fmt_list,  # type: ignore[arg-type]
            eval_split=split,
            stratify_by=stratify_by,  # type: ignore[arg-type]
            validate_template=None if (template or "none").lower() == "none" else template,  # type: ignore[arg-type]
            include_judge_scores=not no_scores,
            gate_on_score=gate,
            seed=seed,
            push=push_cfg,
        )
    except Exception as exc:  # pydantic validation of the option values
        raise typer.BadParameter(str(exc)) from exc

    init_db()
    with session_scope() as s:
        project = s.scalars(select(Project).where(Project.slug == slug)).first()
        if project is None:
            err_console.print(f"[red]no project with slug {slug!r}[/]")
            raise typer.Exit(code=1)
        try:
            res = build_bundle(project.id, req, s)
        except ExportValidationError as exc:
            _print_validation_error(exc)
            raise typer.Exit(code=1) from exc
        rec = record_export(s, project.id, res, req)
        if push_cfg is not None and token:
            url = push_bundle(res.path, push_cfg, token)
            rec.hf_repo, rec.hf_url = push_cfg.repo_id, url
    _print_bundle(res, slug)
    if push_cfg is not None:
        console.print(f"pushed: {rec.hf_url}")


# ---------------------------------------------------------------- models
@app.command()
def models(search: str | None = typer.Option(None, "--search", "-s", help="substring filter")) -> None:
    """List the OpenRouter model catalogue with $/1M token prices."""
    try:
        from .providers.openrouter import MissingApiKey, OpenRouterError, get_client
    except ImportError as exc:
        err_console.print(f"[red]model catalogue unavailable[/]: {exc}")
        raise typer.Exit(code=1) from exc

    async def _fetch():
        # get_client() reads the keychain via genie.secrets and raises MissingApiKey — the same
        # path the runner and /api/models use, so there is exactly one place to configure.
        return await get_client().catalogue()

    try:
        infos = asyncio.run(_fetch())
    except MissingApiKey as exc:
        err_console.print(f"[red]{exc}[/] (or run `genie secrets set openrouter`)")
        raise typer.Exit(code=1) from exc
    except OpenRouterError as exc:
        err_console.print(f"[red]could not fetch the model catalogue[/]: {exc}")
        raise typer.Exit(code=1) from exc
    q = (search or "").lower()
    table = Table(title="OpenRouter models")
    for col, just in (("id", "left"), ("name", "left"), ("ctx", "right"), ("$/1M in", "right"), ("$/1M out", "right")):
        table.add_column(col, justify=just)
    shown = 0
    for m in infos:
        mid = str(getattr(m, "id", ""))
        mname = str(getattr(m, "name", ""))
        if q and q not in mid.lower() and q not in mname.lower():
            continue
        table.add_row(
            mid,
            mname,
            str(getattr(m, "context_length", "") or ""),
            _money(getattr(m, "prompt_price_per_m", None)),
            _money(getattr(m, "completion_price_per_m", None)),
        )
        shown += 1
    console.print(table)
    console.print(f"{shown} model(s)")


def _money(v) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "-"


# ---------------------------------------------------------------- secrets
@secrets_app.command("set")
def secrets_set(name: Annotated[str, typer.Argument(help="openrouter | huggingface")]) -> None:
    """Store an API token in the OS keychain (hidden prompt; never echoed or logged)."""
    key = SECRET_NAMES.get(name.lower())
    if key is None:
        raise typer.BadParameter(f"unknown secret {name!r}; choose openrouter or huggingface")
    try:
        from . import secrets as secrets_mod
    except ImportError as exc:
        err_console.print(f"[red]secrets backend unavailable[/]: {exc}")
        raise typer.Exit(code=1) from exc
    value = typer.prompt(f"{key} token", hide_input=True).strip()
    if not value:
        raise typer.BadParameter("empty token")
    secrets_mod.set_secret(key, value)
    console.print(f"stored {key} token in the keychain ({len(value)} chars)")


@secrets_app.command("status")
def secrets_status() -> None:
    """Show which tokens are configured (never the values)."""
    try:
        from . import secrets as secrets_mod
    except ImportError as exc:
        err_console.print(f"[red]secrets backend unavailable[/]: {exc}")
        raise typer.Exit(code=1) from exc
    status = secrets_mod.secret_status()
    for k, present in status.items():
        console.print(f"{k}: {'set' if present else 'missing'}")


if __name__ == "__main__":
    app()
