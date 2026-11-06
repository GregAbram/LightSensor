# Tuning the sensors: threshold and gap

Each board decides for itself whether each of its two sensors is lit, using two
numbers kept in its flash: a **threshold** and a **gap**. Getting them right is
what makes a flashlight hit count once, every time, and nothing else count at
all.

## What the numbers mean

The board reads each sensor as a **light** value from 0 to 4095: 0 is dark, and
it rises toward 4095 as more light falls on the sensor. (The sensors themselves
read the other way round; the firmware flips them, so higher always means
brighter.) Each reading is an average of 8 samples, taken every 30 ms.

- A sensor turns **lit** when its light value goes **above the threshold**.
- It turns **dark** again only when the value falls **below threshold − gap**.

```
light
4095 ┤
     │          ╭──────╮            flashlight on the sensor
     │         ╭╯      ╰╮
 T   ┼ ─ ─ ─ ─ ┼ ─ ─ ─ ─┼ ─ ─ ─     above this: turns lit
     │        ╱    lit   ╲
 T−G ┼ ─ ─ ─ ┼─ ─ ─ ─ ─ ─ ┼ ─ ─     below this: turns dark
     │~~~~~~~╯            ╰~~~~~~   room light, with its flicker and noise
   0 ┤
```

The space between the two lines is the gap. Inside it, a sensor stays as it
was, so a reading that wobbles around the threshold doesn't flip it back and
forth. Every flip from dark to lit is a hit, which moves the wall on to its next
image, so a gap that's too small makes one hit look like several.

Both sensors on a board share its threshold and gap. They're saved in flash, so
they survive reboots, battery changes and reflashing (unless the upload erases
all flash). A new board starts with threshold 2000 and gap 10. The gap of 10 is
only a placeholder and almost certainly too small.

## Choosing the values

Do this where the sensors will be used, in the room's real light, with the wall
on, since its light reaches the sensors too.

1. **Measure the room.** With no flashlight, watch each sensor's light value for
   a while. Walk past, open the door, and stand where visitors will stand. Note
   the **highest** value you see: call it *room*.
2. **Measure the flashlight.** Shine it on each sensor from the distance and
   angle visitors will use, including a bad aim and a weak battery. Note the
   **lowest** value you see while it's on the sensor: call it *flash*.
3. **Set the threshold** about halfway between: **T = (room + flash) / 2**.
4. **Set the gap** to about a third of that space: **G = (flash − room) / 3**.
   That keeps T − G well above *room* while still letting the sensor go dark
   when the flashlight leaves.
5. **Check it.** Hit each sensor a few times. Each hit should turn it lit once,
   and it should go dark each time you move the light away.

For example, with *room* = 900 and *flash* = 3100: T = 2000, G = 730, so the
sensor turns lit above 2000 and dark below 1270.

If *flash* is barely above *room*, no setting will be reliable. Shade the
sensor (a short tube around it helps a lot), use a brighter or narrower
flashlight, or move the sensor away from the light that's swamping it.

Tune for the weaker of a board's two sensors, since they share one setting.

## Three ways to set them

| Tool | When | How |
|---|---|---|
| `firmware/sensor-jig` | On the bench, or with the board on USB. The only one that shows the light values, so use it for steps 1 and 2. | Flash it, open the Serial Monitor at 115200 baud, and watch `light 2314 LIT` four times a second. Type `T2000`, `G300`, `?`, or `S1` to watch the other sensor. Reflash `detector3` when done. The values carry over. |
| `manager.py` | With the installation running. | Type into its window: `1 T2000`, `1 G300`, `all G300`, or `?` to show every board's settings. Watch the `sensor 0 ... True/False` lines as you hit the sensors. |
| `tune.py` | Over Bluetooth, with `manager.py` stopped. | `venv/bin/python tune.py 1 T2000 G300`, or `tune.py 1` to read them back. |

The Bluetooth firmware only reports lit or dark, not the light values. So
measure with the jig, and fine-tune in place with `manager.py`.

The jig needs **Tools → USB CDC On Boot: Enabled** in the Arduino IDE. It won't
compile without it, because nothing would reach the Serial Monitor.

## When it goes wrong

| What you see | Likely cause | Try |
|---|---|---|
| One hit moves the image on two or more times | the gap is too small, so the reading bounces across the threshold | a bigger gap |
| The image changes with no flashlight, as people walk by or lights change | the threshold is too close to *room* | a higher threshold, or shade the sensor |
| A hit sometimes doesn't count | the threshold is too high for a weak or badly aimed flashlight | a lower threshold |
| A sensor stays lit after the flashlight has gone | T − G is below the room's light, so it never goes dark | a smaller gap, or a higher threshold |
| A board's two sensors behave differently | they share one setting, but see different light | tune for the weaker one, or shade the brighter one |

A very quick flick of the light can be missed: the board reads every 30 ms, and
a sensor that turns lit and dark again while `manager.py` is still sending the
last change doesn't count.
