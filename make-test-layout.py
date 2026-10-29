"""Writes a red/green test layout as a DisplayCluster state file plus the
matching config.json, so the window names in both always agree.

The wall is a grid with one column per board and one row per sensor. Each
cell gets a red and a green window stacked in the same spot, named
b<board>s<sensor>-red and b<board>s<sensor>-green. Green shows when that
sensor is lit, red when it's dark. Everything starts dark (red showing).

Board b's sensor s is config.json column b * SENSORS_PER_BOARD + s, the same
numbering manager.py uses. It's also bit b * SENSORS_PER_BOARD + s of the
wall's sensor value: each window's <sensorMask> and <showWhenLit> let a
DisplayCluster with sensor support switch them itself (older versions ignore
them and use config.json via manager.py).

Usage: python make-test-layout.py [state-file]   (default lights.dcx)
The images are red.png and green.png in CONTENT_DIR - /home/gda/Content, as on
rattler-dev, unless the environment sets CONTENT_DIR (e.g. to this folder's
content/ for DisplayCluster running on the Mac).
Then copy the .dcx to ~/.displaycluster/states on the wall host and load it,
e.g. dc.loadState('lights.dcx').
"""
import json
import os
import sys
from xml.sax.saxutils import escape

NUM_BOARDS = 3
SENSORS_PER_BOARD = 2
CONTENT_DIR = os.environ.get('CONTENT_DIR', '/home/gda/Content')
RED = CONTENT_DIR + '/red.png'
GREEN = CONTENT_DIR + '/green.png'

# Space between cells, as fractions of the wall's width and height
GAP_X = 0.01
GAP_Y = 0.02

state_file = sys.argv[1] if len(sys.argv) > 1 else 'lights.dcx'

def content_window(uri, name, x, y, w, h, hidden, sensor_mask, show_when_lit):
   return f""" <ContentWindow>
  <URI>{escape(uri)}</URI>
  <name>{escape(name)}</name>
  <x>{x:.6f}</x>
  <y>{y:.6f}</y>
  <w>{w:.6f}</w>
  <h>{h:.6f}</h>
  <centerX>0.5</centerX>
  <centerY>0.5</centerY>
  <zoom>1</zoom>
  <selected>0</selected>
  <hidden>{1 if hidden else 0}</hidden>
  <sensorMask>{sensor_mask}</sensorMask>
  <showWhenLit>{1 if show_when_lit else 0}</showWhenLit>
 </ContentWindow>
"""

windows = []
config = []
cell_w = 1.0 / NUM_BOARDS
cell_h = 1.0 / SENSORS_PER_BOARD
for b in range(NUM_BOARDS):
   for s in range(SENSORS_PER_BOARD):
      x = b * cell_w + GAP_X / 2
      y = s * cell_h + GAP_Y / 2
      w = cell_w - GAP_X
      h = cell_h - GAP_Y
      red, green = f'b{b}s{s}-red', f'b{b}s{s}-green'
      bit = 1 << (b * SENSORS_PER_BOARD + s)
      windows.append(content_window(RED, red, x, y, w, h, hidden=False, sensor_mask=bit, show_when_lit=False))
      windows.append(content_window(GREEN, green, x, y, w, h, hidden=True, sensor_mask=bit, show_when_lit=True))
      # manager.py shows an entry when (flag == 1) == lit
      config.append([[red, 0], [green, 1]])

with open(state_file, 'w') as f:
   f.write('<!DOCTYPE state>\n<state>\n <version>1</version>\n')
   f.writelines(windows)
   f.write('</state>\n')

with open('config.json', 'w') as f:
   json.dump(config, f, indent=4)

print(f'wrote {state_file} ({len(windows)} windows) and config.json ({len(config)} columns)')
