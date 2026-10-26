# Hardware

The 12 V DC testbench, its controller, and the fault injector. Nothing here has been built yet: the values below are the design that the firmware, the simulator and the scripts assume. Update this file (and add photos under `docs/photos/`) as the bench is built. The step-by-step build order is in `docs/OWNER_TODO.md`.

Status: **not yet built**. No hardware result exists.

## Safety layers

1. The bench ESP32 firmware enforces every electrical limit on its own: test-load pulse cap (500 ms), minimum cool-down (2 s, and off-time at least 9 times the on-time, 10 % duty), abort above 8 A source current, no load test below 11.5 V battery, host heartbeat (3 s, then everything off), safe state on boot and reset, task watchdog (5 s). It refuses any load test until its self-test has passed since boot.
2. The host policy guard (`config/policy.json`) enforces the budget, per-session limits and the same pulse bounds. The host refuses to use firmware whose limits are looser than the policy.
3. Fuses (below) protect the wiring if both of the above fail.
4. The GUI and the AI are never the only protection. Closing the laptop, killing Python or pulling USB leaves the bench safe: the heartbeat cutoff switches the test load off, and the injector releases every fault relay after 10 s.
5. Only 12 V DC. No test on 230 V AC. The battery charger is disconnected during runs.

## Power arrangement

- The bench runs from the 12 V lead-acid battery (the aged battery is part of the experiment).
- **Both ESP32 boards are powered from the laptop's USB, not from the battery.** A motor stall pulls the battery down hard enough to brown out an ESP32 fed from it. The ESP32 grounds join the bench at one point only: battery negative (star point).
- The INA226 and ADS1115 modules run from the bench ESP32's 3.3 V pin.
- Relay coils on the injector modules run from the injector ESP32's 5 V (VIN, from USB), with the JD-VCC jumper fitted. Three relays at most are on at once (firmware limit), about 210 mA.
- The F3 brake solenoid runs from a separate 12 V adapter, not the bench battery, so it never shows up in the bench's source current.

## Bench circuit

Topology (matches `faultdiag/circuit.py`):

```
battery(+) --[F1 0.22R, bypassed]-- src --[H3 0.47R, bypassed]-- [main fuse 10 A] -- bus
bus --[fuse 5 A]--[F2 1.5R, bypassed]-- motor tap -- [INA226 #2 shunt] -- motor -- ret
bus --[fuse 2 A]--[F4 relay NC]------- lamp tap --- 12 V 10 W lamp ------------- ret
bus --[fuse 1 A]---------------------- load tap --- 24R 10 W resistor ----------- ret
bus --[fuse 5 A]-- P-MOSFET (high side) -- 4R 50 W test load --------------------- ret
bus --[fuse 3 A]--[H2 relay NO]-- 6.8R 50 W (unmonitored branch) ---------------- ret
ret --[H1 1.0R, bypassed]-- battery(-)
INA226 #1 shunt in the battery + lead, between the battery post and the src terminal block
```

"Bypassed" means a relay's normally closed contact shorts the resistor: with the injector off or unpowered the bench is healthy.

| Element | Value | Notes |
|---|---|---|
| Battery | 12 V lead-acid, 7 to 12 Ah | the aged one under test; fused at the post |
| Battery post fuse | 15 A blade, inline holder within 10 cm of the post | protects all wiring |
| Main fuse | 10 A blade | between src and bus; its holder is the H3 site |
| Motor | 12 V DC, armature about 4 ohm | stall current about 3 A |
| Lamp | 12 V 10 W | about 14.4 ohm hot |
| Resistive load | 24 ohm 10 W | 0.5 A, 6 W |
| Test load | 4 ohm 50 W aluminium-clad on a heatsink | 3.1 A when on; pulses only, average under 4 W |
| Test-load switch | IRF9540N P-MOSFET, high side, driven by a 2N2222 NPN | GPIO 23 to the NPN base through 1 k, 100 k base pull-down (off during reset), 10 k gate-source pull-up on the P-MOSFET. High side because the test load returns through `ret`: a low-side switch with its source on `ret` would lose gate drive when H1 lifts `ret` by several volts |
| Branch fuses | motor 5 A, lamp 2 A, load 1 A, test load 5 A, H2 branch 3 A | blade fuses |

## Voltage taps

Five taps (src, bus, motor, lamp, load), each a divider into the CD74HC4067 multiplexer, whose common pin goes to ADS1115 AIN0.

| Part | Value |
|---|---|
| Divider top | 40.2 k 1 % |
| Divider bottom | 10 k 1 % (the F5 sites: 1.8 k 1 % in series, bypassed by a relay NC contact) |
| Filter | 100 nF from each divider output to ground |
| Clamp | BAT54S from each divider output to 3.3 V and ground |
| Full scale | 16.4 V at the tap is 3.28 V at the ADC (firmware reports "adc_saturated" at or above this) |
| Multiplexer channel 15 | tied to ground, the self-test reference |

ADS1115: address 0x48, +/-4.096 V range, single shot at 860 SPS, first conversion after a channel change discarded, 500 us settling.

## Current sensors

| Sensor | Address | Shunt | Range |
|---|---|---|---|
| INA226 #1, battery output (src) | 0x40 (A0, A1 to GND) | two 0.005 ohm 1 % 3 W in series (0.01 ohm), with a Kelvin tap at the midpoint for H4 | 8.19 A |
| INA226 #2, motor branch | 0x41 (A0 to VS) | 0.01 ohm 1 % 3 W | 8.19 A |

Current LSB 0.25 mA, calibration register 2048, 332 us conversions. The firmware rewrites the calibration register if a power glitch clears it, reports "current_overflow" when the shunt reading saturates, and recovers a hung I2C bus (9 clocks and a STOP) once before reporting a timeout. Use Kelvin (separate sense) leads from each shunt to the INA226 VIN+ and VIN- pins.

## Bench ESP32 pin map (ESP32 DevKit v1)

| GPIO | Function |
|---|---|
| 21 | I2C SDA (ADS1115, both INA226), 4.7 k pull-up to 3.3 V |
| 22 | I2C SCL, 4.7 k pull-up to 3.3 V |
| 23 | Test-load switch: NPN driver base via 1 k (HIGH = load on; 100 k pull-down keeps it off during reset) |
| 25, 26, 27, 14 | Multiplexer S0, S1, S2, S3 |
| 13 | Multiplexer enable (active low) |
| 2 | Status LED |
| USB | Serial to the laptop at 115200 baud, and power |

## Fault injector

A second ESP32 DevKit v1 drives two 8-channel 5 V relay modules (SRD-05VDC-SL-C contacts, 10 A 30 V DC, inputs active low). De-energised is always healthy. The injector clears every fault if the host is silent for 10 s, and allows at most 3 faults at once. F4 is opened at random in 300 ms slots (about 40 % open).

| Relay | GPIO | Fault | Wiring |
|---|---|---|---|
| 0 | 13 | F1 aged battery | NC contact across 0.22 ohm 5 W, in series between the battery + post and the src terminal block |
| 1 | 27 | F2 motor connection | NC contact across 1.5 ohm 10 W, in series between the bus and the motor tap point |
| 2 | 16 | F3 motor stall | NO contact switches the 12 V brake solenoid (own supply), which locks the motor shaft |
| 3 | 17 | F4 intermittent lamp | NC contact in series with the lamp feed |
| 4 | 18 | F5 at motor tap | NC contact across 1.8 k in series with the motor tap's 10 k bottom leg |
| 5 | 19 | F5 at lamp tap | same, lamp tap |
| 6 | 21 | F5 at load tap | same, load tap |
| 7 | 22 | H1 ground return | NC contact across 1.0 ohm 10 W, in series between the ret bar and battery negative |
| 8 | 23 | H2 hidden load | NO contact connects 6.8 ohm 50 W (on a heatsink) from the bus to the ret bar, through a 3 A fuse |
| 9 | 25 | H3 main wiring | NC contact across 0.47 ohm 10 W, in series between the src terminal block and the main fuse |
| 10 | 26 | H4 source current sensor | changeover contact on INA226 #1 VIN- sense lead: NC to the far end of the shunt, NO to its midpoint |

GPIO 14 is not used on the injector: it toggles during boot and would click a relay.

The tool-failure scenarios (TF1 timeout on the motor tap, TF2 garbage from the motor current sensor) cannot be produced by the bench on demand. They are injected in the host, between the policy guard and the bench (`faultdiag/serialbench.py`, `ToolFailures`), and recorded in the truth file.

F1b (marginal battery) is not run on hardware: one series resistor gives one severity (0.22 ohm plus the battery's own resistance falls in the simulator's "degraded" range).

## Known differences from the simulator

- Relay contacts and wiring add tens of milliohms to every bypassed path (F1, F2, H1, H3 sites). The simulator's healthy values (0.01 to 0.05 ohm) are therefore optimistic. After calibration, run healthy sessions and compare; see `docs/OPEN_QUESTIONS.md`.
- The F5 fault on hardware is a gain error (+13.9 %), not a fixed offset; at 12 V it reads about 1.7 V high, inside the simulator's 1.2 to 2.0 V offset range, but it scales with the tap voltage.
- H4 on hardware halves the source current reading exactly (gain 0.5); the simulator draws 0.45 to 0.65.

## Photos

None yet. Add them to `docs/photos/` once the bench is built: the whole bench, the tap divider board, the shunts with their Kelvin leads, the injector relay boards, and the star ground point.
