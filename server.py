"""GT7 Telemetry Dashboard Server.

Receives UDP telemetry from Gran Turismo 7, serves a web dashboard via WebSocket.

Usage:
    python server.py [--console-ip IP] [--port PORT]
"""

import argparse
import asyncio
import json
import logging
import math
from pathlib import Path

from aiohttp import web

from gt7_packet import decrypt_packet, parse_packet
from coach import DrivingCoach

logger = logging.getLogger('gt7dash')

HEARTBEAT_INTERVAL = 10  # seconds
GT7_PORT = 33739
STATIC_DIR = Path(__file__).parent / 'static'

# Shared state
latest_data: dict = {}
ws_clients: set[web.WebSocketResponse] = set()
track_points: list[list[float]] = []  # [[x, z, speed], ...] for track map
MAX_TRACK_POINTS = 3000
last_track_lap = -1
coach = DrivingCoach()
prev_vel = None  # for G-force calc
prev_fuel_per_lap = []  # for fuel prediction


class GT7Protocol(asyncio.DatagramProtocol):
    """UDP protocol for receiving GT7 telemetry."""

    def __init__(self, console_ip: str):
        self.console_ip = console_ip
        self.transport = None
        self.packets_received = 0

    def connection_made(self, transport):
        self.transport = transport
        self.send_heartbeat()
        logger.info('UDP socket ready, heartbeat sent to %s:%d', self.console_ip, GT7_PORT)

    def send_heartbeat(self):
        self.transport.sendto(b'A', (self.console_ip, GT7_PORT))

    def datagram_received(self, data, addr):
        global latest_data, track_points, last_track_lap
        decrypted = decrypt_packet(data)
        if decrypted is None:
            return

        parsed = parse_packet(decrypted)
        if parsed:
            self.packets_received += 1
            global prev_vel

            # G-force from velocity delta (assuming ~60fps)
            vx = parsed.get('vel_x', 0)
            vy = parsed.get('vel_y', 0)
            vz = parsed.get('vel_z', 0)
            yaw = parsed.get('yaw', 0)
            if prev_vel is not None:
                dt = 1.0 / 60.0
                ax = (vx - prev_vel[0]) / dt
                az = (vz - prev_vel[2]) / dt
                # Rotate to car-local frame using yaw
                cos_y = math.cos(yaw)
                sin_y = math.sin(yaw)
                g_lat = (ax * cos_y - az * sin_y) / 9.81
                g_lon = (ax * sin_y + az * cos_y) / 9.81
                parsed['g_lat'] = round(g_lat, 2)
                parsed['g_lon'] = round(g_lon, 2)
            else:
                parsed['g_lat'] = 0
                parsed['g_lon'] = 0
            prev_vel = (vx, vy, vz)

            # Tire slip warnings
            slips = [
                parsed.get('slip_fl', 1), parsed.get('slip_fr', 1),
                parsed.get('slip_rl', 1), parsed.get('slip_rr', 1),
            ]
            lockup = any(s < 0.85 for s in slips) and parsed.get('brake', 0) > 0.3
            wheelspin = any(s > 1.3 for s in slips) and parsed.get('throttle', 0) > 0.5
            parsed['lockup'] = lockup
            parsed['wheelspin'] = wheelspin

            # Fuel prediction
            fuel_laps = 0
            coach_data = coach.on_telemetry(parsed)
            if coach_data.get('all_laps') and parsed.get('fuel_level', 0) > 0:
                laps_done = len(coach_data['all_laps'])
                if laps_done > 0 and parsed.get('fuel_capacity', 0) > 0:
                    fuel_used = parsed['fuel_capacity'] - parsed['fuel_level']
                    if fuel_used > 0:
                        fuel_per_lap = fuel_used / laps_done
                        fuel_laps = parsed['fuel_level'] / fuel_per_lap
                        parsed['fuel_laps'] = round(fuel_laps, 1)

            parsed['coach'] = coach_data
            latest_data = parsed

            # Collect track points with speed for colored map
            cur_lap = parsed.get('lap', 0)
            if cur_lap == 1 and last_track_lap != 1:
                track_points.clear()
            last_track_lap = cur_lap

            px, pz = parsed.get('pos_x', 0), parsed.get('pos_z', 0)
            spd = parsed.get('speed', 0)
            if abs(px) > 0.1 or abs(pz) > 0.1:
                if not track_points or (
                    abs(px - track_points[-1][0]) > 2 or
                    abs(pz - track_points[-1][1]) > 2
                ):
                    if len(track_points) < MAX_TRACK_POINTS:
                        track_points.append([px, pz, spd])


async def heartbeat_loop(protocol: GT7Protocol):
    """Send periodic heartbeat to keep telemetry flowing."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            protocol.send_heartbeat()
        except Exception as e:
            logger.warning('Heartbeat failed: %s', e)


async def broadcast_loop():
    """Send latest telemetry to all connected WebSocket clients at ~30Hz."""
    last_packet_id = -1
    while True:
        await asyncio.sleep(1 / 30)
        if not latest_data or not ws_clients:
            continue
        if latest_data.get('packet_id') == last_packet_id:
            continue
        last_packet_id = latest_data.get('packet_id', -1)

        payload = dict(latest_data)
        # Slim down coach data: only send heavy fields periodically
        if 'coach' in payload:
            coach_data = dict(payload['coach'])
            if last_packet_id % 30 != 0:
                coach_data.pop('all_laps', None)
            if last_packet_id % 60 != 0:
                coach_data.pop('lap_reports', None)
            payload['coach'] = coach_data

        # Send track trail every ~2 seconds
        if last_packet_id % 60 == 0 and track_points:
            payload['trail'] = track_points

        msg = json.dumps(payload)
        dead = set()
        for ws in ws_clients:
            try:
                await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        ws_clients.difference_update(dead)


async def ws_handler(request):
    """WebSocket endpoint for real-time telemetry."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ws_clients.add(ws)
    logger.info('Client connected (%d total)', len(ws_clients))

    try:
        async for _ in ws:
            pass  # No messages expected from client
    finally:
        ws_clients.discard(ws)
        logger.info('Client disconnected (%d remaining)', len(ws_clients))
    return ws


async def index_handler(request):
    """Serve the dashboard HTML."""
    return web.FileResponse(STATIC_DIR / 'index.html')


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get('/', index_handler)
    app.router.add_get('/ws', ws_handler)
    app.router.add_static('/static', STATIC_DIR)
    return app


async def main():
    parser = argparse.ArgumentParser(description='GT7 Telemetry Dashboard')
    parser.add_argument('--console-ip', default='192.168.112.107',
                        help='PlayStation console IP address')
    parser.add_argument('--port', type=int, default=8080,
                        help='Web server port')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')

    # Start UDP telemetry receiver
    loop = asyncio.get_event_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: GT7Protocol(args.console_ip),
        local_addr=('0.0.0.0', 33740),
    )

    asyncio.create_task(heartbeat_loop(protocol))
    asyncio.create_task(broadcast_loop())

    # Start web server
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', args.port)
    await site.start()

    logger.info('Dashboard:  http://0.0.0.0:%d', args.port)
    logger.info('Console IP: %s', args.console_ip)
    logger.info('Open the URL above on your iPad to view telemetry')

    try:
        await asyncio.Event().wait()
    finally:
        transport.close()
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
