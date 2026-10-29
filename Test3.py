"""Bench tester for all 3 boards / 6 sensors at once. No DC, no config.json.

Connects to every board over BLE, acks each message the way manager.py does,
and prints the full 6-sensor picture whenever any sensor changes.
Columns are numbered the same way manager.py maps them to config.json:
board N's sensor k is column N * 2 + k.

Usage: python Test3.py
"""
import asyncio
from bleak import BleakClient, BleakScanner

CHAR_UUID_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notifications from ESP32
CHAR_UUID_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to ESP32

DEVICE_NAMES = [
   "XIAO-ESP32C3-0",
   "XIAO-ESP32C3-1",
   "XIAO-ESP32C3-2",
]
SENSORS_PER_BOARD = 2
NUM_COLUMNS = len(DEVICE_NAMES) * SENSORS_PER_BOARD

# Last known lit state per column (None = not yet received / board offline)
column_state = [None] * NUM_COLUMNS

# BLE scans running at the same time can conflict (especially on Windows),
# so only one board scans at a time.
scan_lock = asyncio.Lock()

def parse_state(data):
   """Each board reports all its sensors as e.g. '10': '1' = lit, '0' = unlit.
   Returns a list of bools, or None if the message is malformed."""
   text = data.decode("utf-8", errors="replace").strip()
   if len(text) != SENSORS_PER_BOARD or any(c not in "01" for c in text):
       return None
   return [c == '1' for c in text]

def show_all():
   """One line per change: '#' = lit, '.' = dark, '-' = unknown, grouped by board."""
   def mark(s):
       return '-' if s is None else ('#' if s else '.')
   groups = []
   for b in range(len(DEVICE_NAMES)):
       cols = column_state[b * SENSORS_PER_BOARD:(b + 1) * SENSORS_PER_BOARD]
       groups.append(f"B{b}[{''.join(mark(s) for s in cols)}]")
   lit = [i for i, s in enumerate(column_state) if s]
   print("   ", "  ".join(groups), "  lit columns:", lit if lit else "none")

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
           changed = False
           for k, lit in enumerate(states):
               col = index * SENSORS_PER_BOARD + k
               if lit != column_state[col]:
                   print(f"{tag} sensor {k} (column {col}) {'LIT' if lit else 'dark'} (was {column_state[col]!r})")
                   column_state[col] = lit
                   changed = True
           if changed:
               show_all()
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
       # Forget the old state so this board's columns show as unknown until it reconnects
       for k in range(SENSORS_PER_BOARD):
           column_state[index * SENSORS_PER_BOARD + k] = None
       show_all()
       print(f"[{index}:{DEVICE_NAMES[index]}] Retrying in 3s...")
       await asyncio.sleep(3)

async def main():
   await asyncio.gather(*(device_loop(i) for i in range(len(DEVICE_NAMES))))

if __name__ == "__main__":
   try:
       asyncio.run(main())
   except KeyboardInterrupt:
       pass
