// Injector logic on the laptop:  pio test -e native
#include <ArduinoJson.h>
#include <unity.h>

#include "injcore.h"

struct FakeHal : inj::Hal {
  uint32_t now = 0;
  bool relay[inj::kRelays] = {};
  uint32_t seq = 0;
  uint32_t millis() override { return now; }
  void set_relay(int ch, bool on) override { relay[ch] = on; }
  uint32_t random(uint32_t n) override { return (seq++ * 37) % n; }
};

static FakeHal* hal;
static inj::Injector* in;

void setUp() {
  hal = new FakeHal();
  for (bool& r : hal->relay) r = true;  // relays in an unknown state before boot
  in = new inj::Injector(*hal);
  in->boot("poweron");
}
void tearDown() {
  delete in;
  delete hal;
}

static JsonDocument call(const char* line) {
  JsonDocument d;
  deserializeJson(d, in->handle_line(line));
  return d;
}

void test_boot_releases_every_relay() {
  for (bool r : hal->relay) TEST_ASSERT_FALSE(r);
  JsonDocument d;
  deserializeJson(d, in->hello());
  TEST_ASSERT_EQUAL_STRING("injector-native", d["fw"]);
  TEST_ASSERT_EQUAL(inj::kFaults, (int)d["faults"].size());
}

void test_set_is_all_or_nothing() {
  JsonDocument d = call(R"({"id":1,"cmd":"set","faults":["F2","F5@motor"]})");
  TEST_ASSERT_EQUAL_STRING("ok", d["status"]);
  TEST_ASSERT_TRUE(hal->relay[1] && hal->relay[4]);
  d = call(R"({"id":2,"cmd":"set","faults":["F1","F9"]})");
  TEST_ASSERT_EQUAL_STRING("error", d["status"]);
  TEST_ASSERT_TRUE(hal->relay[1] && hal->relay[4] && !hal->relay[0]);  // unchanged
  d = call(R"({"id":3,"cmd":"set","faults":["F1","F2","F3","H1"]})");
  TEST_ASSERT_EQUAL_STRING("at most 3 faults at once", d["error"]);
  call(R"({"id":4,"cmd":"clear"})");
  for (bool r : hal->relay) TEST_ASSERT_FALSE(r);
}

void test_intermittent_lamp_toggles() {
  call(R"({"id":1,"cmd":"set","faults":["F4"]})");
  int opened = 0, closed = 0;
  for (int i = 0; i < 50; ++i) {
    hal->now += 300;
    in->tick();
    (hal->relay[3] ? opened : closed)++;
  }
  TEST_ASSERT_TRUE(opened > 5 && closed > 5);
}

void test_host_loss_returns_to_healthy() {
  call(R"({"id":1,"cmd":"set","faults":["H2"]})");
  TEST_ASSERT_TRUE(hal->relay[8]);
  hal->now += 10001;
  auto ev = in->tick();
  TEST_ASSERT_EQUAL(1, (int)ev.size());
  TEST_ASSERT_FALSE(hal->relay[8]);
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_boot_releases_every_relay);
  RUN_TEST(test_set_is_all_or_nothing);
  RUN_TEST(test_intermittent_lamp_toggles);
  RUN_TEST(test_host_loss_returns_to_healthy);
  return UNITY_END();
}
