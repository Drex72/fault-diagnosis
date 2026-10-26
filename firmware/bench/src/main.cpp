// Bench controller, ESP32 DevKit v1. Board glue for benchcore: ADS1115 behind a
// CD74HC4067 multiplexer for the voltage taps, two INA226 current sensors, the
// test-load MOSFET, NVS for calibration, and the task watchdog.
//
// Pin map and component values: docs/HARDWARE.md. Safety limits: benchcore.h.
#include <Arduino.h>
#include <Preferences.h>
#include <Wire.h>
#include <esp_system.h>
#include <esp_task_wdt.h>

#include "benchcore.h"

namespace {

constexpr int PIN_SDA = 21;
constexpr int PIN_SCL = 22;
constexpr int PIN_TEST_LOAD = 23;  // NPN driver of the high-side P-MOSFET (HIGH = on); 100 k base pull-down keeps it off during reset
constexpr int PIN_MUX[4] = {25, 26, 27, 14};
constexpr int PIN_MUX_EN = 13;  // active low
constexpr int PIN_LED = 2;

constexpr uint8_t ADS_ADDR = 0x48;
constexpr uint8_t INA_ADDR[2] = {0x40, 0x41};  // src, motor
constexpr float TAP_DIVIDER = 5.0f;            // 40 k / 10 k: 16.4 V at the tap is 3.28 V at the ADC
constexpr float ADC_SATURATION_V = 3.28f;      // the ADS1115 runs at 3.3 V; at or above this the reading is clipped
constexpr uint32_t MUX_SETTLE_US = 500;        // multiplexer plus divider RC settling
constexpr float SHUNT_OHM = 0.01f;
constexpr float CURRENT_LSB = 0.00025f;  // A per bit: 8.19 A full scale with 0.01 ohm
constexpr uint16_t INA_CAL = 2048;       // 0.00512 / (CURRENT_LSB * SHUNT_OHM)
constexpr uint16_t INA_CONFIG = 0x4097;  // 1 average, 332 us bus and shunt conversions, continuous
constexpr uint32_t WDT_TIMEOUT_S = 5;
constexpr size_t MAX_LINE = 512;

bool g_feed_wdt = true;

const char* reset_reason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_EXT: return "external";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    default: return "unknown";
  }
}

bench::Io i2c_status(uint8_t err) {
  if (err == 0) return bench::Io::Ok;
  return err == 5 ? bench::Io::Timeout : bench::Io::Error;  // 5: timeout in the Arduino-ESP32 Wire driver
}

bench::Io write16(uint8_t addr, uint8_t reg, uint16_t v) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(v >> 8);
  Wire.write(v & 0xFF);
  return i2c_status(Wire.endTransmission());
}

bench::Io read16(uint8_t addr, uint8_t reg, uint16_t& v) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  bench::Io st = i2c_status(Wire.endTransmission(false));
  if (st != bench::Io::Ok) return st;
  if (Wire.requestFrom(addr, (uint8_t)2) != 2) return bench::Io::Timeout;
  v = (uint16_t)(Wire.read() << 8);
  v |= (uint16_t)Wire.read();
  return bench::Io::Ok;
}

void ina_init(int ch) {
  write16(INA_ADDR[ch], 0x00, INA_CONFIG);
  write16(INA_ADDR[ch], 0x05, INA_CAL);
}

class EspHal : public bench::Hal {
 public:
  uint32_t millis() override { return ::millis(); }
  void delay_ms(uint32_t ms) override {
    if (g_feed_wdt) esp_task_wdt_reset();
    ::delay(ms);
  }
  void set_test_load(bool on) override { digitalWrite(PIN_TEST_LOAD, on ? HIGH : LOW); }
  bool test_load_pin() override { return digitalRead(PIN_TEST_LOAD) == HIGH; }  // OUTPUT mode keeps the input path enabled

  bench::Io read_tap(int channel, float& volts) override {
    for (int b = 0; b < 4; ++b) digitalWrite(PIN_MUX[b], (channel >> b) & 1);
    delayMicroseconds(MUX_SETTLE_US);
    int16_t raw = 0;
    for (int pass = 0; pass < 2; ++pass) {  // first conversion after switching is discarded
      // single shot, AIN0 vs GND, +/-4.096 V, 860 SPS, comparator off
      bench::Io st = write16(ADS_ADDR, 0x01, 0xC3E3);
      if (st != bench::Io::Ok) return st;
      uint32_t t0 = micros();
      uint16_t cfg = 0;
      do {
        st = read16(ADS_ADDR, 0x01, cfg);
        if (st != bench::Io::Ok) return st;
        if (micros() - t0 > 5000) return bench::Io::Timeout;
      } while (!(cfg & 0x8000));
      uint16_t v;
      st = read16(ADS_ADDR, 0x00, v);
      if (st != bench::Io::Ok) return st;
      raw = (int16_t)v;
    }
    float adc_v = raw * 4.096f / 32768.0f;
    if (raw >= 32767 || adc_v >= ADC_SATURATION_V) return bench::Io::Saturated;
    volts = adc_v * TAP_DIVIDER;
    return bench::Io::Ok;
  }

  bench::Io read_current(int channel, float& amps) override {
    uint8_t a = INA_ADDR[channel];
    uint16_t cal = 0;
    bench::Io st = read16(a, 0x05, cal);
    if (st != bench::Io::Ok) return st;
    if (cal != INA_CAL) {  // the INA226 lost its calibration (power glitch): restore it and wait one conversion
      ina_init(channel);
      ::delay(2);
    }
    uint16_t shunt = 0, cur = 0, mask = 0;
    if ((st = read16(a, 0x01, shunt)) != bench::Io::Ok) return st;
    if ((st = read16(a, 0x06, mask)) != bench::Io::Ok) return st;
    int16_t s = (int16_t)shunt;
    if ((mask & 0x0004) || s >= 32760 || s <= -32760) return bench::Io::Overflow;
    if ((st = read16(a, 0x04, cur)) != bench::Io::Ok) return st;
    amps = (int16_t)cur * CURRENT_LSB;
    return bench::Io::Ok;
  }

  bool i2c_recover() override {
    // Free a slave holding SDA low: clock SCL up to 9 times, then a STOP.
    Wire.end();
    pinMode(PIN_SDA, INPUT_PULLUP);
    pinMode(PIN_SCL, OUTPUT_OPEN_DRAIN);
    for (int i = 0; i < 9 && digitalRead(PIN_SDA) == LOW; ++i) {
      digitalWrite(PIN_SCL, LOW);
      delayMicroseconds(5);
      digitalWrite(PIN_SCL, HIGH);
      delayMicroseconds(5);
    }
    pinMode(PIN_SDA, OUTPUT_OPEN_DRAIN);
    digitalWrite(PIN_SDA, LOW);
    delayMicroseconds(5);
    digitalWrite(PIN_SCL, HIGH);
    delayMicroseconds(5);
    digitalWrite(PIN_SDA, HIGH);
    Wire.begin(PIN_SDA, PIN_SCL, 400000);
    Wire.setTimeOut(20);
    ina_init(0);
    ina_init(1);
    return digitalRead(PIN_SDA) == HIGH;
  }

  bool i2c_present(uint8_t addr) override {
    Wire.beginTransmission(addr);
    return Wire.endTransmission() == 0;
  }

  bool watchdog_enabled() override { return esp_task_wdt_status(NULL) == ESP_OK; }
  void watchdog_stop_feeding() override { g_feed_wdt = false; }

  bool load_calibration(bench::Calibration& cal) override {
    Preferences p;
    if (!p.begin("cal", true)) return false;
    bool ok = p.getBytes("blob", &cal, sizeof cal) == sizeof cal;
    p.end();
    cal.id[sizeof cal.id - 1] = '\0';
    return ok;
  }

  bool save_calibration(const bench::Calibration& cal) override {
    Preferences p;
    if (!p.begin("cal", false)) return false;
    bool ok = p.putBytes("blob", &cal, sizeof cal) == sizeof cal;
    p.end();
    return ok;
  }
};

EspHal g_hal;
bench::Bench g_bench(g_hal);
bench::LineReader g_reader(MAX_LINE);

void watchdog_start() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
  esp_task_wdt_config_t cfg = {.timeout_ms = WDT_TIMEOUT_S * 1000, .idle_core_mask = 0, .trigger_panic = true};
  esp_task_wdt_reconfigure(&cfg);
#else
  esp_task_wdt_init(WDT_TIMEOUT_S, true);
#endif
  esp_task_wdt_add(NULL);
}

void send(const std::string& line) {
  Serial.write(line.data(), line.size());
  Serial.write('\n');
}

void led(uint32_t now) {
  uint32_t period = g_bench.inhibited() ? 0 : (g_bench.host_lost() ? 200 : 1000);
  digitalWrite(PIN_LED, period == 0 ? HIGH : ((now / period) & 1));
}

}  // namespace

void setup() {
  // Safe state before anything else: the test load off.
  pinMode(PIN_TEST_LOAD, OUTPUT);
  digitalWrite(PIN_TEST_LOAD, LOW);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_MUX_EN, OUTPUT);
  digitalWrite(PIN_MUX_EN, LOW);
  for (int p : PIN_MUX) pinMode(p, OUTPUT);

  Serial.setRxBufferSize(1024);
  Serial.begin(115200);
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  Wire.setTimeOut(20);
  ina_init(0);
  ina_init(1);
  watchdog_start();
  g_bench.boot(reset_reason());
  send(g_bench.hello());
}

void loop() {
  if (g_feed_wdt) esp_task_wdt_reset();
  while (Serial.available() > 0) {
    int r = g_reader.feed((char)Serial.read());
    if (r == 1) {
      send(g_bench.handle_line(g_reader.line()));
      if (g_bench.watchdog_test_requested()) {
        Serial.flush();
        g_hal.watchdog_stop_feeding();  // the task watchdog resets the board within WDT_TIMEOUT_S
      }
    } else if (r == -1) {
      send(g_bench.overflow_response(g_reader.max_len()));
    }
  }
  for (const std::string& ev : g_bench.tick()) send(ev);
  led(millis());
  delay(1);
}
