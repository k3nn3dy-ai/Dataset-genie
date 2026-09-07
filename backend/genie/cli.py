"""`genie` CLI. Owned by the export track; the lead provides `serve` so the script resolves."""
from __future__ import annotations

import typer

app = typer.Typer(help="Dataset Genie — generate Unsloth fine-tuning datasets via OpenRouter.")


@app.command()
def serve(port: int = 8765, reload: bool = False) -> None:
    """Start the web app (API + built frontend)."""
    import uvicorn

    uvicorn.run("genie.main:app", port=port, reload=reload)


if __name__ == "__main__":
    app()
