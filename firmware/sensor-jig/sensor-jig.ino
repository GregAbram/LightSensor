#include <Arduino.h>
#include <Preferences.h>

// Bench jig for tuning one sensor on one board, over USB serial (115200 baud).
// Every quarter second it prints the sensor's light reading and whether it
// counts as lit, using the same reading, scale and hysteresis as detector3:
//
//   board 1 sensor 0  light 2314  LIT    (threshold 2000, gap 300)
//
// Commands, typed into the Serial Monitor:
//   "T<n>"   set threshold, e.g. "T2000"
//   "G<n>"   set gap, e.g. "G300"
//   "S<n>"   watch sensor 0 or 1 instead
//   "?"      report the current threshold/gap
//
// Threshold and gap are kept in the same flash settings detector3 uses, so
// values tuned here are the ones detector3 starts with after reflashing it
// (unless the upload erases all flash).
//
// A sensor turns lit when its light reading rises above the threshold, and
// dark again only when it falls below threshold - gap (hysteresis).

// The board and sensor to start with. The board number only labels the output.
#define BOARD_INDEX 0
#define SENSOR 0

// GPIO2 (D0/A0) is sensor 0 and GPIO4 (D2/A2) is sensor 1
const int SENSOR_PINS[] = {2, 4};
const int NUM_SENSORS = 2;

// These sensors read high (4095) when covered and drop with light, so flip
// the reading: "light" is 0 when dark and rises toward 4095 with light.
const bool INVERTED = true;

// First-boot defaults, on the "light" scale (0..4095); after that the values
// saved in flash are used
const int DEFAULT_THRESHOLD = 2000;
const int DEFAULT_GAP = 10;

const int SAMPLES = 8;                 // averaged per reading, to steady the value
const unsigned long PRINT_MS = 250;

int sensor = SENSOR;
int threshold = DEFAULT_THRESHOLD;
int gap = DEFAULT_GAP;
bool lit = false;
unsigned long lastPrint = 0;

Preferences prefs;

int readSensor(int pin) {
  long sum = 0;
  for (int i = 0; i < SAMPLES; i++) {
    sum += analogRead(pin);
  }
  return sum / SAMPLES;
}

void reportSettings() {
  Serial.printf("#T%d G%d  (board %d sensor %d)\n", threshold, gap, BOARD_INDEX, sensor);
}

// Handles "T<n>", "G<n>", "S<n>" or "?" and reports the settings
void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;
  char c = toupper(cmd.charAt(0));
  int value = cmd.substring(1).toInt();
  bool valid = cmd.length() > 1 && value >= 0 && value <= 4095;
  if (c == 'T' && valid) {
    threshold = value;
    prefs.putInt("threshold", threshold);
  } else if (c == 'G' && valid) {
    gap = value;
    prefs.putInt("gap", gap);
  } else if (c == 'S' && valid && value < NUM_SENSORS) {
    sensor = value;
    lit = false;
  } else if (c != '?') {
    Serial.print("Unknown command: ");
    Serial.println(cmd);
    return;
  }
  reportSettings();
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(20);
  analogReadResolution(12);
  for (int i = 0; i < NUM_SENSORS; i++) {
    pinMode(SENSOR_PINS[i], INPUT);
  }

  prefs.begin("lightsensor", false);
  threshold = prefs.getInt("threshold", DEFAULT_THRESHOLD);
  gap = prefs.getInt("gap", DEFAULT_GAP);

  delay(1000);   // give the Serial Monitor time to attach
  Serial.println("sensor-jig ready");
  reportSettings();
}

void loop() {
  if (Serial.available()) {
    handleCommand(Serial.readStringUntil('\n'));
  }

  // read as often as detector3 does, so lit follows the light the same way
  int raw = readSensor(SENSOR_PINS[sensor]);
  int light = INVERTED ? 4095 - raw : raw;
  if (!lit && light > threshold) {
    lit = true;
  } else if (lit && light < threshold - gap) {
    lit = false;
  }

  if (millis() - lastPrint >= PRINT_MS) {
    lastPrint = millis();
    Serial.printf("board %d sensor %d  light %4d  %s  (threshold %d, gap %d)\n",
                  BOARD_INDEX, sensor, light, lit ? "LIT  " : "dark ", threshold, gap);
  }
  delay(30);
}
