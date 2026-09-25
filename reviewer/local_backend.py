"""Local-model reviewer backend: a small instruct model via transformers.

Same one-method shape as MockBackend, so Reviewer needs no changes.
Runs fully offline once the weights are in the HF cache (they are on the
dev box; the box itself installs nothing new). The default is
Qwen2-0.5B-Instruct — small enough for CPU, good enough for a first pass.
Pick another instruct model with the LOCAL_MODEL env var.

Like the anthropic backend it fails soft: transformers/torch missing or
weights not cached and not downloadable (offline box) -> mock heuristics,
so `reviewer: local` always runs end to end and stays useful in evals.
"""

from __future__ import annotations

import json
import os
import sys

from .config import confidence_to_severity
from .diff_parser import FileDiff
from .reviewer import MockBackend, ReviewBackend

_ENV_MODEL = "LOCAL_MODEL"
_DEFAULT_MODEL = "Qwen/Qwen2-0.5B-Instruct"  # fits CPU, cached on the dev box
_SEVERITIES = {"critical", "warning", "info"}

_OFFLINE = os.environ.get("LOCAL_OFFLINE") == "1"
if _OFFLINE:
    # huggingface_hub snapshots HF_HUB_OFFLINE at import time — so this has to
    # be set here, before transformers or the hub get imported anywhere below
    os.environ["HF_HUB_OFFLINE"] = "1"

# same strict schema as the API backends; small models wander, so the
# parser below also slices prose down to the outermost [ ... ].
# confidence is calibrated on our side (config.py) — the model just reports
# honestly how sure it is; 0.8+ lands critical, 0.5+ warning, below is info.
_SYSTEM_PROMPT = (
    "You review code diffs. Reply with ONLY a JSON array of findings, no prose. "
    "Each finding: {\"severity\": \"critical\"|\"warning\"|\"info\", "
    "\"line\": <new-file line number or null>, "
    "\"confidence\": <0.0-1.0, how sure you are this is a real problem>, "
    "\"message\": \"<why it matters>\"}. "
    "Only flag added lines. critical = exploitable bugs/leaked secrets, "
    "warning = real defects, info = minor. Empty array if nothing is wrong. "
    "Report confidence honestly — don't inflate it to get attention."
)


def _file_prompt(f: FileDiff) -> str:
    # one file per call: keeps prompts small and findings attributable
    lines = []
    for h in f.hunks:
        marker = {"add": "+", "del": "-", "ctx": " "}[h.kind]
        no = h.new_no if h.new_no is not None else h.old_no
        lines.append(f"{marker} {no}: {h.text}")
    new_flag = " (new file)" if f.is_new else ""
    return ("File: " + f.path + new_flag +
            "\nDiff (line numbers are new-file lines):\n" +
            "\n".join(lines))


def _extract_json(text: str) -> str:
    # small models often preface the array with prose — cut to the brackets
    start = text.find("[")
    end = text.rfind("]")
    return text[start:end + 1] if 0 <= start < end else text


def _confidence(item: dict) -> float | None:
    # a real 0-1 number remaps the model's severity through our calibration;
    # anything else falls back to whatever severity the model gave
    c = item.get("confidence")
    if isinstance(c, bool) or not isinstance(c, (int, float)):
        return None
    return min(1.0, max(0.0, float(c)))


def _parse_findings(text: str, f: FileDiff) -> list[dict]:
    """Validate model JSON into the finding schema. Malformed output -> []."""
    try:
        data = json.loads(_extract_json(text.strip()))
    except (json.JSONDecodeError, ValueError):
        return []  # garbage in, empty out — never crash the review
    if not isinstance(data, list):
        return []
    added = {h.new_no for h in f.added_lines}
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        message = item.get("message")
        line = item.get("line")
        conf = _confidence(item)
        severity = (confidence_to_severity(conf) if conf is not None
                    else item.get("severity"))
        if severity not in _SEVERITIES or not message or not isinstance(message, str):
            continue  # drop malformed entries, keep the good ones
        if line is not None and (not isinstance(line, int) or line not in added):
            # model pointed at a non-added line — keep the note, drop the anchor
            line = None
        out.append({
            "severity": severity, "file": f.path, "line": line,
            "check": "llm-local", "confidence": conf,
            "message": message.strip(),
        })
    return out


def _cached_snapshot(model_id: str) -> bool:
    # True when the weights are already in the HF cache — the offline path
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return False
    try:
        snapshot_download(model_id, local_files_only=True)
        return True
    except Exception:
        return False


class LocalBackend(ReviewBackend):
    """Per-file diff review by a local HF instruct model. Fails soft."""

    def __init__(self, model: str | None = None):
        self._model_id = model or os.environ.get(_ENV_MODEL, _DEFAULT_MODEL)
        self._pipe = None  # lazy: weights only load when the first file is reviewed
        self._fallback: ReviewBackend | None = None

    def _load(self) -> bool:
        if self._pipe is not None:
            return True
        if self._fallback is not None:
            return False
        try:
            from transformers import pipeline
        except ImportError:
            # no transformers on this box: mock heuristics keep the pipeline green
            print("local backend: transformers isn't installed — mock "
                  "heuristics instead (pip install transformers torch for "
                  "the local model)", file=sys.stderr)
            self._fallback = MockBackend()
            return False
        offline = _OFFLINE
        if offline and not _cached_snapshot(self._model_id):
            print(f"local backend: {self._model_id} not in the HF cache and "
                  "LOCAL_OFFLINE=1 — mock heuristics instead", file=sys.stderr)
            self._fallback = MockBackend()
            return False
        try:
            self._pipe = pipeline(
                "text-generation", model=self._model_id, device="cpu",
                dtype="auto", trust_remote_code=True,
            )  # HF_HUB_OFFLINE already set at import above, so this reads cache only
        except Exception as exc:  # weights missing and no network, bad model id, …
            print(f"local backend: couldn't load {self._model_id} ({exc}) — "
                  "mock heuristics instead", file=sys.stderr)
            self._fallback = MockBackend()
            return False
        return True

    def review_file(self, f: FileDiff) -> list[dict]:
        if not self._load():
            return self._fallback.review_file(f)
        try:
            tok = self._pipe.tokenizer
            prompt = tok.apply_chat_template(
                [{"role": "system", "content": _SYSTEM_PROMPT},
                 {"role": "user", "content": _file_prompt(f)}],
                tokenize=False, add_generation_prompt=True)
            out = self._pipe(prompt, max_new_tokens=256, do_sample=False,
                             return_full_text=False,
                             pad_token_id=tok.eos_token_id)
            text = out[0]["generated_text"]
        except Exception as exc:  # OOM or weird tokenizer edge — skip the file
            print(f"local backend: generation failed ({exc}), skipping file",
                  file=sys.stderr)
            return []
        return _parse_findings(text, f)
