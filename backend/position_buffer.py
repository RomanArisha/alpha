from collections import deque
from dataclasses import dataclass
import math

import  numpy as np

@dataclass
class PositionSample:
    x: float
    y: float
    timestamp: float
    quality: float = 1.0

class PositionBuffer:
    def __init__(
            self,
            max_samples: int = 7,
            ttl: float = 2.0,
            gate_distance: float = 2.0,
            min_consensus: int = 3,
    ):
        if max_samples < 1:
            raise ValueError("max_samples must be positive")
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if gate_distance <= 0:
            raise ValueError("gate_distance must be positive")
        if not 1 <= min_consensus <= max_samples:
            raise ValueError("Invalid min_consensus")

        self.samples = deque(maxlen=max_samples)
        self.ttl = ttl
        self.gate_distance = gate_distance
        self.min_consensus = min_consensus

    def add(
            self,
            x: float,
            y: float,
            timestamp: float,
            quality: float = 1.0,
    ) -> tuple[float, float] | None:

        values = (x, y, timestamp, quality)

        if not all(math.isfinite(v) for v in values):
            return None

        if quality <= 0:
            return None

        self._cleanup(timestamp)

        if self.samples:
            consensus = self._estimate()

            if consensus is not None:
                cx, cy = consensus
                distance = math.hypot(x - cx, y - cy)

                if distance > self.gate_distance:
                    return None

        self.samples.append(
            PositionSample(
                x=x,
                y=y,
                timestamp=timestamp,
                quality=quality,
            )
        )

        return self._estimate()

    def _cleanup(self, now: float) -> None:
        while self.samples:
            age = now - self.samples[0].timestamp

            if 0 <= age <= self.ttl:
                break

            self.samples.popleft()

        self.samples = deque(
            (
                sample
                for sample in self.samples
                if 0 <= now - sample.timestamp <= self.ttl
            ),
            maxlen=self.samples.maxlen,
        )

    def _estimate(self) -> tuple[float, float] | None:
        if not self.samples:
            return None

        points = np.array(
            [[sample.x, sample.y] for sample in self.samples],
            dtype=float,
        )

        weights = np.array(
            [sample.quality for sample in self.samples],
            dtype=float,
        )

        pairwise = np.linalg.norm(
            points[:, None, :] - points[None, :, :],
            axis=2,
        )

        medoid_index = int(
            np.argmin(pairwise.sum(axis=1))
        )

        center = points[medoid_index]

        distances = np.linalg.norm(
            points - center,
            axis=1,
        )

        inliers = distances <= self.gate_distance

        if not np.any(inliers):
            return None

        inlier_points = points[inliers]
        inlier_weights = weights[inliers]

        if len(inlier_points) < self.min_consensus:
            return None

        estimate = np.average(
            inlier_points,
            axis=0,
            weights=inlier_weights,
        )

        return float(estimate[0]), float(estimate[1])