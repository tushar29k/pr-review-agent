#!/usr/bin/env python3
"""Review a diff file and print the markdown review comment.

Usage:
    python cli.py --diff evals/sample_pr.diff
    python cli.py --diff evals/sample_pr.diff --reviewer openai   # needs OPENAI_API_KEY
    python cli.py --diff evals/sample_pr.diff --reviewer anthropic   # needs ANTHROPIC_API_KEY
    python cli.py --diff evals/sample_pr.diff --reviewer local    # small HF model, offline if cached
    python cli.py --diff evals/sample_pr.diff --log reviews.jsonl # + cost/latency JSONL record
    python cli.py --diff evals/sample_pr.diff --config repo/.pr-review.yaml
        # without --config, .pr-review.yaml is auto-discovered in the cwd
    python cli.py --diff evals/sample_pr.diff --ignore-pattern "docs/*"
        # extra fnmatch patterns, layered on top of the config (or defaults)
"""

import argparse
import os
import sys

from reviewer.comments import findings_to_markdown
from reviewer.ignore import effective_patterns
from reviewer.repo_config import discover_config
from reviewer.reviewer import Reviewer, make_backend


def main() -> int:
    ap = argparse.ArgumentParser(description="Review a unified diff.")
    ap.add_argument("--diff", required=True, help="Path to a unified diff file")
    ap.add_argument("--reviewer", default=os.environ.get("REVIEWER_BACKEND"),
                    help="mock (default), openai, anthropic, or local")
    ap.add_argument("--prompt-version", default=os.environ.get("REVIEWER_PROMPT_VERSION"),
                    help="reviewer prompt version from prompts/ (default v1)")
    ap.add_argument("--log", default=os.environ.get("REVIEW_LOG_PATH"),
                    help="append a per-review cost/latency record to this JSONL file")
    ap.add_argument("--config", default=os.environ.get("PR_REVIEW_CONFIG"),
                    help="path to a .pr-review.yaml; without it, .pr-review.yaml "
                         "is auto-discovered in the current directory")
    ap.add_argument("--ignore-pattern", action="append", default=[],
                    help="extra fnmatch pattern for vendored/generated files "
                         "(repeatable); layered on top of the config patterns "
                         "or the built-in defaults")
    args = ap.parse_args()

    try:
        with open(args.diff) as fh:
            diff_text = fh.read()
    except OSError as exc:
        print(f"can't read {args.diff}: {exc}", file=sys.stderr)
        return 1

    try:
        backend = make_backend(args.reviewer,
                               prompt_version=args.prompt_version)
    except (RuntimeError, ValueError) as exc:
        print(f"reviewer backend error: {exc}", file=sys.stderr)
        return 2

    config = discover_config(args.config)
    if args.ignore_pattern:
        # cli patterns layer on top of whatever the config (or the
        # defaults) already ignores
        config.ignore_patterns = (effective_patterns(config.ignore_patterns)
                                  + args.ignore_pattern)

    findings, skipped = Reviewer(backend, config=config).review_and_log(
        diff_text, log_path=args.log, backend=args.reviewer)
    print(findings_to_markdown(findings, skipped))
    # exit 1 if anything critical turned up — handy for CI gates
    return 1 if any(f["severity"] == "critical" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
