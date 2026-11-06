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

Given a logic file (--logic), that file turns the sensors into the wall's value instead
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

While it runs, commands typed into its window set the boards' thresholds and
gaps over the connections it has open: '1 T2400', '1 G300', 'all G300', or '?'
to show them all.

--state FILE loads a state file from the wall's state directory first (e.g.
--state show.dcx), taking control of the wall just for that.

Usage: python manager.py [--logic FILE] [--state FILE] [--host H] [--port P] [--cert FILE | --https] [--token FILE]
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
parser.add_argument('--logic', help="a logic file, e.g. one from make-sensor-layout.py (default: toggle each sensor's bit)")
# the logic file used to be the one positional argument; it still works that way
parser.add_argument('logic_file', nargs='?', help=argparse.SUPPRESS)
parser.add_argument('--state', help="a state file to load first, relative to the wall's state directory")
parser.add_argument('--host', default='localhost', help="the wall's DisplayCluster host (default localhost)")
parser.add_argument('--port', type=int, default=1910, help='its API port (default 1910)')
parser.add_argument('--cert', help="HTTPS, trusting this certificate (a self-signed wall's, copied from it); "
                    'connect by the name the certificate is for')
parser.add_argument('--https', action='store_true', help="HTTPS, trusting the system's certificate authorities")
parser.add_argument('--token', help="a file holding the wall's API token (default: none)")
ARGS = parser.parse_args()
if ARGS.logic and ARGS.logic_file:
   parser.error('give the logic file once, with --logic')
ARGS.logic = ARGS.logic or ARGS.logic_file

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

   def request(self, method, path, body=None, lease=None, timeout=5):
      headers = {'Authorization': 'Bearer ' + DC_TOKEN} if DC_TOKEN else {}
      if lease:
         headers['X-DC-Lease'] = lease
      data = None
      if body is not None:
         data = json.dumps(body)
         headers['Content-Type'] = 'application/json'
      # one retry on a fresh connection, for a kept-open one the server closed
      for attempt in (1, 2):
         if self.conn is None:
            if self.context:
               self.conn = http.client.HTTPSConnection(DC_HOST, DC_PORT, context=self.context)
            else:
               self.conn = http.client.HTTPConnection(DC_HOST, DC_PORT)
         self.conn.timeout = timeout
         if self.conn.sock:
            self.conn.sock.settimeout(timeout)
         try:
            self.conn.request(method, path, body=data, headers=headers)
            response = self.conn.getresponse()
            reply = json.loads(response.read() or b'null')
            if response.status >= 400:
               message = reply.get('error') if isinstance(reply, dict) else None
               raise RuntimeError(f"{method} {path}: {message or response.reason} (HTTP {response.status})")
            return reply
         except (http.client.HTTPException, OSError) as e:
            self.conn.close()
            self.conn = None
            # a timeout means the wall got the request and is slow, so sending
            # it again would only do it twice
            if attempt == 2 or isinstance(e, TimeoutError):
               raise

   def load_state(self, file):
      """Takes control of the wall, loads a state file and gives control back.
      Fails if someone else (the web UI, say) has control. The wall answers
      once it has opened every file, which for big images can take a while."""
      lease = self.request('POST', '/control', {'label': 'manager.py'})['lease']
      try:
         self.request('POST', '/state/load', {'file': file}, lease=lease, timeout=LOAD_TIMEOUT)
      finally:
         self.request('DELETE', '/control', lease=lease)

   def set_sensors(self, value):
      return self.request('PUT', '/sensors', {'value': value, 'mask': LOGIC.SENSOR_MASK})['value']

# seconds to wait for the wall to load a state file
LOAD_TIMEOUT = 300

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

# Each connected board's BLE client, so typed commands can reach it
clients = [None] * len(DEVICE_NAMES)

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
       clients[index] = client
       try:
           while client.is_connected:
               await asyncio.sleep(1)
       finally:
           clients[index] = None

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

COMMAND_HELP = """Commands (the board saves T and G in flash, as with tune.py):
   <board> T<n>    set a board's threshold, e.g. 1 T2400
   <board> G<n>    set its gap, e.g. 1 G300
   <board> ?       show its threshold and gap
   all ...         the same for every connected board, e.g. all G300
   ?               show every connected board's settings"""

def parse_command(line):
   """A typed line as (boards, commands), or a message saying what's wrong."""
   words = line.upper().split()
   if not words:
      return None
   if words in (['?'], ['HELP']):
      return list(range(len(DEVICE_NAMES))), ['?']
   target, commands = words[0], words[1:] or ['?']
   if target == 'ALL':
      boards = list(range(len(DEVICE_NAMES)))
   elif target.isdigit() and int(target) < len(DEVICE_NAMES):
      boards = [int(target)]
   else:
      return f"no board {words[0]!r}\n{COMMAND_HELP}"
   for c in commands:
      if c != '?' and not (c[0] in 'TG' and c[1:].isdigit() and int(c[1:]) <= 4095):
         return f"bad command {c!r}: use T<n> or G<n> with n from 0 to 4095, or ?"
   return boards, commands

async def command_loop():
   """Takes tuning commands typed into this window and sends them to the boards
   over the connections already open; each board's reply prints as 'settings'."""
   if not sys.stdin.isatty():
      return
   lines = asyncio.Queue()
   asyncio.get_running_loop().add_reader(sys.stdin, lambda: lines.put_nowait(sys.stdin.readline()))
   print("Type T/G commands to tune the boards, e.g. '1 T2400' or 'all G300'; '?' for help")
   while True:
      line = await lines.get()
      if line == '':
         return   # end of input
      parsed = parse_command(line)
      if parsed is None:
         continue
      if isinstance(parsed, str):
         print(parsed)
         continue
      boards, commands = parsed
      if len(boards) > 1 and not any(clients):
         print("no boards connected")
      for b in boards:
         client = clients[b]
         if client is None:
            if len(boards) == 1:
               print(f"[{b}:{DEVICE_NAMES[b]}] not connected")
            continue
         for c in commands:
            try:
               await client.write_gatt_char(CHAR_UUID_RX, c.encode(), response=True)
            except Exception as e:
               print(f"[{b}:{DEVICE_NAMES[b]}] couldn't send {c}: {e}")
               break
      if line.strip().upper() in ('?', 'HELP'):
         print(COMMAND_HELP)

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
   if ARGS.state:
      print(f"Loading {ARGS.state}...")
      start = time.perf_counter()
      try:
         await asyncio.to_thread(wall.load_state, ARGS.state)
      except TimeoutError:
         raise SystemExit(f"Couldn't load {ARGS.state}: the wall didn't finish within {LOAD_TIMEOUT} s")
      except (RuntimeError, http.client.HTTPException, OSError) as e:
         raise SystemExit(f"Couldn't load {ARGS.state}: {e}")
      print(f"Loaded {ARGS.state} ({time.perf_counter() - start:.1f} s)")
   print(f"Connected; wall sensor value is {value['value']:0{VALUE_BITS}b}")
   # carry on from the bits the wall has, so a restart doesn't change the images;
   # if the logic changes them (bits outside its range), the sender sends its own
   sent = value['value'] & LOGIC.SENSOR_MASK
   if LOGIC.start(value['value']) != sent:
      changed.set()
   await asyncio.gather(sender(sent), command_loop(), *(device_loop(i) for i in range(len(DEVICE_NAMES))))

if __name__ == "__main__":
   try:
       asyncio.run(main())
   except KeyboardInterrupt:
       pass
