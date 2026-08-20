# Seedance Managed Video Loop

A standalone BytePlus ModelArk workflow for iterative video generation and direct multimodal evaluation.

The loop uses three model roles:

1. A text LLM generates or revises a production-ready Seedance prompt.
2. Seedance generates multiple candidates from the same prompt.
3. A multimodal LLM receives each generated video through `input_video`, watches and listens to it, and returns a structured score with evidence and one highest-priority improvement.

The runner selects the best candidate, applies focused feedback, and stops when the target score is reached. It runs locally or inside a ModelArk Managed Agent Cloud Environment without an external prompt/evaluation service or a public MCP server.

## Defaults

| Flag | Default | Description |
| --- | ---: | --- |
| `--max-rounds` | `5` | Maximum improvement rounds |
| `--candidates` | `2` | Candidates generated from the same prompt per round |
| `--target-score` | `3.5` | Early-stop score from 0 to 5 |
| `--timeout` | `1200` | Per-video polling timeout in seconds |
| `--video-model` | `dreamina-seedance-2-5-260628` | Seedance generation model |
| `--prompt-model` | `seed-2-0-lite-260428` | Text prompt-agent model |
| `--evaluator-model` | `seed-2-0-lite-260428` | Direct multimodal evaluator model |

All values are user-configurable CLI options.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export ARK_API_KEY="<your-modelark-api-key>"

.venv/bin/python managed_video_loop.py
```

Override the policy or models when needed:

```bash
.venv/bin/python managed_video_loop.py \
  --max-rounds 3 \
  --candidates 4 \
  --target-score 4.2 \
  --timeout 1800 \
  --prompt-model seed-2-0-lite-260428 \
  --evaluator-model seed-2-0-lite-260428
```

Run the offline tests without API calls or video charges:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Managed Agent

The recommended MA architecture uploads `managed_video_loop.py` as a Session Resource and executes it through the Agent's default bash tool. `ARK_API_KEY` is injected with an MA Vault credential. The evaluator is an LLM role invoked by the runner through the ModelArk Responses API with direct video input.

- [English guide](docs/guide.en.md)
- [中文指南](docs/guide.zh-CN.md)

## Evaluation Contract

The evaluator returns JSON containing:

- score from 0 to 5 and confidence from 0 to 1
- timestamped timeline and visual evidence
- speech, music, sound effects, and other audio evidence
- passed and failed requirements
- continuity and unintended-text findings
- one highest-priority failure and one focused repair instruction

The evaluator must judge observable evidence and must not infer audio solely from visuals.

## Security

- Never commit API keys, tokens, `.env` files, or Vault secret source files.
- Generated `runs/` directories may contain temporary signed video URLs and are ignored.
- Generated videos are ignored and should be persisted to controlled object storage when needed.
- `examples/example-manifest.json` illustrates the output schema and contains no signed URL or resource ID.

## Verification Status

- The standalone runner compiles and starts with only `requests` as a direct dependency.
- Offline tests validate structured response parsing, direct video-evaluation payloads, defaults, and focused-repair selection.
- ArkCLI dry-run validates the MA Cloud Environment setup and default bash/read/write toolset.
- A real MA Cloud Session execution remains the final end-to-end validation step for this revised implementation.
