// Bench controller logic: the tool contract over newline-delimited JSON and
// every electrical limit, enforced here independently of the host.
//
// This file has no Arduino dependency. The board-specific parts (ADC, INA226,
// multiplexer, GPIO, NVS, watchdog) sit behind the Hal interface, so the same
// logic is unit-tested on the laptop (pio test -e native) with a fake Hal.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <string>
#include <vector>

#ifndef FW_VERSION
#define FW_VERSION "bench-dev"
#endif

namespace bench {

// Electrical limits. They mirror config/policy.json; the host refuses to run if
// the firmware reports looser limits than the policy.
struct Limits {
  uint32_t pulse_min_ms = 10;
  uint32_t pulse_max_ms = 500;        // test-load pulse width cap
  uint32_t pulse_default_ms = 100;
  uint32_t steady_ms = 2000;          // steady load test on-time (fixed)
  uint32_t cooldown_min_ms = 2000;    // minimum off-time between load tests
  float duty_max = 0.10f;             // off-time >= on-time * (1/duty - 1): thermal limit on the test resistor
  float src_limit_a = 8.0f;           // abort and switch off above this source current
  float battery_cutoff_v = 11.5f;     // refuse load tests below this battery voltage
  uint32_t heartbeat_timeout_ms = 3000;
  uint32_t idle_check_ms = 100;       // over-current check interval while idle
  float supply_min_v = 1.0f;          // battery tap below this: no supply
  float bus_dead_v = 0.5f;            // bus below this with a healthy battery: main fuse open
};

constexpr int kTaps = 5;      // src, bus, motor, lamp, load
constexpr int kCurrents = 2;  // src, motor
constexpr int kGroundRefChannel = 15;  // multiplexer channel tied to ground, for the self-test
extern const char* const kTapNames[kTaps];
extern const char* const kCurrentNames[kCurrents];

enum class Io { Ok, Timeout, Saturated, Overflow, Error };

struct ChannelCal {
  float gain = 1.0f;
  float offset = 0.0f;
};

struct Calibration {
  char id[48] = "uncalibrated";
  ChannelCal tap[kTaps];
  ChannelCal current[kCurrents];
};

// Everything that touches the board.
class Hal {
 public:
  virtual ~Hal() = default;
  virtual uint32_t millis() = 0;
  virtual void delay_ms(uint32_t ms) = 0;
  virtual void set_test_load(bool on) = 0;
  virtual bool test_load_pin() = 0;  // read back the gate drive pin
  // Tap voltage at the bench (nominal divider applied), multiplexer channel 0..15.
  virtual Io read_tap(int channel, float& volts) = 0;
  // Current in amps from INA226 channel 0 (src) or 1 (motor).
  virtual Io read_current(int channel, float& amps) = 0;
  virtual bool i2c_recover() = 0;
  virtual bool i2c_present(uint8_t addr) = 0;
  virtual bool watchdog_enabled() = 0;
  virtual void watchdog_stop_feeding() = 0;  // self-test: let the watchdog reset the board
  virtual bool load_calibration(Calibration& cal) = 0;
  virtual bool save_calibration(const Calibration& cal) = 0;
};

// Assembles request lines from serial bytes, discarding lines that are too long.
class LineReader {
 public:
  explicit LineReader(size_t max_len = 512) : max_(max_len) {}
  // 1: a complete line is in line(); -1: a line overflowed and was dropped; 0: need more bytes.
  int feed(char c);
  const std::string& line() const { return done_; }
  size_t max_len() const { return max_; }

 private:
  size_t max_;
  std::string buf_, done_;
  bool overflow_ = false;
};

class Bench {
 public:
  explicit Bench(Hal& hal, const Limits& limits = Limits());

  // Call first thing after reset: every switched load off.
  void boot(const char* reset_reason);
  std::string hello() const;

  // One request line in, one response line out (no trailing newline).
  std::string handle_line(const std::string& line);
  std::string overflow_response(size_t max_len) const;
  // Periodic housekeeping: heartbeat timeout and idle over-current check.
  // Returns event lines to send (possibly none).
  std::vector<std::string> tick();

  bool host_lost() const { return host_lost_; }
  bool inhibited() const { return inhibited_; }
  bool self_test_passed() const { return self_test_passed_; }
  bool watchdog_test_requested() const { return wdt_test_; }
  const Limits& limits() const { return lim_; }
  uint32_t cooldown_remaining(uint32_t now) const;
  bool heartbeat_expired(uint32_t now) const;

  struct Result;

 private:
  Result tool_read_voltage(const char* point);
  Result tool_read_current(const char* channel);
  Result tool_load_test(const char* mode, long width_ms, bool has_width);
  Result tool_self_test(bool watchdog);
  Result tool_info();
  Result tool_calibration(const char* action, const void* args);
  Result tool_read_raw(const char* kind, const char* name);

  Io tap(int idx, float& v, bool raw = false);
  Io current(int idx, float& a, bool raw = false);
  Result io_error(Io st, const char* what);
  bool supply_fault(std::string& why);
  void all_off();

  Hal& hal_;
  Limits lim_;
  Calibration cal_;
  const char* reset_reason_ = "unknown";
  bool boot_safe_ = false;      // test-load pin read back off at boot
  bool host_lost_ = false;
  bool inhibited_ = false;      // safe_state latched until resume
  bool self_test_passed_ = false;
  bool wdt_test_ = false;
  bool had_load_test_ = false;
  uint32_t last_host_ms_ = 0;
  uint32_t last_idle_check_ms_ = 0;
  uint32_t last_load_end_ms_ = 0;
  uint32_t last_load_on_ms_ = 0;
};

}  // namespace bench
