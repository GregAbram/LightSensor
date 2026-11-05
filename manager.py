"""Drives the wall from the light sensors.

Connects to every board over BLE and keeps the latest state of all 6 sensors.
Each sensor's bit in the value sent to DisplayCluster (bit N * 2 + k = board N's
sensor k, with PUT /sensors) toggles each time that sensor goes from dark to lit,
and stays put in between. DisplayCluster shows and hides the windows itself, from
each window's sensorMask and sensorPattern (see make-demo-layout.py), so there's
no config.json and one request per change, however many windows.

Needs a DisplayCluster with sensorPattern (GET/PUT /sensors). Setting the
sensors needs no control of the wall, so this runs alongside the web UI.
It starts from the bits the wall has, so a restart leaves the images as they are.
A board that disconnects keeps its bits as they are, and counts as dark, so a
sensor that's lit when its board reconnects counts as going from dark to lit.

Given a logic file, that file turns the sensors into the wall's value instead
(make-sensor-layout.py writes one to go with its state file). It provides
SENSOR_MASK, the bits it owns; start(value), which takes up the wall's value at
startup and returns its own; and logic(sensor_value), as logic() here.

It talks to DisplayCluster's API on localhost:1910 by default, over plain HTTP
with no token: a wall with no token accepts that from its own machine, or
through an SSH tunnel to it (ssh -N -L 1910:localhost:1910 wallhost).
--cert and --https switch to HTTPS, and --token gives a file holding the
wall's token. For rattler-dev:
   python manager.py --host rattler-dev.tacc.utexas.edu \
      --cert ~/.displaycluster/rattler-dev_cert.pem \
      --token ~/.displaycluster/rattler-dev_token

Usage: python manager.py [logic.py] [--host H] [--port P] [--cert FILE | --https] [--token FILE]
"""
import argparse

import asyncio
import http.client
import importlib.util
import json
import os
import ssl
import sys
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
# the bits the built-in logic owns; any others on the wall are left alone
SENSOR_MASK = (1 << NUM_SENSORS) - 1

# What logic() remembers between calls: the sensors' lit bits last time, and the
# toggled bits last sent to the wall (set from the wall's own value at startup)
PREV_SENSORS = 0
PREV_MASK = 0

parser = argparse.ArgumentParser(description='Drives the wall from the light sensors.')
parser.add_argument('logic', nargs='?', help='a logic file, e.g. one from make-sensor-layout.py')
parser.add_argument('--host', default='localhost', help="the wall's DisplayCluster host (default localhost)")
parser.add_argument('--port', type=int, default=1910, help='its API port (default 1910)')
parser.add_argument('--cert', help="HTTPS, trusting this certificate (a self-signed wall's, copied from it); "
                    'connect by the name the certificate is for')
parser.add_argument('--https', action='store_true', help="HTTPS, trusting the system's certificate authorities")
parser.add_argument('--token', help="a file holding the wall's API token (default: none)")
ARGS = parser.parse_args()

DC_HOST = ARGS.host
DC_PORT = ARGS.port
DC_TOKEN = None
if ARGS.token:
   with open(os.path.expanduser(ARGS.token)) as f:
      DC_TOKEN = f.read().strip()

class Wall:
   """PUT /sensors over one kept-open HTTPS connection: after the first
   request, each one costs about a network round trip instead of a new TLS
   handshake. Blocking, so it's called from a worker thread."""

   def __init__(self):
      self.context = None
      if ARGS.cert or ARGS.https:
         self.context = ssl.create_default_context(cafile=os.path.expanduser(ARGS.cert) if ARGS.cert else None)
      self.conn = None

   def request(self, method, path, body=None):
      headers = {'Authorization': 'Bearer ' + DC_TOKEN} if DC_TOKEN else {}
      data = None
      if body is not None:
         data = json.dumps(body)
         headers['Content-Type'] = 'application/json'
      # one retry on a fresh connection, for a kept-open one the server closed
      for attempt in (1, 2):
         if self.conn is None:
            if self.context:
               self.conn = http.client.HTTPSConnection(DC_HOST, DC_PORT, context=self.context, timeout=5)
            else:
               self.conn = http.client.HTTPConnection(DC_HOST, DC_PORT, timeout=5)
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
      return self.request('PUT', '/sensors', {'value': value, 'mask': LOGIC.SENSOR_MASK})['value']

wall = Wall()

# Latest reported lit state per sensor (None = board not connected)
sensor_state = [None] * NUM_SENSORS

# set when sensor_state changes; the sender sends the latest value
changed = asyncio.Event()

# BLE scans running at the same time can conflict (especially on Windows),
# so only one board scans at a time.
scan_lock = asyncio.Lock()

def sensor_value():
   """sensor_state as one bit per sensor, set if lit; unknown sensors count as
   dark. logic() turns this into the wall's sensor value."""
   return sum(1 << i for i, lit in enumerate(sensor_state) if lit)

def logic(sensorValue):
    """Returns the value for the wall, given the sensors' lit bits: each bit
    flips when its sensor goes from dark to lit, and otherwise stays as it was,
    so a window's image stays up after the flashlight has gone. Calling it again
    with the same bits changes nothing, so a failed send can simply retry."""
    global PREV_SENSORS
    global PREV_MASK

    # bits that changed and are now set: the sensors that just went dark to lit
    rising = (PREV_SENSORS ^ sensorValue) & sensorValue & SENSOR_MASK
    # flip those bits and leave the rest
    next_mask = PREV_MASK ^ rising
    PREV_SENSORS = sensorValue
    PREV_MASK = next_mask
    return next_mask

def start(value):
    """Carries on from the bits the wall has, so a restart doesn't change the images."""
    global PREV_MASK
    PREV_MASK = value & SENSOR_MASK
    return PREV_MASK

def load_logic(path):
   """The logic file at path as a module, or this file's own logic without one."""
   if path is None:
      return sys.modules[__name__]
   spec = importlib.util.spec_from_file_location('wall_logic', path)
   module = importlib.util.module_from_spec(spec)
   spec.loader.exec_module(module)
   return module

LOGIC = load_logic(ARGS.logic)
VALUE_BITS = LOGIC.SENSOR_MASK.bit_length()


async def sender(sent):
   """Sends the toggled value whenever it changes, one request at a time. Changes
   that arrive while a request is in flight are folded into the next one, so a
   burst never queues up behind a slow network; a sensor that goes lit and dark
   again in that time doesn't toggle. sent is the value the wall has now."""
   while True:
      await changed.wait()
      changed.clear()
      mask = LOGIC.logic(sensor_value())
      if mask == sent:
         continue
      begin = time.perf_counter()
      try:
         await asyncio.to_thread(wall.set_sensors, mask)
         sent = mask
         print(f"wall {mask:0{VALUE_BITS}b} ({(time.perf_counter() - begin) * 1000:.0f} ms)")
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
   scheme = 'https' if wall.context else 'http'
   print(f"Connecting to {scheme}://{DC_HOST}:{DC_PORT}")
   try:
      value = await asyncio.to_thread(wall.request, 'GET', '/sensors')
   except RuntimeError as e:
      raise SystemExit(f"{e}\nThe wall's DisplayCluster needs sensor support (GET/PUT /sensors), "
                       "and a token if it has one (--token).")
   except (http.client.HTTPException, OSError) as e:
      raise SystemExit(f"Can't reach {scheme}://{DC_HOST}:{DC_PORT}: {e}")
   print(f"Connected; wall sensor value is {value['value']:0{VALUE_BITS}b}")
   # carry on from the bits the wall has, so a restart doesn't change the images;
   # if the logic changes them (bits outside its range), the sender sends its own
   sent = value['value'] & LOGIC.SENSOR_MASK
   if LOGIC.start(value['value']) != sent:
      changed.set()
   await asyncio.gather(sender(sent), *(device_loop(i) for i in range(len(DEVICE_NAMES))))

if __name__ == "__main__":
   try:
       asyncio.run(main())
   except KeyboardInterrupt:
       pass
