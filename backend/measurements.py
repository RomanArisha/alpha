import math
import time

class MeasurementStore:
    def __init__(self, ttl: float = 3.0):
        if ttl <= 0:
            raise ValueError('ttl must be positive')

        self.ttl = ttl
        self.data: dict[
            str,
            dict[str, dict[str, float]]
        ] = {}

    def update(
            self,
            tag_id: str,
            scanner_id: str,
            distance: float,
            timestamp: float | None = None
    ) -> None:
        if not tag_id or not scanner_id:
            raise ValueError("tag_id and scanner_id are required")

        distance = float(distance)

        if not math.isfinite(distance) or distance <= 0:
            raise ValueError("distance must be finite and positive")

        if timestamp is None:
            timestamp = time.time()

        timestamp = float(timestamp)

        if not math.isfinite(timestamp):
            raise ValueError("timestamp must be finite")

        tag_data = self.data.setdefault(tag_id, {})
        previous = tag_data.get(scanner_id)

        if previous is not None:
            if timestamp <= previous['timestamp']:
                return

        tag_data[scanner_id] = {
            "distance": distance,
            "timestamp": timestamp,
        }

    def get_fresh(
            self,
            tag_id: str,
            now: float | None = None,
    ) -> dict[str, dict[str, float]]:
        if now is None:
            now = time.time()

        tag_data = self.data.get(tag_id)

        if not tag_data:
            return {}

        for scanner_id, measurement in list(tag_data.items()):
            age = now - measurement['timestamp']
            if age < 0 or age > self.ttl:
                del tag_data[scanner_id]

        if not tag_data:
            self.data.pop(tag_id, None)
            return {}

        return {
            scanner_id: measurement.copy()
            for scanner_id, measurement in tag_data.items()
        }


    def cleanup(
            self,
            now: float | None = None,
    ) -> None:
        if now is None:
            now = time.time()

        for tag_id in list (self.data):
            self.get_fresh(tag_id, now=now)

    def remove_tag(self, tag_id: str) -> None:
        self.data.pop(tag_id, None)

    def tag_ids(self) -> list[str]:
        return list(self.data)