#include "injcore.h"

#include <ArduinoJson.h>
#include <string.h>

namespace inj {

const FaultDef kFaultDefs[kFaults] = {
    {"F1", 0, "0.22 ohm in series with the battery positive (bypass relay opens)"},
    {"F2", 1, "1.5 ohm in series with the motor connection (bypass relay opens)"},
    {"F3", 2, "solenoid brake locks the motor shaft"},
    {"F4", 3, "lamp feed interrupter, opened at random (intermittent)"},
    {"F5@motor", 4, "1.8 kohm added to the bottom leg of the motor tap divider (reads about 1.7 V high at 12 V)"},
    {"F5@lamp", 5, "1.8 kohm added to the bottom leg of the lamp tap divider (reads about 1.7 V high at 12 V)"},
    {"F5@load", 6, "1.8 kohm added to the bottom leg of the load tap divider (reads about 1.7 V high at 12 V)"},
    {"H1", 7, "1.0 ohm in the shared return to battery negative (bypass relay opens)"},
    {"H2", 8, "6.8 ohm 50 W aluminium-clad resistor on a heatsink, across the bus on an unmonitored branch"},
    {"H3", 9, "0.47 ohm in the main feed between battery and bus (bypass relay opens)"},
    {"H4", 10, "moves the source INA226 VIN- sense lead to the midpoint of the split shunt (reads half)"},
};

namespace {
int find_fault(const char* name) {
  if (!name) return -1;
  for (int i = 0; i < kFaults; ++i)
    if (strcmp(name, kFaultDefs[i].name) == 0) return i;
  return -1;
}
std::string dump(const JsonDocument& d) {
  std::string s;
  serializeJson(d, s);
  return s;
}
}  // namespace

Injector::Injector(Hal& hal, uint32_t host_timeout_ms, uint32_t f4_slot_ms, uint32_t f4_open_pct)
    : hal_(hal), host_timeout_ms_(host_timeout_ms), f4_slot_ms_(f4_slot_ms), f4_open_pct_(f4_open_pct) {}

void Injector::apply() {
  for (int i = 0; i < kFaults; ++i) {
    bool on = active_[i];
    if (i == 3) on = on && lamp_open_;  // F4: the interrupter is only open part of the time
    hal_.set_relay(kFaultDefs[i].relay, on);
  }
}

void Injector::boot(const char* reset_reason) {
  for (int i = 0; i < kFaults; ++i) active_[i] = false;
  lamp_open_ = false;
  for (int r = 0; r < kRelays; ++r) hal_.set_relay(r, false);
  reset_reason_ = reset_reason ? reset_reason : "unknown";
  host_seen_ = false;
  last_host_ms_ = last_slot_ms_ = hal_.millis();
}

std::string Injector::hello() const {
  JsonDocument d;
  d["event"] = "hello";
  d["fw"] = INJ_FW_VERSION;
  d["reset_reason"] = reset_reason_;
  JsonArray f = d["faults"].to<JsonArray>();
  for (const FaultDef& fd : kFaultDefs) f.add(fd.name);
  return dump(d);
}

std::string Injector::handle_line(const std::string& line) {
  JsonDocument req, resp;
  DeserializationError err = deserializeJson(req, line);
  if (err || !req.is<JsonObject>()) {
    resp["id"] = nullptr;
    resp["status"] = "error";
    resp["error"] = "bad json";
    return dump(resp);
  }
  resp["id"] = req["id"];
  host_seen_ = true;
  last_host_ms_ = hal_.millis();
  const char* cmd = req["cmd"].as<const char*>();
  std::string error;
  if (!cmd) {
    error = "missing cmd";
  } else if (strcmp(cmd, "set") == 0) {
    bool next[kFaults] = {};
    JsonArrayConst list = req["faults"].as<JsonArrayConst>();
    int n = 0;
    for (JsonVariantConst v : list) {
      int i = find_fault(v.as<const char*>());
      if (i < 0) {
        error = std::string("unknown fault: ") + (v.as<const char*>() ? v.as<const char*>() : "?");
        break;
      }
      next[i] = true;
      ++n;
    }
    if (error.empty() && n > kMaxActive) error = "at most 3 faults at once";
    if (error.empty()) {  // all or nothing
      for (int i = 0; i < kFaults; ++i) active_[i] = next[i];
      lamp_open_ = false;
      apply();
    }
  } else if (strcmp(cmd, "clear") == 0) {
    for (int i = 0; i < kFaults; ++i) active_[i] = false;
    lamp_open_ = false;
    apply();
  } else if (strcmp(cmd, "status") != 0 && strcmp(cmd, "heartbeat") != 0) {
    error = std::string("unknown cmd: ") + cmd;
  }
  resp["status"] = error.empty() ? "ok" : "error";
  if (!error.empty()) resp["error"] = error;
  JsonArray a = resp["data"]["active"].to<JsonArray>();
  for (int i = 0; i < kFaults; ++i)
    if (active_[i]) a.add(kFaultDefs[i].name);
  return dump(resp);
}

std::vector<std::string> Injector::tick() {
  std::vector<std::string> ev;
  uint32_t now = hal_.millis();
  if (host_seen_ && now - last_host_ms_ > host_timeout_ms_) {
    host_seen_ = false;
    bool any = false;
    for (int i = 0; i < kFaults; ++i) any = any || active_[i];
    for (int i = 0; i < kFaults; ++i) active_[i] = false;
    lamp_open_ = false;
    apply();
    JsonDocument d;
    d["event"] = "host_lost";
    d["cleared"] = any;
    ev.push_back(dump(d));
  }
  if (active_[3] && now - last_slot_ms_ >= f4_slot_ms_) {
    last_slot_ms_ = now;
    lamp_open_ = hal_.random(100) < f4_open_pct_;
    apply();
  }
  return ev;
}

}  // namespace inj
