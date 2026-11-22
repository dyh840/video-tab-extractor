"""Find the six horizontal strings in a cropped guitar TAB image."""

from bisect import bisect_left
from itertools import groupby

from PIL import Image


def find_staff(image: Image.Image) -> tuple[int, ...] | None:
    """Return six line centers from top to bottom, or None for an unclear crop."""
    width, height = image.size
    if width < 24 or height < 16:
        return None

    columns = min(width, 128)
    sample = image.convert("L").resize((columns, height), Image.Resampling.BOX).tobytes()
    rows = [sample[y * columns:(y + 1) * columns] for y in range(height)]
    # Compare nearby rows in the same column so yellow and white backgrounds work alike.
    offset = max(3, round(height / 120))
    strength = []
    for y, row in enumerate(rows):
        above = rows[y - offset] if y >= offset else rows[y + offset]
        below = rows[y + offset] if y + offset < height else above
        strength.append(sum(min(a, b) - p >= 9 for a, b, p in zip(above, below, row)))

    line_centers = [
        max(run, key=strength.__getitem__)
        for seen, run in groupby(range(height), key=lambda y: strength[y] >= columns * 0.36)
        if seen
    ]
    matches = {}
    for i, first in enumerate(line_centers[:-5]):
        for last in line_centers[i + 5:]:
            spacing = (last - first) / 5
            if spacing < 3:
                continue
            lines = [first]
            tolerance = max(1.5, spacing * 0.08)
            for n in range(1, 5):
                target = first + n * spacing
                index = bisect_left(line_centers, target - tolerance)
                near = line_centers[index:bisect_left(line_centers, target + tolerance + 1)]
                near = [y for y in near if lines[-1] < y < last and abs(y - target) <= tolerance]
                if not near:
                    break
                lines.append(min(near, key=lambda y: abs(y - target)))
            if len(lines) == 5:
                lines.append(last)
                matches[tuple(lines)] = sum(strength[y] for y in lines)

    if not matches:
        return None
    best = max(matches, key=matches.__getitem__)
    if any(lines[-1] < best[0] or best[-1] < lines[0] for lines in matches):
        return None
    return best
