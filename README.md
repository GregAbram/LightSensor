# Light sensors

Flashlight-controlled content on a DisplayCluster wall. Three Seeed XIAO
ESP32-C3 boards each read two light sensors and report them over BLE; a host
program toggles a bit for each sensor each time it's lit, and sends the six bits
as one sensor value to DisplayCluster (`PUT /sensors`), which shows a window
when `(value & sensorMask) == sensorPattern`. Needs a DisplayCluster with
`sensorPattern` (TACC/DisplayCluster).

Board N's sensor k is bit `N * 2 + k` of the sensor value.

## Firmware (`firmware/`)

| Sketch | |
|---|---|
| `detector3/` | The board firmware. Sensors on GPIO2 and GPIO4, read as analog against a threshold and gap that are set over BLE or serial (`T<n>`, `G<n>`, `?`) and kept in flash. Set `BOARD_INDEX` (0-2) before flashing each board; it advertises as `XIAO-ESP32C3-<n>`. |
| `threshold-test/` | Serial-only bench test: prints both sensors' raw readings continuously, for picking a threshold. |
| `threshold-change/` | Serial-only bench test: prints only when a sensor changes. |

Build for **XIAO_ESP32C3** with **USB CDC On Boot: Enabled**, or nothing
reaches the Serial Monitor.

`detector3` is set up for battery life: an 80 MHz CPU, a reading every 50 ms
with the CPU idle in between, 0 dBm transmit power, slow advertising, and a
request for a 60-120 ms connection interval with slave latency. The constants
are together near the top of the sketch. The stock Arduino core is built
without light sleep or Bluetooth modem sleep, so the board can't sleep while
connected; that would take a custom build of the core.

## Host programs

Run them with the venv's Python (`venv/bin/python`, which has `bleak`; see
`requirements.txt`), from a terminal that has Bluetooth permission - VS
Code's, for one. A board takes only one BLE connection, so run one of these at
a time.

| Program | |
|---|---|
| `manager.py` | Drives the wall: all three boards over BLE, one `PUT /sensors` per change to rattler-dev. Each sensor's bit toggles when the sensor goes from dark to lit. |
| `jig.py [period]` | Test jig: samples all six sensors and prints them as true/false when they change. No DisplayCluster. |
| `Test1.py [board]`, `Test3.py` | BLE testers for one board, or all three. |
| `tune.py <board> [T<n>] [G<n>]` | Reads or sets a board's threshold and gap over BLE. |
| `make-test-layout.py` | Writes `lights.dcx`, a red/green test layout with a cell per sensor; copy it to `~/.displaycluster/states` on the wall. `CONTENT_DIR` picks where the images are (default `/home/gda/Content`; `content/` here has them). |
| `make-demo-layout.py [out-dir]` | Writes `sensor-demo.dcx` and its placeholder images (default `demo/`): the wall in thirds, with four images per third, one for each combination of that board's two sensors. Each image shows its mask and pattern. Copy the images to `~/Content/sensor-demo` and the `.dcx` to `~/.displaycluster/states` on the wall; `CONTENT_DIR` changes where it looks for the images. |
| `sensors-test.py` | Checks DisplayCluster's sensor support through its API, without boards. |
| `DC.py` | A copy of DisplayCluster's Python client. |

`.vscode/launch.json` has debug configurations for the BLE programs.

The wall connection uses rattler-dev's certificate and token in
`~/.displaycluster/rattler-dev_cert.pem` and `rattler-dev_token`.

### How `manager.py` sets the bits

The boards report whether each sensor is lit, but the wall isn't sent that. Each
sensor has a bit that flips when the sensor goes from dark to lit and otherwise
stays as it is. A hit with the flashlight changes the image, and the image stays
up after the flashlight has gone. With two sensors picking from four images, each
hit moves one step around `00`, `01`, `11`, `10`, and the opposite image takes two
hits.

- **Starting up:** it starts from the value the wall already has, so restarting
  `manager.py` leaves the images as they are. To start over, send the wall 0
  (`~/bin/sensors` on rattler-dev) or restart DisplayCluster, which clears the
  value. After restarting DisplayCluster, restart `manager.py` too, or it keeps
  the old bits and sends them with the next hit.
- **Disconnects:** a board that drops out keeps its bits. It counts as dark
  meanwhile, so a sensor that's lit when its board reconnects, or when
  `manager.py` starts, counts as a hit.
- **Bounce:** a flicker across the threshold counts as another hit and leaves the
  wrong image up, and nothing in `manager.py` filters it out. If that happens,
  widen the gap with `tune.py`.
- **Very quick hits:** the boards read every 50 ms, and a sensor that goes lit and
  dark again while a request is in flight (about 80 ms) doesn't toggle.

## `old/`

The earlier host side, for a DisplayCluster without sensor support: it hid
and revealed windows one by one from `config.json` (made by `dc-config.py`).
Kept for reference; run from the top folder, since they import `DC.py`.
