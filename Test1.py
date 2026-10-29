"""Bench tester for a single board with two light sensors. No DC, no config.json.

Connects to one board over BLE, prints each sensor change and acks every
message the way manager.py does.

Usage: python Test1.py [board-index-or-name]   (default: XIAO-ESP32C3-0)
"""
import asyncio
import sys
from bleak import BleakClient, BleakScanner

CHAR_UUID_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notifications from ESP32
CHAR_UUID_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to ESP32

SENSORS_PER_BOARD = 2

arg = sys.argv[1] if len(sys.argv) > 1 else "0"
DEVICE_NAME = f"XIAO-ESP32C3-{arg}" if arg.isdigit() else arg

# Last known lit state per sensor (None = not yet received)
sensor_state = [None] * SENSORS_PER_BOARD

def parse_state(data):
   """The board reports all its sensors as e.g. '10': '1' = lit, '0' = unlit.
   Returns a list of bools, or None if the message is malformed."""
   text = data.decode("utf-8", errors="replace").strip()
   if len(text) != SENSORS_PER_BOARD or any(c not in "01" for c in text):
       return None
   return [c == '1' for c in text]

async def run_once():
   print(f"Scanning for {DEVICE_NAME}...")
   device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
   if device is None:
       print("Could not find device")
       return

   print(f"Found: {device.name} [{device.address}]")

   async with BleakClient(device) as client:
       print("Connected.")

       def notification_handler(sender, data):
           if data.startswith(b'#'):
               # Reply to a settings command (see tune.py), e.g. '#T2000 G300'
               print(f"settings {data[1:].decode(errors='replace').strip()}")
               return
           states = parse_state(data)
           if states is None:
               # No ack, so the board resends after its timeout
               print(f"Ignoring malformed message {data!r}")
               return
           print(f"Received {data!r}")
           for k, lit in enumerate(states):
               if lit != sensor_state[k]:
                   print(f"   sensor {k}: {'LIT' if lit else 'dark'} (was {sensor_state[k]!r})")
                   sensor_state[k] = lit
           asyncio.create_task(client.write_gatt_char(CHAR_UUID_RX, b"OK", response=False))

       await client.start_notify(CHAR_UUID_TX, notification_handler)

       while client.is_connected:
           await asyncio.sleep(1)

       print("Disconnected.")

async def main():
   # Reconnect indefinitely, e.g. after the board reboots or goes out of range.
   while True:
       try:
           await run_once()
       except Exception as e:
           print(f"Error: {e}")
       for k in range(SENSORS_PER_BOARD):
           sensor_state[k] = None
       print("Retrying in 3s...")
       await asyncio.sleep(3)

if __name__ == "__main__":
   try:
       asyncio.run(main())
   except KeyboardInterrupt:
       pass
