import math

import numpy as np


class Kalman2D:
    def __init__(
            self,
            initial_position: tuple[float, float],
            measurement_noise: float = 0.5,
            acceleration_noise: float = 1.5,
            gate_threshold: float = 16.0,
    ):
        x, y = initial_position

        if not all(math.isfinite(v) for v in (x, y)):
            raise ValueError("Initial position must be finite")

        if measurement_noise <= 0:
            raise ValueError("measurement_noise must be positive")

        if acceleration_noise < 0:
            raise ValueError("acceleration_noise cannot be negative")

        if gate_threshold <= 0:
            raise ValueError("gate_threshold must be positive")

        self.state = np.array(
            [x, y, 0.0, 0.0],
            dtype=float
        )

        self.P = np.diag([1.0, 1.0, 4.0, 4.0])

        self.measurement_noise = measurement_noise
        self.acceleration_noise = acceleration_noise
        self.gate_threshold = gate_threshold

        self.last_time: float | None = None

    def update(
            self,
            measurement: tuple[float, float],
            timestamp: float
    ) -> tuple[float, float]:
        z = np.asarray(measurement, dtype=float)
        timestamp = float(timestamp)

        if z.shape != (2,) or not np.all(np.isfinite(z)):
            raise ValueError("Measurement must contain finite x and y")

        if not math.isfinite(timestamp):
            raise ValueError("Timestamp must be finite")

        if self.last_time is None:
            self.last_time = timestamp
            self.state[:2] = z

            return float(z[0]), float(z[1])

        dt = timestamp - self.last_time

        if dt <= 0:
            return float(self.state[0]), float(self.state[1])

        self.last_time = timestamp

        dt = min(dt, 1.0)
        F = np.array([
            [1.0, 0.0, dt, 0.0],
            [0.0, 1.0, 0.0, dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ])

        H = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ])

        q = self.acceleration_noise ** 2

        Q = q * np.array([
            [dt ** 4 / 4, 0.0, dt ** 3 / 2, 0.0],
            [0.0, dt ** 4 / 4, 0.0, dt ** 3 / 2],
            [dt ** 3 / 2, 0.0, dt ** 2, 0.0],
            [0.0, dt ** 3 / 2, 0.0, dt ** 2],
        ])

        R = np.eye(2) * self.measurement_noise ** 2

        predicted_state = F @ self.state
        predicted_P = F @ self.P @ F.T + Q

        innovation = z - H @ predicted_state
        S = H @ predicted_P @ H.T + R

        try:
            mahalanobis_sq = float(
                innovation @ np.linalg.solve(S, innovation)
            )
        except np.linalg.LinAlgError:
            raise ValueError("Kalman innovation covariance is singular")

        if not math.isfinite(mahalanobis_sq):
            raise ValueError("Invalid Kalman innovation")

        if mahalanobis_sq > self.gate_threshold:
            self.state = predicted_state
            self.P = predicted_P

        else:
            K = np.linalg.solve( S, H @ predicted_P).T

            self.state = predicted_state + K @ innovation

            I =  np.eye(4)
            IKH = I - K @ H

            self.P = (
                IKH @ predicted_P @ IKH.T
                + K @ R @ K.T
            )

            self.P = (self.P + self.P.T) / 2.0


        return (float(self.state[0]), float(self.state[1]))