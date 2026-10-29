"""Drives the wall from the light sensors.

Connects to every board over BLE, keeps the latest state of all 6 sensors, and
sends them to DisplayCluster as one sensor value (bit N * 2 + k = board N's
sensor k) with PUT /sensors. DisplayCluster shows and hides the windows itself,
from each window's sensorMask and showWhenLit (see make-test-layout.py), so
there's no config.json and one request per change, however many windows.

Needs a DisplayCluster with sensor support (GET/PUT /sensors). Setting the
sensors needs no control of the wall, so this runs alongside the web UI.
A board that disconnects has its sensors reported dark until it's back.

Usage: python manager.py
"""
import asyncio
import http.client
import json
import os
import ssl
import time
from bleak import BleakClient, BleakScanner

CHAR_UUID_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notifications from ESP32
CHAR_UUID_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to ESP32

# Board N's sensor k is bit N * SENSORS_PER_BOARD + k of the sensor value.
# Each board's firmware must advertise its own unique name (set BOARD_INDEX).
DEVICE_NAMES = [
   "XIAO-ESP32C3-0",
   "XIAO-ESP32C3-1",
   "XIAO-ESP32C3-2",
]
SENSORS_PER_BOARD = 2
NUM_SENSORS = len(DEVICE_NAMES) * SENSORS_PER_BOARD
# the bits this program owns; any others on the wall are left alone
ALL_SENSORS_MASK = (1 << NUM_SENSORS) - 1

# The wall's DisplayCluster serves HTTPS with a certificate for this name only,
# so connect by this name and trust that certificate (copied from the wall host).
DC_HOST = 'rattler-dev.tacc.utexas.edu'
DC_PORT = 1910
DC_CAFILE = os.path.expanduser('~/.displaycluster/rattler-dev_cert.pem')
# rattler-dev's own token, kept apart from the default ~/.displaycluster/api_token
with open(os.path.expanduser('~/.displaycluster/rattler-dev_token')) as f:
   DC_TOKEN = f.read().strip()

class Wall:
   """PUT /sensors over one kept-open HTTPS connection: after the first
   request, each one costs about a network round trip instead of a new TLS
   handshake. Blocking, so it's called from a worker thread."""

   def __init__(self):
      self.context = ssl.create_default_context(cafile=DC_CAFILE)
      self.conn = None

   def request(self, method, path, body=None):
      headers = {'Authorization': 'Bearer ' + DC_TOKEN}
      data = None
      if body is not None:
         data = json.dumps(body)
         headers['Content-Type'] = 'application/json'
      # one retry on a fresh connection, for a kept-open one the server closed
      for attempt in (1, 2):
         if self.conn is None:
            self.conn = http.client.HTTPSConnection(DC_HOST, DC_PORT, context=self.context, timeout=5)
         try:
            self.conn.request(method, path, body=data, headers=headers)
            response = self.conn.getresponse()
            reply = json.loads(response.read() or b'null')
            if response.status >= 400:
               message = reply.get('error') if isinstance(reply, dict) else None
               raise RuntimeError(f"{method} {path}: {message or response.reason} (HTTP {response.status})")
            return reply
         except (http.client.HTTPException, OSError):
            self.conn.close()
            self.conn = None
            if attempt == 2:
               raise

   def set_sensors(self, value):
      return self.request('PUT', '/sensors', {'value': value, 'mask': ALL_SENSORS_MASK})['value']

wall = Wall()

# Latest reported lit state per sensor (None = board not connected)
sensor_state = [None] * NUM_SENSORS

# set when sensor_state changes; the sender sends the latest value
changed = asyncio.Event()

# BLE scans running at the same time can conflict (especially on Windows),
# so only one board scans at a time.
scan_lock = asyncio.Lock()

def sensor_value():
   """sensor_state as the wall's sensor value; unknown sensors count as dark."""
   return sum(1 << i for i, lit in enumerate(sensor_state) if lit)

async def sender():
   """Sends the sensor value whenever it changes, one request at a time. Changes
   that arrive while a request is in flight are folded into the next one, so a
   burst never queues up behind a slow network."""
   sent = None
   while True:
      await changed.wait()
      changed.clear()
      value = sensor_value()
      if value == sent:
         continue
      start = time.perf_counter()
      try:
         await asyncio.to_thread(wall.set_sensors, value)
         sent = value
         print(f"wall {value:0{NUM_SENSORS}b} ({(time.perf_counter() - start) * 1000:.0f} ms)")
      except Exception as e:
         print(f"wall error: {e}; retrying in 1s")
         await asyncio.sleep(1)
         changed.set()

def parse_state(data):
   """Each board reports all its sensors as e.g. '10': '1' = lit, '0' = unlit.
   Returns a list of bools, or None if the message is malformed."""
   text = data.decode("utf-8", errors="replace").strip()
   if len(text) != SENSORS_PER_BOARD or any(c not in "01" for c in text):
       return None
   return [c == '1' for c in text]

async def run_once(index):
   name = DEVICE_NAMES[index]
   tag = f"[{index}:{name}]"

   async with scan_lock:
       print(f"{tag} Scanning...")
       device = await BleakScanner.find_device_by_name(name, timeout=10.0)
   if device is None:
       print(f"{tag} Could not find device")
       return

   print(f"{tag} Found: {device.name} [{device.address}]")

   async with BleakClient(device) as client:
       print(f"{tag} Connected.")

       def notification_handler(sender, data):
           if data.startswith(b'#'):
               # Reply to a settings command (see tune.py), e.g. '#T2000 G300'
               print(f"{tag} settings {data[1:].decode(errors='replace').strip()}")
               return
           states = parse_state(data)
           if states is None:
               # No ack, so the board resends after its timeout
               print(f"{tag} ignoring malformed message {data!r}")
               return
           for k, lit in enumerate(states):
               bit = index * SENSORS_PER_BOARD + k
               if lit != sensor_state[bit]:
                   print(f"{tag} sensor {k} (bit {bit}) {lit!r} (was {sensor_state[bit]!r})")
                   sensor_state[bit] = lit
                   changed.set()
           asyncio.create_task(client.write_gatt_char(CHAR_UUID_RX, b"OK", response=False))

       await client.start_notify(CHAR_UUID_TX, notification_handler)

       while client.is_connected:
           await asyncio.sleep(1)

       print(f"{tag} Disconnected.")

async def device_loop(index):
   # Reconnect indefinitely, e.g. after the board reboots or goes out of range.
   while True:
       try:
           await run_once(index)
       except Exception as e:
           print(f"[{index}:{DEVICE_NAMES[index]}] Error: {e}")
       # Report this board's sensors dark until it reconnects and re-syncs
       for k in range(SENSORS_PER_BOARD):
           sensor_state[index * SENSORS_PER_BOARD + k] = None
       changed.set()
       print(f"[{index}:{DEVICE_NAMES[index]}] Retrying in 3s...")
       await asyncio.sleep(3)

async def main():
   print(f"Connecting to {DC_HOST}")
   try:
      value = await asyncio.to_thread(wall.request, 'GET', '/sensors')
   except RuntimeError as e:
      raise SystemExit(f"{e}\nThe wall's DisplayCluster needs sensor support (GET/PUT /sensors).")
   print(f"Connected; wall sensor value is {value['value']:0{NUM_SENSORS}b}")
   # start from all dark, until the boards report in
   changed.set()
   await asyncio.gather(sender(), *(device_loop(i) for i in range(len(DEVICE_NAMES))))

if __name__ == "__main__":
   try:
       asyncio.run(main())
   except KeyboardInterrupt:
       pass
