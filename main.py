import argparse
from dataclasses import replace

from smart_lock.config import load_config
from smart_lock.controller import SmartLockController
from smart_lock.logging_utils import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart lock MVP")
    parser.add_argument("--once", action="store_true", help="run one authentication attempt and exit")
    parser.add_argument("--no-unlock", action="store_true", help="never drive the relay output")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="force desktop/demo mode")
    mode.add_argument("--hardware", action="store_true", help="force Jetson hardware mode")
    args = parser.parse_args()

    config = load_config("config.yaml")
    if args.dry_run:
        config = replace(config, system=replace(config.system, dry_run=True))
    elif args.hardware:
        config = replace(config, system=replace(config.system, dry_run=False))

    setup_logging(config.system.log_level)
    controller = SmartLockController(config, force_lock_dry_run=args.no_unlock)
    if args.once:
        controller.run_once()
        controller.close()
    else:
        controller.run_forever()


if __name__ == "__main__":
    main()
