# Seedance Managed Video Loop

A BytePlus ModelArk workflow for iterative video generation and two-stage evaluation inside a Managed Agent.

The recommended Managed Agent loop uses four roles:

1. A text LLM generates or revises a production-ready Seedance prompt.
2. Seedance generates multiple candidates from the same prompt.
3. A multimodal LLM receives each video through `input_video` and returns compact observable AV evidence.
4. The MA main model reads the evidence and scores it directly, returning one highest-priority improvement.

The MA main model orchestrates candidate selection, focused feedback, and early stopping. `managed_video_loop.py` supplies the generation and video-analysis operations; no external evaluation service or public MCP server is required.

## Defaults

| Flag | Default | Description |
| --- | ---: | --- |
| `--max-rounds` | `5` | Maximum improvement rounds |
| `--candidates` | `2` | Candidates generated from the same prompt per round |
| `--target-score` | `3.5` | Early-stop score from 0 to 5 |
| `--timeout` | `1200` | Per-video polling timeout in seconds |
| `--video-model` | `dreamina-seedance-2-5-260628` | Seedance generation model |
| `--prompt-model` | `seed-2-0-lite-260428` | Text prompt-agent model |
| `--analysis-model` | `seed-2-0-lite-260428` | Multimodal AV evidence model |
| `--evaluator-model` | `seed-2-0-lite-260428` | Standalone-runner evaluator; the recommended MA path uses the MA main model instead |

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
  --analysis-model seed-2-0-lite-260428 \
  --evaluator-model seed-2-0-lite-260428
```

Run the offline tests without API calls or video charges:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Managed Agent

The recommended MA architecture uploads `managed_video_loop.py` as a Session Resource and uses it from the Agent's default bash tool. `ARK_API_KEY` is injected with an MA Vault credential. For each candidate, the Agent calls `/responses` with `input_video` to extract compact AV evidence. The MA main model then reads that evidence and performs rubric scoring directly.

- [English guide](docs/guide.en.md)
- [中文指南](docs/guide.zh-CN.md)

## Evaluation Contract

The two stages together produce JSON containing:

- score from 0 to 5 and confidence from 0 to 1
- timestamped timeline and visual evidence
- speech, music, sound effects, and other audio evidence
- passed and failed requirements
- continuity and unintended-text findings
- one highest-priority failure and one focused repair instruction

The analysis stage must not infer audio solely from visuals. The MA main-model scoring stage judges the supplied evidence against the source brief.

## Security

- Never commit API keys, tokens, `.env` files, or Vault secret source files.
- Generated `runs/` directories may contain temporary signed video URLs and are ignored.
- Generated videos are ignored and should be persisted to controlled object storage when needed.
- `examples/example-manifest.json` illustrates the output schema and contains no signed URL or resource ID.

## Verification Status

- The standalone runner compiles and starts with only `requests` as a direct dependency.
- Offline tests validate structured response parsing, the standalone two-call fallback, defaults, and focused-repair selection.
- ArkCLI dry-run validates the MA Cloud Environment setup and default bash/read/write toolset.
- Direct `seed-2-0-lite + input_video` evaluation through the Responses API is verified.
- A real MA Cloud Session completed the recommended path: `/responses + input_video` extracted AV evidence, and the MA main model read that evidence and returned the score directly.
