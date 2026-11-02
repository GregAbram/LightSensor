# Light sensors

Flashlight-controlled content on a DisplayCluster wall. Three Seeed XIAO
ESP32-C3 boards each read two light sensors and report them over BLE; a host
program combines the six into one sensor value and sends it to DisplayCluster
(`PUT /sensors`), which shows a window when `(value & sensorMask) ==
sensorPattern`. Needs a DisplayCluster with `sensorPattern` (TACC/DisplayCluster).

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
| `manager.py` | Drives the wall: all three boards over BLE, one `PUT /sensors` per change to rattler-dev. |
| `jig.py [period]` | Test jig: samples all six sensors and prints them as true/false when they change. No DisplayCluster. |
| `Test1.py [board]`, `Test3.py` | BLE testers for one board, or all three. |
| `tune.py <board> [T<n>] [G<n>]` | Reads or sets a board's threshold and gap over BLE. |
| `make-test-layout.py` | Writes `lights.dcx`, a red/green test layout with a cell per sensor; copy it to `~/.displaycluster/states` on the wall. `CONTENT_DIR` picks where the images are (default `/home/gda/Content`; `content/` here has them). |
| `sensors-test.py` | Checks DisplayCluster's sensor support through its API, without boards. |
| `DC.py` | DisplayCluster's Python client, from the `sensors` branch. |

`.vscode/launch.json` has debug configurations for the BLE programs.

The wall connection uses rattler-dev's certificate and token in
`~/.displaycluster/rattler-dev_cert.pem` and `rattler-dev_token`.

## `old/`

The earlier host side, for a DisplayCluster without sensor support: it hid
and revealed windows one by one from `config.json` (made by `dc-config.py`).
Kept for reference; run from the top folder, since they import `DC.py`.
