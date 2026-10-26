// Firmware safety and protocol logic, tested on the laptop with a fake board.
//   pio test -e native
#include <ArduinoJson.h>
#include <unity.h>

#include <string>

#include "benchcore.h"

using bench::Io;

struct FakeHal : bench::Hal {
  uint32_t now = 1000;
  bool load = false;
  uint32_t load_on_total = 0;  // ms the test load was on
  float tap_v[16] = {12.6f, 12.59f, 12.55f, 12.55f, 12.55f};
  float tap_drop_on = 0.10f;   // src and bus drop with the test load on
  float cur[2] = {1.7f, 0.35f};
  float cur_on = 3.1f;         // extra source current with the test load on
  Io tap_status = Io::Ok, cur_status = Io::Ok;
  int timeouts_left = 0;       // next N reads time out
  int recoveries = 0;
  bool present[256] = {};
  bool wdt = true, wdt_fed = true;
  bench::Calibration stored;
  bool has_stored = false;

  FakeHal() {
    present[0x48] = present[0x40] = present[0x41] = true;
    tap_v[15] = 0.002f;
  }
  uint32_t millis() override { return now; }
  void delay_ms(uint32_t ms) override {
    if (load) load_on_total += ms;
    now += ms;
  }
  void set_test_load(bool on) override { load = on; }
  bool test_load_pin() override { return load; }
  Io read_tap(int ch, float& v) override {
    if (timeouts_left > 0) { --timeouts_left; return Io::Timeout; }
    if (tap_status != Io::Ok) return tap_status;
    v = tap_v[ch] - ((load && ch <= 1) ? tap_drop_on : 0.0f);
    return Io::Ok;
  }
  Io read_current(int ch, float& a) override {
    if (timeouts_left > 0) { --timeouts_left; return Io::Timeout; }
    if (cur_status != Io::Ok) return cur_status;
    a = cur[ch] + ((load && ch == 0) ? cur_on : 0.0f);
    return Io::Ok;
  }
  bool i2c_recover() override { ++recoveries; return true; }
  bool i2c_present(uint8_t a) override { return present[a]; }
  bool watchdog_enabled() override { return wdt; }
  void watchdog_stop_feeding() override { wdt_fed = false; }
  bool load_calibration(bench::Calibration& c) override {
    if (!has_stored) return false;
    c = stored;
    return true;
  }
  bool save_calibration(const bench::Calibration& c) override {
    stored = c;
    has_stored = true;
    return true;
  }
};

static FakeHal* hal;
static bench::Bench* b;

void setUp() {
  hal = new FakeHal();
  b = new bench::Bench(*hal);
  b->boot("poweron");
}
void tearDown() {
  delete b;
  delete hal;
}

static JsonDocument call(const std::string& line) {
  JsonDocument d;
  deserializeJson(d, b->handle_line(line));
  return d;
}
static std::string status(const JsonDocument& d) { return d["status"].as<std::string>(); }
static std::string error(const JsonDocument& d) { return d["error"] | ""; }
static void pass_self_test() { TEST_ASSERT_TRUE(call(R"({"id":1,"tool":"self_test"})")["data"]["passed"]); }

// ---- boot and protocol --------------------------------------------------
void test_boot_forces_safe_state_and_says_hello() {
  FakeHal h;
  h.load = true;  // pin floated high before setup
  bench::Bench bb(h);
  bb.boot("brownout");
  TEST_ASSERT_FALSE(h.load);
  JsonDocument d;
  deserializeJson(d, bb.hello());
  TEST_ASSERT_EQUAL_STRING("hello", d["event"]);
  TEST_ASSERT_EQUAL_STRING("bench-native", d["fw"]);
  TEST_ASSERT_EQUAL_STRING("uncalibrated", d["calibration"]);
  TEST_ASSERT_EQUAL_STRING("brownout", d["reset_reason"]);
  TEST_ASSERT_TRUE(d["boot_safe"]);
  TEST_ASSERT_EQUAL(500, d["limits"]["pulse_max_ms"].as<int>());
}

void test_corrupt_json_and_unknown_tool() {
  JsonDocument d = call(R"({"id":4,"tool":"read_vol)");
  TEST_ASSERT_EQUAL_STRING("error", status(d).c_str());
  TEST_ASSERT_TRUE(d["id"].isNull());
  TEST_ASSERT_TRUE(error(d).rfind("bad json", 0) == 0);
  d = call(R"([1,2])");
  TEST_ASSERT_EQUAL_STRING("error", status(d).c_str());
  d = call(R"({"id":5,"tool":"format_disk","args":{}})");
  TEST_ASSERT_EQUAL(5, d["id"].as<int>());
  TEST_ASSERT_EQUAL_STRING("unknown tool: format_disk", error(d).c_str());
  d = call(R"({"id":6,"tool":"read_voltage","args":{"point":"kitchen"}})");
  TEST_ASSERT_EQUAL_STRING("unknown point", error(d).c_str());
}

void test_readings() {
  JsonDocument d = call(R"({"id":7,"tool":"read_voltage","args":{"point":"bus"}})");
  TEST_ASSERT_EQUAL_STRING("ok", status(d).c_str());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 12.59, d["data"]["volts"].as<float>());
  d = call(R"({"id":"x8","tool":"read_current","args":{"channel":"motor"}})");
  TEST_ASSERT_EQUAL_STRING("x8", d["id"]);
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 0.35, d["data"]["amps"].as<float>());
}

void test_line_reader_drops_overlong_lines() {
  bench::LineReader r(16);
  std::string longline(40, 'a');
  int res = 0;
  for (char c : longline) res = r.feed(c);
  TEST_ASSERT_EQUAL(0, res);
  TEST_ASSERT_EQUAL(-1, r.feed('\n'));
  for (char c : std::string("{\"id\":1}\r")) r.feed(c);
  TEST_ASSERT_EQUAL(1, r.feed('\n'));
  TEST_ASSERT_EQUAL_STRING("{\"id\":1}", r.line().c_str());
  JsonDocument d;
  deserializeJson(d, b->overflow_response(16));
  TEST_ASSERT_TRUE(d["id"].isNull());
}

// ---- load test limits ---------------------------------------------------
void test_load_test_refused_until_self_test_passes() {
  JsonDocument d = call(R"({"id":1,"tool":"load_test","args":{"mode":"pulse"}})");
  TEST_ASSERT_EQUAL_STRING("error", status(d).c_str());
  TEST_ASSERT_FALSE(hal->load);
  TEST_ASSERT_EQUAL(0, hal->load_on_total);
  hal->present[0x40] = false;  // no source current sensor: over-current abort impossible
  TEST_ASSERT_FALSE(call(R"({"id":2,"tool":"self_test"})")["data"]["passed"]);
  TEST_ASSERT_EQUAL_STRING("error", status(call(R"({"id":3,"tool":"load_test","args":{"mode":"pulse"}})")).c_str());
  hal->present[0x40] = true;
  pass_self_test();
  d = call(R"({"id":4,"tool":"load_test","args":{"mode":"pulse","width_ms":100}})");
  TEST_ASSERT_EQUAL_STRING("ok", status(d).c_str());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 12.6, d["data"]["v_src_before"].as<float>());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 12.5, d["data"]["v_src_immediate"].as<float>());
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 4.8, d["data"]["i_src_during"].as<float>());
  TEST_ASSERT_FALSE(hal->load);
}

void test_pulse_width_cap() {
  pass_self_test();
  TEST_ASSERT_EQUAL_STRING("width_ms outside 10..500",
                           error(call(R"({"id":1,"tool":"load_test","args":{"mode":"pulse","width_ms":501}})")).c_str());
  TEST_ASSERT_EQUAL_STRING("width_ms must be an integer",
                           error(call(R"({"id":2,"tool":"load_test","args":{"mode":"pulse","width_ms":"100"}})")).c_str());
  TEST_ASSERT_EQUAL(0, hal->load_on_total);
  call(R"({"id":3,"tool":"load_test","args":{"mode":"pulse","width_ms":500}})");
  TEST_ASSERT_TRUE(hal->load_on_total >= 500 && hal->load_on_total <= 510);
}

void test_cooldown_between_load_tests() {
  pass_self_test();
  TEST_ASSERT_EQUAL_STRING("ok", status(call(R"({"id":1,"tool":"load_test","args":{"mode":"pulse"}})")).c_str());
  JsonDocument d = call(R"({"id":2,"tool":"load_test","args":{"mode":"pulse"}})");
  TEST_ASSERT_TRUE(error(d).rfind("cooldown", 0) == 0);
  hal->now += 2000;
  TEST_ASSERT_EQUAL_STRING("ok", status(call(R"({"id":3,"tool":"load_test","args":{"mode":"steady"}})")).c_str());
  // 2 s on at 10 % duty: 18 s off before the next test
  TEST_ASSERT_UINT32_WITHIN(20, 18000, b->cooldown_remaining(hal->now));
  hal->now += 17000;
  TEST_ASSERT_TRUE(error(call(R"({"id":4,"tool":"load_test","args":{"mode":"pulse"}})")).rfind("cooldown", 0) == 0);
}

void test_battery_cutoff() {
  pass_self_test();
  hal->tap_v[0] = 11.4f;
  JsonDocument d = call(R"({"id":1,"tool":"load_test","args":{"mode":"pulse"}})");
  TEST_ASSERT_TRUE(error(d).rfind("battery_low", 0) == 0);
  TEST_ASSERT_EQUAL(0, hal->load_on_total);
}

void test_overcurrent_aborts_and_switches_off() {
  pass_self_test();
  hal->cur_on = 7.0f;  // 1.7 + 7.0 = 8.7 A with the test load on
  JsonDocument d = call(R"({"id":1,"tool":"load_test","args":{"mode":"steady"}})");
  TEST_ASSERT_EQUAL_STRING("safety_abort", status(d).c_str());
  TEST_ASSERT_FALSE(hal->load);
  TEST_ASSERT_TRUE(hal->load_on_total < 10);  // cut at the first check, not after 2 s
  TEST_ASSERT_TRUE(b->cooldown_remaining(hal->now) > 0);
}

void test_heartbeat_loss_switches_off_and_refuses() {
  pass_self_test();
  hal->now += 3001;
  auto ev = b->tick();
  TEST_ASSERT_EQUAL(1, (int)ev.size());
  TEST_ASSERT_TRUE(ev[0].find("heartbeat_lost") != std::string::npos);
  TEST_ASSERT_TRUE(b->host_lost());
  TEST_ASSERT_TRUE(b->tick().empty());  // reported once
  // the next request is itself a heartbeat, so the host is back
  TEST_ASSERT_EQUAL_STRING("ok", status(call(R"({"id":2,"tool":"heartbeat"})")).c_str());
  TEST_ASSERT_FALSE(b->host_lost());
}

void test_idle_overcurrent_latches_inhibit() {
  pass_self_test();
  hal->cur[0] = 8.5f;
  hal->now += 200;
  auto ev = b->tick();
  TEST_ASSERT_TRUE(ev.size() == 1 && ev[0].find("overcurrent") != std::string::npos);
  TEST_ASSERT_TRUE(b->inhibited());
  hal->cur[0] = 1.7f;
  TEST_ASSERT_TRUE(error(call(R"({"id":1,"tool":"load_test","args":{"mode":"pulse"}})")).rfind("inhibited", 0) == 0);
  call(R"({"id":2,"tool":"resume"})");
  TEST_ASSERT_EQUAL_STRING("ok", status(call(R"({"id":3,"tool":"load_test","args":{"mode":"pulse"}})")).c_str());
}

void test_safe_state_command_latches() {
  pass_self_test();
  JsonDocument d = call(R"({"id":1,"tool":"safe_state"})");
  TEST_ASSERT_TRUE(d["data"]["inhibited"]);
  TEST_ASSERT_FALSE(d["data"]["test_load_on"]);
  TEST_ASSERT_TRUE(error(call(R"({"id":2,"tool":"load_test","args":{"mode":"pulse"}})")).rfind("inhibited", 0) == 0);
}

void test_stuck_drive_is_forced_off() {
  hal->load = true;
  auto ev = b->tick();
  TEST_ASSERT_FALSE(hal->load);
  TEST_ASSERT_TRUE(ev.size() >= 1 && ev[0].find("test_load_forced_off") != std::string::npos);
}

// ---- measurement faults -------------------------------------------------
void test_i2c_timeout_recovers_or_reports() {
  hal->timeouts_left = 1;
  JsonDocument d = call(R"({"id":1,"tool":"read_voltage","args":{"point":"src"}})");
  TEST_ASSERT_EQUAL_STRING("ok", status(d).c_str());
  TEST_ASSERT_EQUAL(1, hal->recoveries);
  hal->timeouts_left = 5;
  d = call(R"({"id":2,"tool":"read_current","args":{"channel":"src"}})");
  TEST_ASSERT_EQUAL_STRING("timeout", status(d).c_str());
  TEST_ASSERT_EQUAL(2, hal->recoveries);
}

void test_adc_saturation_and_ina_overflow() {
  hal->tap_status = Io::Saturated;
  TEST_ASSERT_TRUE(error(call(R"({"id":1,"tool":"read_voltage","args":{"point":"lamp"}})")).rfind("adc_saturated", 0) == 0);
  hal->tap_status = Io::Ok;
  hal->cur_status = Io::Overflow;
  TEST_ASSERT_TRUE(error(call(R"({"id":2,"tool":"read_current","args":{"channel":"src"}})")).rfind("current_overflow", 0) == 0);
}

void test_supply_faults_are_reported_not_diagnosed() {
  hal->tap_v[3] = 0.05f;  // lamp open with a healthy supply: a normal low reading
  TEST_ASSERT_EQUAL_STRING("ok", status(call(R"({"id":1,"tool":"read_voltage","args":{"point":"lamp"}})")).c_str());
  hal->tap_v[1] = hal->tap_v[2] = hal->tap_v[3] = hal->tap_v[4] = 0.02f;  // main fuse open
  TEST_ASSERT_TRUE(error(call(R"({"id":2,"tool":"read_voltage","args":{"point":"motor"}})")).rfind("main_fuse_open", 0) == 0);
  hal->tap_v[0] = 0.01f;  // everything near 0 V
  TEST_ASSERT_TRUE(error(call(R"({"id":3,"tool":"read_voltage","args":{"point":"bus"}})")).rfind("no_supply", 0) == 0);
}

// ---- self-test, calibration ---------------------------------------------
void test_self_test_reports_checks_and_watchdog_request() {
  JsonDocument d = call(R"({"id":1,"tool":"self_test","args":{"watchdog":true}})");
  TEST_ASSERT_TRUE(d["data"]["passed"]);
  TEST_ASSERT_TRUE(d["data"]["critical"]["heartbeat_cutoff_logic"]);
  TEST_ASSERT_TRUE(d["data"]["critical"]["boot_safe_state"]);
  TEST_ASSERT_TRUE(d["data"]["info"]["adc_ground_reference"]);
  TEST_ASSERT_TRUE(b->watchdog_test_requested());
  hal->wdt = false;
  TEST_ASSERT_FALSE(call(R"({"id":2,"tool":"self_test"})")["data"]["passed"]);
}

void test_calibration_set_apply_persist_clear() {
  JsonDocument d = call(
      R"({"id":1,"tool":"calibration","args":{"action":"set","id":"cal-20261026","taps":{"bus":{"gain":1.01,"offset":-0.02}},"currents":{"src":{"gain":0.98,"offset":0.01}}}})");
  TEST_ASSERT_EQUAL_STRING("ok", status(d).c_str());
  TEST_ASSERT_EQUAL_STRING("cal-20261026", d["data"]["id"]);
  d = call(R"({"id":2,"tool":"read_voltage","args":{"point":"bus"}})");
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 12.59 * 1.01 - 0.02, d["data"]["volts"].as<float>());
  d = call(R"({"id":3,"tool":"read_raw","args":{"kind":"tap","name":"bus"}})");
  TEST_ASSERT_FLOAT_WITHIN(1e-3, 12.59, d["data"]["raw"].as<float>());
  bench::Bench again(*hal);  // reboot: calibration comes back from NVS
  again.boot("software");
  TEST_ASSERT_TRUE(again.hello().find("cal-20261026") != std::string::npos);
  d = call(R"({"id":4,"tool":"calibration","args":{"action":"set","id":"bad","taps":{"src":{"gain":3}}}})");
  TEST_ASSERT_EQUAL_STRING("error", status(d).c_str());
  d = call(R"({"id":5,"tool":"calibration","args":{"action":"clear"}})");
  TEST_ASSERT_EQUAL_STRING("uncalibrated", d["data"]["id"]);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_boot_forces_safe_state_and_says_hello);
  RUN_TEST(test_corrupt_json_and_unknown_tool);
  RUN_TEST(test_readings);
  RUN_TEST(test_line_reader_drops_overlong_lines);
  RUN_TEST(test_load_test_refused_until_self_test_passes);
  RUN_TEST(test_pulse_width_cap);
  RUN_TEST(test_cooldown_between_load_tests);
  RUN_TEST(test_battery_cutoff);
  RUN_TEST(test_overcurrent_aborts_and_switches_off);
  RUN_TEST(test_heartbeat_loss_switches_off_and_refuses);
  RUN_TEST(test_idle_overcurrent_latches_inhibit);
  RUN_TEST(test_safe_state_command_latches);
  RUN_TEST(test_stuck_drive_is_forced_off);
  RUN_TEST(test_i2c_timeout_recovers_or_reports);
  RUN_TEST(test_adc_saturation_and_ina_overflow);
  RUN_TEST(test_supply_faults_are_reported_not_diagnosed);
  RUN_TEST(test_self_test_reports_checks_and_watchdog_request);
  RUN_TEST(test_calibration_set_apply_persist_clear);
  return UNITY_END();
}
