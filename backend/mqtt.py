import asyncio
import json
import time
import warnings

from aiomqtt import Client

from backend.calculations import (
    get_fresh_measurements,
    rssi_to_distance,
    trilaterate,
)


MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "ips/scanners/+/rssi"

DISTANCE_TTL = 3.0


warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
)


# --------------------------------------------------
# Current RSSI-derived distances
# --------------------------------------------------

distances: dict[
    str,
    dict[
        str,
        dict[str, float]
    ]
] = {}


# --------------------------------------------------
# Remove old measurements
# --------------------------------------------------

def remove_old_distances():
    now = time.time()

    for tag_id in list(distances):

        for scanner_id in list(distances[tag_id]):

            timestamp = distances[tag_id][scanner_id]["timestamp"]

            if now - timestamp > DISTANCE_TTL:
                del distances[tag_id][scanner_id]

        if not distances[tag_id]:
            del distances[tag_id]


# --------------------------------------------------
# MQTT loop
# --------------------------------------------------

async def mqtt_loop(
    scanners: list[dict],
    tag_positions: dict[str, dict[str, float]],
):
    """
    Background MQTT listener.

    Receives:
        ips/scanners/{scanner_id}/rssi

    Payload:
        {
            "tag_id": "tag-001",
            "rssi": -63
        }

    Pipeline:
        RSSI
        ↓
        distance
        ↓
        fresh measurements
        ↓
        trilateration
        ↓
        tag_positions
    """

    # Convert scanner list to dictionary
    # for fast lookup by scanner ID.

    scanners_by_id = {
        scanner["id"]: scanner
        for scanner in scanners
    }

    while True:

        try:
            async with Client(
                hostname=MQTT_HOST,
                port=MQTT_PORT,
            ) as client:

                print("MQTT connected")

                await client.subscribe(MQTT_TOPIC)

                print(
                    f"Subscribed to {MQTT_TOPIC}"
                )

                async for message in client.messages:

                    # ----------------------------------------
                    # Topic
                    # ----------------------------------------

                    topic = str(message.topic)

                    # ----------------------------------------
                    # Payload
                    # ----------------------------------------

                    payload = message.payload.decode()

                    try:
                        data = json.loads(payload)

                    except json.JSONDecodeError:
                        print(
                            f"Invalid payload: {payload}"
                        )
                        continue

                    # ----------------------------------------
                    # Topic validation
                    # ----------------------------------------

                    topic_parts = topic.split("/")

                    if len(topic_parts) != 4:
                        print(
                            f"Invalid topic: {topic}"
                        )
                        continue

                    scanner_id = topic_parts[2]

                    # ----------------------------------------
                    # Payload validation
                    # ----------------------------------------

                    tag_id = data.get("tag_id")
                    rssi = data.get("rssi")

                    if tag_id is None or rssi is None:
                        print(
                            f"Invalid payload: {payload}"
                        )
                        continue

                    try:
                        rssi = float(rssi)

                    except (TypeError, ValueError):
                        print(
                            f"Invalid RSSI: {rssi}"
                        )
                        continue

                    # ----------------------------------------
                    # Check scanner
                    # ----------------------------------------

                    if scanner_id not in scanners_by_id:
                        print(
                            f"Unknown scanner: {scanner_id}"
                        )
                        continue

                    # ----------------------------------------
                    # RSSI -> distance
                    # ----------------------------------------

                    distance = rssi_to_distance(rssi)

                    # ----------------------------------------
                    # Save measurement
                    # ----------------------------------------

                    if tag_id not in distances:
                        distances[tag_id] = {}

                    distances[tag_id][scanner_id] = {
                        "distance": distance,
                        "timestamp": time.time(),
                    }

                    # ----------------------------------------
                    # Remove old measurements
                    # ----------------------------------------

                    remove_old_distances()

                    # ----------------------------------------
                    # Log measurement
                    # ----------------------------------------

                    print(
                        f"Measurement: "
                        f"scanner={scanner_id}, "
                        f"tag={tag_id}, "
                        f"rssi={rssi:.2f}, "
                        f"distance={distance:.2f} m"
                    )

                    # ----------------------------------------
                    # Get fresh measurements
                    # ----------------------------------------

                    tag_distances = distances.get(
                        tag_id,
                        {},
                    )

                    measurements = get_fresh_measurements(
                        tag_distances,
                        scanners_by_id,
                        ttl=DISTANCE_TTL,
                    )

                    # ----------------------------------------
                    # Need at least 3 scanners
                    # ----------------------------------------

                    if len(measurements) < 3:

                        print(
                            f"Not enough measurements: "
                            f"{len(measurements)}/3"
                        )

                        continue

                    # ----------------------------------------
                    # Trilateration
                    # ----------------------------------------

                    try:
                        x, y = trilaterate(
                            measurements
                        )

                    except ValueError as error:

                        print(
                            f"Trilateration error: "
                            f"{error}"
                        )

                        continue

                    # ----------------------------------------
                    # Save current tag position
                    # ----------------------------------------

                    tag_positions[tag_id] = {
                        "x": x,
                        "y": y,
                    }

                    # ----------------------------------------
                    # Log position
                    # ----------------------------------------

                    print(
                        f"Position: "
                        f"tag={tag_id}, "
                        f"x={x:.2f}, "
                        f"y={y:.2f}"
                    )

        except asyncio.CancelledError:
            print("MQTT task cancelled")
            raise

        except Exception as error:
            print(
                f"MQTT connection error: {error}"
            )

            print(
                "Reconnecting in 3 seconds..."
            )

            await asyncio.sleep(3)
