import time

import numpy as np
from scipy.optimize import least_squares


RSSI_AT_1M = -59.0
PATH_LOSS_EXPONENT = 2.0
DISTANCE_TTL = 3.0


def rssi_to_distance(
    rssi: float,
    rssi_at_1m: float = RSSI_AT_1M,
    path_loss_exponent: float = PATH_LOSS_EXPONENT,
) -> float:
    """
    Convert RSSI to estimated distance in meters.
    """

    return 10 ** (
        (rssi_at_1m - rssi)
        / (10 * path_loss_exponent)
    )


def trilaterate(
    measurements: list[
        tuple[
            tuple[float, float],
            float,
        ]
    ],
) -> tuple[float, float]:
    """
    Estimate tag position from scanner positions
    and measured distances.
    """

    if len(measurements) < 3:
        raise ValueError(
            "At least 3 measurements are required"
        )

    scanner_positions = np.array(
        [
            position
            for position, _ in measurements
        ],
        dtype=float,
    )

    distances = np.array(
        [
            distance
            for _, distance in measurements
        ],
        dtype=float,
    )

    def residuals(position):
        x, y = position

        calculated_distances = np.sqrt(
            (scanner_positions[:, 0] - x) ** 2
            + (scanner_positions[:, 1] - y) ** 2
        )

        return calculated_distances - distances

    initial_guess = np.mean(
        scanner_positions,
        axis=0,
    )

    result = least_squares(
        residuals,
        initial_guess,
    )

    if not result.success:
        raise ValueError(
            f"Trilateration failed: "
            f"{result.message}"
        )

    x, y = result.x

    return float(x), float(y)


def get_fresh_measurements(
    tag_distances: dict,
    scanners: dict,
    ttl: float = DISTANCE_TTL,
) -> list[
    tuple[
        tuple[float, float],
        float,
    ]
]:
    """
    Return only fresh measurements.
    """

    now = time.time()

    measurements = []

    for scanner_id, measurement in tag_distances.items():

        timestamp = measurement["timestamp"]

        if now - timestamp > ttl:
            continue

        scanner = scanners.get(scanner_id)

        if scanner is None:
            continue

        position = scanner["position"]

        measurements.append(
            (
                (
                    position["x"],
                    position["y"],
                ),
                measurement["distance"],
            )
        )

    return measurements
