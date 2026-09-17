---
name: dataset-genie
description: Generate a fine-tuning dataset with Dataset Genie over MCP. Use when the user wants to build, generate or expand training data — SFT rows, DPO/ORPO preference pairs, GRPO reasoning traces or tool-calling trajectories — from a domain brief; when they name a pipeline stage (taxonomy, prompts, responses, preferences, judge, filters, review, export) or ask to run, re-run or resume one; when they ask what a run will cost or to estimate before spending; when they want to triage judge scores, low-scoring leaves, filtered rows or flagged rows; or when they want an Unsloth-ready export bundle or a push to Hugging Face. Not for training or evaluating a model, and not for loading a dataset that already exists.
---

# Dataset Genie

Drive the eight-stage dataset pipeline over MCP.

## Tools

- `health`
- `secrets_status`
- `set_secret`
- `get_settings`
- `update_settings`
- `list_models`
- `list_presets`
- `create_project`
- `list_projects`
- `get_project`
- `update_project`
- `delete_project`
- `estimate_stage`
- `run_stage`
- `get_run`
- `wait_for_run`
- `cancel_run`
- `resume_run`
- `get_stage_data`
- `update_taxonomy`
- `resample_prompts`
- `run_filters`
- `restore_filtered`
- `review_rows`
- `export_dataset`
