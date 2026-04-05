"""Track identification and reference lap storage.

Identifies tracks by lap distance + start position fingerprint.
Stores track database and reference laps as JSON on disk.
"""

import json
import math
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent / 'track_data'
TRACKS_DB = DATA_DIR / 'tracks.json'

# Tolerance for matching tracks
LENGTH_TOLERANCE = 0.05      # 5% of track length
START_POS_TOLERANCE = 50.0   # meters


def _lap_distance(samples: list[dict]) -> float:
    """Calculate total distance of a lap from samples."""
    total = 0.0
    for i in range(1, len(samples)):
        dx = samples[i]['x'] - samples[i - 1]['x']
        dz = samples[i]['z'] - samples[i - 1]['z']
        total += math.sqrt(dx * dx + dz * dz)
    return total


def _start_pos(samples: list[dict]) -> tuple[float, float]:
    """Get start position from first valid sample."""
    for s in samples:
        if abs(s['x']) > 1 or abs(s['z']) > 1:
            return (round(s['x'], 0), round(s['z'], 0))
    return (0.0, 0.0)


def _load_db() -> dict:
    """Load tracks database."""
    if TRACKS_DB.exists():
        with open(TRACKS_DB) as f:
            return json.load(f)
    return {'tracks': []}


def _save_db(db: dict):
    """Save tracks database."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(TRACKS_DB, 'w') as f:
        json.dump(db, f, indent=2)


def identify_track(samples: list[dict]) -> dict | None:
    """Identify a track from lap samples.

    Returns track dict {id, name, length, start_x, start_z} or None.
    """
    if len(samples) < 20:
        return None

    length = _lap_distance(samples)
    if length < 500:  # too short to be a real lap
        return None

    sx, sz = _start_pos(samples)
    db = _load_db()

    for track in db['tracks']:
        # Match by length (within tolerance)
        len_diff = abs(track['length'] - length) / track['length']
        if len_diff > LENGTH_TOLERANCE:
            continue

        # Match by start position
        dx = abs(track['start_x'] - sx)
        dz = abs(track['start_z'] - sz)
        if dx < START_POS_TOLERANCE and dz < START_POS_TOLERANCE:
            return track

    return None


def register_track(samples: list[dict], name: str = '') -> dict:
    """Register a new track or return existing. Returns track dict."""
    existing = identify_track(samples)
    if existing:
        return existing

    length = _lap_distance(samples)
    sx, sz = _start_pos(samples)
    db = _load_db()

    track_id = len(db['tracks']) + 1
    if not name:
        name = f'Track {track_id} ({int(length)}m)'

    track = {
        'id': track_id,
        'name': name,
        'length': round(length, 1),
        'start_x': sx,
        'start_z': sz,
    }
    db['tracks'].append(track)
    _save_db(db)
    return track


def rename_track(track_id: int, name: str):
    """Rename a track."""
    db = _load_db()
    for track in db['tracks']:
        if track['id'] == track_id:
            track['name'] = name
            _save_db(db)
            return True
    return False


def save_reference_lap(track_id: int, samples: list[dict],
                       time_ms: int, source: str = 'personal'):
    """Save a reference lap for a track.

    source: 'personal' for own best, 'demo' for circuit experience record.
    """
    DATA_DIR.mkdir(exist_ok=True)
    ref_file = DATA_DIR / f'ref_{track_id}_{source}.json'

    data = {
        'track_id': track_id,
        'source': source,
        'time_ms': time_ms,
        'samples': samples,
    }
    with open(ref_file, 'w') as f:
        json.dump(data, f)


def load_reference_lap(track_id: int) -> dict | None:
    """Load best reference lap for a track. Prefers demo over personal.

    Returns {source, time_ms, samples} or None.
    """
    DATA_DIR.mkdir(exist_ok=True)

    # Prefer demo (circuit experience record) over personal
    for source in ('demo', 'personal'):
        ref_file = DATA_DIR / f'ref_{track_id}_{source}.json'
        if ref_file.exists():
            with open(ref_file) as f:
                return json.load(f)
    return None


def list_tracks() -> list[dict]:
    """Return all known tracks."""
    db = _load_db()
    return db['tracks']
