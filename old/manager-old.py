import asyncio
import json
import os
import sys

# Read one keypress without waiting for Enter: msvcrt on Windows, termios elsewhere
try:
   import msvcrt

   def getch():
      return msvcrt.getch()
except ImportError:
   import termios
   import tty

   def getch():
      fd = sys.stdin.fileno()
      old = termios.tcgetattr(fd)
      try:
         tty.setraw(fd)
         return sys.stdin.buffer.read(1)
      finally:
         termios.tcsetattr(fd, termios.TCSADRAIN, old)
from bleak import BleakClient, BleakScanner
from DC import DC

CHAR_UUID_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notifications from ESP32
CHAR_UUID_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to ESP32

# Each board has SENSORS_PER_BOARD light sensors, one per config.json column.
# Board N's sensor k controls config[N * SENSORS_PER_BOARD + k], so
# DEVICE_NAMES[0] controls columns 0-1, DEVICE_NAMES[1] columns 2-3, and so on.
# Each board's firmware must advertise its own unique name (set BOARD_INDEX).
DEVICE_NAMES = [
   "XIAO-ESP32C3-0",
   "XIAO-ESP32C3-1",
   "XIAO-ESP32C3-2",
]
SENSORS_PER_BOARD = 2
NUM_COLUMNS = len(DEVICE_NAMES) * SENSORS_PER_BOARD

# The wall's DisplayCluster serves HTTPS with a certificate for this name only,
# so connect by this name and trust that certificate (copied from the wall host).
DC_HOST = 'rattler-dev.tacc.utexas.edu'
DC_CAFILE = os.path.expanduser('~/.displaycluster/rattler-dev_cert.pem')
# rattler-dev's own token, kept apart from the default ~/.displaycluster/api_token
with open(os.path.expanduser('~/.displaycluster/rattler-dev_token')) as f:
   DC_TOKEN = f.read().strip()

testing = False
userOK = False

with open('config.json') as f:
   config = json.load(f)

if len(config) < NUM_COLUMNS:
   raise SystemExit(
       f"config.json has {len(config)} columns but {NUM_COLUMNS} sensors are expected"
   )

if not testing:
   print(f"Connecting to {DC_HOST}")
   dc = DC(DC_HOST, cafile=DC_CAFILE, token=DC_TOKEN)
   print("Connected")

# Last known lit state per column (None = not yet received)
column_state = [None] * NUM_COLUMNS

# BLE scans running at the same time can conflict (especially on Windows),
# so only one board scans at a time.
scan_lock = asyncio.Lock()

def step(fn, *args):
   desc = f"DC.{fn.__name__}({', '.join(repr(a) for a in args)})"
   print(f"About to run: {desc}")
   if userOK:
       print("Press any key to send it, or 'q' to quit...", end=' ', flush=True)
       key = getch()
       print()
       if key in (b'q', b'Q'):
           print("Quitting.")
           os._exit(0)
   return fn(*args)


def update_column(i, lit):
   """Apply the lit/unlit state for a single column."""
   print("================ column", i, 'lit', lit)
   for j in config[i]:
       show = (j[1] == 1) == lit
       if not testing:
           step(dc.reveal if show else dc.hide, j[0])
   if not testing:
       step(dc.updateContent)

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
               col = index * SENSORS_PER_BOARD + k
               if lit != column_state[col]:
                   print(f"{tag} sensor {k} (column {col}) {lit!r} (was {column_state[col]!r})")
                   update_column(col, lit)
                   column_state[col] = lit
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
       # Forget the old state so this board's columns re-sync on reconnect
       for k in range(SENSORS_PER_BOARD):
           column_state[index * SENSORS_PER_BOARD + k] = None
       print(f"[{index}:{DEVICE_NAMES[index]}] Retrying in 3s...")
       await asyncio.sleep(3)

async def main():
   await asyncio.gather(*(device_loop(i) for i in range(len(DEVICE_NAMES))))

if __name__ == "__main__":
   try:
       asyncio.run(main())
   except KeyboardInterrupt:
       pass
