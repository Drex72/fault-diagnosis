// Fault injector, ESP32 DevKit v1 driving two 8-channel relay modules
// (inputs active low). Relay wiring: docs/HARDWARE.md.
#include <Arduino.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include "injcore.h"

namespace {

// GPIO 14 avoided: it toggles during boot and would click a fault relay.
constexpr int RELAY_PINS[inj::kRelays] = {13, 27, 16, 17, 18, 19, 21, 22, 23, 25, 26};
constexpr bool RELAY_ACTIVE_LOW = true;
constexpr size_t MAX_LINE = 256;

class EspHal : public inj::Hal {
 public:
  uint32_t millis() override { return ::millis(); }
  void set_relay(int ch, bool energised) override {
    digitalWrite(RELAY_PINS[ch], (energised != RELAY_ACTIVE_LOW) ? HIGH : LOW);
  }
  uint32_t random(uint32_t n) override { return esp_random() % n; }
};

EspHal g_hal;
inj::Injector g_inj(g_hal);
std::string g_line;
bool g_overflow = false;

const char* reset_reason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    default: return "other";
  }
}

void send(const std::string& s) {
  Serial.write(s.data(), s.size());
  Serial.write('\n');
}

}  // namespace

void setup() {
  for (int p : RELAY_PINS) {  // de-energised (no fault) before anything else
    digitalWrite(p, RELAY_ACTIVE_LOW ? HIGH : LOW);
    pinMode(p, OUTPUT);
  }
  Serial.begin(115200);
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t cfg = {.timeout_ms = 5000, .idle_core_mask = 0, .trigger_panic = true};
  esp_task_wdt_reconfigure(&cfg);
#else
  esp_task_wdt_init(5, true);
#endif
  esp_task_wdt_add(NULL);
  g_inj.boot(reset_reason());
  send(g_inj.hello());
}

void loop() {
  esp_task_wdt_reset();
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (g_overflow) {
        send("{\"id\":null,\"status\":\"error\",\"error\":\"line too long\"}");
      } else if (!g_line.empty()) {
        send(g_inj.handle_line(g_line));
      }
      g_line.clear();
      g_overflow = false;
    } else if (g_line.size() >= MAX_LINE) {
      g_overflow = true;
    } else {
      g_line.push_back(c);
    }
  }
  for (const std::string& ev : g_inj.tick()) send(ev);
  delay(1);
}
