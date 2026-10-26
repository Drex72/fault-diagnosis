// Fault injector logic: which relay carries which fault, the command protocol,
// the intermittent lamp, and a host timeout that returns the bench to healthy.
// Relays are wired so that de-energised means no fault: a reset, a crash or a
// lost host always leaves a healthy bench.
#pragma once

#include <stdint.h>

#include <string>
#include <vector>

#ifndef INJ_FW_VERSION
#define INJ_FW_VERSION "injector-dev"
#endif

namespace inj {

struct FaultDef {
  const char* name;
  int relay;
  const char* hardware;  // what the relay switches, for the hello message and docs
};

constexpr int kFaults = 11;
constexpr int kRelays = 11;
constexpr int kMaxActive = 3;
extern const FaultDef kFaultDefs[kFaults];

class Hal {
 public:
  virtual ~Hal() = default;
  virtual uint32_t millis() = 0;
  virtual void set_relay(int channel, bool energised) = 0;
  virtual uint32_t random(uint32_t n) = 0;  // 0..n-1
};

class Injector {
 public:
  explicit Injector(Hal& hal, uint32_t host_timeout_ms = 10000, uint32_t f4_slot_ms = 300, uint32_t f4_open_pct = 40);
  void boot(const char* reset_reason);
  std::string hello() const;
  std::string handle_line(const std::string& line);
  std::vector<std::string> tick();
  bool active(int fault) const { return active_[fault]; }
  bool lamp_open() const { return lamp_open_; }

 private:
  void apply();
  Hal& hal_;
  uint32_t host_timeout_ms_, f4_slot_ms_, f4_open_pct_;
  bool active_[kFaults] = {};
  bool lamp_open_ = false;
  bool host_seen_ = false;
  uint32_t last_host_ms_ = 0, last_slot_ms_ = 0;
  const char* reset_reason_ = "unknown";
};

}  // namespace inj
