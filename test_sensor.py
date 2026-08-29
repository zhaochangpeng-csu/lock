import argparse
import time

from smart_lock.config import load_config
from smart_lock.logging_utils import setup_logging
from smart_lock.sensor import MockInfraredSensor, SerialInfraredSensor


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll the infrared sensor without unlocking")
    parser.add_argument("--count", type=int, default=20, help="number of polls")
    parser.add_argument("--interval", type=float, default=None, help="poll interval in seconds")
    parser.add_argument("--mock", action="store_true", help="use mock sensor")
    parser.add_argument("--verbose", action="store_true", help="print raw sensor value when available")
    args = parser.parse_args()

    config = load_config("config.yaml")
    setup_logging(config.system.log_level)
    interval = args.interval or config.sensor.poll_interval_seconds
    sensor = MockInfraredSensor(config.sensor) if args.mock else SerialInfraredSensor(config.sensor)

    try:
        for index in range(args.count):
            if args.verbose and hasattr(sensor, "read_value"):
                value = sensor.read_value()
                detected = value == config.sensor.serial.detected_value
                print(f"{index + 1:03d}: detected={detected} value={value}")
            else:
                detected = sensor.detected()
                print(f"{index + 1:03d}: detected={detected}")
            time.sleep(interval)
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
