#!/usr/bin/env python3
"""MCP tools that let a Managed Agent run the video generation/evaluation loop."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from video_agent_loop import (
    analyze_video,
    evaluate_video,
    generate_video_prompt,
    get_video_task,
    submit_video,
)


mcp = FastMCP("seedance-promptpilot-loop")


@mcp.tool()
def prompt_generate(
    brief: str,
    current_prompt: str = "",
    feedback: str = "",
) -> dict:
    """Generate or improve a production-ready Seedance prompt with PromptPilot."""
    return generate_video_prompt(brief, current_prompt or None, feedback or None)


@mcp.tool()
def video_submit(prompt: str) -> dict:
    """Submit an asynchronous Seedance video-generation task."""
    return submit_video(prompt)


@mcp.tool()
def video_status(task_id: str) -> dict:
    """Get Seedance task status; a succeeded task includes content.video_url."""
    return get_video_task(task_id)


@mcp.tool()
def video_audio_analyze(video_url: str, brief: str) -> dict:
    """Analyze actual video and audio evidence with Seed 2.0 Lite."""
    result = analyze_video(video_url, brief)
    return {
        "response_id": result["response_id"],
        "model": result["model"],
        "usage": result["usage"],
        "analysis": result["analysis"],
    }


@mcp.tool()
def promptpilot_eval(brief: str, video_analysis: dict, round_number: int = 1) -> dict:
    """Score generated-video compliance and return actionable retry feedback."""
    return evaluate_video(brief, video_analysis, round_number)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
