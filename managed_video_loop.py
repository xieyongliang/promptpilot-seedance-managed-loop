#!/usr/bin/env python3
"""Seedance generation loop with direct LLM prompt creation and video evaluation."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"
DEFAULT_ARK_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
DEFAULT_VIDEO_MODEL = "dreamina-seedance-2-5-260628"
DEFAULT_PROMPT_MODEL = "seed-2-0-lite-260428"
DEFAULT_ANALYSIS_MODEL = "seed-2-0-lite-260428"
DEFAULT_EVALUATOR_MODEL = "seed-2-0-lite-260428"

DEFAULT_BRIEF = """Create an 11-second, 16:9 first-person POV fruit-tea promotional video with generated audio. Use Reference Video 1 for first-person framing and motion language and Reference Audio 1 as continuous background music. From 0-2 seconds, use Reference Image 1 as the opening and show a hand picking a dew-covered red apple with a crisp apple-tapping sound. From 2-4 seconds, rapidly cut between apple chunks entering a shaker, ice and tea base being added, and vigorous shaking synchronized with ice clinks and upbeat music; a female voice says exactly: "Fresh-cut, shaken fresh." From 4-6 seconds, pour layered fruit tea into a clear cup, spread milk foam on top, apply the pink product sticker, and move closer to show texture. From 6-8 seconds, raise the drink from Reference Image 2 toward the viewer in a first-person toast; keep the label visible and use a female voice saying exactly: "Take a sip of fresh refreshment." Hold the final frame on Reference Image 2 through 11 seconds. Preserve cup, hand, apple, label, liquid, and foam continuity. Avoid third-person views, extra fingers, morphing, flicker, unreadable labels, unintended text, continuity errors, and audio desynchronization."""

REFERENCES = [
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_image/r2v_tea_pic1.jpg"
        },
        "role": "reference_image",
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_image/r2v_tea_pic2.jpg"
        },
        "role": "reference_image",
    },
    {
        "type": "video_url",
        "video_url": {
            "url": "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_video/r2v_tea_video1.mp4"
        },
        "role": "reference_video",
    },
    {
        "type": "audio_url",
        "audio_url": {
            "url": "https://ark-doc.tos-ap-southeast-1.bytepluses.com/doc_audio/r2v_tea_audio1.mp3"
        },
        "role": "reference_audio",
    },
]

PRIORITY_RULES = [
    ("exact_speech", ("speech", "voice", "dialogue", "pronunciation", "台词")),
    ("viewpoint", ("first-person", "third-person", "pov", "viewpoint", "视角")),
    ("unintended_text", ("unintended text", "overlay", "caption", "额外文字")),
    ("timeline_actions", ("timeline", "timing", "timestamp", "action", "时间线")),
    ("continuity", ("continuity", "morph", "flicker", "background", "连续性")),
    ("audio", ("audio", "music", "sound effect", "silence", "音频")),
]


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def ark_base_url() -> str:
    return os.getenv("ARK_BASE_URL", DEFAULT_ARK_BASE_URL).rstrip("/")


def ark_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {required_env('ARK_API_KEY')}",
        "Content-Type": "application/json",
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_response_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE
    )
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM response must be a JSON object")
    return parsed


def call_responses(payload: dict[str, Any], timeout: int = 300) -> dict[str, Any]:
    response = requests.post(
        f"{ark_base_url()}/responses",
        headers=ark_headers(),
        json=payload,
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Responses API failed ({response.status_code}): {response.text}"
        )
    raw = response.json()
    text = extract_response_text(raw)
    if not text:
        raise RuntimeError("Responses API returned no output_text")
    return {
        "response_id": raw.get("id"),
        "model": raw.get("model"),
        "usage": raw.get("usage"),
        "result": parse_json_text(text),
    }


def validate_video_prompt(prompt: str) -> str:
    cleaned = re.sub(r"\{\{[A-Za-z0-9_.-]+\}\}", "", prompt)
    cleaned = re.sub(r"</?[A-Za-z][^>]*>", "", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    if not cleaned:
        raise RuntimeError("Prompt agent returned an empty prompt")
    if "{{" in cleaned or "}}" in cleaned:
        raise RuntimeError("Prompt agent returned unresolved template variables")
    return cleaned


def harden_prompt(prompt: str, brief: str) -> str:
    marker = "NON-NEGOTIABLE SOURCE BRIEF:"
    base = prompt.split(marker, 1)[0].rstrip() if marker in prompt else prompt.strip()
    return (
        f"{base}\n\n{marker}\n{brief.strip()}\n\n"
        "Preserve every requirement in the source brief. Do not add creative elements, "
        "text, props, dialogue, camera viewpoints, or technical parameters that conflict "
        "with it."
    )


def generate_video_prompt(
    brief: str,
    model: str,
    current_prompt: str | None = None,
    feedback: str | None = None,
) -> dict[str, Any]:
    instruction = f"""Act as a video prompt engineering agent. Create a concise English prompt that can be submitted directly to Seedance content.text.

SOURCE BRIEF:
{brief}

Use [Image 1], [Image 2], [Video 1], and [Audio 1] when the brief refers to the supplied assets. Keep the prompt chronological and visually concrete. Preserve exact timing, dialogue, viewpoint, continuity, audio, and forbidden elements. Do not include explanations, chain of thought, placeholders, XML, production checklists, post-processing instructions, or invented model parameters.

CURRENT PROMPT:
{current_prompt or "None; create the first prompt."}

FOCUSED REPAIR:
{feedback or "None; preserve all source requirements."}

Return JSON with one field: prompt."""
    response = call_responses(
        {
            "model": model,
            "stream": False,
            "text": {"format": {"type": "json_object"}},
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": instruction}],
                }
            ],
        }
    )
    prompt = validate_video_prompt(str(response["result"].get("prompt") or ""))
    return {
        "prompt": harden_prompt(prompt, brief),
        "model": response["model"],
        "response_id": response["response_id"],
        "usage": response["usage"],
        "focused_repair": feedback,
    }


def normalize_reference_names(prompt: str) -> str:
    replacements = {
        "Reference Image 1": "[Image 1]",
        "Reference Image 2": "[Image 2]",
        "Reference Video 1": "[Video 1]",
        "Reference Audio 1": "[Audio 1]",
    }
    for source, target in replacements.items():
        prompt = prompt.replace(source, target)
    return prompt


def submit_video(prompt: str, model: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "content": [
            {"type": "text", "text": normalize_reference_names(prompt)},
            *REFERENCES,
        ],
        "generate_audio": True,
        "ratio": "16:9",
        "duration": 11,
        "watermark": False,
    }
    response = requests.post(
        f"{ark_base_url()}/contents/generations/tasks",
        headers=ark_headers(),
        json=payload,
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(
            f"Seedance submit failed ({response.status_code}): {response.text}"
        )
    return response.json()


def get_video_task(task_id: str) -> dict[str, Any]:
    for attempt in range(1, 4):
        try:
            response = requests.get(
                f"{ark_base_url()}/contents/generations/tasks/{task_id}",
                headers=ark_headers(),
                timeout=60,
            )
            if not response.ok:
                raise RuntimeError(
                    f"Seedance poll failed ({response.status_code}): {response.text}"
                )
            return response.json()
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def wait_for_video(task_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        result = get_video_task(task_id)
        status = result.get("status", "unknown")
        if status != last_status:
            print(
                json.dumps({"step": "video", "task_id": task_id, "status": status}),
                flush=True,
            )
            last_status = status
        if status == "succeeded":
            if not result.get("content", {}).get("video_url"):
                raise RuntimeError("Seedance succeeded without content.video_url")
            return result
        if status in {"failed", "cancelled", "expired"}:
            raise RuntimeError(f"Seedance task ended with status {status}: {result}")
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for Seedance task {task_id}")


def normalize_evaluation(value: dict[str, Any]) -> dict[str, Any]:
    score = max(0.0, min(5.0, float(value.get("score") or 0)))
    confidence = max(0.0, min(1.0, float(value.get("confidence") or 0)))
    value["score"] = score
    value["confidence"] = confidence
    for key in (
        "timeline",
        "visual_evidence",
        "audio_evidence",
        "exact_speech",
        "continuity_issues",
        "unintended_text",
        "passed_requirements",
        "failed_requirements",
    ):
        current = value.get(key)
        if current is None:
            value[key] = []
        elif not isinstance(current, list):
            value[key] = [str(current)]
    return value


def normalize_analysis(value: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "timeline",
        "visual_evidence",
        "audio_evidence",
        "exact_speech",
        "continuity_issues",
        "unintended_text",
    ):
        current = value.get(key)
        if current is None:
            value[key] = []
        elif not isinstance(current, list):
            value[key] = [str(current)]
    return value


def analyze_video(video_url: str, brief: str, model: str) -> dict[str, Any]:
    instruction = f"""Watch and listen to the video. Extract compact observable evidence relevant to the brief. Do not score, recommend changes, or infer audio from visuals.

SOURCE BRIEF:
{brief}

Return concise JSON with: summary, timeline, visual_evidence, audio_evidence, exact_speech, continuity_issues, and unintended_text. Every timeline item must include a timestamp. Use at most 12 timeline items and at most 8 items in every other list."""
    response = call_responses(
        {
            "model": model,
            "stream": False,
            "max_output_tokens": 1200,
            "text": {"format": {"type": "json_object"}},
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_video", "video_url": video_url, "fps": 1},
                        {"type": "input_text", "text": instruction},
                    ],
                }
            ],
        }
    )
    return {
        "response_id": response["response_id"],
        "model": response["model"],
        "usage": response["usage"],
        "analysis": normalize_analysis(response["result"]),
    }


def evaluate_video_analysis(
    brief: str, analysis: dict[str, Any], model: str
) -> dict[str, Any]:
    instruction = f"""Act as an independent video evaluation agent. Score the supplied evidence report against the source brief. The report came from a separate video-and-audio understanding call. Judge only that report and do not claim to have watched the video.

SOURCE BRIEF:
{brief}

OBSERVABLE EVIDENCE REPORT:
{json.dumps(analysis, ensure_ascii=False)}

SCORING RUBRIC:
- 5: all material requirements pass; no meaningful defects.
- 4: nearly complete; only minor defects remain.
- 3: usable but one or more substantial requirements fail.
- 2: multiple major requirements fail.
- 1: poor match with little usable compliance.
- 0: invalid, inaccessible, or unrelated evidence.

Return concise JSON with: score (0-5), confidence (0-1), summary, passed_requirements, failed_requirements, highest_priority_failure, and improvement_instruction. The improvement instruction must address only the single highest-priority failure while preserving passed requirements."""
    response = call_responses(
        {
            "model": model,
            "stream": False,
            "max_output_tokens": 900,
            "text": {"format": {"type": "json_object"}},
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": instruction}],
                }
            ],
        }
    )
    return {
        "response_id": response["response_id"],
        "model": response["model"],
        "usage": response["usage"],
        "evaluation": normalize_evaluation(response["result"]),
    }


def choose_focus(evaluation: dict[str, Any]) -> dict[str, str]:
    highest = str(evaluation.get("highest_priority_failure") or "").strip()
    failures = [str(item) for item in evaluation.get("failed_requirements") or []]
    candidates = [highest, *failures] if highest else failures
    for category, keywords in PRIORITY_RULES:
        for failure in candidates:
            lowered = failure.lower()
            if any(keyword in lowered for keyword in keywords):
                return {"category": category, "failure": failure}
    if candidates:
        return {"category": "other", "failure": candidates[0]}
    return {
        "category": "evaluator_feedback",
        "failure": "Improve brief compliance while preserving every passed requirement.",
    }


def focused_feedback(focus: dict[str, str]) -> str:
    return (
        "Repair only this highest-priority failure while preserving all passed requirements: "
        f"[{focus['category']}] {focus['failure']}. Do not add unrelated creative changes."
    )


def download_video(video_url: str, output_path: Path) -> dict[str, Any]:
    partial_path = output_path.with_suffix(output_path.suffix + ".part")
    try:
        with requests.get(video_url, stream=True, timeout=(30, 300)) as response:
            response.raise_for_status()
            with partial_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
        partial_path.replace(output_path)
        return {
            "status": "downloaded",
            "path": str(output_path),
            "bytes": output_path.stat().st_size,
        }
    except requests.RequestException as exc:
        partial_path.unlink(missing_ok=True)
        return {"status": "failed", "error": str(exc)}


@dataclass
class LoopConfig:
    brief: str
    max_rounds: int = 5
    candidates_per_round: int = 2
    target_score: float = 3.5
    timeout_seconds: int = 1200
    video_model: str = DEFAULT_VIDEO_MODEL
    prompt_model: str = DEFAULT_PROMPT_MODEL
    analysis_model: str = DEFAULT_ANALYSIS_MODEL
    evaluator_model: str = DEFAULT_EVALUATOR_MODEL
    run_id: str | None = None


def run_loop(config: LoopConfig) -> dict[str, Any]:
    run_id = config.run_id or datetime.now(timezone.utc).strftime(
        "managed-%Y%m%dT%H%M%SZ"
    )
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "config.json", asdict(config) | {"run_id": run_id})

    current_prompt: str | None = None
    focus: dict[str, str] | None = None
    round_summaries: list[dict[str, Any]] = []
    overall_best: dict[str, Any] | None = None

    for round_number in range(1, config.max_rounds + 1):
        round_dir = run_dir / f"round-{round_number:02d}"
        round_dir.mkdir()
        generated = generate_video_prompt(
            config.brief,
            config.prompt_model,
            current_prompt,
            focused_feedback(focus) if focus else None,
        )
        current_prompt = generated["prompt"]
        write_json(round_dir / "llm_prompt_generation.json", generated)
        (round_dir / "prompt.txt").write_text(current_prompt + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "step": "round_start",
                    "round": round_number,
                    "focused_repair": focus,
                    "candidates": config.candidates_per_round,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        submitted: list[tuple[int, Path, dict[str, Any]]] = []
        for candidate_number in range(1, config.candidates_per_round + 1):
            candidate_dir = round_dir / f"candidate-{candidate_number:02d}"
            candidate_dir.mkdir()
            result = submit_video(current_prompt, config.video_model)
            write_json(candidate_dir / "seedance_submit.json", result)
            task_id = result.get("id")
            if not task_id:
                raise RuntimeError(f"Seedance response has no task id: {result}")
            submitted.append((candidate_number, candidate_dir, result))
            print(
                json.dumps(
                    {
                        "step": "candidate_submitted",
                        "round": round_number,
                        "candidate": candidate_number,
                        "task_id": task_id,
                    }
                ),
                flush=True,
            )

        candidates: list[dict[str, Any]] = []
        for candidate_number, candidate_dir, submission in submitted:
            task_id = submission["id"]
            video_result = wait_for_video(task_id, config.timeout_seconds)
            write_json(candidate_dir / "seedance_result.json", video_result)
            video_url = video_result["content"]["video_url"]
            analyzed = analyze_video(video_url, config.brief, config.analysis_model)
            write_json(candidate_dir / "llm_video_analysis.json", analyzed)
            evaluated = evaluate_video_analysis(
                config.brief, analyzed["analysis"], config.evaluator_model
            )
            write_json(candidate_dir / "llm_video_evaluation.json", evaluated)
            evaluation = {**analyzed["analysis"], **evaluated["evaluation"]}
            for evidence_key in (
                "timeline",
                "visual_evidence",
                "audio_evidence",
                "exact_speech",
                "continuity_issues",
                "unintended_text",
            ):
                evaluation[evidence_key] = analyzed["analysis"].get(evidence_key, [])
            candidate = {
                "round": round_number,
                "candidate": candidate_number,
                "task_id": task_id,
                "video_url": video_url,
                "analysis_model": analyzed.get("model"),
                "evaluator_model": evaluated.get("model"),
                "analysis_response_id": analyzed.get("response_id"),
                "evaluation_response_id": evaluated.get("response_id"),
                "audio_tokens": (analyzed.get("usage") or {})
                .get("input_tokens_details", {})
                .get("audio_tokens"),
                "analysis": analyzed["analysis"],
                "score": evaluation["score"],
                "confidence": evaluation["confidence"],
                "evaluation": evaluation,
            }
            write_json(candidate_dir / "summary.json", candidate)
            candidates.append(candidate)
            print(
                json.dumps(
                    {
                        "step": "candidate_complete",
                        "round": round_number,
                        "candidate": candidate_number,
                        "score": candidate["score"],
                        "audio_tokens": candidate["audio_tokens"],
                    }
                ),
                flush=True,
            )

        best = max(candidates, key=lambda item: (item["score"], item["confidence"]))
        best_dir = round_dir / f"candidate-{best['candidate']:02d}"
        best["local_video"] = download_video(best["video_url"], best_dir / "video.mp4")
        write_json(best_dir / "summary.json", best)
        if overall_best is None or (best["score"], best["confidence"]) > (
            overall_best["score"],
            overall_best["confidence"],
        ):
            overall_best = best

        passed = best["score"] >= config.target_score
        focus = None if passed else choose_focus(best["evaluation"])
        round_summary = {
            "round": round_number,
            "prompt_path": str(round_dir / "prompt.txt"),
            "candidate_scores": [candidate["score"] for candidate in candidates],
            "selected_candidate": best["candidate"],
            "selected_score": best["score"],
            "next_focused_repair": focus,
            "passed": passed,
        }
        round_summaries.append(round_summary)
        write_json(round_dir / "summary.json", round_summary)
        print(
            json.dumps(
                {"step": "round_complete", **round_summary}, ensure_ascii=False
            ),
            flush=True,
        )
        if passed:
            break

    manifest = {
        "strategy": "two-stage-llm-video-eval-focused-repair-best-of-n",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "target_score": config.target_score,
        "completed_rounds": len(round_summaries),
        "generated_candidates": sum(
            len(item["candidate_scores"]) for item in round_summaries
        ),
        "passed": bool(overall_best and overall_best["score"] >= config.target_score),
        "best_candidate": overall_best,
        "rounds": round_summaries,
    }
    write_json(run_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--brief-file", type=Path, help="UTF-8 video brief file")
    parser.add_argument(
        "--max-rounds", type=int, default=5, help="Maximum improvement rounds"
    )
    parser.add_argument(
        "--candidates", type=int, default=2, help="Candidates generated per round"
    )
    parser.add_argument(
        "--target-score", type=float, default=3.5, help="Early-stop score from 0 to 5"
    )
    parser.add_argument(
        "--timeout", type=int, default=1200, help="Per-video polling timeout in seconds"
    )
    parser.add_argument("--video-model", default=DEFAULT_VIDEO_MODEL)
    parser.add_argument("--prompt-model", default=DEFAULT_PROMPT_MODEL)
    parser.add_argument("--analysis-model", default=DEFAULT_ANALYSIS_MODEL)
    parser.add_argument("--evaluator-model", default=DEFAULT_EVALUATOR_MODEL)
    parser.add_argument("--run-id", help="Unique output directory name under runs/")
    args = parser.parse_args()
    if args.max_rounds < 1 or args.candidates < 1:
        parser.error("--max-rounds and --candidates must be at least 1")
    if not 0 <= args.target_score <= 5:
        parser.error("--target-score must be between 0 and 5")
    brief = (
        args.brief_file.read_text(encoding="utf-8").strip()
        if args.brief_file
        else DEFAULT_BRIEF
    )
    run_loop(
        LoopConfig(
            brief=brief,
            max_rounds=args.max_rounds,
            candidates_per_round=args.candidates,
            target_score=args.target_score,
            timeout_seconds=args.timeout,
            video_model=args.video_model,
            prompt_model=args.prompt_model,
            analysis_model=args.analysis_model,
            evaluator_model=args.evaluator_model,
            run_id=args.run_id,
        )
    )


if __name__ == "__main__":
    main()
