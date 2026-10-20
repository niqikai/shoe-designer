"""Intent parsing demo (#16): text / images -> structured intent, via the Claude API.

  ANTHROPIC_API_KEY=... uv run --extra intent python scripts/intent_demo.py "波浪纹的镂空运动鞋"
  ... intent_demo.py "like this shoe" --reference shoe.jpg
  ... intent_demo.py "use this pattern" --pattern pattern.png
  ... intent_demo.py "..." --rules          # offline rule backend, no key needed

Without ANTHROPIC_API_KEY (and without --rules) the demo skips with a hint. The key is read by the SDK from the
environment and is never printed. Model: config/intent.toml, or SHOE_INTENT_MODEL / --model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from shoe_designer import intent as it

EXAMPLES = ["波浪纹的镂空运动鞋", "极简风格", "我想要高跟尖头", "a sporty perforated sneaker, 孔径 50mm, instep ease 5mm"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="*", help="request text; default: built-in examples")
    ap.add_argument("--reference", action="append", default=[], help="reference shoe image")
    ap.add_argument("--pattern", help="pattern image (becomes PatternSpec.image)")
    ap.add_argument("--model", help="override the configured model id")
    ap.add_argument("--rules", action="store_true", help="use the offline rule backend")
    args = ap.parse_args()

    if args.rules:
        backend = it.RuleBackend()
    elif os.environ.get("ANTHROPIC_API_KEY"):
        backend = it.ClaudeBackend(model=args.model)
        print(f"model: {backend.model}")
    else:
        print("skipped: set ANTHROPIC_API_KEY to call the Claude API (or pass --rules for the offline backend).")
        return 0

    images = [(p, "reference") for p in args.reference] + ([(args.pattern, "pattern")] if args.pattern else [])
    texts = [" ".join(args.text)] if args.text else EXAMPLES
    status = 0
    for text in texts:
        result = it.parse_intent(text, backend, images=images)
        print(f"\n> {text}")
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        status |= 0 if result.ok else 1
    return status


if __name__ == "__main__":
    sys.exit(main())
