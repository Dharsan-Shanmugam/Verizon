"""Claude transport and advisory context tests; never call the real provider."""
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch

from vdi import ai
from tests._util import HAS_ARTIFACTS, SKIP_ART


class TestClaudeClient(unittest.TestCase):
    def response(self, data):
        return io.BytesIO(json.dumps(data).encode())

    def test_missing_key_and_status_never_reveal_credentials(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": ""}):
            self.assertFalse(ai.status()["configured"])
            with patch("vdi.ai.urllib.request.urlopen") as send:
                with self.assertRaises(ai.AIError) as err:
                    ai.models()
                self.assertEqual(err.exception.status, 503)
                send.assert_not_called()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
            self.assertNotIn("test-key-not-real", json.dumps(ai.status()))

    def test_model_discovery(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
                patch("vdi.ai.urllib.request.urlopen", return_value=self.response({
                    "data": [{"id": "test-model", "display_name": "Test Claude"}]})) as send:
            self.assertEqual(ai.models()["models"][0]["id"], "test-model")
            req = send.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.anthropic.com/v1/models?limit=1000")
            self.assertEqual(req.get_header("X-api-key"), "test-key-not-real")
            self.assertIsNone(req.data)

    def test_message_contract_and_no_execution_tools(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
                patch("vdi.ai.urllib.request.urlopen", return_value=self.response({
                    "content": [{"type": "text", "text": "Synthetic advice."}],
                    "model": "test-model", "stop_reason": "max_tokens",
                    "usage": {"input_tokens": 10, "output_tokens": 5}})) as send:
            answer = ai.explain({"synthetic": True}, "Explain", "test-model")
            self.assertEqual(answer["text"], "Synthetic advice.")
            self.assertTrue(answer["advisory_only"])
            self.assertTrue(answer["truncated"])
            payload = json.loads(send.call_args.args[0].data)
            self.assertEqual(payload["model"], "test-model")
            self.assertEqual(payload["max_tokens"], 1200)
            self.assertNotIn("tools", payload)
            self.assertNotIn("test-key-not-real", json.dumps(payload))
            self.assertEqual(send.call_args.kwargs["timeout"], 60)

    def test_provider_failures_are_explicit_and_redacted(self):
        errors = [
            urllib.error.HTTPError("url", code, "test-key-not-real", {}, None)
            for code in (400, 401, 403, 404, 429, 500, 529)
        ] + [urllib.error.URLError("test-key-not-real"), TimeoutError("test-key-not-real")]
        for error in errors:
            with self.subTest(error=type(error).__name__, code=getattr(error, "code", None)), \
                    patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
                    patch("vdi.ai.urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ai.AIError) as caught:
                    ai.models()
                self.assertNotIn("test-key-not-real", str(caught.exception))

    def test_invalid_provider_payloads_and_context_limits(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
            for data in ({}, {"content": []}, {"content": [{"type": "thinking"}]}, []):
                with self.subTest(data=data), patch("vdi.ai.urllib.request.urlopen",
                                                   return_value=self.response(data)):
                    with self.assertRaises(ai.AIError):
                        ai.explain({}, "Explain", "test-model")
            with patch("vdi.ai.urllib.request.urlopen", return_value=io.BytesIO(b"invalid")):
                with self.assertRaises(ai.AIError):
                    ai.models()
            with patch("vdi.ai.urllib.request.urlopen") as send:
                with self.assertRaises(ValueError):
                    ai.explain({"large": "x" * 40_001}, "Explain", "test-model")
                send.assert_not_called()

    def test_history_validation_and_message_forwarding(self):
        history = [{"role": "user", "content": "Prior question"},
                   {"role": "assistant", "content": "Prior answer"}]
        with patch("vdi.ai._request", return_value={"content": [{"type": "text", "text": "Follow-up"}]}) as send:
            ai.explain({"current": 42}, "Why?", "test-model", history=history)
            messages = send.call_args.args[1]["messages"]
            self.assertEqual(messages[:2], history)
            self.assertEqual(messages[-1]["role"], "user")
            self.assertIn('"current": 42', messages[-1]["content"])
        invalid = [None, {}, history[:1], history[::-1], history * 4,
                   [{"role": "user", "content": ""}, history[1]],
                   [{"role": "user", "content": "x" * 6001}, history[1]],
                   [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 3000}
                    for i in range(6)]]
        for value in invalid:
            with self.subTest(history=value), self.assertRaises(ValueError):
                ai.validate_history(value)


@unittest.skipUnless(HAS_ARTIFACTS, SKIP_ART)
class TestAdvisoryContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from vdi.api import AppState
        cls.state = AppState(auto_train=False)

    def request(self, view, selection=None):
        return {"view": view, "selection": selection or {}, "question": "Explain", "model": "test-model"}

    def test_all_views_build_bounded_server_context(self):
        state = self.state
        selections = {
            "forecast": {"event_id": state.scores["events"][0]["event_id"]},
            "live": {"seconds": state.live["times"][0]},
            "simulator": {"attendance_pct": 110, "traffic_moved_pct": 20, "temp_capacity_mbps": 200},
        }
        for view in ("overview", "forecast", "live", "routing", "simulator", "gatherings", "health", "catalog"):
            with self.subTest(view=view), patch("vdi.api.ai.explain", return_value={"text": "advice"}) as explain:
                self.assertEqual(state.ai_explain(self.request(view, selections.get(view))), {"text": "advice"})
                context = explain.call_args.args[0]
                self.assertTrue(context["synthetic"])
                self.assertEqual(context["view"], view)
                self.assertLessEqual(len(json.dumps(context, ensure_ascii=True, allow_nan=False)), 40_000)
                self.assertNotIn("data_dir", context["facts"])
                self.assertNotIn("history", explain.call_args.kwargs)

    def test_filtered_context_comparison_and_conversation(self):
        history = [{"role": "user", "content": "Compare"}, {"role": "assistant", "content": "First plan"}]
        request = self.request("simulator", {
            "attendance_pct": 120, "traffic_moved_pct": 30,
            "comparison": {"attendance_pct": 100, "traffic_moved_pct": 0}})
        request["history"] = history
        with patch("vdi.api.ai.explain") as explain:
            self.state.ai_explain(request)
            context = explain.call_args.args[0]
            self.assertEqual(context["facts"]["inputs"]["attendance_pct"], 120)
            self.assertEqual(context["comparison"]["inputs"]["attendance_pct"], 100)
            self.assertEqual(explain.call_args.kwargs["history"], history)
            self.assertLessEqual(len(json.dumps(context)), 40000)
            venue = self.state.cache["overview"]["venue_board"][0]["venue_id"]
            self.state.ai_explain(self.request("overview", {"venue": venue, "risk": "all"}))
            facts = explain.call_args.args[0]["facts"]
            self.assertEqual([row["venue_id"] for row in facts["venue_board"]], [venue])
            self.assertIn("portfolio-wide", facts["scope_note"])

    def test_live_context_has_no_future_verification(self):
        decision = next(d for d in self.state.live["decisions"] if d["verify_t"] > d["t"])
        with patch("vdi.api.ai.explain") as explain:
            self.state.ai_explain(self.request("live", {"seconds": decision["t"],
                                                      "decision_id": decision["decision_id"]}))
            facts = explain.call_args.args[0]["facts"]
            self.assertEqual(len(facts["decisions"]), 1)
            self.assertNotIn("verify", facts["decisions"][0])
            self.assertNotIn("simulated_verify", facts["decisions"][0])
            self.state.ai_explain(self.request("live", {"seconds": decision["verify_t"],
                                                      "decision_id": decision["decision_id"]}))
            self.assertIn("simulated_verify", explain.call_args.args[0]["facts"]["decisions"][0])

    def test_invalid_input_never_calls_provider(self):
        invalid = [
            {"view": "unknown"}, {**self.request("overview"), "question": ""},
            {**self.request("overview"), "question": "x" * 2001},
            {**self.request("overview"), "model": ["invalid"]},
            {**self.request("overview"), "selection": []},
            self.request("forecast"), self.request("live", {"seconds": float("nan")}),
            self.request("live", {"seconds": True}), self.request("live", {"seconds": 1e9}),
        ]
        with patch("vdi.api.ai.explain") as explain:
            for body in invalid:
                with self.subTest(body=body), self.assertRaises(ValueError):
                    self.state.ai_explain(body)
            explain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
