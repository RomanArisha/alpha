import asyncio
import json
import math
import time
import warnings
from .position_buffer import PositionBuffer

from aiomqtt import Client

from backend.calculations import (
    DISTANCE_TTL,
    rssi_to_distance,
    trilaterate,
)

from backend.kalman import Kalman2D
from backend.measurements import MeasurementStore


MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "ips/scanners/+/rssi"

warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
)

CALCULATION_INTERVAL = 0.2

POSITION_TTL = 5.0

KALMAN_MEASUREMENT_NOISE = 0.5
KALMAN_ACCELERATION_NOISE = 0.5
KALMAN_GATE_THRESHOLD = 16.0

async def maintenance_loop(
        measurement_store: MeasurementStore,
        tag_positions: dict[str, dict[str, float]],
        position_timestamps: dict[str, float],
        position_buffers: dict[str, PositionBuffer],
        kalman_filters: dict[str, Kalman2D]
) -> None:

    while True:
        await asyncio.sleep(0.2)

        measurement_store.cleanup()

        now = time.time()

        for tag_id, timestamp in list(
            position_timestamps.items()
        ):
            if now - timestamp > POSITION_TTL:
                tag_positions.pop(tag_id, None)
                position_timestamps.pop(tag_id, None)
                kalman_filters.pop(tag_id, None)
                position_buffers.pop(tag_id, None)
                measurement_store.remove_tag(tag_id)

async def mqtt_loop(
        scanners: list[dict],
        tag_positions: dict[str, dict[str, float]],
) -> None:
    scanners_by_id = {
        scanner["id"]: scanner
        for scanner in scanners
    }

    measurement_store = MeasurementStore(
        ttl=DISTANCE_TTL,
    )

    kalman_filters: dict[str, Kalman2D] = {}
    position_timestamps: dict[str, float] = {}
    last_calculation: dict[str, float] = {}
    position_buffers: dict[str, PositionBuffer] = {}

    maintenance_task = asyncio.create_task(
        maintenance_loop(
            measurement_store=measurement_store,
            tag_positions=tag_positions,
            position_timestamps=position_timestamps,
            position_buffers=position_buffers,
            kalman_filters=kalman_filters,
        )
    )

    try:
        while True:
            try:
                async with Client(
                    hostname=MQTT_HOST,
                    port=MQTT_PORT,
                ) as client:
                    print("MQTT connected")

                    await client.subscribe(MQTT_TOPIC)

                    print(f"Subscribed to {MQTT_TOPIC}")

                    async for message in client.messages:
                        try:
                            topic = str(message.topic)
                            payload = message.payload.decode("utf-8")

                            topic_parts = topic.split("/")

                            if (
                                    len(topic_parts) != 4
                                    or topic_parts[0] != "ips"
                                    or topic_parts[1] != "scanners"
                                    or topic_parts[3] != "rssi"
                            ):
                                print(f"Invalid topic: {topic}")
                                continue

                            scanner_id = topic_parts[2]

                            if scanner_id not in scanners_by_id:
                                print(f"Unknown scanner: {scanner_id}")
                                continue

                            try:
                                data = json.loads(payload)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                print(f"Invalid payload: {payload}")
                                continue

                            if not isinstance(data, dict):
                                print("Invalid payload: expected a JSON object")
                                continue

                            tag_id = data.get("tag_id")
                            rssi_value = data.get("rssi")

                            if (
                                    not isinstance(tag_id, str)
                                    or not tag_id.strip()
                                    or isinstance(rssi_value, bool)
                                    or rssi_value is None
                            ):
                                print(f"Invalid payload: {payload}")
                                continue

                            try:
                                rssi = float(rssi_value)
                            except (TypeError, ValueError):
                                print(f"Invalid RSSI: {rssi_value}")
                                continue

                            if not math.isfinite(rssi):
                                print(f"Invalid RSSI: {rssi_value}")
                                continue

                            try:
                                distance = rssi_to_distance(rssi)
                            except ValueError as error:
                                print(f"Distance conversion error: {error}")
                                continue

                            now = time.time()

                            measurement_store.update(
                                tag_id=tag_id,
                                scanner_id=scanner_id,
                                distance=distance,
                                timestamp=now,
                            )

                            previous_calculation = last_calculation.get(
                                tag_id,
                                0.0,
                            )

                            if (
                                    now - previous_calculation
                                    < CALCULATION_INTERVAL
                            ):
                                continue

                            last_calculation[tag_id] = now

                            tag_distances = (
                                measurement_store.get_fresh(tag_id)
                            )

                            measurements = []

                            for current_scanner_id, measurement in (
                                    tag_distances.items()
                            ):
                                scanner = scanners_by_id.get(
                                    current_scanner_id
                                )

                                if scanner is None:
                                    continue

                                position = scanner.get("position", {})

                                try:
                                    sx = float(position["x"])
                                    sy = float(position["y"])
                                    d = float(measurement["distance"])
                                except (KeyError, TypeError, ValueError):
                                    continue

                                if not all(
                                        math.isfinite(v)
                                        for v in (sx, sy, d)
                                ):
                                    continue

                                if d <= 0:
                                    continue

                                measurements.append(
                                    ((sx, sy), d)
                                )

                            if len(measurements) < 3:
                                print(
                                    f"Not enough measurements for {tag_id}: "
                                    f"{len(measurements)}/3"
                                )
                                continue

                            kalman = kalman_filters.get(tag_id)

                            initial_position = None

                            if kalman is not None:
                                initial_position = (
                                    float(kalman.state[0]),
                                    float(kalman.state[1]),
                                )

                            try:
                                x, y = trilaterate(
                                    measurements,
                                    initial_position=initial_position,
                                )
                            except ValueError as error:
                                print(
                                    f"Trilateration rejected for {tag_id}: "
                                    f"{error}"
                                )
                                continue

                            buffer = position_buffers.get(tag_id)

                            if buffer is None:
                                buffer = PositionBuffer(
                                    max_samples=7,
                                    ttl=2.0,
                                    gate_distance=2.0,
                                    min_consensus=3,
                                )
                                position_buffers[tag_id] = buffer

                            consensus = buffer.add(
                                x=x,
                                y=y,
                                timestamp=now,
                            )

                            if consensus is None:
                                print(f"Waiting for position consensus: tag={tag_id}")
                                continue

                            if kalman is None:
                                kalman = Kalman2D(
                                    initial_position=consensus,
                                    measurement_noise=(
                                        KALMAN_MEASUREMENT_NOISE
                                    ),
                                    acceleration_noise=(
                                        KALMAN_ACCELERATION_NOISE
                                    ),
                                    gate_threshold=(
                                        KALMAN_GATE_THRESHOLD
                                    ),
                                )

                                kalman_filters[tag_id] = kalman

                            try:
                                filtered_x, filtered_y = kalman.update(
                                    measurement=consensus,
                                    timestamp=now,
                                )
                            except ValueError as error:
                                print(
                                    f"Kalman update rejected for {tag_id}: "
                                    f"{error}"
                                )
                                continue

                            tag_positions[tag_id] = {
                                "x": filtered_x,
                                "y": filtered_y,
                            }

                            position_timestamps[tag_id] = now

                            print(
                                f"Position: tag={tag_id}, "
                                f"x={filtered_x:.2f}, "
                                f"y={filtered_y:.2f}"
                            )

                        except Exception as error:
                            print(f"Message processing error: {error}")

            except asyncio.CancelledError:
                raise

            except Exception as error:
                print(f"MQTT connection error: {error}")
                print("Reconnecting in 3 seconds...")
                await asyncio.sleep(3)

    finally:
        maintenance_task.cancel()

        try:
            await maintenance_task
        except asyncio.CancelledError:
            pass