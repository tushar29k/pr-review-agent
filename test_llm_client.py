"""Tests for llm_client.py — the free-tier provider client.

    python3 test_llm_client.py
Never touches a real API: urlopen is stubbed, so these run offline.
"""
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

import llm_client
from llm_client import FreeLLMClient, FreeLLMError


def _resp(payload):
    r = mock.Mock()
    r.read.return_value = json.dumps(payload).encode()
    return r


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO())


class ClientTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("LLM_MODEL", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_no_key_means_no_client(self):
        # the whole point: unset key -> caller keeps its mock path
        self.assertIsNone(FreeLLMClient.from_env())

    def test_unknown_provider_rejected(self):
        os.environ["LLM_API_KEY"] = "k"
        with self.assertRaises(FreeLLMError):
            FreeLLMClient(provider="nope")

    def test_gemini_request_shape(self):
        os.environ.update(LLM_API_KEY="secret-key", LLM_PROVIDER="gemini")
        payload = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        with mock.patch.object(llm_client.urllib.request, "urlopen",
                               return_value=_resp(payload)) as u, \
             mock.patch.object(llm_client.time, "sleep"):
            c = FreeLLMClient.from_env()
            self.assertEqual(c.generate("hello"), "hi")
            req = u.call_args[0][0]
            url = req.full_url
            self.assertIn("generativelanguage.googleapis.com", url)
            self.assertIn("gemini-2.0-flash", url)  # default model
            body = json.loads(req.data.decode())
            self.assertEqual(body["contents"][0]["parts"][0]["text"], "hello")

    def test_openrouter_request_shape(self):
        os.environ.update(LLM_API_KEY="secret-key",
                          LLM_PROVIDER="openrouter")
        payload = {"choices": [{"message": {"content": "yo"}}]}
        with mock.patch.object(llm_client.urllib.request, "urlopen",
                               return_value=_resp(payload)) as u, \
             mock.patch.object(llm_client.time, "sleep"):
            c = FreeLLMClient.from_env()
            self.assertEqual(c.generate("hello"), "yo")
            req = u.call_args[0][0]
            self.assertEqual(req.full_url,
                             "https://openrouter.ai/api/v1/chat/completions")
            self.assertEqual(req.get_header("Authorization"),
                             "Bearer secret-key")
            body = json.loads(req.data.decode())
            self.assertIn(":free", body["model"])  # free-tier slug only

    def test_model_override(self):
        os.environ.update(LLM_API_KEY="k", LLM_MODEL="custom-model")
        c = FreeLLMClient.from_env()
        self.assertEqual(c.model, "custom-model")

    def test_retry_once_on_429_then_success(self):
        os.environ.update(LLM_API_KEY="k", LLM_PROVIDER="openrouter")
        payload = {"choices": [{"message": {"content": "recovered"}}]}
        with mock.patch.object(
                llm_client.urllib.request, "urlopen",
                side_effect=[_http_error(429), _resp(payload)]) as u, \
             mock.patch.object(llm_client.time, "sleep") as s:
            c = FreeLLMClient.from_env()
            self.assertEqual(c.generate("hello"), "recovered")
            self.assertEqual(u.call_count, 2)
            s.assert_called_once()  # one backoff, not a hammer loop

    def test_total_failure_raises_without_key_in_message(self):
        os.environ.update(LLM_API_KEY="super-secret-key",
                          LLM_PROVIDER="gemini")
        with mock.patch.object(llm_client.urllib.request, "urlopen",
                               side_effect=[_http_error(500),
                                            _http_error(503)]), \
             mock.patch.object(llm_client.time, "sleep"):
            c = FreeLLMClient.from_env()
            with self.assertRaises(FreeLLMError) as ctx:
                c.generate("hello")
            # the key must never appear in error text (logs, UI, anywhere)
            self.assertNotIn("super-secret-key", str(ctx.exception))

    def test_bad_response_shape_raises(self):
        os.environ.update(LLM_API_KEY="k", LLM_PROVIDER="gemini")
        with mock.patch.object(llm_client.urllib.request, "urlopen",
                               return_value=_resp({"nope": 1})), \
             mock.patch.object(llm_client.time, "sleep"):
            c = FreeLLMClient.from_env()
            with self.assertRaises(FreeLLMError):
                c.generate("hello")


if __name__ == "__main__":
    unittest.main(verbosity=1)
