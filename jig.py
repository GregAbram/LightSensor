"""Test jig for all 3 boards / 6 sensors. No DC, no config.json.

Connects to every board over BLE and keeps the latest state each board has
reported (acking each message the way manager.py does). Every PERIOD seconds
it samples all 6 sensors and prints them as true/false, but only when the
sample differs from the last one printed. A sensor whose board isn't
connected shows as None.

Sensors are listed in config.json column order: board N's sensor k is
column N * 2 + k, so the order is b0s0 b0s1 b1s0 b1s1 b2s0 b2s1.

Usage: python jig.py [period-seconds]   (default 0.1)
"""
import asyncio
import sys
import time
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

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.1

# Latest reported lit state per column (None = board not connected yet)
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

async def run_once(index):
   name = DEVICE_NAMES[index]
   tag = f"[{index}:{name}]"

   async with scan_lock:
       print(f"{tag} Scanning...")
       device = await BleakScanner.find_device_by_name(name, timeout=10.0)
   if device is None:
       print(f"{tag} Could not find device")
       return

   async with BleakClient(device) as client:
       print(f"{tag} Connected.")

       def notification_handler(sender, data):
           if data.startswith(b'#'):
               # Reply to a settings command (see tune.py)
               return
           states = parse_state(data)
           if states is None:
               # No ack, so the board resends after its timeout
               print(f"{tag} ignoring malformed message {data!r}")
               return
           for k, lit in enumerate(states):
               column_state[index * SENSORS_PER_BOARD + k] = lit
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
       for k in range(SENSORS_PER_BOARD):
           column_state[index * SENSORS_PER_BOARD + k] = None
       await asyncio.sleep(3)

async def sampler():
   last = None
   while True:
       sample = list(column_state)
       if sample != last:
           stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
           print(stamp, " ".join(str(s).lower() for s in sample))
           last = sample
       await asyncio.sleep(PERIOD)

async def main():
   print(f"Sampling every {PERIOD}s; columns b0s0 b0s1 b1s0 b1s1 b2s0 b2s1")
   await asyncio.gather(sampler(), *(device_loop(i) for i in range(len(DEVICE_NAMES))))

if __name__ == "__main__":
   try:
       asyncio.run(main())
   except KeyboardInterrupt:
       pass
