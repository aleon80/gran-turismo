"""Post-lap error analysis: compares each lap to reference and identifies mistakes."""

import math

NUM_ZONES = 20  # divide track into this many analysis zones
SPEED_LOSS_THRESHOLD = 5  # km/h slower than reference to flag
BRAKE_EARLY_DIST = 12  # meters earlier braking to flag
TIME_LOSS_THRESHOLD = 0.1  # seconds lost to flag a zone


def _dist(p1, p2):
    dx = p1[0] - p2[0]
    dz = p1[1] - p2[1]
    return math.sqrt(dx * dx + dz * dz)


def _cumulative_distances(samples):
    dists = [0.0]
    for i in range(1, len(samples)):
        d = _dist(
            (samples[i]['x'], samples[i]['z']),
            (samples[i - 1]['x'], samples[i - 1]['z']),
        )
        dists.append(dists[-1] + d)
    return dists


def _find_brake_start(samples, zone_start, zone_end):
    """Find the index where heavy braking starts in a zone."""
    for i in range(zone_start, min(zone_end, len(samples))):
        if samples[i].get('brake', 0) > 0.3:
            return i
    return -1


def _zone_avg(samples, start, end, key):
    vals = [s[key] for s in samples[start:end] if key in s]
    return sum(vals) / len(vals) if vals else 0


def analyze_lap(lap_samples, ref_samples, ref_time_ms):
    """Compare a completed lap to reference. Returns list of zone analyses.

    Each zone: {
        zone: int,
        x, z: center position,
        time_delta: float (seconds, positive = slower),
        errors: [{type, detail, severity}],
    }
    """
    if not lap_samples or not ref_samples:
        return []
    if len(lap_samples) < 20 or len(ref_samples) < 20:
        return []

    ref_dists = _cumulative_distances(ref_samples)
    lap_dists = _cumulative_distances(lap_samples)
    ref_total = ref_dists[-1]
    lap_total = lap_dists[-1]

    if ref_total < 100 or lap_total < 100:
        return []

    # Build zones from reference
    zone_size = ref_total / NUM_ZONES
    ref_zones = []  # [(start_idx, end_idx)]
    zi = 0
    for z in range(NUM_ZONES):
        zone_start_dist = z * zone_size
        zone_end_dist = (z + 1) * zone_size
        start_idx = zi
        while zi < len(ref_dists) - 1 and ref_dists[zi] < zone_end_dist:
            zi += 1
        ref_zones.append((start_idx, zi))

    # Map lap samples to zones by closest reference point
    lap_zone_map = [[] for _ in range(NUM_ZONES)]
    ref_search = 0
    for li in range(len(lap_samples)):
        lx, lz = lap_samples[li]['x'], lap_samples[li]['z']
        # Find closest ref sample
        best_d = float('inf')
        best_ri = ref_search
        for ri in range(max(0, ref_search - 10), min(len(ref_samples), ref_search + 30)):
            d = _dist((lx, lz), (ref_samples[ri]['x'], ref_samples[ri]['z']))
            if d < best_d:
                best_d = d
                best_ri = ri
        ref_search = best_ri
        # Which zone is this ref index in?
        for zidx, (zs, ze) in enumerate(ref_zones):
            if zs <= best_ri < ze:
                lap_zone_map[zidx].append(li)
                break

    # Analyze each zone
    ref_start_time = ref_samples[0]['time']
    lap_start_time = lap_samples[0]['time']
    results = []

    for z in range(NUM_ZONES):
        rs, re = ref_zones[z]
        lap_indices = lap_zone_map[z]
        if not lap_indices or rs >= re:
            continue

        # Zone center position
        mid_ref = ref_samples[(rs + re) // 2]

        # Time comparison
        ref_zone_time = (ref_samples[min(re, len(ref_samples)-1)]['time'] -
                         ref_samples[rs]['time']) / 1000.0
        if lap_indices:
            li_s, li_e = lap_indices[0], lap_indices[-1]
            lap_zone_time = (lap_samples[min(li_e, len(lap_samples)-1)]['time'] -
                             lap_samples[li_s]['time']) / 1000.0
        else:
            lap_zone_time = ref_zone_time

        time_delta = lap_zone_time - ref_zone_time

        # Error detection
        errors = []

        # 1) Speed analysis
        ref_avg_speed = _zone_avg(ref_samples, rs, re, 'speed')
        lap_avg_speed = _zone_avg(lap_samples, lap_indices[0], lap_indices[-1] + 1,
                                   'speed') if lap_indices else 0
        speed_diff = lap_avg_speed - ref_avg_speed

        ref_min_speed = min((s['speed'] for s in ref_samples[rs:re]), default=0)
        lap_min_speed = min((lap_samples[i]['speed'] for i in lap_indices), default=0) if lap_indices else 0

        # 2) Braking analysis
        ref_brake_idx = _find_brake_start(ref_samples, rs, re)
        lap_brake_idx = -1
        if lap_indices:
            for li in lap_indices:
                if lap_samples[li].get('brake', 0) > 0.3:
                    lap_brake_idx = li
                    break

        has_brake_zone = ref_brake_idx >= 0 or lap_brake_idx >= 0

        if has_brake_zone:
            if ref_brake_idx >= 0 and lap_brake_idx >= 0:
                # Both brake in this zone — compare positions
                ref_bp = (ref_samples[ref_brake_idx]['x'], ref_samples[ref_brake_idx]['z'])
                lap_bp = (lap_samples[lap_brake_idx]['x'], lap_samples[lap_brake_idx]['z'])
                # Approximate: how far along the zone each brakes
                # Use distance from zone start
                ref_bd = _dist((ref_samples[rs]['x'], ref_samples[rs]['z']), ref_bp)
                lap_bd = _dist((lap_samples[lap_indices[0]]['x'], lap_samples[lap_indices[0]]['z']), lap_bp)
                brake_diff = lap_bd - ref_bd  # negative = braked earlier

                if brake_diff < -BRAKE_EARLY_DIST:
                    errors.append({
                        'type': 'brake_early',
                        'detail': 'Гальмував на ' + str(int(abs(brake_diff))) + 'м зарано',
                        'severity': min(abs(brake_diff) / 30, 1.0),
                    })
                elif brake_diff > BRAKE_EARLY_DIST:
                    errors.append({
                        'type': 'brake_late',
                        'detail': 'Гальмував на ' + str(int(brake_diff)) + 'м пізніше',
                        'severity': 0.3,
                    })
            elif lap_brake_idx >= 0 and ref_brake_idx < 0:
                errors.append({
                    'type': 'unnecessary_brake',
                    'detail': 'Гальмував, а еталон ні',
                    'severity': 0.6,
                })

        # 3) Corner speed (min speed in zone)
        if ref_min_speed > 20 and lap_min_speed > 0:
            corner_diff = lap_min_speed - ref_min_speed
            if corner_diff < -SPEED_LOSS_THRESHOLD:
                errors.append({
                    'type': 'slow_corner',
                    'detail': 'На ' + str(int(abs(corner_diff))) + ' км/г повільніше в повороті',
                    'severity': min(abs(corner_diff) / 20, 1.0),
                })
            elif corner_diff > SPEED_LOSS_THRESHOLD:
                errors.append({
                    'type': 'fast_corner',
                    'detail': 'На ' + str(int(corner_diff)) + ' км/г швидше!',
                    'severity': 0,
                })

        # 4) Exit speed (speed at zone end)
        if lap_indices:
            ref_exit_speed = ref_samples[min(re - 1, len(ref_samples) - 1)]['speed']
            lap_exit_speed = lap_samples[lap_indices[-1]]['speed']
            exit_diff = lap_exit_speed - ref_exit_speed
            if exit_diff < -SPEED_LOSS_THRESHOLD:
                errors.append({
                    'type': 'slow_exit',
                    'detail': 'На ' + str(int(abs(exit_diff))) + ' км/г повільніше на виході',
                    'severity': min(abs(exit_diff) / 15, 1.0),
                })

        # 5) Gear errors
        if lap_indices:
            ref_gears = set(ref_samples[i]['gear'] for i in range(rs, re) if ref_samples[i]['gear'] > 0)
            lap_gears = set(lap_samples[i]['gear'] for i in lap_indices if lap_samples[i]['gear'] > 0)
            wrong_gears = lap_gears - ref_gears
            if wrong_gears and ref_gears:
                errors.append({
                    'type': 'wrong_gear',
                    'detail': 'Передача ' + ','.join(str(g) for g in sorted(wrong_gears)) +
                              ' (етал: ' + ','.join(str(g) for g in sorted(ref_gears)) + ')',
                    'severity': 0.4,
                })

        # Only include zones with significant time loss or errors
        if abs(time_delta) > TIME_LOSS_THRESHOLD or errors:
            results.append({
                'zone': z + 1,
                'x': round(mid_ref['x'], 1),
                'z': round(mid_ref['z'], 1),
                'time_delta': round(time_delta, 3),
                'errors': errors,
            })

    # Sort by time loss (worst first)
    results.sort(key=lambda r: -r['time_delta'])
    return results


def summarize_lap(analysis):
    """Create a human-readable summary from zone analysis."""
    if not analysis:
        return {'total_loss': 0, 'top_errors': [], 'improvements': []}

    total_loss = sum(z['time_delta'] for z in analysis if z['time_delta'] > 0)
    total_gain = sum(z['time_delta'] for z in analysis if z['time_delta'] < 0)

    top_errors = []
    improvements = []

    for z in analysis:
        label = 'T' + str(z['zone'])
        if z['time_delta'] > TIME_LOSS_THRESHOLD:
            for err in z['errors']:
                top_errors.append({
                    'zone': label,
                    'loss': round(z['time_delta'], 2),
                    'type': err['type'],
                    'detail': err['detail'],
                    'severity': err.get('severity', 0.5),
                })
        elif z['time_delta'] < -TIME_LOSS_THRESHOLD:
            improvements.append({
                'zone': label,
                'gain': round(abs(z['time_delta']), 2),
            })

    # Top 5 errors by time loss
    top_errors.sort(key=lambda e: -e['loss'])
    top_errors = top_errors[:5]

    return {
        'total_loss': round(total_loss, 2),
        'total_gain': round(abs(total_gain), 2),
        'top_errors': top_errors,
        'improvements': improvements[:3],
    }
