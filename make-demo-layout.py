"""Writes a demo of DisplayCluster's sensor masks and patterns: the wall split
into three thirds, each with four images stacked in it, one per combination of
that third's two sensors. Each third always shows exactly one image.

   third    board   mask     patterns
   left     0       000011   000000 000001 000010 000011
   middle   1       001100   000000 000100 001000 001100
   right    2       110000   000000 010000 100000 110000

Each image says which third it's for, its two sensor bits, and its mask and
pattern, so the wall shows what it's matching. The images are plain generated
placeholders, written with only the standard library: swap in real ones by
changing the URIs in the state file.

Usage: python make-demo-layout.py [out-dir]   (default demo)
Writes sensor-demo.dcx and the images to out-dir. The state file names the
images in CONTENT_DIR - /home/gda/Content/sensor-demo, as on rattler-dev, unless
the environment sets CONTENT_DIR. Copy the images there and the .dcx to
~/.displaycluster/states on the wall host, then load it.
"""
import os
import struct
import sys
import zlib
from xml.sax.saxutils import escape

THIRDS = ['LEFT', 'MIDDLE', 'RIGHT']   # board 0, 1, 2
SENSORS_PER_THIRD = 2
NUM_SENSORS = len(THIRDS) * SENSORS_PER_THIRD
CONTENT_DIR = os.environ.get('CONTENT_DIR', '/home/gda/Content/sensor-demo')

# rattler-dev's wall is 3 square tiles, so each third is square
IMAGE_SIZE = 1024

# Space between thirds, as a fraction of the wall's width
GAP_X = 0.01

# Background for each value of a third's two bits, resting state first
COLORS = [(0x2b, 0x34, 0x40), (0x1f, 0x6f, 0xb2), (0xc8, 0x64, 0x1e), (0x2e, 0x8b, 0x57)]
TEXT = (0xff, 0xff, 0xff)

# 5x7 bitmap font, only the characters the images use
FONT = {
   ' ': '00000 00000 00000 00000 00000 00000 00000',
   '0': '01110 10001 10011 10101 11001 10001 01110',
   '1': '00100 01100 00100 00100 00100 00100 01110',
   'A': '01110 10001 10001 11111 10001 10001 10001',
   'D': '11100 10010 10001 10001 10001 10010 11100',
   'E': '11111 10000 10000 11110 10000 10000 11111',
   'F': '11111 10000 10000 11110 10000 10000 10000',
   'G': '01110 10001 10000 10111 10001 10001 01111',
   'H': '10001 10001 10001 11111 10001 10001 10001',
   'I': '01110 00100 00100 00100 00100 00100 01110',
   'K': '10001 10010 10100 11000 10100 10010 10001',
   'L': '10000 10000 10000 10000 10000 10000 11111',
   'M': '10001 11011 10101 10101 10001 10001 10001',
   'N': '10001 11001 10101 10011 10001 10001 10001',
   'P': '11110 10001 10001 11110 10000 10000 10000',
   'R': '11110 10001 10001 11110 10100 10010 10001',
   'S': '01111 10000 10000 01110 00001 00001 11110',
   'T': '11111 00100 00100 00100 00100 00100 00100',
}

def draw_text(pixels, text, y, scale, color):
   """Draws text centered across the image, with its top at y."""
   x = (IMAGE_SIZE - (len(text) * 6 - 1) * scale) // 2
   for c in text:
      for r, row in enumerate(FONT[c].split()):
         for col, bit in enumerate(row):
            if bit == '1':
               for py in range(y + r * scale, y + (r + 1) * scale):
                  start = (py * IMAGE_SIZE + x + col * scale) * 3
                  pixels[start:start + scale * 3] = bytes(color) * scale
      x += 6 * scale

def write_png(path, pixels):
   def chunk(kind, data):
      return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
   stride = IMAGE_SIZE * 3
   raw = b''.join(b'\0' + pixels[y * stride:(y + 1) * stride] for y in range(IMAGE_SIZE))
   with open(path, 'wb') as f:
      f.write(b'\x89PNG\r\n\x1a\n')
      f.write(chunk(b'IHDR', struct.pack('>IIBBBBB', IMAGE_SIZE, IMAGE_SIZE, 8, 2, 0, 0, 0)))
      f.write(chunk(b'IDAT', zlib.compress(raw, 9)))
      f.write(chunk(b'IEND', b''))

def make_image(path, third, bits, mask, pattern):
   pixels = bytearray(bytes(COLORS[bits]) * (IMAGE_SIZE * IMAGE_SIZE))
   draw_text(pixels, third, 110, 16, TEXT)
   draw_text(pixels, f'{bits:02b}', 310, 48, TEXT)
   draw_text(pixels, f'MASK    {mask:0{NUM_SENSORS}b}', 760, 7, TEXT)
   draw_text(pixels, f'PATTERN {pattern:0{NUM_SENSORS}b}', 840, 7, TEXT)
   write_png(path, pixels)

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

out_dir = sys.argv[1] if len(sys.argv) > 1 else 'demo'
os.makedirs(out_dir, exist_ok=True)

windows = []
third_w = 1.0 / len(THIRDS)
for t, third in enumerate(THIRDS):
   shift = t * SENSORS_PER_THIRD
   mask = ((1 << SENSORS_PER_THIRD) - 1) << shift
   for bits in range(1 << SENSORS_PER_THIRD):
      pattern = bits << shift
      filename = f'{third.lower()}-{bits:02b}.png'
      make_image(os.path.join(out_dir, filename), third, bits, mask, pattern)
      windows.append(content_window(CONTENT_DIR + '/' + filename, f'{third.lower()}-{bits:02b}',
                                    t * third_w + GAP_X / 2, 0, third_w - GAP_X, 1,
                                    pattern != 0, mask, pattern))

state_file = os.path.join(out_dir, 'sensor-demo.dcx')
with open(state_file, 'w') as f:
   f.write('<!DOCTYPE state>\n<state>\n <version>1</version>\n')
   f.writelines(windows)
   f.write('</state>\n')

print(f'wrote {state_file} ({len(windows)} windows) and their images')
