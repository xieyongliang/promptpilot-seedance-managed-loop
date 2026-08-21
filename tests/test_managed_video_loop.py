import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import managed_video_loop as loop


class ManagedVideoLoopTests(unittest.TestCase):
    def test_parse_json_text_accepts_fenced_object_and_rejects_array(self):
        self.assertEqual(loop.parse_json_text('```json\n{"score": 4}\n```'), {"score": 4})
        with self.assertRaises(RuntimeError):
            loop.parse_json_text("[]")

    def test_normalize_evaluation_clamps_numbers_and_normalizes_lists(self):
        result = loop.normalize_evaluation(
            {
                "score": 8,
                "confidence": -1,
                "failed_requirements": "missing speech",
            }
        )
        self.assertEqual(result["score"], 5.0)
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["failed_requirements"], ["missing speech"])
        self.assertEqual(result["audio_evidence"], [])

    def test_two_stage_evaluation_sends_video_only_to_analysis(self):
        captured = []

        def fake_call(payload, timeout=300):
            captured.append(payload)
            if len(captured) == 1:
                return {
                    "response_id": "analysis-1",
                    "model": "analysis-model",
                    "usage": {"input_tokens_details": {"audio_tokens": 12}},
                    "result": {"summary": "observed", "audio_evidence": ["music"]},
                }
            return {
                "response_id": "eval-1",
                "model": "evaluator-model",
                "usage": {},
                "result": {"score": 3.7, "confidence": 0.8},
            }

        with patch.object(loop, "call_responses", side_effect=fake_call):
            analysis = loop.analyze_video(
                "https://example.test/video.mp4", "test brief", "analysis-model"
            )
            result = loop.evaluate_video_analysis(
                "test brief", analysis["analysis"], "evaluator-model"
            )

        analysis_content = captured[0]["input"][0]["content"]
        evaluation_content = captured[1]["input"][0]["content"]
        self.assertEqual(captured[0]["model"], "analysis-model")
        self.assertEqual(analysis_content[0]["type"], "input_video")
        self.assertEqual(
            analysis_content[0]["video_url"], "https://example.test/video.mp4"
        )
        self.assertEqual(captured[1]["model"], "evaluator-model")
        self.assertEqual([item["type"] for item in evaluation_content], ["input_text"])
        self.assertIn("OBSERVABLE EVIDENCE REPORT", evaluation_content[0]["text"])
        self.assertEqual(result["evaluation"]["score"], 3.7)

    def test_loop_selects_best_candidate_and_stops_at_target(self):
        analyses = iter(
            [
                {
                    "response_id": "analysis-1",
                    "model": "analysis-model",
                    "usage": {"input_tokens_details": {"audio_tokens": 10}},
                    "analysis": {"summary": "candidate 1 evidence"},
                },
                {
                    "response_id": "analysis-2",
                    "model": "analysis-model",
                    "usage": {"input_tokens_details": {"audio_tokens": 14}},
                    "analysis": {"summary": "candidate 2 evidence"},
                },
            ]
        )
        evaluations = iter(
            [
                {
                    "response_id": "eval-1",
                    "model": "evaluator-model",
                    "usage": {"input_tokens_details": {"audio_tokens": 10}},
                    "evaluation": loop.normalize_evaluation(
                        {
                            "score": 2.5,
                            "confidence": 0.9,
                            "failed_requirements": ["The exact speech is missing."],
                            "highest_priority_failure": "The exact speech is missing.",
                        }
                    ),
                },
                {
                    "response_id": "eval-2",
                    "model": "evaluator-model",
                    "usage": {"input_tokens_details": {"audio_tokens": 14}},
                    "evaluation": loop.normalize_evaluation(
                        {
                            "score": 4.0,
                            "confidence": 0.8,
                            "passed_requirements": ["The exact speech is present."],
                        }
                    ),
                },
            ]
        )
        submissions = iter([{"id": "task-1"}, {"id": "task-2"}])

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            loop, "RUNS_DIR", Path(temp_dir)
        ), patch.object(
            loop,
            "generate_video_prompt",
            return_value={
                "prompt": "generated prompt",
                "model": "prompt-model",
                "response_id": "prompt-1",
                "usage": {},
                "focused_repair": None,
            },
        ), patch.object(
            loop, "submit_video", side_effect=lambda *_: next(submissions)
        ), patch.object(
            loop,
            "wait_for_video",
            side_effect=lambda task_id, _: {
                "status": "succeeded",
                "content": {"video_url": f"https://example.test/{task_id}.mp4"},
            },
        ), patch.object(
            loop, "analyze_video", side_effect=lambda *_: next(analyses)
        ), patch.object(
            loop, "evaluate_video_analysis", side_effect=lambda *_: next(evaluations)
        ), patch.object(
            loop,
            "download_video",
            return_value={"status": "downloaded", "path": "/tmp/video.mp4"},
        ):
            manifest = loop.run_loop(
                loop.LoopConfig(
                    brief="test brief",
                    max_rounds=5,
                    candidates_per_round=2,
                    target_score=3.5,
                    run_id="offline-test",
                )
            )
            saved = json.loads(
                (Path(temp_dir) / "offline-test" / "manifest.json").read_text()
            )

        self.assertTrue(manifest["passed"])
        self.assertEqual(manifest["completed_rounds"], 1)
        self.assertEqual(manifest["generated_candidates"], 2)
        self.assertEqual(manifest["best_candidate"]["task_id"], "task-2")
        self.assertEqual(saved["best_candidate"]["score"], 4.0)


if __name__ == "__main__":
    unittest.main()
