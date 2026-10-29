"""Tests DisplayCluster's sensor support on rattler-dev, through the REST API.
No boards or BLE needed.

Loads lights.dcx (3 boards x 2 sensors, a red and a green window per cell),
then drives the wall's sensor value and checks every window follows it. It
also checks partial updates, hiding by hand, taking a window out of sensor
control, saving/reloading state, and times PUT /sensors.

The wall visibly changes while it runs, and it ends with lights.dcx loaded
and all sensors dark. It takes control of the wall and gives it back at exit.

Usage: python sensors-test.py
"""
import os
import time
from DC import DC

DC_HOST = 'rattler-dev.tacc.utexas.edu'
DC_CAFILE = os.path.expanduser('~/.displaycluster/rattler-dev_cert.pem')
with open(os.path.expanduser('~/.displaycluster/rattler-dev_token')) as f:
   DC_TOKEN = f.read().strip()

NUM_BOARDS = 3
SENSORS_PER_BOARD = 2
NUM_SENSORS = NUM_BOARDS * SENSORS_PER_BOARD
TEST_STATE = 'sensors-test.dcx'

failures = 0

def check(ok, what):
   global failures
   print(('  ok   ' if ok else '  FAIL ') + what)
   if not ok:
      failures += 1

def cell(bit):
   return f'b{bit // SENSORS_PER_BOARD}s{bit % SENSORS_PER_BOARD}'

def check_follows(dc, value, what):
   """Every cell shows green if its sensor bit is set in value, red if not."""
   dc.updateContent()
   wrong = []
   for bit in range(NUM_SENSORS):
      lit = bool((value >> bit) & 1)
      red, green = dc.content.get(cell(bit) + '-red'), dc.content.get(cell(bit) + '-green')
      if red is None or green is None:
         wrong.append(f'{cell(bit)} missing')
      elif red['hidden'] != lit or green['hidden'] == lit:
         wrong.append(f"{cell(bit)} red {'hidden' if red['hidden'] else 'shown'}, green {'hidden' if green['hidden'] else 'shown'}")
   check(not wrong, f'{what}: value {value:06b} (sensor 0 last)' + (' - ' + '; '.join(wrong) if wrong else ''))

dc = DC(DC_HOST, cafile=DC_CAFILE, token=DC_TOKEN, strict=True, label='sensors-test.py')

print('Server')
config = dc._request('GET', '/config')
print(f"  version {config.get('version')}")
check('sensorCount' in config, f"/config has sensorCount ({config.get('sensorCount')})")
check(dc.getSensors() is not None, 'GET /sensors works')

print('Layout')
dc.loadState('lights.dcx')
check(len(dc.content) == 2 * NUM_SENSORS, f'lights.dcx loaded {len(dc.content)} windows')
masks = {n: (p['sensorMask'], p['showWhenLit']) for n, p in dc.content.items()}
check(masks.get('b1s0-red') == (1 << 2, False) and masks.get('b1s0-green') == (1 << 2, True),
      f"b1s0 masks from the file: red {masks.get('b1s0-red')}, green {masks.get('b1s0-green')}")

print('Following the sensors')
for value in (0, 0b000001, 0b000101, 0b111111, 0b101010, 0):
   dc.setSensors(value)
   check_follows(dc, value, 'set')

print('Partial updates (mask)')
dc.setSensors(0b110000)
got = dc.setSensors(0b000011, mask=0b000011)
check(got == 0b110011, f'mask 000011 kept the other bits: {got:06b}')
check_follows(dc, got, 'after mask')
got = dc.setSensors(0, mask=0b010000)
check(got == 0b100011, f'clearing one bit: {got:06b}')
check_follows(dc, got, 'after clearing')

print('Hiding by hand lasts until the sensors change')
dc.setSensors(0)
dc.hide('b0s0-red')
check(dc.content['b0s0-red']['hidden'], 'b0s0-red hidden by hand')
dc.setSensors(0b000010)   # a different sensor changes
check_follows(dc, 0b000010, 'next sensor change puts it back')

print('Taking a window out of sensor control')
dc.setSensorControl('b2s1-green', 0)
dc.updateContent()
before = dc.content['b2s1-green']['hidden']
dc.setSensors(0b100000)
dc.updateContent()
check(dc.content['b2s1-green']['hidden'] == before, 'mask 0: b2s1-green ignores sensor 5')
check(dc.content['b2s1-red']['hidden'], 'b2s1-red still follows (hidden while lit)')
dc.setSensorControl('b2s1-green', 1 << 5, True)
check_follows(dc, 0b100000, 'mask restored: follows again straight away')

print('Saving and reloading state')
dc.setSensors(0b001001)
dc.saveState(TEST_STATE)
dc.clear()
check(len(dc.content) == 0, 'cleared the wall')
dc.setSensors(0b000110)
dc.loadState(TEST_STATE)
masks2 = {n: (p['sensorMask'], p['showWhenLit']) for n, p in dc.content.items()}
check(masks2 == masks, 'masks survived save/load')
check_follows(dc, 0b000110, 'reloaded windows follow the current value, not the saved one')

print('Speed of PUT /sensors (one request per change)')
n = 20
start = time.perf_counter()
for i in range(n):
   dc.setSensors(i % 2)
elapsed = (time.perf_counter() - start) / n * 1000
print(f'  {elapsed:.0f} ms per change (DC.py opens a new connection each time)')

print('Cleaning up')
dc.setSensors(0)
dc.loadState('lights.dcx')
check_follows(dc, 0, 'lights.dcx back, all dark')
dc.disconnect()

print()
print('All passed' if failures == 0 else f'{failures} FAILED')
print(f'(left {TEST_STATE} in the state folder on the wall)')
