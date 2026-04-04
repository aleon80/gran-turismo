"""Gran Turismo 7 telemetry packet decryption and parsing."""

import struct
from Crypto.Cipher import Salsa20

# First 32 bytes of "Simulator Interface Packet GT7 ver 0.0"
SALSA_KEY = b'Simulator Interface Packet GT7 v'
MAGIC = 0x47375330
PACKET_SIZE = 0x128  # 296 bytes


def decrypt_packet(raw: bytes) -> bytes | None:
    """Decrypt a raw GT7 telemetry packet using Salsa20."""
    if len(raw) < PACKET_SIZE:
        return None

    # Extract IV seed from raw (encrypted) packet
    seed = struct.unpack_from('<I', raw, 0x40)[0]
    iv = seed ^ 0xDEADBEAF
    nonce = struct.pack('<II', iv, seed)

    cipher = Salsa20.new(key=SALSA_KEY, nonce=nonce)
    return cipher.decrypt(raw)


def parse_packet(data: bytes) -> dict | None:
    """Parse a decrypted GT7 telemetry packet into a dict."""
    if len(data) < PACKET_SIZE:
        return None

    magic = struct.unpack_from('<i', data, 0x00)[0]
    if magic != MAGIC:
        return None

    # Position & velocity
    pos_x, pos_y, pos_z = struct.unpack_from('<3f', data, 0x04)
    vel_x, vel_y, vel_z = struct.unpack_from('<3f', data, 0x10)

    # Rotation
    rot_pitch, rot_yaw, rot_roll = struct.unpack_from('<3f', data, 0x1C)

    # Core engine
    body_height = struct.unpack_from('<f', data, 0x38)[0]
    rpm = struct.unpack_from('<f', data, 0x3C)[0]

    # Fuel
    fuel_level = struct.unpack_from('<f', data, 0x44)[0]
    fuel_capacity = struct.unpack_from('<f', data, 0x48)[0]

    # Speed & boost
    speed_mps = struct.unpack_from('<f', data, 0x4C)[0]
    turbo_boost = struct.unpack_from('<f', data, 0x50)[0]

    # Temps & pressures
    oil_pressure = struct.unpack_from('<f', data, 0x54)[0]
    water_temp = struct.unpack_from('<f', data, 0x58)[0]
    oil_temp = struct.unpack_from('<f', data, 0x5C)[0]

    # Tire temps (FL, FR, RL, RR)
    tire_fl = struct.unpack_from('<f', data, 0x60)[0]
    tire_fr = struct.unpack_from('<f', data, 0x64)[0]
    tire_rl = struct.unpack_from('<f', data, 0x68)[0]
    tire_rr = struct.unpack_from('<f', data, 0x6C)[0]

    # Time / lap info
    packet_id = struct.unpack_from('<i', data, 0x70)[0]
    current_lap = struct.unpack_from('<h', data, 0x74)[0]
    total_laps = struct.unpack_from('<h', data, 0x76)[0]
    best_lap_ms = struct.unpack_from('<i', data, 0x78)[0]
    last_lap_ms = struct.unpack_from('<i', data, 0x7C)[0]

    # RPM limits
    rpm_alert = struct.unpack_from('<H', data, 0x88)[0]
    rpm_max = struct.unpack_from('<H', data, 0x8A)[0]

    # Controls & gear (byte values at 0x90-0x92)
    gear_byte = data[0x90]
    current_gear = gear_byte & 0x0F        # 0=R, 1-8=gears
    suggested_gear = (gear_byte >> 4) & 0x0F  # >14 = no suggestion
    throttle = data[0x91] / 255.0
    brake = data[0x92] / 255.0

    # Race info
    time_on_track = struct.unpack_from('<i', data, 0x80)[0]  # ms
    race_pos = struct.unpack_from('<h', data, 0x84)[0]
    num_cars = struct.unpack_from('<h', data, 0x86)[0]

    # Tire angular speed (rad/s) & diameter (m)
    tire_spd_fl = struct.unpack_from('<f', data, 0xA4)[0]
    tire_spd_fr = struct.unpack_from('<f', data, 0xA8)[0]
    tire_spd_rl = struct.unpack_from('<f', data, 0xAC)[0]
    tire_spd_rr = struct.unpack_from('<f', data, 0xB0)[0]
    tire_dia_fl = struct.unpack_from('<f', data, 0xB4)[0]
    tire_dia_fr = struct.unpack_from('<f', data, 0xB8)[0]
    tire_dia_rl = struct.unpack_from('<f', data, 0xBC)[0]
    tire_dia_rr = struct.unpack_from('<f', data, 0xC0)[0]

    # Suspension travel (m)
    susp_fl = struct.unpack_from('<f', data, 0xC4)[0]
    susp_fr = struct.unpack_from('<f', data, 0xC8)[0]
    susp_rl = struct.unpack_from('<f', data, 0xCC)[0]
    susp_rr = struct.unpack_from('<f', data, 0xD0)[0]

    fuel_pct = (fuel_level / fuel_capacity * 100) if fuel_capacity > 0 else 0

    # Compute tire slip ratios (>1 = wheelspin, <1 approaching 0 = lockup)
    def _slip(tire_spd, tire_dia):
        tire_v = abs(tire_spd * tire_dia * 0.5)  # m/s at tire surface
        if speed_mps < 1:
            return 1.0
        return tire_v / speed_mps

    return {
        'speed': round(speed_mps * 3.6, 1),
        'speed_mps': round(speed_mps, 2),
        'rpm': round(rpm),
        'rpm_max': rpm_max if rpm_max > 0 else 9000,
        'rpm_alert': rpm_alert,
        'gear': current_gear,
        'suggested_gear': suggested_gear,
        'throttle': round(throttle, 2),
        'brake': round(brake, 2),
        'turbo': round(turbo_boost, 2),
        'fuel': round(fuel_pct, 1),
        'fuel_level': round(fuel_level, 2),
        'fuel_capacity': round(fuel_capacity, 2),
        'oil_pressure': round(oil_pressure, 1),
        'water_temp': round(water_temp, 1),
        'oil_temp': round(oil_temp, 1),
        'tire_fl': round(tire_fl, 1),
        'tire_fr': round(tire_fr, 1),
        'tire_rl': round(tire_rl, 1),
        'tire_rr': round(tire_rr, 1),
        'slip_fl': round(_slip(tire_spd_fl, tire_dia_fl), 2),
        'slip_fr': round(_slip(tire_spd_fr, tire_dia_fr), 2),
        'slip_rl': round(_slip(tire_spd_rl, tire_dia_rl), 2),
        'slip_rr': round(_slip(tire_spd_rr, tire_dia_rr), 2),
        'vel_x': round(vel_x, 3),
        'vel_y': round(vel_y, 3),
        'vel_z': round(vel_z, 3),
        'yaw': round(rot_yaw, 4),
        'lap': current_lap,
        'total_laps': total_laps,
        'best_lap': best_lap_ms,
        'last_lap': last_lap_ms,
        'packet_id': packet_id,
        'pos_x': round(pos_x, 1),
        'pos_z': round(pos_z, 1),
        'race_pos': race_pos,
        'num_cars': num_cars,
        'track_time': time_on_track,
    }
