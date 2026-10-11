import math

import numpy as np
from scipy.optimize import least_squares


RSSI_AT_1M = -59.0
PATH_LOSS_EXPONENT = 2.0
DISTANCE_TTL = 3.0

MAX_TRILATERATION_RMSE = 3.0

def rssi_to_distance(
        rssi: float,
        rssi_at_1m: float = RSSI_AT_1M,
        path_loss_exponent: float = PATH_LOSS_EXPONENT
) -> float:

    rssi = float(rssi)

    if not math.isfinite(rssi):
        raise ValueError("RSSI must be finite")

    if path_loss_exponent <= 0:
        raise ValueError("path_loss_exponent must be positive")

    exponent = (
            (rssi_at_1m - rssi)
            / (10.0 * path_loss_exponent)
    )

    distance = 10 ** exponent
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("RSSI produced an invalid distance")

    return distance

def trilaterate(
        measurements: list[tuple[tuple[float, float], float]],
        initial_position: tuple[float, float] | None = None,
        max_rmse: float | None = MAX_TRILATERATION_RMSE,
) -> tuple[float, float]:
    if len(measurements) < 3:
        raise ValueError("At least 3 measurements are required")

    scanner_positions = np.asarray(
        [position for position, _ in measurements],
        dtype=float,
    )

    distances = np.asarray(
        [distance for _, distance in measurements],
        dtype=float,
    )

    if scanner_positions.shape != (len(measurements), 2):
        raise ValueError("Scanner positions must be 2D")

    if not np.all(np.isfinite(scanner_positions)):
        raise ValueError("Scanner positions must be finite")

    if not np.all(np.isfinite(distances)):
        raise ValueError("Distances must be finite")

    if np.any(distances <= 0):
        raise ValueError("Distances must be positive")

    centered = scanner_positions - np.mean(
        scanner_positions,
        axis=0,
    )

    if np.linalg.matrix_rank(centered) < 2:
        raise ValueError("Scanner geometry is degenerate")

    def residuals(position: np.ndarray) -> np.ndarray:
        calculated_distances = np.linalg.norm(
            scanner_positions - position,
            axis=1,
        )

        return calculated_distances - distances

    if initial_position is None:
        initial_guess = np.mean(
            scanner_positions,
            axis=0,
        )
    else:
        initial_guess = np.asarray(
            initial_position,
            dtype=float,
        )

        if (
                initial_guess.shape != (2,)
                or not np.all(np.isfinite(initial_guess))
        ):
            raise ValueError("Initial position must contain finite x and y")

    result = least_squares(
        residuals,
        initial_guess,
        loss="soft_l1",
        f_scale=1.0,
        max_nfev=200,
    )

    if not result.success:
        raise ValueError(
            f"Trilateration failed: {result.message}"
        )

    x, y = result.x

    if not np.all(np.isfinite(result.x)):
        raise ValueError("Trilateration produced invalid coordinates")

    final_residuals = residuals(result.x)

    rmse = float(
        np.sqrt(np.mean(final_residuals ** 2))
    )

    if max_rmse is not None and rmse > max_rmse:
        raise ValueError(
            f"Poor trilateration quality: RMSE={rmse:.2f} m"
        )

    return float(x), float(y)


def get_fresh_measurements(
        tag_distances: dict,
        scanners: dict,
        ttl: float = DISTANCE_TTL,
) -> list[tuple[[float, float], float]]:

    now = __import__("time").time()
    measurements = []

    for scanner_id, measurement in tag_distances.items():
        timestamp = measurement.get("timestamp")
        distance = measurement.get("distance")

        if timestamp is None or distance is None:
            continue

        try:
            timestamp = float(timestamp)
            distance = float(distance)
        except (TypeError, ValueError):
            continue

        age = now - timestamp

        if not math.isfinite(age) or age < 0 or age > ttl:
            continue

        if not math.isfinite(distance) or distance <= 0:
            continue

        scanner = scanners.get(scanner_id)

        if scanner is None:
            continue

        try:
            x = float(scanner["position"]["x"])
            y = float(scanner["position"]["y"])
        except (KeyError, TypeError, ValueError):
            continue

        if not math.isfinite(x) or not math.isfinite(y):
            continue

        measurements.append(
            ((x, y), distance)
        )

    return measurements