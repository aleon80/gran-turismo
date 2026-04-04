"""Real-time driving coach: records laps, compares to best, generates tips."""

import math
from analyzer import analyze_lap, summarize_lap

# Minimum distance (meters) between recorded samples along the track
SAMPLE_INTERVAL = 5.0
# How close (meters) current position must be to a reference point to compare
MATCH_RADIUS = 15.0
# Cooldown: don't repeat the same tip within N meters of track
TIP_COOLDOWN_DIST = 80.0
# Speed delta thresholds (km/h)
SPEED_SLOW_THRESHOLD = 8
SPEED_FAST_THRESHOLD = 5
# Throttle threshold
THROTTLE_DIFF = 0.3  # 30% difference
# Number of track sectors
NUM_SECTORS = 3
# How close to a sector boundary to trigger crossing (meters)
SECTOR_TRIGGER_DIST = 20.0


def _dist(p1, p2):
    dx = p1[0] - p2[0]
    dz = p1[1] - p2[1]
    return math.sqrt(dx * dx + dz * dz)


def _cumulative_distances(samples):
    """Return list of cumulative distances along samples."""
    dists = [0.0]
    for i in range(1, len(samples)):
        d = _dist(
            (samples[i]['x'], samples[i]['z']),
            (samples[i - 1]['x'], samples[i - 1]['z']),
        )
        dists.append(dists[-1] + d)
    return dists


class LapRecorder:
    """Records telemetry samples for a single lap."""

    def __init__(self):
        self.samples = []
        self.last_pos = None

    def add(self, data: dict):
        x, z = data.get('pos_x', 0), data.get('pos_z', 0)
        if abs(x) < 0.1 and abs(z) < 0.1:
            return

        if self.last_pos and _dist((x, z), self.last_pos) < SAMPLE_INTERVAL:
            return

        self.last_pos = (x, z)
        self.samples.append({
            'x': x,
            'z': z,
            'speed': data.get('speed', 0),
            'rpm': data.get('rpm', 0),
            'throttle': data.get('throttle', 0),
            'brake': data.get('brake', 0),
            'gear': data.get('gear', 0),
            'time': data.get('track_time', 0),
        })


class SectorTracker:
    """Divides the track into sectors and times them."""

    def __init__(self):
        self.boundaries = []        # [(x, z), ...] for sector split points
        self.ready = False
        self.current_sector = 0     # 0-based
        self.sector_enter_time = 0  # track_time when entered current sector
        self.current_sectors = []   # times for sectors completed this lap
        self.all_laps = []          # [{lap, s1, s2, s3, total}, ...]
        self._cooldown = False      # avoid double-trigger

    def build_from_reference(self, samples):
        """Create sector boundaries from a completed lap's samples."""
        if len(samples) < NUM_SECTORS * 3:
            return

        dists = _cumulative_distances(samples)
        total_dist = dists[-1]
        if total_dist < 100:
            return

        self.boundaries = []
        for s in range(1, NUM_SECTORS):
            target = total_dist * s / NUM_SECTORS
            # Find the sample closest to this distance
            best_idx = 0
            best_diff = abs(dists[0] - target)
            for i in range(1, len(dists)):
                diff = abs(dists[i] - target)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
            self.boundaries.append(
                (samples[best_idx]['x'], samples[best_idx]['z'])
            )

        self.ready = True
        self.current_sector = 0
        self.current_sectors = []

    def on_lap_start(self, track_time):
        """Reset for a new lap."""
        self.current_sector = 0
        self.sector_enter_time = track_time
        self.current_sectors = []
        self._cooldown = False

    def on_telemetry(self, data, lap_num):
        """Check if we crossed a sector boundary. Returns sector index crossed or -1."""
        if not self.ready or not self.boundaries:
            return -1

        x = data.get('pos_x', 0)
        z = data.get('pos_z', 0)
        track_time = data.get('track_time', 0)

        # Check if we're near the next sector boundary
        next_boundary_idx = self.current_sector  # boundaries[0] = S1→S2, boundaries[1] = S2→S3
        if next_boundary_idx >= len(self.boundaries):
            return -1  # past last boundary, waiting for lap end

        bx, bz = self.boundaries[next_boundary_idx]
        d = _dist((x, z), (bx, bz))

        if d < SECTOR_TRIGGER_DIST and not self._cooldown:
            sector_time = track_time - self.sector_enter_time
            self.current_sectors.append(sector_time)
            self.sector_enter_time = track_time
            self.current_sector += 1
            self._cooldown = True
            return self.current_sector - 1
        elif d > SECTOR_TRIGGER_DIST * 2:
            self._cooldown = False

        return -1

    def finish_lap(self, lap_num, track_time, total_time_ms):
        """Record the final sector time and save the lap."""
        if not self.ready:
            return
        # Last sector: from last boundary to finish line
        last_sector_time = track_time - self.sector_enter_time
        self.current_sectors.append(last_sector_time)

        if len(self.current_sectors) == NUM_SECTORS:
            entry = {
                'lap': lap_num,
                'total': total_time_ms,
            }
            for i, t in enumerate(self.current_sectors):
                entry['s' + str(i + 1)] = t
            self.all_laps.append(entry)

    def get_all_laps(self):
        """Return sector data for all completed laps."""
        return self.all_laps

    def get_current_sectors(self):
        """Return sector times for the in-progress lap."""
        return list(self.current_sectors)

    def get_current_sector_index(self):
        return self.current_sector


# How far ahead (in reference samples) to look for upcoming gear changes
SHIFT_LOOKAHEAD = 12
# Min RPM fraction of rev limit to suggest upshift even without reference
RPM_UPSHIFT_FRACTION = 0.92
# Gear speed limit learning
GEAR_LIMIT_MARGIN = 0.97       # upshift at 97% of learned max speed for gear
GEAR_LIMIT_DOWN_MARGIN = 0.60  # downshift when below 60% of current gear's max
GEAR_LIMIT_MIN_SAMPLES = 20    # min samples before trusting
GEAR_LIMIT_THROTTLE_MIN = 0.8  # only learn limits at high throttle


class GearLimits:
    """Learns max speed per gear from live telemetry. Works for any car."""

    def __init__(self):
        # gear -> {max_speed, samples, rpm_at_max, speeds: [last N]}
        self.gears = {}
        self._last_car_id = None

    def on_telemetry(self, data: dict):
        """Feed a telemetry frame. Learns gear speed limits."""
        gear = data.get('gear', 0)
        speed = data.get('speed', 0)
        throttle = data.get('throttle', 0)
        rpm = data.get('rpm', 0)

        # Reset if car changed (rpm_max changes between cars)
        car_sig = data.get('rpm_max', 0)
        if self._last_car_id and car_sig != self._last_car_id:
            self.gears.clear()
        self._last_car_id = car_sig

        if gear <= 0 or speed < 5:
            return

        if gear not in self.gears:
            self.gears[gear] = {
                'max_speed': 0,
                'samples': 0,
                'rpm_at_max': 0,
            }

        g = self.gears[gear]
        g['samples'] += 1

        # Only update max speed when throttle is high (actually pushing the gear)
        if throttle >= GEAR_LIMIT_THROTTLE_MIN:
            if speed > g['max_speed']:
                g['max_speed'] = speed
                g['rpm_at_max'] = rpm

    def get_shift_advice(self, gear, speed):
        """Return shift advice based on learned limits.

        Returns: (advice, target_gear)
            advice: 'up', 'down', or ''
            target_gear: which gear to shift to
        """
        if gear <= 0:
            return '', 0

        g_data = self.gears.get(gear)
        if not g_data or g_data['samples'] < GEAR_LIMIT_MIN_SAMPLES:
            return '', 0

        max_spd = g_data['max_speed']
        if max_spd < 10:
            return '', 0

        # Upshift: approaching this gear's speed ceiling
        if speed >= max_spd * GEAR_LIMIT_MARGIN:
            next_gear = gear + 1
            # Make sure next gear exists and has headroom
            if next_gear in self.gears:
                return 'up', next_gear
            else:
                return 'up', next_gear  # suggest even if not yet seen

        # Downshift: speed too low for this gear, lower gear would be better
        if gear > 1:
            lower = self.gears.get(gear - 1)
            if lower and lower['samples'] >= GEAR_LIMIT_MIN_SAMPLES:
                # If current speed is below the max of the lower gear,
                # and well below current gear's optimal range — downshift
                if speed < max_spd * GEAR_LIMIT_DOWN_MARGIN:
                    return 'down', gear - 1

        return '', 0

    def get_limits_display(self):
        """Return gear limits for display: {gear: max_speed, ...}."""
        result = {}
        for gear in sorted(self.gears):
            g = self.gears[gear]
            if g['samples'] >= GEAR_LIMIT_MIN_SAMPLES and g['max_speed'] > 10:
                result[gear] = round(g['max_speed'], 1)
        return result
# Cooldown distance for shift tips (meters)
SHIFT_TIP_COOLDOWN = 40.0


def _build_shift_points(samples):
    """Pre-compute gear change points from a lap's samples.

    Returns list of {idx, x, z, from_gear, to_gear, type='up'|'down'}.
    """
    shifts = []
    for i in range(1, len(samples)):
        prev_g = samples[i - 1]['gear']
        cur_g = samples[i]['gear']
        if cur_g != prev_g and prev_g > 0 and cur_g > 0:
            shifts.append({
                'idx': i,
                'x': samples[i]['x'],
                'z': samples[i]['z'],
                'from_gear': prev_g,
                'to_gear': cur_g,
                'type': 'up' if cur_g > prev_g else 'down',
            })
    return shifts


class DrivingCoach:
    """Compares current driving to a reference lap and generates tips."""

    def __init__(self):
        self.reference_lap = None
        self.reference_time = None
        self.reference_shifts = []
        self.current_recorder = LapRecorder()
        self.completed_laps = {}
        self.current_lap = -1
        self.lap_start_time = 0

        self.sectors = SectorTracker()
        self.gear_limits = GearLimits()
        self.lap_reports = []  # [{lap, time_ms, summary, zones}]

        # Tip cooldown tracking
        self._last_tip = ''
        self._last_tip_pos = None
        self._last_ref_idx = 0
        self._last_shift_tip_pos = None

    def on_telemetry(self, data: dict) -> dict:
        """Process a telemetry frame. Returns coaching data dict."""
        lap = data.get('lap', 0)
        track_time = data.get('track_time', 0)

        # Detect lap change
        if lap != self.current_lap:
            self._finish_lap(data)
            self.current_lap = lap
            self.lap_start_time = track_time
            self.current_recorder = LapRecorder()
            self._last_ref_idx = 0
            self.sectors.on_lap_start(track_time)

        # Record current lap
        self.current_recorder.add(data)

        # Learn gear speed limits
        self.gear_limits.on_telemetry(data)

        # Track sectors
        self.sectors.on_telemetry(data, lap)

        # Compare to reference
        coaching = {
            'has_reference': self.reference_lap is not None,
            'delta_speed': 0,
            'ref_speed': 0,
            'delta_time': 0,
            'tip': '',
            'tip_type': '',
            'sectors_ready': self.sectors.ready,
            'cur_sector': self.sectors.get_current_sector_index(),
            'cur_sectors': self.sectors.get_current_sectors(),
            'all_laps': self.sectors.get_all_laps(),
            'gear_limits': self.gear_limits.get_limits_display(),
            'lap_reports': self.lap_reports,
        }

        if self.reference_lap:
            self._compare(data, coaching)
        else:
            # Even without reference, give gear shift advice from learned limits
            self._gear_limit_shift(data, coaching)

        return coaching

    def _finish_lap(self, data):
        """Called when a new lap starts — process the just-completed lap."""
        if self.current_lap <= 0:
            return
        if len(self.current_recorder.samples) < 10:
            return

        last_lap_ms = data.get('last_lap', -1)
        if last_lap_ms <= 0:
            return

        track_time = data.get('track_time', 0)

        # Finish sector timing
        self.sectors.finish_lap(self.current_lap, track_time, last_lap_ms)

        completed_samples = self.current_recorder.samples

        self.completed_laps[self.current_lap] = {
            'samples': completed_samples,
            'time_ms': last_lap_ms,
        }

        # Analyze against reference BEFORE updating it
        if self.reference_lap and len(completed_samples) > 20:
            zones = analyze_lap(completed_samples, self.reference_lap, self.reference_time)
            summary = summarize_lap(zones)
            self.lap_reports.append({
                'lap': self.current_lap,
                'time_ms': last_lap_ms,
                'summary': summary,
                'zones': zones,
            })
            # Keep only last 20 reports
            if len(self.lap_reports) > 20:
                self.lap_reports = self.lap_reports[-20:]

        # Update reference if this was the best lap
        is_new_best = False
        if self.reference_time is None or last_lap_ms < self.reference_time:
            self.reference_time = last_lap_ms
            self.reference_lap = completed_samples
            is_new_best = True

        # Build sectors and shift map from reference
        if is_new_best or not self.sectors.ready:
            if self.reference_lap:
                self.sectors.build_from_reference(self.reference_lap)
                self.reference_shifts = _build_shift_points(self.reference_lap)

    def _find_closest_ref(self, x, z):
        """Find the closest reference sample to (x, z), searching forward."""
        if not self.reference_lap:
            return None, -1

        ref = self.reference_lap
        n = len(ref)
        best_dist = float('inf')
        best_idx = -1

        search_start = max(0, self._last_ref_idx - 20)
        search_end = min(n, self._last_ref_idx + 80)

        ranges = [(search_start, search_end)]
        if self._last_ref_idx > n - 40:
            ranges.append((0, 40))

        for rng_start, rng_end in ranges:
            for i in range(rng_start, rng_end):
                d = _dist((x, z), (ref[i]['x'], ref[i]['z']))
                if d < best_dist:
                    best_dist = d
                    best_idx = i

        if best_dist < MATCH_RADIUS:
            self._last_ref_idx = best_idx
            return ref[best_idx], best_idx
        return None, -1

    def _compare(self, data, coaching):
        """Compare current telemetry to reference and generate coaching."""
        x = data.get('pos_x', 0)
        z = data.get('pos_z', 0)
        ref_sample, ref_idx = self._find_closest_ref(x, z)
        if ref_sample is None:
            return

        cur_speed = data.get('speed', 0)
        cur_gear = data.get('gear', 0)
        cur_rpm = data.get('rpm', 0)
        rpm_max = data.get('rpm_max', 9000)
        ref_speed = ref_sample['speed']
        ref_gear = ref_sample['gear']
        coaching['ref_speed'] = round(ref_speed, 1)
        coaching['delta_speed'] = round(cur_speed - ref_speed, 1)
        coaching['ref_gear'] = ref_gear

        cur_time_in_lap = data.get('track_time', 0) - self.lap_start_time
        if ref_idx >= 0 and self.reference_time and self.reference_time > 0:
            ref_time_at_point = ref_sample['time']
            ref_start = self.reference_lap[0]['time'] if self.reference_lap else 0
            ref_elapsed = ref_time_at_point - ref_start
            coaching['delta_time'] = round((ref_elapsed - cur_time_in_lap) / 1000, 2)

        # --- Gear shift analysis ---
        shift_tip = ''
        shift_type = ''  # 'up', 'down', 'up_now', 'down_now'

        # 1) Look ahead on reference for upcoming gear changes
        if ref_idx >= 0 and self.reference_shifts:
            upcoming = self._find_upcoming_shift(ref_idx, x, z)
            if upcoming:
                shift_tip = upcoming['tip']
                shift_type = upcoming['type']

        # 2) Direct comparison: wrong gear right now
        if not shift_tip and cur_gear > 0 and ref_gear > 0:
            if cur_gear < ref_gear:
                shift_tip = 'SHIFT UP \u2191 ' + str(ref_gear)
                shift_type = 'up_now'
            elif cur_gear > ref_gear:
                shift_tip = 'SHIFT DOWN \u2193 ' + str(ref_gear)
                shift_type = 'down_now'

        # 3) Gear speed limit: hitting the ceiling for this gear
        if not shift_tip:
            gl_advice, gl_target = self.gear_limits.get_shift_advice(cur_gear, cur_speed)
            if gl_advice == 'up':
                g_max = self.gear_limits.gears.get(cur_gear, {}).get('max_speed', 0)
                shift_tip = 'LIMIT \u2191 ' + str(gl_target) + ' (max ' + str(int(g_max)) + ')'
                shift_type = 'up_now'
            elif gl_advice == 'down':
                shift_tip = 'TOO SLOW \u2193 ' + str(gl_target)
                shift_type = 'down_now'

        # 4) RPM-based upshift fallback
        if not shift_tip and cur_rpm > rpm_max * RPM_UPSHIFT_FRACTION and cur_gear > 0:
            shift_tip = 'SHIFT UP \u2191'
            shift_type = 'up_now'

        # Apply shift tip cooldown
        if shift_tip and self._last_shift_tip_pos:
            if _dist((x, z), self._last_shift_tip_pos) < SHIFT_TIP_COOLDOWN:
                shift_tip = ''
                shift_type = ''

        if shift_tip:
            self._last_shift_tip_pos = (x, z)

        coaching['shift_tip'] = shift_tip
        coaching['shift_type'] = shift_type

        # --- General driving tips ---
        cur_brake = data.get('brake', 0)
        cur_throttle = data.get('throttle', 0)
        ref_brake = ref_sample['brake']
        ref_throttle = ref_sample['throttle']
        speed_diff = cur_speed - ref_speed

        tip = ''
        tip_type = ''

        if cur_brake > 0.3 and ref_brake < 0.1 and ref_idx >= 0:
            tip = 'BRAKE LATER'
            tip_type = 'brake'
        elif cur_brake < 0.1 and ref_brake > 0.3:
            if cur_speed > ref_speed + 5:
                tip = 'BRAKE NOW!'
                tip_type = 'brake_urgent'
            else:
                tip = 'GOOD SPEED'
                tip_type = 'good'
        elif speed_diff < -SPEED_SLOW_THRESHOLD and cur_throttle < 0.5:
            tip = 'MORE THROTTLE'
            tip_type = 'throttle'
        elif speed_diff < -SPEED_SLOW_THRESHOLD:
            tip = 'CARRY MORE SPEED'
            tip_type = 'speed'
        elif speed_diff > SPEED_FAST_THRESHOLD and cur_brake < 0.1:
            tip = 'GOOD SPEED!'
            tip_type = 'good'
        elif cur_throttle < ref_throttle - THROTTLE_DIFF and ref_throttle > 0.5:
            tip = 'MORE GAS'
            tip_type = 'throttle'

        # Apply cooldown
        if tip:
            if tip == self._last_tip and self._last_tip_pos:
                if _dist((x, z), self._last_tip_pos) < TIP_COOLDOWN_DIST:
                    tip = ''
                    tip_type = ''

        if tip:
            self._last_tip = tip
            self._last_tip_pos = (x, z)

        coaching['tip'] = tip
        coaching['tip_type'] = tip_type

    def _gear_limit_shift(self, data, coaching):
        """Gear shift advice from learned limits only (no reference lap needed)."""
        cur_gear = data.get('gear', 0)
        cur_speed = data.get('speed', 0)
        cur_rpm = data.get('rpm', 0)
        rpm_max = data.get('rpm_max', 9000)
        x = data.get('pos_x', 0)
        z = data.get('pos_z', 0)

        shift_tip = ''
        shift_type = ''

        # Speed-based from learned limits
        gl_advice, gl_target = self.gear_limits.get_shift_advice(cur_gear, cur_speed)
        if gl_advice == 'up':
            g_max = self.gear_limits.gears.get(cur_gear, {}).get('max_speed', 0)
            shift_tip = 'LIMIT \u2191 ' + str(gl_target) + ' (max ' + str(int(g_max)) + ')'
            shift_type = 'up_now'
        elif gl_advice == 'down':
            shift_tip = 'TOO SLOW \u2193 ' + str(gl_target)
            shift_type = 'down_now'

        # RPM fallback
        if not shift_tip and cur_rpm > rpm_max * RPM_UPSHIFT_FRACTION and cur_gear > 0:
            shift_tip = 'SHIFT UP \u2191'
            shift_type = 'up_now'

        # Cooldown
        if shift_tip and self._last_shift_tip_pos:
            if _dist((x, z), self._last_shift_tip_pos) < SHIFT_TIP_COOLDOWN:
                shift_tip = ''
                shift_type = ''

        if shift_tip:
            self._last_shift_tip_pos = (x, z)

        coaching['shift_tip'] = shift_tip
        coaching['shift_type'] = shift_type

    def _find_upcoming_shift(self, ref_idx, cur_x, cur_z):
        """Look ahead on reference to find the next gear change."""
        for sp in self.reference_shifts:
            if sp['idx'] <= ref_idx:
                continue
            if sp['idx'] > ref_idx + SHIFT_LOOKAHEAD:
                break

            d = _dist((cur_x, cur_z), (sp['x'], sp['z']))
            if d < 150:  # within 150m — announce
                gear_str = str(sp['to_gear'])
                if sp['type'] == 'up':
                    if d < 30:
                        return {'tip': 'SHIFT UP \u2191 ' + gear_str, 'type': 'up_now'}
                    else:
                        return {'tip': '\u2191 ' + gear_str + ' in ' + str(int(d)) + 'm', 'type': 'up'}
                else:
                    if d < 30:
                        return {'tip': 'SHIFT DOWN \u2193 ' + gear_str, 'type': 'down_now'}
                    else:
                        return {'tip': '\u2193 ' + gear_str + ' in ' + str(int(d)) + 'm', 'type': 'down'}
        return None
