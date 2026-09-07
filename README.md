# Dataset Genie

Local web app that generates synthetic fine-tuning datasets through OpenRouter and exports
Unsloth-ready JSONL (SFT, Alpaca, DPO/ORPO, tool-calling, GRPO). Work in progress — see
`docs/superpowers/specs/` and `docs/superpowers/plans/`.

```
make install   # python venv + npm install
make dev       # backend :8765 + vite :5173
make test
```
