#include "benchcore.h"

#include <ArduinoJson.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

namespace bench {

const char* const kTapNames[kTaps] = {"src", "bus", "motor", "lamp", "load"};
const char* const kCurrentNames[kCurrents] = {"src", "motor"};

struct Bench::Result {
  const char* status = "ok";  // ok | error | timeout | safety_abort
  std::string error;
  JsonDocument data;

  static Result fail(const char* status, const std::string& msg) {
    Result r;
    r.status = status;
    r.error = msg;
    return r;
  }
};

namespace {

int find(const char* name, const char* const* names, int n) {
  if (!name) return -1;
  for (int i = 0; i < n; ++i)
    if (strcmp(name, names[i]) == 0) return i;
  return -1;
}

double r4(double x) { return round(x * 10000.0) / 10000.0; }

std::string f2(float x) {
  char buf[24];
  snprintf(buf, sizeof buf, "%.2f", x);
  return buf;
}

std::string dump(const JsonDocument& doc) {
  std::string out;
  serializeJson(doc, out);
  return out;
}

}  // namespace

// ---------------------------------------------------------------- line reader
int LineReader::feed(char c) {
  if (c == '\r') return 0;
  if (c == '\n') {
    bool was_overflow = overflow_;
    overflow_ = false;
    if (was_overflow) {
      buf_.clear();
      return -1;
    }
    done_.swap(buf_);
    buf_.clear();
    return done_.empty() ? 0 : 1;
  }
  if (overflow_) return 0;
  if (buf_.size() >= max_) {
    overflow_ = true;
    buf_.clear();
    return 0;
  }
  buf_.push_back(c);
  return 0;
}

// ---------------------------------------------------------------- bench
Bench::Bench(Hal& hal, const Limits& limits) : hal_(hal), lim_(limits) {}

void Bench::all_off() { hal_.set_test_load(false); }

void Bench::boot(const char* reset_reason) {
  all_off();  // safe state first, before anything else can fail
  boot_safe_ = !hal_.test_load_pin();
  reset_reason_ = reset_reason ? reset_reason : "unknown";
  if (!hal_.load_calibration(cal_)) cal_ = Calibration();
  uint32_t now = hal_.millis();
  last_host_ms_ = now;
  last_idle_check_ms_ = now;
  last_load_end_ms_ = now;
  host_lost_ = true;  // no host yet
  inhibited_ = false;
  self_test_passed_ = false;
  wdt_test_ = false;
  had_load_test_ = false;
}

static void put_limits(JsonObject o, const Limits& l) {
  o["pulse_min_ms"] = l.pulse_min_ms;
  o["pulse_max_ms"] = l.pulse_max_ms;
  o["steady_ms"] = l.steady_ms;
  o["cooldown_min_ms"] = l.cooldown_min_ms;
  o["duty_max"] = l.duty_max;
  o["src_limit_a"] = l.src_limit_a;
  o["battery_cutoff_v"] = l.battery_cutoff_v;
  o["heartbeat_timeout_ms"] = l.heartbeat_timeout_ms;
}

std::string Bench::hello() const {
  JsonDocument d;
  d["event"] = "hello";
  d["fw"] = FW_VERSION;
  d["calibration"] = cal_.id;
  d["reset_reason"] = reset_reason_;
  d["boot_safe"] = boot_safe_;
  put_limits(d["limits"].to<JsonObject>(), lim_);
  return dump(d);
}

std::string Bench::overflow_response(size_t max_len) const {
  JsonDocument d;
  d["id"] = nullptr;
  d["status"] = "error";
  d["error"] = "line too long (max " + std::to_string(max_len) + " bytes); request dropped";
  d["data"].to<JsonObject>();
  return dump(d);
}

uint32_t Bench::cooldown_remaining(uint32_t now) const {
  if (!had_load_test_) return 0;
  float needed = last_load_on_ms_ * (1.0f / lim_.duty_max - 1.0f);
  if (needed < lim_.cooldown_min_ms) needed = (float)lim_.cooldown_min_ms;
  uint32_t since = now - last_load_end_ms_;
  return since >= needed ? 0 : (uint32_t)(needed - since);
}

bool Bench::heartbeat_expired(uint32_t now) const { return now - last_host_ms_ > lim_.heartbeat_timeout_ms; }

Io Bench::tap(int idx, float& v, bool raw) {
  Io st = hal_.read_tap(idx, v);
  if (st == Io::Timeout) {  // I2C hang: recover the bus and try once more
    hal_.i2c_recover();
    st = hal_.read_tap(idx, v);
  }
  if (st == Io::Ok && !raw && idx < kTaps) v = v * cal_.tap[idx].gain + cal_.tap[idx].offset;
  return st;
}

Io Bench::current(int idx, float& a, bool raw) {
  Io st = hal_.read_current(idx, a);
  if (st == Io::Timeout) {
    hal_.i2c_recover();
    st = hal_.read_current(idx, a);
  }
  if (st == Io::Ok && !raw) a = a * cal_.current[idx].gain + cal_.current[idx].offset;
  return st;
}

Bench::Result Bench::io_error(Io st, const char* what) {
  switch (st) {
    case Io::Timeout:
      return Result::fail("timeout", std::string("i2c timeout reading ") + what + "; bus recovery attempted");
    case Io::Saturated:
      return Result::fail("error", std::string("adc_saturated: ") + what + " at or above full scale");
    case Io::Overflow:
      return Result::fail("error", std::string("current_overflow: ") + what + " beyond the INA226 range");
    default:
      return Result::fail("error", std::string("i2c error reading ") + what);
  }
}

bool Bench::supply_fault(std::string& why) {
  float s, b;
  if (tap(0, s) != Io::Ok || tap(1, b) != Io::Ok) return false;  // the I/O error is reported by the reading itself
  if (s < lim_.supply_min_v) {
    why = "no_supply: battery tap " + f2(s) + " V, all taps near 0 V (battery disconnected or battery fuse open); "
          "not diagnosable, check the supply";
    return true;
  }
  if (b < lim_.bus_dead_v) {
    why = "main_fuse_open: bus " + f2(b) + " V with the battery at " + f2(s) +
          " V; not diagnosable, check the main fuse";
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- tools
Bench::Result Bench::tool_read_voltage(const char* point) {
  int i = find(point, kTapNames, kTaps);
  if (i < 0) return Result::fail("error", "unknown point");
  float v;
  Io st = tap(i, v);
  if (st != Io::Ok) return io_error(st, kTapNames[i]);
  std::string why;
  if (v < lim_.supply_min_v && supply_fault(why)) return Result::fail("error", why);
  Result r;
  r.data["volts"] = r4(v);
  return r;
}

Bench::Result Bench::tool_read_current(const char* channel) {
  int i = find(channel, kCurrentNames, kCurrents);
  if (i < 0) return Result::fail("error", "unknown channel");
  float a;
  Io st = current(i, a);
  if (st != Io::Ok) return io_error(st, kCurrentNames[i]);
  Result r;
  r.data["amps"] = r4(a);
  return r;
}

Bench::Result Bench::tool_load_test(const char* mode, long width_ms, bool has_width) {
  bool pulse = mode && strcmp(mode, "pulse") == 0;
  bool steady = mode && strcmp(mode, "steady") == 0;
  if (!pulse && !steady) return Result::fail("error", "mode must be pulse or steady");
  long w = has_width ? width_ms : (long)lim_.pulse_default_ms;
  if (w < (long)lim_.pulse_min_ms || w > (long)lim_.pulse_max_ms)
    return Result::fail("error", "width_ms outside " + std::to_string(lim_.pulse_min_ms) + ".." +
                                     std::to_string(lim_.pulse_max_ms));
  uint32_t on_ms = pulse ? (uint32_t)w : lim_.steady_ms;
  if (!self_test_passed_)
    return Result::fail("error", "refused: the safety self-test has not passed since boot; send self_test first");
  if (inhibited_) return Result::fail("error", "inhibited: safe state latched; send resume to allow load tests");
  if (host_lost_) return Result::fail("error", "refused: no host heartbeat");
  uint32_t wait = cooldown_remaining(hal_.millis());
  if (wait) return Result::fail("error", "cooldown: " + std::to_string(wait) + " ms left (test resistor thermal limit)");

  float vb, ib;
  Io st = tap(0, vb);
  if (st != Io::Ok) return io_error(st, "src");
  st = current(0, ib);
  if (st != Io::Ok) return io_error(st, "current src");
  std::string why;
  if (vb < lim_.supply_min_v && supply_fault(why)) return Result::fail("error", why);
  if (vb < lim_.battery_cutoff_v)
    return Result::fail("error", "battery_low: " + f2(vb) + " V is below the " + f2(lim_.battery_cutoff_v) +
                                     " V cutoff; load test refused");
  if (ib > lim_.src_limit_a) {
    all_off();
    return Result::fail("safety_abort", "source current " + f2(ib) + " A over the limit before the test");
  }

  uint32_t t_on = hal_.millis();
  auto finish = [&]() {
    all_off();
    uint32_t now = hal_.millis();
    last_load_on_ms_ = now - t_on;
    last_load_end_ms_ = now;
    had_load_test_ = true;
  };
  hal_.set_test_load(true);
  float v_imm = 0, i_dur = 0, v_end = 0;
  hal_.delay_ms(2);  // gate and ADC settling
  st = tap(0, v_imm);
  if (st == Io::Ok) st = current(0, i_dur);
  if (st != Io::Ok) {
    finish();
    return io_error(st, "src during the load test");
  }
  if (i_dur > lim_.src_limit_a) {
    finish();
    return Result::fail("safety_abort", "source current " + f2(i_dur) + " A over the limit; test load switched off");
  }
  while (hal_.millis() - t_on < on_ms) {
    uint32_t left = on_ms - (hal_.millis() - t_on);
    hal_.delay_ms(left < 5 ? left : 5);
    float i_now;
    st = current(0, i_now);
    if (st != Io::Ok) {
      finish();
      return io_error(st, "current src during the load test");
    }
    if (i_now > lim_.src_limit_a) {
      finish();
      return Result::fail("safety_abort", "source current " + f2(i_now) + " A over the limit; test load switched off");
    }
    if (steady) i_dur = i_now;  // steady reports the current at the end of the on-time
  }
  st = tap(0, v_end);
  finish();
  if (hal_.test_load_pin()) return Result::fail("safety_abort", "test load drive still on after switch-off");
  if (st != Io::Ok) return io_error(st, "src at the end of the load test");

  Result r;
  r.data["v_src_before"] = r4(vb);
  r.data["i_src_before"] = r4(ib);
  if (pulse) {
    r.data["v_src_immediate"] = r4(v_imm);
    r.data["v_src_end"] = r4(v_end);
  } else {
    r.data["v_src_during"] = r4(v_end);
  }
  r.data["i_src_during"] = r4(i_dur);
  return r;
}

Bench::Result Bench::tool_self_test(bool watchdog) {
  Result r;
  JsonObject crit = r.data["critical"].to<JsonObject>();
  JsonObject info = r.data["info"].to<JsonObject>();
  crit["boot_safe_state"] = boot_safe_;
  crit["test_load_off"] = !hal_.test_load_pin();
  crit["watchdog_enabled"] = hal_.watchdog_enabled();
  crit["heartbeat_cutoff_logic"] = heartbeat_expired(last_host_ms_ + lim_.heartbeat_timeout_ms + 1) &&
                                   !heartbeat_expired(last_host_ms_ + lim_.heartbeat_timeout_ms - 1);
  crit["limits_within_policy"] = lim_.pulse_max_ms <= 500 && lim_.src_limit_a <= 8.0f &&
                                 lim_.battery_cutoff_v >= 11.5f && lim_.heartbeat_timeout_ms <= 5000 &&
                                 lim_.duty_max <= 0.10f;
  crit["adc_present"] = hal_.i2c_present(0x48);       // battery cutoff needs the ADC
  crit["ina226_src_present"] = hal_.i2c_present(0x40);  // over-current abort needs it
  info["ina226_motor_present"] = hal_.i2c_present(0x41);
  float g = 99;
  bool gnd_ok = hal_.read_tap(kGroundRefChannel, g) == Io::Ok && fabsf(g) < 0.05f;
  info["adc_ground_reference"] = gnd_ok;
  bool passed = true;
  for (JsonPair kv : crit) passed = passed && kv.value().as<bool>();
  self_test_passed_ = passed;
  r.data["passed"] = passed;
  r.data["watchdog_reset_pending"] = watchdog;
  wdt_test_ = watchdog;
  return r;
}

Bench::Result Bench::tool_info() {
  Result r;
  uint32_t now = hal_.millis();
  r.data["fw"] = FW_VERSION;
  r.data["calibration"] = cal_.id;
  r.data["uptime_ms"] = now;
  r.data["reset_reason"] = reset_reason_;
  r.data["host_lost"] = host_lost_;
  r.data["inhibited"] = inhibited_;
  r.data["self_test_passed"] = self_test_passed_;
  r.data["cooldown_remaining_ms"] = cooldown_remaining(now);
  r.data["test_load_on"] = hal_.test_load_pin();
  put_limits(r.data["limits"].to<JsonObject>(), lim_);
  return r;
}

Bench::Result Bench::tool_calibration(const char* action, const void* args_ptr) {
  JsonObjectConst args = *static_cast<const JsonObjectConst*>(args_ptr);
  if (action && strcmp(action, "clear") == 0) {
    cal_ = Calibration();
    if (!hal_.save_calibration(cal_)) return Result::fail("error", "could not save calibration");
    action = "get";
  }
  if (action && strcmp(action, "set") == 0) {
    Calibration c;
    const char* id = args["id"].as<const char*>();
    if (!id || !*id || strlen(id) >= sizeof c.id) return Result::fail("error", "calibration id missing or too long");
    strncpy(c.id, id, sizeof c.id - 1);
    for (int i = 0; i < kTaps + kCurrents; ++i) {
      bool is_tap = i < kTaps;
      const char* name = is_tap ? kTapNames[i] : kCurrentNames[i - kTaps];
      JsonObjectConst ch = args[is_tap ? "taps" : "currents"][name];
      ChannelCal& cc = is_tap ? c.tap[i] : c.current[i - kTaps];
      if (ch.isNull()) continue;  // channel not calibrated: identity
      cc.gain = ch["gain"] | 1.0f;
      cc.offset = ch["offset"] | 0.0f;
      if (cc.gain < 0.5f || cc.gain > 2.0f || cc.offset < -2.0f || cc.offset > 2.0f)
        return Result::fail("error", std::string("calibration for ") + name + " out of range (gain 0.5..2, offset -2..2)");
    }
    cal_ = c;
    if (!hal_.save_calibration(cal_)) return Result::fail("error", "could not save calibration");
    action = "get";
  }
  if (!action || strcmp(action, "get") != 0) return Result::fail("error", "action must be get, set or clear");
  Result r;
  r.data["id"] = cal_.id;
  JsonObject taps = r.data["taps"].to<JsonObject>();
  for (int i = 0; i < kTaps; ++i) {
    taps[kTapNames[i]]["gain"] = cal_.tap[i].gain;
    taps[kTapNames[i]]["offset"] = cal_.tap[i].offset;
  }
  JsonObject cur = r.data["currents"].to<JsonObject>();
  for (int i = 0; i < kCurrents; ++i) {
    cur[kCurrentNames[i]]["gain"] = cal_.current[i].gain;
    cur[kCurrentNames[i]]["offset"] = cal_.current[i].offset;
  }
  return r;
}

Bench::Result Bench::tool_read_raw(const char* kind, const char* name) {
  Result r;
  float x;
  Io st;
  if (kind && strcmp(kind, "tap") == 0) {
    int i = name && strcmp(name, "gnd") == 0 ? kGroundRefChannel : find(name, kTapNames, kTaps);
    if (i < 0) return Result::fail("error", "unknown tap");
    st = tap(i, x, true);
  } else if (kind && strcmp(kind, "current") == 0) {
    int i = find(name, kCurrentNames, kCurrents);
    if (i < 0) return Result::fail("error", "unknown channel");
    st = current(i, x, true);
  } else {
    return Result::fail("error", "kind must be tap or current");
  }
  if (st != Io::Ok) return io_error(st, name ? name : "?");
  r.data["raw"] = r4(x);
  return r;
}

std::string Bench::handle_line(const std::string& line) {
  JsonDocument req, resp;
  DeserializationError err = deserializeJson(req, line);
  if (err || !req.is<JsonObject>()) {
    resp["id"] = nullptr;
    resp["status"] = "error";
    resp["error"] = err ? std::string("bad json: ") + err.c_str() : std::string("request must be a JSON object");
    resp["data"].to<JsonObject>();
    return dump(resp);
  }
  resp["id"] = req["id"];
  last_host_ms_ = hal_.millis();  // any valid request counts as a heartbeat
  host_lost_ = false;

  const char* tool = req["tool"].as<const char*>();
  JsonObjectConst args = req["args"].as<JsonObjectConst>();
  Result r;
  if (!tool) {
    r = Result::fail("error", "missing tool");
  } else if (strcmp(tool, "read_voltage") == 0) {
    r = tool_read_voltage(args["point"].as<const char*>());
  } else if (strcmp(tool, "read_current") == 0) {
    r = tool_read_current(args["channel"].as<const char*>());
  } else if (strcmp(tool, "load_test") == 0) {
    bool has_w = !args["width_ms"].isNull();
    if (has_w && !args["width_ms"].is<long>()) {
      r = Result::fail("error", "width_ms must be an integer");
    } else {
      r = tool_load_test(args["mode"].as<const char*>(), has_w ? args["width_ms"].as<long>() : 0, has_w);
    }
  } else if (strcmp(tool, "heartbeat") == 0) {
    r.data["uptime_ms"] = hal_.millis();
  } else if (strcmp(tool, "safe_state") == 0) {
    all_off();
    inhibited_ = true;
    r.data["inhibited"] = true;
    r.data["test_load_on"] = hal_.test_load_pin();
  } else if (strcmp(tool, "resume") == 0) {
    inhibited_ = false;
    r.data["inhibited"] = false;
  } else if (strcmp(tool, "self_test") == 0) {
    r = tool_self_test(args["watchdog"] | false);
  } else if (strcmp(tool, "info") == 0) {
    r = tool_info();
  } else if (strcmp(tool, "calibration") == 0) {
    r = tool_calibration(args["action"] | "get", &args);
  } else if (strcmp(tool, "read_raw") == 0) {
    r = tool_read_raw(args["kind"].as<const char*>(), args["name"].as<const char*>());
  } else {
    r = Result::fail("error", std::string("unknown tool: ") + tool);
  }

  resp["status"] = r.status;
  if (r.data.isNull()) {
    resp["data"].to<JsonObject>();
  } else {
    resp["data"] = r.data;
  }
  if (!r.error.empty()) resp["error"] = r.error;
  return dump(resp);
}

std::vector<std::string> Bench::tick() {
  std::vector<std::string> ev;
  uint32_t now = hal_.millis();
  if (!host_lost_ && heartbeat_expired(now)) {
    host_lost_ = true;
    all_off();
    JsonDocument d;
    d["event"] = "heartbeat_lost";
    d["uptime_ms"] = now;
    d["test_load_on"] = hal_.test_load_pin();
    ev.push_back(dump(d));
  }
  if (hal_.test_load_pin()) {  // only a load test may drive it, and that blocks the loop
    all_off();
    JsonDocument d;
    d["event"] = "test_load_forced_off";
    ev.push_back(dump(d));
  }
  if (now - last_idle_check_ms_ >= lim_.idle_check_ms) {
    last_idle_check_ms_ = now;
    float a;
    if (current(0, a) == Io::Ok && a > lim_.src_limit_a) {
      all_off();
      inhibited_ = true;
      JsonDocument d;
      d["event"] = "overcurrent";
      d["amps"] = r4(a);
      d["inhibited"] = true;
      ev.push_back(dump(d));
    }
  }
  return ev;
}

}  // namespace bench
