"""Free-tier LLM client. stdlib only — no new deps, keeps Render light.

Env:
  LLM_PROVIDER  gemini (default) | openrouter
  LLM_MODEL     override the default model for the provider
  LLM_API_KEY   the key. FreeLLMClient.from_env() returns None without it,
                so callers keep their mock path with zero behavior change.

Gemini = Google AI Studio free tier (no card needed), key rides in the
query param — that's how their REST API takes it; it's never logged.
OpenRouter = OpenAI-compatible /chat/completions with a :free model slug.

Resilience: 20s timeout, one retry with backoff on 429/5xx, then
FreeLLMError whose message never contains the key.
"""
import json
import os
import time
import urllib.request
import urllib.error


class FreeLLMError(RuntimeError):
    pass


_TIMEOUT = 20
_BACKOFF = 1.5

_DEFAULTS = {
    "gemini": "gemini-3.8-flash",
    "openrouter": "openai/gpt-oss-20b:free",
}


class FreeLLMClient:
    def __init__(self, provider=None, model=None, api_key=None):
        self.provider = (provider or os.environ.get("LLM_PROVIDER",
                                                     "gemini")).lower()
        if self.provider not in _DEFAULTS:
            raise FreeLLMError(f"unknown LLM_PROVIDER '{self.provider}' "
                               f"— want one of {sorted(_DEFAULTS)}")
        self.model = model or os.environ.get("LLM_MODEL") or \
            _DEFAULTS[self.provider]
        self.api_key = api_key or os.environ.get("LLM_API_KEY")
        if not self.api_key:
            raise FreeLLMError("no LLM_API_KEY in the environment")

    @classmethod
    def from_env(cls):
        # None = no key configured → caller stays on its mock path
        if not os.environ.get("LLM_API_KEY"):
            return None
        return cls()

    @property
    def label(self):
        return f"{self.provider}:{self.model}"

    def generate(self, prompt, max_tokens=256, temperature=0.7):
        req = self._request(prompt, max_tokens, temperature)
        return self._send(req)

    def _request(self, prompt, max_tokens, temperature):
        if self.provider == "gemini":
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{self.model}:generateContent?key={self.api_key}")
            body = {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens,
                                        "temperature": temperature}}
            data = json.dumps(body).encode()
            return urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
        # openrouter — openai-compatible chat completions
        url = "https://openrouter.ai/api/v1/chat/completions"
        body = {"model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens, "temperature": temperature}
        return urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})

    def _send(self, req):
        last = None
        for attempt in (0, 1):
            try:
                resp = urllib.request.urlopen(req, timeout=_TIMEOUT)
                return self._parse(json.loads(resp.read().decode()))
            except urllib.error.HTTPError as e:
                last = e
                # 429/5xx are worth one retry; anything else is final
                if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(_BACKOFF)
                    continue
                raise FreeLLMError(
                    f"{self.provider} rejected the request "
                    f"(HTTP {e.code})") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                if attempt == 0:
                    time.sleep(_BACKOFF)
                    continue
                raise FreeLLMError(
                    f"{self.provider} unreachable ({type(e).__name__})") from None
        # unreachable — both attempts raised without the typed branches
        # catching them (defensive; keeps the type checker honest)
        raise FreeLLMError(f"{self.provider} call failed ({last})") from None

    def _parse(self, payload):
        try:
            if self.provider == "gemini":
                return payload["candidates"][0]["content"]["parts"][0]["text"]
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise FreeLLMError(
                f"{self.provider} returned an unexpected response shape") \
                from None
