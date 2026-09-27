"""GET /info reports the real-LLM status honestly.  python3 test_info.py

Runs in mock mode (no key in the environment): real_llm must be False and
provider/model must be None. Never touches a real API.
"""
import os

os.environ.pop("LLM_API_KEY", None)  # the mock path is what we're checking
os.environ.pop("REVIEWER_BACKEND", None)

from fastapi.testclient import TestClient  # noqa: E402

import service  # noqa: E402


def main():
    r = TestClient(service.app).get("/info")
    assert r.status_code == 200, f"/info -> {r.status_code}"
    body = r.json()
    for key in ("real_llm", "provider", "model"):
        assert key in body, f"/info missing {key!r}: {body}"
    assert body["real_llm"] is False, f"no key set, want mock: {body}"
    assert body["provider"] is None and body["model"] is None, body
    print("info ok: mock mode reported honestly")


if __name__ == "__main__":
    main()
