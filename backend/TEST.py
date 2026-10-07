
from backend.calculations import trilaterate


measurements = [
    ((1, 1), 1.58),
    ((7.5, 5), 3.55),
    ((13, 7), 2.51),
]


position = trilaterate(measurements)

print(f"Estimated position: x={position[0]:.2f}, y={position[1]:.2f}")
