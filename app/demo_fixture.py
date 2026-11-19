"""Fixed demo coverage values for the dashboard (temporary).

The numbers live in fixtures/demo_coverage.json and are identical for every
user and every page load. They are NOT calculated from questionnaire answers.
The real coverage calculation is still open (tasks.md, task 4).
"""

import json
from functools import lru_cache
from math import cos, pi, sin
from pathlib import Path

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "demo_coverage.json"

# Radar geometry (SVG viewBox 0 0 360 250).
CX, CY, RADIUS = 180, 125, 80
LABEL_GAP = 16


def band(value):
    """Colour band for a value: strong, fair or weak."""
    if value >= 70:
        return "strong"
    if value >= 40:
        return "fair"
    return "weak"


def _point(index, count, fraction, radius=RADIUS):
    angle = 2 * pi * index / count
    return CX + radius * fraction * sin(angle), CY - radius * fraction * cos(angle)


def _points(coords):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)


@lru_cache(maxsize=1)
def load():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    areas = data["areas"]
    if not 0 <= data["score"] <= 100 or not all(isinstance(v, int) and 0 <= v <= 100 for v in areas.values()):
        raise ValueError("demo_coverage.json values must be integers from 0 to 100")

    names = list(areas)
    count = len(names)
    axes = []
    for index, name in enumerate(names):
        value = areas[name]
        lx, ly = _point(index, count, 1, RADIUS + LABEL_GAP)
        axes.append(
            {
                "name": name,
                "value": value,
                "band": band(value),
                "label_x": round(lx, 1),
                "label_y": round(ly + 4, 1),
                "anchor": "end" if lx < CX - 1 else ("start" if lx > CX + 1 else "middle"),
                "end": _point(index, count, 1),
            }
        )

    return {
        "title": data["title"],
        "note": data["note"],
        "score": data["score"],
        "score_label": data["score_label"],
        "axes": axes,
        "rings": [_points([_point(i, count, f) for i in range(count)]) for f in (1 / 3, 2 / 3, 1)],
        "shape": _points([_point(i, count, areas[name] / 100) for i, name in enumerate(names)]),
        "spokes": [(round(a["end"][0], 1), round(a["end"][1], 1)) for a in axes],
        "centre": (CX, CY),
    }
