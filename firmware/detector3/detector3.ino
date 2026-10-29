
#include <Arduino.h>
#include <string.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <Preferences.h>

// Two light sensors read as analog, each decided lit/dark with one shared
// software threshold, reported to the host over BLE (Nordic UART Service).
//
// Messages to the host (TX):
//   "10\n"            sensor states, '1' = lit; sent on every change and on connect
//   "#T2000 G300\n"   current threshold/gap, in reply to a settings command
// Messages from the host (RX):
//   "OK"              ack for the last state message
//   "T<n>"            set threshold, e.g. "T2000"
//   "G<n>"            set gap, e.g. "G300"
//   "?"               just report the current threshold/gap
// The same T/G/? commands also work from the Serial Monitor (115200 baud).
// Threshold and gap are saved in flash, so they survive a reboot.
//
// A sensor turns lit when its light reading rises above the threshold, and
// dark again only when it falls below threshold - gap (hysteresis).

// Nordic UART Service UUIDs (widely supported)
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

// Set this to 0, 1 or 2 before flashing each board. The host maps board N's
// two sensors to config.json columns 2N and 2N+1.
#define BOARD_INDEX 2


#define STR_(x) #x
#define STR(x) STR_(x)
#define DEVICE_NAME "XIAO-ESP32C3-" STR(BOARD_INDEX)

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
const unsigned long ACK_TIMEOUT_MS = 2000;

int threshold = DEFAULT_THRESHOLD;
int gap = DEFAULT_GAP;
bool lit[NUM_SENSORS] = {false, false};

Preferences prefs;

BLEServer *pServer = nullptr;
BLECharacteristic *pTxCharacteristic = nullptr;

volatile bool deviceConnected = false;
volatile bool ackReceived = false;
bool ackPending = false;
unsigned long sentAt = 0;

// A settings command from BLE, handed from the BLE task to loop()
char bleCommand[16];
volatile bool bleCommandPending = false;

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) {
    deviceConnected = true;
    Serial.println("Central connected");
  }
  void onDisconnect(BLEServer* server) {
    deviceConnected = false;
    ackPending = false;
    Serial.println("Central disconnected, restarting advertising...");
    server->startAdvertising();
  }
};

class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) {
    String value = characteristic->getValue();
    value.trim();
    if (value == "OK") {
      ackReceived = true;
    } else if (!bleCommandPending) {
      strncpy(bleCommand, value.c_str(), sizeof(bleCommand) - 1);
      bleCommand[sizeof(bleCommand) - 1] = '\0';
      bleCommandPending = true;
    }
  }
};

int readSensor(int pin) {
  long sum = 0;
  for (int i = 0; i < SAMPLES; i++) {
    sum += analogRead(pin);
  }
  return sum / SAMPLES;
}

// Updates lit[] from fresh readings; returns true if any sensor changed
bool updateSensors() {
  bool changed = false;
  for (int i = 0; i < NUM_SENSORS; i++) {
    int raw = readSensor(SENSOR_PINS[i]);
    int light = INVERTED ? 4095 - raw : raw;
    if (!lit[i] && light > threshold) {
      lit[i] = true;
      changed = true;
    } else if (lit[i] && light < threshold - gap) {
      lit[i] = false;
      changed = true;
    }
  }
  return changed;
}

void notify(const char *msg) {
  pTxCharacteristic->setValue((uint8_t*)msg, strlen(msg));
  pTxCharacteristic->notify();
}

void sendState() {
  char msg[NUM_SENSORS + 2];
  for (int i = 0; i < NUM_SENSORS; i++) {
    msg[i] = lit[i] ? '1' : '0';
  }
  msg[NUM_SENSORS] = '\n';
  msg[NUM_SENSORS + 1] = '\0';
  Serial.print("State ");
  Serial.print(msg);
  if (!deviceConnected) return;
  ackReceived = false;
  notify(msg);
  ackPending = true;
  sentAt = millis();
}

// Handles "T<n>", "G<n>" or "?" (from BLE or serial) and reports the settings
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
  } else if (c != '?') {
    Serial.print("Unknown command: ");
    Serial.println(cmd);
    return;
  }

  char msg[24];
  snprintf(msg, sizeof(msg), "#T%d G%d\n", threshold, gap);
  Serial.print(msg);
  if (deviceConnected) notify(msg);
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
  updateSensors();

  BLEDevice::init(DEVICE_NAME);
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);

  pTxCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID_TX,
    BLECharacteristic::PROPERTY_NOTIFY
  );
  pTxCharacteristic->addDescriptor(new BLE2902());

  BLECharacteristic *pRxCharacteristic = pService->createCharacteristic(
    CHARACTERISTIC_UUID_RX,
    BLECharacteristic::PROPERTY_WRITE
  );
  pRxCharacteristic->setCallbacks(new RxCallbacks());

  pService->start();
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  BLEDevice::startAdvertising();

  Serial.print(DEVICE_NAME);
  Serial.print(" ready, threshold ");
  Serial.print(threshold);
  Serial.print(" gap ");
  Serial.println(gap);
}

void loop() {
  if (bleCommandPending) {
    handleCommand(String(bleCommand));
    bleCommandPending = false;
  }
  if (Serial.available()) {
    handleCommand(Serial.readStringUntil('\n'));
  }

  static bool wasConnected = false;
  if (deviceConnected && !wasConnected) {
    sendState();  // resync a freshly connected central with the full current state
  }
  wasConnected = deviceConnected;

  if (updateSensors()) {
    sendState();
  }

  if (ackPending && !ackReceived && (millis() - sentAt > ACK_TIMEOUT_MS)) {
    Serial.println("No ack — resending");
    sendState();
  }
}
