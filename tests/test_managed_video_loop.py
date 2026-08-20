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

    def test_evaluate_video_sends_direct_multimodal_input(self):
        captured = {}

        def fake_call(payload, timeout=300):
            captured.update(payload)
            return {
                "response_id": "resp-1",
                "model": "seed-2-0-lite-260428",
                "usage": {"input_tokens_details": {"audio_tokens": 12}},
                "result": {"score": 3.7, "confidence": 0.8},
            }

        with patch.object(loop, "call_responses", side_effect=fake_call):
            result = loop.evaluate_video(
                "https://example.test/video.mp4", "test brief", "evaluator-model"
            )

        content = captured["input"][0]["content"]
        self.assertEqual(captured["model"], "evaluator-model")
        self.assertEqual(content[0]["type"], "input_video")
        self.assertEqual(content[0]["video_url"], "https://example.test/video.mp4")
        self.assertEqual(result["evaluation"]["score"], 3.7)

    def test_loop_selects_best_candidate_and_stops_at_target(self):
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
            loop, "evaluate_video", side_effect=lambda *_: next(evaluations)
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
