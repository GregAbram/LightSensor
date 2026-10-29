
#include <Arduino.h>

// Two-sensor bench test, change-only version of threshold-test: reads light
// sensors as analog, decides lit/dark for each with one shared software
// threshold, and prints a line only when a sensor changes between lit and
// dark (plus once at startup). Serial only, no BLE.
//
// A sensor turns lit when its reading rises above THRESHOLD, and dark again
// only when it falls below THRESHOLD - GAP. The gap (hysteresis) stops
// flicker near the edge.
//
// Open the Serial Monitor (115200 baud). Each line shows every sensor's raw
// reading (0..4095, high = dark) and whether it counts as lit. Note the
// threshold and gap are on the flipped "light" scale (4095 - raw), so lit
// means raw < 4095 - THRESHOLD. Tune live by sending, e.g.:
//   threshold 2000
//   gap 300
// (or just "t 2000" / "g 300"). Once you have good values, put them in
// THRESHOLD and GAP below.

// GPIO2 (D0/A0) is sensor 0 and GPIO4 (D2/A2) is sensor 1. Sensor 2 is on
// GPIO6, which has no ADC, so it's left out.
const int SENSOR_PINS[] = {2, 4};
const int NUM_SENSORS = 2;

// These sensors read high (4095) when covered and drop with light, so flip
// the reading: "light" below is 0 when dark and rises toward 4095 with light.
const bool INVERTED = true;

// On the "light" scale, so higher = more light (0..4095)
int THRESHOLD = 2000;
int GAP = 10;

const int SAMPLES = 8;                 // averaged per reading, to steady the value

bool lit[NUM_SENSORS] = {false, false};
bool reported = false;                 // false until the startup state is printed

int readSensor(int pin) {
  long sum = 0;
  for (int i = 0; i < SAMPLES; i++) {
    sum += analogRead(pin);
  }
  return sum / SAMPLES;
}

// Accepts "threshold <n>" / "t <n>" and "gap <n>" / "g <n>"
void handleSerial() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  int space = line.indexOf(' ');
  if (space < 0) return;
  String cmd = line.substring(0, space);
  int value = line.substring(space + 1).toInt();
  if (value < 0 || value > 4095) return;
  if (cmd == "threshold" || cmd == "t") THRESHOLD = value;
  else if (cmd == "gap" || cmd == "g") GAP = value;
  else return;
  Serial.print("threshold:"); Serial.print(THRESHOLD);
  Serial.print(" gap:"); Serial.println(GAP);
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(20);
  analogReadResolution(12);
  for (int i = 0; i < NUM_SENSORS; i++) {
    pinMode(SENSOR_PINS[i], INPUT);
  }
}

void loop() {
  handleSerial();

  bool changed = !reported;
  int raw[NUM_SENSORS];
  for (int i = 0; i < NUM_SENSORS; i++) {
    raw[i] = readSensor(SENSOR_PINS[i]);
    int light = INVERTED ? 4095 - raw[i] : raw[i];
    if (!lit[i] && light > THRESHOLD) {
      lit[i] = true;
      changed = true;
    } else if (lit[i] && light < THRESHOLD - GAP) {
      lit[i] = false;
      changed = true;
    }
  }

  if (changed) {
    reported = true;
    // e.g. "raw0:3950 lit0:false raw1:1200 lit1:true"
    for (int i = 0; i < NUM_SENSORS; i++) {
      Serial.print("raw"); Serial.print(i); Serial.print(":"); Serial.print(raw[i]);
      Serial.print(" lit"); Serial.print(i); Serial.print(":"); Serial.print(lit[i] ? "true" : "false");
      Serial.print(i < NUM_SENSORS - 1 ? " " : "\n");
    }
  }
}
