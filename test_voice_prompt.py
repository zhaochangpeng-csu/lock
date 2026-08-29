from __future__ import annotations

import argparse

from smart_lock.config import load_config
from smart_lock.logging_utils import setup_logging
from smart_lock.voice_feedback import VoiceFeedback


def main() -> None:
    parser = argparse.ArgumentParser(description="Play one smart-lock voice prompt")
    parser.add_argument("key", nargs="?", default="voice_prompt")
    parser.add_argument("--text", default=None)
    parser.add_argument("--no-block", action="store_true")
    args = parser.parse_args()

    config = load_config("config.yaml")
    setup_logging(config.system.log_level)
    feedback = VoiceFeedback(config.voice_feedback)
    feedback.speak(args.key, text=args.text, force=True, block=not args.no_block)


if __name__ == "__main__":
    main()
