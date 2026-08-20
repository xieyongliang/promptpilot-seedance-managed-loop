# PromptPilot + Seedance Managed Video Loop

A standalone BytePlus workflow for iterative video generation and evaluation:

1. PromptPilot generates or revises a Seedance prompt.
2. Seedance generates multiple candidates from the same prompt.
3. Seed 2.0 Lite analyzes the actual video and audio through `input_video`.
4. PromptPilot Eval scores each structured AV report.
5. The runner selects the best candidate and repairs one highest-priority defect per round.

The primary entry point is a single file: `managed_video_loop.py`. It runs locally or inside a ModelArk Managed Agent Cloud Environment without a public MCP server.

## Defaults

| Flag | Default | Description |
| --- | ---: | --- |
| `--max-rounds` | `5` | Maximum improvement rounds |
| `--candidates` | `2` | Candidates generated from the same prompt per round |
| `--target-score` | `3.5` | Early-stop score from 0 to 5 |
| `--timeout` | `1200` | Per-video polling timeout in seconds |

All values are user-configurable CLI options.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export ARK_API_KEY="<your-modelark-api-key>"
export AGENTPILOT_API_KEY="<your-promptpilot-api-key>"
export AGENTPILOT_WORKSPACE_ID="ws-xxxxxxxx"
export AGENTPILOT_API_URL="https://prompt-pilot.ap-southeast.bytepluses.com"

.venv/bin/python managed_video_loop.py
```

Override the policy when needed:

```bash
.venv/bin/python managed_video_loop.py \
  --max-rounds 3 \
  --candidates 4 \
  --target-score 4.2 \
  --timeout 1800
```

## Managed Agent

The recommended MA architecture uploads `managed_video_loop.py` as a Session Resource and executes it through the Agent's default bash tool. Secrets are injected with MA Vault credentials. No public MCP endpoint is required.

- [English guide](docs/guide.en.md)
- [中文指南](docs/guide.zh-CN.md)

The `optional-mcp/` directory contains the alternative external MCP architecture.

## Security

- Never commit API keys, tokens, `.env` files, or Vault secret source files.
- Generated `runs/` directories may contain temporary signed video URLs and are ignored.
- Generated videos are ignored and should be persisted to controlled object storage when needed.
- `examples/verified-manifest.json` is sanitized and contains no signed URL.

## Verification Status

- The standalone runner compiles and starts independently without the legacy V1/V2 modules.
- ArkCLI dry-run validates the MA Cloud Environment setup and default bash/read/write toolset.
- A local end-to-end run produced two first-round candidates scoring 2.0 and 4.0; the 4.0 candidate passed the 3.5 threshold.
- A real MA Cloud Session execution remains the final end-to-end validation step.

