from DC import DC
import json
import os
import sys

# 3 boards x 2 sensors, one column per sensor
NUM_COLUMNS = 6

# The wall's DisplayCluster serves HTTPS with a certificate for its full name,
# so connect by that name and trust that certificate (copied from the wall host).
# Usage: python dc-config.py [host]   (default rattler-dev.tacc.utexas.edu)
host = sys.argv[1] if len(sys.argv) > 1 else 'rattler-dev.tacc.utexas.edu'
cafile = os.path.expanduser('~/.displaycluster/rattler-dev_cert.pem')
# rattler-dev's own token, kept apart from the default ~/.displaycluster/api_token
with open(os.path.expanduser('~/.displaycluster/rattler-dev_token')) as f:
    token = f.read().strip()

dc = DC(host, cafile=cafile, token=token)
content = dc.content

# Window x/w are in tile units, so the wall is tilesWide units across
WALL_WIDTH, _ = dc.getConfiguration()
print(f'wall is {WALL_WIDTH} tiles wide')

columns = [[] for i in range(NUM_COLUMNS)]

for c in content:
    x = content[c]['x']
    w = content[c]['w']
    ix = int((x + (w / 2)) * NUM_COLUMNS / WALL_WIDTH)
    ix = max(0, min(NUM_COLUMNS - 1, ix))
    print(c, ix)
    columns[ix].append([c, 1])

for i, c in enumerate(columns):
    print(f'column {i}')
    print(c)

for c in columns:
    if len(c) > 0:
         c[0][1] = 0

for i in range(NUM_COLUMNS):
    print(i)
    for j in columns[i]:
        print("    ", j)

if 1 == 1:
    with open('config.json', 'w') as f:
        json.dump(columns, f, indent=4)
