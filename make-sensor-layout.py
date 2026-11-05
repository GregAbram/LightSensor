"""Writes a DisplayCluster state file that splits the wall into three columns,
each showing one file at a time from its own directory, and the logic file
manager.py uses to step through them with the light sensors.

Content goes in three directories, left, center and right, inside one content
directory. Each file is fitted to its third of the wall (full height, a third of
the width), keeping its aspect ratio, and centered in it. Every file in a column
is stacked in the same place, under sensor control:

   k bits enumerate the largest column (at least 1), so for n files the
   patterns are 0 .. n-1, and
   column   field of the sensor value    mask                  board
   left     bits 2k .. 3k-1              (2^k - 1) << 2k       0
   center   bits k .. 2k-1               (2^k - 1) << k        1
   right    bits 0 .. k-1                (2^k - 1)             2

so each column shows the file whose index is in its field. The sensor value
starts at 0, so each column starts with its first file (sorted by name).

The logic file has manager.py move a column on to its next file, modulo its
count, whenever either of its board's two sensors goes from dark to lit:
   python manager.py --logic OUT-logic.py

The state file names the files relative to its own folder (left/..., or
../left/... if it's written elsewhere), which DisplayCluster resolves against
the state file's folder when it loads it. By default it's written into the
content directory, so the whole directory - state, logic file and content - can
be copied to the wall's state directory (its content folder) as it is, and
loaded from there: with the directory copied as IceGlass,
   python manager.py --logic IceGlass/sensor-layout-logic.py --state IceGlass/sensor-layout.dcx
Relative paths need a DisplayCluster from 6df8f30 (October 2026) on. manager.py works whatever the wall's
DISPLAYCLUSTER_SENSORS is; it only sets how many bits the web UI shows controls
for, so to flip all 3k from there, start the wall with it at least 3k.

Image sizes are read directly for PNG, JPEG, GIF and BMP, and with ffprobe for
movies and other images. A file whose size can't be found fills its column, and
DisplayCluster then fits it to its aspect ratio at the column's top left.

Usage: python make-sensor-layout.py content-dir wall.json [out]
   wall.json   the wall's DisplayCluster configuration
   out         writes out.dcx and out-logic.py (default content-dir/sensor-layout)
"""
import argparse
import json
import os
import struct
import subprocess
import sys
from xml.sax.saxutils import escape

COLUMNS = ['left', 'center', 'right']   # boards 0, 1, 2; left in the top bits

# What DisplayCluster can show (see Content::getContent); anything else is skipped
IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp', '.ppm', '.pgm', '.pbm', '.xpm', '.xbm'}
MOVIE_TYPES = {'.mov', '.avi', '.mp4', '.mkv', '.mpg', '.flv', '.wmv'}
OTHER_TYPES = {'.svg', '.pyr'}

def wall_size(path):
   """The wall's total size in pixels, mullions included, as DisplayCluster
   works it out (Configuration::getTotalWidth and getTotalHeight)."""
   with open(path) as f:
      d = json.load(f)['dimensions']
   w = d['numTilesWidth'] * d['screenWidth'] + (d['numTilesWidth'] - 1) * d.get('mullionWidth', 0)
   h = d['numTilesHeight'] * d['screenHeight'] + (d['numTilesHeight'] - 1) * d.get('mullionHeight', 0)
   return w, h

def header_size(path):
   """Width and height from a PNG, JPEG, GIF or BMP header, or None."""
   with open(path, 'rb') as f:
      head = f.read(32)
      if head.startswith(b'\x89PNG\r\n\x1a\n'):
         return struct.unpack('>II', head[16:24])
      if head[:6] in (b'GIF87a', b'GIF89a'):
         return struct.unpack('<HH', head[6:10])
      if head.startswith(b'BM'):
         w, h = struct.unpack('<ii', head[18:26])
         return w, abs(h)
      if head.startswith(b'\xff\xd8'):
         # walk the segments to the frame header (SOFn)
         f.seek(2)
         while True:
            marker = f.read(2)
            if len(marker) < 2 or marker[0] != 0xff:
               return None
            if marker[1] in (0xd8, 0x01) or 0xd0 <= marker[1] <= 0xd7:
               continue
            length = struct.unpack('>H', f.read(2))[0]
            if 0xc0 <= marker[1] <= 0xcf and marker[1] not in (0xc4, 0xc8, 0xcc):
               h, w = struct.unpack('>xHH', f.read(5))
               return w, h
            f.seek(length - 2, 1)
   return None

def ffprobe_size(path):
   try:
      out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', path],
                           capture_output=True, text=True, timeout=30).stdout.strip()
      w, h = out.splitlines()[0].split('x')
      return int(w), int(h)
   except (OSError, subprocess.SubprocessError, ValueError, IndexError):
      return None

def svg_size(path):
   import xml.etree.ElementTree as ET
   try:
      root = ET.parse(path).getroot()
      view_box = root.get('viewBox')
      if view_box:
         _, _, w, h = (float(v) for v in view_box.replace(',', ' ').split())
         return w, h
      return float(root.get('width').rstrip('px')), float(root.get('height').rstrip('px'))
   except (ET.ParseError, OSError, TypeError, ValueError, AttributeError):
      return None

def content_size(path):
   ext = os.path.splitext(path)[1].lower()
   if ext == '.svg':
      size = svg_size(path)
   elif ext == '.pyr':
      size = None
   else:
      size = header_size(path) if ext in IMAGE_TYPES else None
      size = size or ffprobe_size(path)
   return size if size and size[0] > 0 and size[1] > 0 else None

def column_files(directory):
   files = []
   for name in sorted(os.listdir(directory)):
      path = os.path.join(directory, name)
      ext = os.path.splitext(name)[1].lower()
      if name.startswith('.') or not os.path.isfile(path):
         continue
      if ext not in IMAGE_TYPES | MOVIE_TYPES | OTHER_TYPES:
         print(f'skipping {path}: not a type DisplayCluster shows')
         continue
      files.append(path)
   return files

def placement(column, size, wall_w, wall_h):
   """x, y, w, h as fractions of the wall: the file fitted to its column,
   keeping its aspect ratio, and centered in it."""
   col_w = wall_w / len(COLUMNS)
   if size is None:
      w, h = col_w, wall_h
   else:
      scale = min(col_w / size[0], wall_h / size[1])
      w, h = size[0] * scale, size[1] * scale
   x = column * col_w + (col_w - w) / 2
   y = (wall_h - h) / 2
   return x / wall_w, y / wall_h, w / wall_w, h / wall_h

def content_window(uri, name, x, y, w, h, hidden, sensor_mask, sensor_pattern):
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
  <sensorPattern>{sensor_pattern}</sensorPattern>
 </ContentWindow>
"""

LOGIC_TEMPLATE = '''"""Sensor logic for {dcx}, written by make-sensor-layout.py: use it with
   python manager.py --logic {logic}

Each column of the wall shows one of its files, the one whose index is in its
field of the sensor value. When either of a board's two sensors goes from dark
to lit, its column moves on to its next file, modulo its count, so the image
changes every time (unless the column has only one file).
"""

K = {k}                      # bits per column
COUNTS = {counts}       # files in each column: left, center, right
SHIFTS = {shifts}       # where each column's field starts in the sensor value
COLUMN_OF_BOARD = [0, 1, 2]  # board N's sensors (bits 2N, 2N+1) drive this column
SENSORS_PER_BOARD = 2

# the bits this logic owns on the wall
SENSOR_MASK = (1 << (len(COUNTS) * K)) - 1

FIELD = (1 << K) - 1

# the sensors' lit bits last time, and the value last returned
prev_sensors = 0
prev_value = 0

def start(value):
   """Takes up the wall's value at startup, so a restart leaves the images as
   they are. A field outside its column's count is brought back inside it."""
   global prev_value
   prev_value = 0
   for column, count in enumerate(COUNTS):
      index = (value >> SHIFTS[column] & FIELD) % count
      prev_value |= index << SHIFTS[column]
   return prev_value

def logic(sensor_value):
   """Returns the value for the wall, given the sensors' lit bits. Calling it
   again with the same bits changes nothing, so a failed send can simply retry."""
   global prev_sensors, prev_value
   rising = (prev_sensors ^ sensor_value) & sensor_value
   prev_sensors = sensor_value
   for board, column in enumerate(COLUMN_OF_BOARD):
      if rising >> (board * SENSORS_PER_BOARD) & ((1 << SENSORS_PER_BOARD) - 1):
         shift = SHIFTS[column]
         index = ((prev_value >> shift & FIELD) + 1) % COUNTS[column]
         prev_value = prev_value & ~(FIELD << shift) | index << shift
   return prev_value
'''

def main():
   parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
   parser.add_argument('content_dir', help='holds the left, center and right directories')
   parser.add_argument('wall', help="the wall's DisplayCluster configuration (wall.json)")
   parser.add_argument('out', nargs='?', help='writes OUT.dcx and OUT-logic.py (default content-dir/sensor-layout)')
   args = parser.parse_args()
   out = args.out or os.path.join(args.content_dir, 'sensor-layout')

   wall_w, wall_h = wall_size(args.wall)
   files = [column_files(os.path.join(args.content_dir, c)) for c in COLUMNS]
   for c, f in zip(COLUMNS, files):
      if not f:
         sys.exit(f'{os.path.join(args.content_dir, c)} has no content')
   counts = [len(f) for f in files]
   k = max(1, (max(counts) - 1).bit_length())
   if 3 * k > 32:
      sys.exit(f'{max(counts)} files in a column needs {3 * k} sensor bits; the wall has 32')
   shifts = [2 * k, k, 0]

   # the files are named relative to the state file's folder
   dcx = out + '.dcx'
   logic = out + '-logic.py'
   dcx_dir = os.path.dirname(os.path.abspath(dcx))
   windows = []
   for column, (name, paths) in enumerate(zip(COLUMNS, files)):
      mask = ((1 << k) - 1) << shifts[column]
      for index, path in enumerate(paths):
         size = content_size(path)
         if size is None:
            print(f"can't find the size of {path}: it fills its column")
         uri = os.path.relpath(os.path.abspath(path), dcx_dir)
         pattern = index << shifts[column]
         windows.append(content_window(uri, f'{name} {index} {os.path.basename(path)}',
                                       *placement(column, size, wall_w, wall_h),
                                       index != 0, mask, pattern))

   with open(dcx, 'w') as f:
      f.write('<!DOCTYPE state>\n<state>\n <version>1</version>\n')
      f.writelines(windows)
      f.write('</state>\n')
   with open(logic, 'w') as f:
      f.write(LOGIC_TEMPLATE.format(dcx=os.path.basename(dcx), logic=os.path.basename(logic),
                                    k=k, counts=counts, shifts=shifts))

   print(f'wrote {dcx} ({len(windows)} windows) and {logic}')
   if os.path.relpath(os.path.abspath(args.content_dir), dcx_dir).startswith('..'):
      print(f'the files are named from outside {dcx_dir} (../...), so keep them in the same '
            'place relative to the state file on the wall')
   print(f'k = {k}; files left, center, right = {counts[0]}, {counts[1]}, {counts[2]}')
   print(f'uses sensor bits 0-{3 * k - 1}; to see them in the web UI, start the wall '
         f'with DISPLAYCLUSTER_SENSORS={3 * k} or more (manager.py doesn\'t need it)')

if __name__ == '__main__':
   main()
