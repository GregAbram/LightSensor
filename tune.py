#!/Users/gda/Documents/LightSensors/venv/bin/python
"""Sets or reads a board's light threshold and gap over BLE.

Usage: python tune.py <board-index-or-name> [T<n>] [G<n>]
   python tune.py 1              report board 1's current threshold and gap
   python tune.py 1 T2000 G300   set them (saved in the board's flash)

The board replies to each command with its settings, e.g. '#T2000 G300'.
Stop manager.py/Test1/Test3 first: a board only accepts one connection.
"""
import asyncio
import sys
from bleak import BleakClient, BleakScanner

CHAR_UUID_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notifications from ESP32
CHAR_UUID_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to ESP32

if len(sys.argv) < 2:
   raise SystemExit(__doc__)

arg = sys.argv[1]
DEVICE_NAME = f"XIAO-ESP32C3-{arg}" if arg.isdigit() else arg
commands = [c.upper() for c in sys.argv[2:]] or ["?"]

for c in commands:
   if c != "?" and not (c[0] in "TG" and c[1:].isdigit() and 0 <= int(c[1:]) <= 4095):
      raise SystemExit(f"Bad command {c!r}: use T<n> or G<n> with n from 0 to 4095")

async def main():
   print(f"Scanning for {DEVICE_NAME}...")
   device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
   if device is None:
      raise SystemExit("Could not find device (is something else connected to it?)")

   async with BleakClient(device) as client:
      replies = asyncio.Queue()

      def notification_handler(sender, data):
         text = data.decode("utf-8", errors="replace").strip()
         if text.startswith("#"):
            replies.put_nowait(text[1:])
         else:
            # A state message: ack it so the board doesn't keep resending
            print(f"state {text}")
            asyncio.create_task(client.write_gatt_char(CHAR_UUID_RX, b"OK", response=False))

      await client.start_notify(CHAR_UUID_TX, notification_handler)

      for c in commands:
         await client.write_gatt_char(CHAR_UUID_RX, c.encode(), response=True)
         try:
            reply = await asyncio.wait_for(replies.get(), timeout=3.0)
            print(f"{c}: {reply}")
         except asyncio.TimeoutError:
            print(f"{c}: no reply")

if __name__ == "__main__":
   asyncio.run(main())
