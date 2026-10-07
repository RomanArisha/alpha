import json
import random
import time

import paho.mqtt.client as mqtt


MQTT_HOST = "localhost"
MQTT_PORT = 1883

TAG_ID = "tag-001"

SCANNERS = [
    "scanner-1",
    "scanner-2",
    "scanner-3",
]


def calculate_rssi(distance: float) -> float:
    """
    Convert distance to simulated RSSI.

    This is the inverse of the RSSI -> distance
    formula used in calculations.py.
    """

    rssi_at_1m = -59.0
    path_loss_exponent = 2.0

    rssi = (
        rssi_at_1m
        - 10 * path_loss_exponent * __import__("math").log10(distance)
    )

    # Add small random noise to simulate real measurements.
    rssi += random.uniform(-1.5, 1.5)

    return rssi


def main():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )

    client.connect(
        MQTT_HOST,
        MQTT_PORT,
    )

    print("MQTT emulator connected")

    # Temporary fixed tag position.
    tag_x = 6.0
    tag_y = 3.0

    scanner_positions = {
        "scanner-1": (1.0, 1.0),
        "scanner-2": (7.5, 5.0),
        "scanner-3": (13.0, 7.0),
    }

    try:
        while True:
            print()
            print(
                f"Tag position: "
                f"x={tag_x:.2f}, "
                f"y={tag_y:.2f}"
            )

            for scanner_id in SCANNERS:
                scanner_x, scanner_y = scanner_positions[scanner_id]

                distance = (
                    (tag_x - scanner_x) ** 2
                    + (tag_y - scanner_y) ** 2
                ) ** 0.5

                rssi = calculate_rssi(distance)

                payload = {
                    "tag_id": TAG_ID,
                    "rssi": round(rssi, 2),
                }

                topic = (
                    f"ips/scanners/"
                    f"{scanner_id}/rssi"
                )

                client.publish(
                    topic,
                    json.dumps(payload),
                )

                print(
                    f"Published: "
                    f"{scanner_id}, "
                    f"distance={distance:.2f} m, "
                    f"rssi={rssi:.2f}"
                )

            time.sleep(1)

    except KeyboardInterrupt:
        print()
        print("MQTT emulator stopped")

    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
