#!/usr/bin/env python3
"""PromptPilot -> Seedance -> Seed 2.0 Lite -> PromptPilot evaluation loop."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from agent_pilot.eval import evaluate
from agent_pilot.pe import generate_prompt_stream


ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"
ARK_BASE_URL = "https://ark.ap-southeast.bytepluses.com/api/v3"
PROMPTPILOT_URL = "https://prompt-pilot.ap-southeast.bytepluses.com"
SEEDANCE_MODEL = "dreamina-seedance-2-5-260628"
ANALYSIS_MODEL = "seed-2-0-lite-260428"

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


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def promptpilot_config() -> dict[str, str]:
    return {
        "api_key": required_env("AGENTPILOT_API_KEY"),
        "workspace_id": required_env("AGENTPILOT_WORKSPACE_ID"),
        "api_url": os.getenv("AGENTPILOT_API_URL", PROMPTPILOT_URL),
    }


def ark_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {required_env('ARK_API_KEY')}",
        "Content-Type": "application/json",
    }


def extract_response_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def parse_json_text(text: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    return json.loads(cleaned)


def validate_and_clean_video_prompt(prompt: str) -> str:
    cleaned = re.sub(r"\{\{[A-Za-z0-9_.-]+\}\}", "", prompt)
    cleaned = re.sub(r"</?[A-Za-z][^>]*>", "", cleaned)
    cleaned = re.sub(r",\s*(?:and\s*)?;", ";", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    forbidden = (
        "output contract",
        "pre-production_check",
        "final_promotional_video",
        "document your internal",
        "output the final video files",
    )
    hits = [item for item in forbidden if item in cleaned.lower()]
    missing_references = [
        reference
        for reference in ("[Image 1]", "[Image 2]", "[Video 1]", "[Audio 1]")
        if reference not in cleaned
    ]
    if missing_references:
        reference_contract = (
            "Use [Image 1] for the opening, [Image 2] for the final product frame, "
            "[Video 1] for first-person framing and motion language, and [Audio 1] "
            "as continuous background music."
        )
        cleaned = f"{reference_contract} {cleaned}"
    if hits or "{{" in cleaned or "}}" in cleaned:
        raise RuntimeError(
            "PromptPilot final prompt failed validation: "
            f"forbidden={hits}"
        )
    return cleaned


def generate_video_prompt(
    brief: str,
    current_prompt: str | None = None,
    feedback: str | None = None,
) -> dict[str, Any]:
    output_contract = """Return only the final English prompt that can be sent directly as Seedance content.text. Use [Image 1], [Image 2], [Video 1], and [Audio 1] for the supplied references. Do not use template variables, placeholders, XML tags, pre-production checks, chain-of-thought requests, file-export instructions, post-processing steps, or directions to an operator. Do not ask Seedance to document compliance. The prompt must be concise, chronological, visually concrete, and directly executable."""
    kwargs: dict[str, Any] = {
        "task_description": f"{brief}\n\nOUTPUT CONTRACT:\n{output_contract}",
        **promptpilot_config(),
    }
    if current_prompt:
        kwargs["current_prompt"] = current_prompt
    if feedback:
        kwargs["feedback"] = (
            "Revise the video-generation prompt to fix the measured failures below. "
            "Keep it chronological, concrete, feasible, and concise. Preserve all requirements "
            "that already passed. Do not invent model parameters or post-production steps.\n\n"
            f"Evaluation feedback:\n{feedback}"
        )

    def stream_prompt(arguments: dict[str, Any]) -> tuple[str, Any]:
        chunks: list[str] = []
        usage = None
        for chunk in generate_prompt_stream(**arguments):
            if chunk.data.content:
                chunks.append(chunk.data.content)
            if chunk.data.usage:
                usage = chunk.data.usage
        return "".join(chunks).strip(), usage

    draft, draft_usage = stream_prompt(kwargs)
    if not draft:
        raise RuntimeError("PromptPilot returned an empty prompt")

    finalization_feedback = """Finalize this draft for direct submission as Seedance content.text. Output only the generation instructions, beginning immediately with the scene description or timeline. Remove all OUTPUT CONTRACT text, explanations, confirmations, capability checks, operator instructions, placeholders, XML/tags, file-output requests, and pre-production checks. Remove every technical value not present in the original brief, including invented volume percentages, codecs, frame rates, resolutions, and metadata. Preserve the original timing, exact dialogue, references, continuity rules, duration, aspect ratio, and generated-audio requirement."""
    final_prompt, final_usage = stream_prompt(
        {
            "task_description": f"{brief}\n\n{output_contract}",
            "current_prompt": draft,
            "feedback": finalization_feedback,
            **promptpilot_config(),
        }
    )
    if not final_prompt:
        raise RuntimeError("PromptPilot returned an empty finalized prompt")
    cleaned_prompt = validate_and_clean_video_prompt(final_prompt)
    return {
        "prompt": cleaned_prompt,
        "usage": final_usage,
        "draft": draft,
        "draft_usage": draft_usage,
        "finalization_raw": final_prompt,
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


def submit_video(prompt: str) -> dict[str, Any]:
    payload = {
        "model": SEEDANCE_MODEL,
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
        f"{ARK_BASE_URL}/contents/generations/tasks",
        headers=ark_headers(),
        json=payload,
        timeout=60,
    )
    if not response.ok:
        raise RuntimeError(f"Seedance submit failed ({response.status_code}): {response.text}")
    return response.json()


def get_video_task(task_id: str) -> dict[str, Any]:
    for attempt in range(1, 4):
        try:
            response = requests.get(
                f"{ARK_BASE_URL}/contents/generations/tasks/{task_id}",
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
            delay = 2**attempt
            print(
                json.dumps(
                    {
                        "step": "video_poll_retry",
                        "task_id": task_id,
                        "attempt": attempt,
                        "delay_seconds": delay,
                    }
                ),
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def wait_for_video(task_id: str, timeout_seconds: int = 1200) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        result = get_video_task(task_id)
        status = result.get("status", "unknown")
        if status != last_status:
            print(json.dumps({"step": "video", "task_id": task_id, "status": status}), flush=True)
            last_status = status
        if status == "succeeded":
            if not result.get("content", {}).get("video_url"):
                raise RuntimeError("Seedance succeeded without content.video_url")
            return result
        if status in {"failed", "cancelled", "expired"}:
            raise RuntimeError(f"Seedance task ended with status {status}: {result}")
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for Seedance task {task_id}")


def analyze_video(video_url: str, brief: str) -> dict[str, Any]:
    instruction = f"""Analyze the actual visual and audio evidence in this generated video against the requested brief below. Do not infer sounds solely from visuals. Return JSON with: summary, timeline, visual_requirements, audio_requirements, exact_speech, continuity_issues, unintended_text, passed_requirements, failed_requirements, and confidence. Every timeline claim must include timestamps. Explicitly state whether audible content exists and identify speech, music, sound effects, and silence intervals.

REQUESTED BRIEF:
{brief}"""
    payload = {
        "model": ANALYSIS_MODEL,
        "stream": False,
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
    response = requests.post(
        f"{ARK_BASE_URL}/responses",
        headers=ark_headers(),
        json=payload,
        timeout=300,
    )
    if not response.ok:
        raise RuntimeError(f"Seed analysis failed ({response.status_code}): {response.text}")
    raw = response.json()
    text = extract_response_text(raw)
    if not text:
        raise RuntimeError("Seed analysis returned no output_text")
    return {
        "response_id": raw.get("id"),
        "model": raw.get("model"),
        "usage": raw.get("usage"),
        "analysis": parse_json_text(text),
        "raw_response": raw,
    }


def evaluate_video(brief: str, analysis: dict[str, Any], round_number: int) -> dict[str, Any]:
    result = evaluate(
        example={
            "example_id": f"seedance-loop-round-{round_number}",
            "input": brief,
            "response": json.dumps(analysis, ensure_ascii=False),
        },
        metric={
            "criteria": """Treat the response as a timestamped report from a video-and-audio understanding model. Score actual compliance with the requested video from 0 to 5. Weight missing audible content, missing exact voice lines, wrong POV, missing actions, continuity defects, unintended text, geometry defects, and timing errors heavily. Reward requirements supported by timestamped evidence. Do not claim to have watched the video yourself. In the analysis, list passed requirements and concrete prompt changes for each failure so the next generation round can improve."""
        },
        **promptpilot_config(),
    )
    return result.model_dump()


@dataclass
class LoopConfig:
    brief: str
    max_rounds: int = 2
    target_score: float = 4.5
    timeout_seconds: int = 1200
    run_id: str | None = None


def run_loop(config: LoopConfig) -> dict[str, Any]:
    run_id = config.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(run_dir / "config.json", config.__dict__ | {"run_id": run_id})

    current_prompt: str | None = None
    feedback: str | None = None
    rounds: list[dict[str, Any]] = []

    for round_number in range(1, config.max_rounds + 1):
        round_dir = run_dir / f"round-{round_number:02d}"
        round_dir.mkdir()
        print(json.dumps({"step": "round_start", "round": round_number}), flush=True)

        generated = generate_video_prompt(config.brief, current_prompt, feedback)
        current_prompt = generated["prompt"]
        (round_dir / "prompt.txt").write_text(current_prompt + "\n", encoding="utf-8")
        write_json(round_dir / "promptpilot_generation.json", generated)

        submitted = submit_video(current_prompt)
        write_json(round_dir / "seedance_submit.json", submitted)
        task_id = submitted.get("id")
        if not task_id:
            raise RuntimeError(f"Seedance response has no task id: {submitted}")

        video_result = wait_for_video(task_id, config.timeout_seconds)
        write_json(round_dir / "seedance_result.json", video_result)
        video_url = video_result["content"]["video_url"]

        model_analysis = analyze_video(video_url, config.brief)
        write_json(round_dir / "seed_analysis.json", model_analysis)

        evaluation = evaluate_video(config.brief, model_analysis["analysis"], round_number)
        write_json(round_dir / "promptpilot_evaluation.json", evaluation)
        score = float(evaluation.get("score") or 0)
        feedback = str(evaluation.get("analysis") or "")

        round_summary = {
            "round": round_number,
            "task_id": task_id,
            "video_url": video_url,
            "analysis_model": model_analysis.get("model"),
            "audio_tokens": (model_analysis.get("usage") or {})
            .get("input_tokens_details", {})
            .get("audio_tokens"),
            "score": score,
            "passed": score >= config.target_score,
        }
        rounds.append(round_summary)
        write_json(round_dir / "summary.json", round_summary)
        print(json.dumps({"step": "round_complete", **round_summary}), flush=True)
        if score >= config.target_score:
            break

    manifest = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "target_score": config.target_score,
        "completed_rounds": len(rounds),
        "passed": bool(rounds and rounds[-1]["passed"]),
        "rounds": rounds,
    }
    write_json(run_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    return manifest


def resume_run(run_id: str, timeout_seconds: int) -> dict[str, Any]:
    run_dir = RUNS_DIR / run_id
    config_path = run_dir / "config.json"
    if not config_path.exists():
        raise RuntimeError(f"Run does not exist or has no config: {run_dir}")
    stored_config = json.loads(config_path.read_text(encoding="utf-8"))
    brief = stored_config["brief"]
    target_score = float(stored_config["target_score"])

    incomplete: list[Path] = []
    for round_dir in sorted(run_dir.glob("round-*")):
        if (round_dir / "seedance_submit.json").exists() and not (
            round_dir / "summary.json"
        ).exists():
            incomplete.append(round_dir)
    if not incomplete:
        manifest_path = run_dir / "manifest.json"
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        raise RuntimeError(f"No incomplete round found in {run_dir}")

    round_dir = incomplete[-1]
    round_number = int(round_dir.name.rsplit("-", 1)[1])
    submitted = json.loads(
        (round_dir / "seedance_submit.json").read_text(encoding="utf-8")
    )
    task_id = submitted.get("id")
    if not task_id:
        raise RuntimeError(f"Saved submit response has no task id: {submitted}")

    video_result = wait_for_video(task_id, timeout_seconds)
    write_json(round_dir / "seedance_result.json", video_result)
    video_url = video_result["content"]["video_url"]
    model_analysis = analyze_video(video_url, brief)
    write_json(round_dir / "seed_analysis.json", model_analysis)
    evaluation = evaluate_video(brief, model_analysis["analysis"], round_number)
    write_json(round_dir / "promptpilot_evaluation.json", evaluation)
    score = float(evaluation.get("score") or 0)
    round_summary = {
        "round": round_number,
        "task_id": task_id,
        "video_url": video_url,
        "analysis_model": model_analysis.get("model"),
        "audio_tokens": (model_analysis.get("usage") or {})
        .get("input_tokens_details", {})
        .get("audio_tokens"),
        "score": score,
        "passed": score >= target_score,
    }
    write_json(round_dir / "summary.json", round_summary)
    summaries = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(run_dir.glob("round-*/summary.json"))
    ]
    manifest = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "target_score": target_score,
        "completed_rounds": len(summaries),
        "passed": bool(summaries and summaries[-1]["passed"]),
        "rounds": summaries,
    }
    write_json(run_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    return manifest


def continue_run(run_id: str, max_rounds: int, timeout_seconds: int) -> dict[str, Any]:
    run_dir = RUNS_DIR / run_id
    config_path = run_dir / "config.json"
    if not config_path.exists():
        raise RuntimeError(f"Run does not exist or has no config: {run_dir}")
    stored_config = json.loads(config_path.read_text(encoding="utf-8"))
    brief = stored_config["brief"]
    target_score = float(stored_config["target_score"])
    stored_config["max_rounds"] = max_rounds
    stored_config["timeout_seconds"] = timeout_seconds
    write_json(config_path, stored_config)

    summaries = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(run_dir.glob("round-*/summary.json"))
    ]
    if not summaries:
        raise RuntimeError(f"No completed round found in {run_dir}")
    if summaries[-1].get("passed"):
        return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if len(summaries) >= max_rounds:
        raise RuntimeError(
            f"Run already has {len(summaries)} completed rounds; max_rounds={max_rounds}"
        )

    previous_round_dir = run_dir / f"round-{len(summaries):02d}"
    current_prompt = (previous_round_dir / "prompt.txt").read_text(encoding="utf-8").strip()
    previous_evaluation = json.loads(
        (previous_round_dir / "promptpilot_evaluation.json").read_text(encoding="utf-8")
    )
    feedback = str(previous_evaluation.get("analysis") or "")

    for round_number in range(len(summaries) + 1, max_rounds + 1):
        round_dir = run_dir / f"round-{round_number:02d}"
        if round_dir.exists():
            if any(round_dir.iterdir()):
                raise RuntimeError(
                    f"Round directory already contains partial state; resume it first: {round_dir}"
                )
        else:
            round_dir.mkdir()
        print(json.dumps({"step": "round_start", "round": round_number}), flush=True)

        generated = generate_video_prompt(brief, current_prompt, feedback)
        current_prompt = generated["prompt"]
        (round_dir / "prompt.txt").write_text(current_prompt + "\n", encoding="utf-8")
        write_json(round_dir / "promptpilot_generation.json", generated)

        submitted = submit_video(current_prompt)
        write_json(round_dir / "seedance_submit.json", submitted)
        task_id = submitted.get("id")
        if not task_id:
            raise RuntimeError(f"Seedance response has no task id: {submitted}")
        video_result = wait_for_video(task_id, timeout_seconds)
        write_json(round_dir / "seedance_result.json", video_result)
        video_url = video_result["content"]["video_url"]

        model_analysis = analyze_video(video_url, brief)
        write_json(round_dir / "seed_analysis.json", model_analysis)
        evaluation = evaluate_video(brief, model_analysis["analysis"], round_number)
        write_json(round_dir / "promptpilot_evaluation.json", evaluation)
        score = float(evaluation.get("score") or 0)
        feedback = str(evaluation.get("analysis") or "")
        round_summary = {
            "round": round_number,
            "task_id": task_id,
            "video_url": video_url,
            "analysis_model": model_analysis.get("model"),
            "audio_tokens": (model_analysis.get("usage") or {})
            .get("input_tokens_details", {})
            .get("audio_tokens"),
            "score": score,
            "passed": score >= target_score,
        }
        summaries.append(round_summary)
        write_json(round_dir / "summary.json", round_summary)
        print(json.dumps({"step": "round_complete", **round_summary}), flush=True)
        if round_summary["passed"]:
            break

    manifest = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "target_score": target_score,
        "completed_rounds": len(summaries),
        "passed": bool(summaries and summaries[-1]["passed"]),
        "rounds": summaries,
    }
    write_json(run_dir / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief-file", type=Path)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--target-score", type=float, default=4.5)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--run-id")
    parser.add_argument("--resume-run")
    parser.add_argument("--continue-run")
    args = parser.parse_args()
    if args.resume_run:
        resume_run(args.resume_run, args.timeout)
        return
    if args.continue_run:
        continue_run(args.continue_run, args.max_rounds, args.timeout)
        return
    if args.max_rounds < 1:
        parser.error("--max-rounds must be at least 1")
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
            target_score=args.target_score,
            timeout_seconds=args.timeout,
            run_id=args.run_id,
        )
    )


if __name__ == "__main__":
    main()
