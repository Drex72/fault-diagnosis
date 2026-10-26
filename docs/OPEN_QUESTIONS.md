# Open questions

Questions that need the owner or a supervisor. Where a safety answer was unclear, the most conservative option was taken and is noted here.

## 2026-10-26 (Phase 3, hardware design)

1. **Test-load duty limit.** The firmware allows at most 10 % duty on the 4 ohm 50 W test resistor (off-time at least 9 times the on-time, never under 2 s). A 2 s steady test therefore needs 18 s of cool-down, which the host waits out. This is conservative for an aluminium-clad resistor on a heatsink; a supervisor may relax it once the resistor's temperature has been measured during a session. Until then it stays.
2. **Over-current limit 8 A.** Taken from the policy. In compound scenarios (F1 plus a stalled motor, with the test load on) the source current comes close to it, so some load tests may be aborted by the firmware. That is a correct safety outcome, but it changes what the methods can measure on hardware. Not changed.
3. **H2 resistor heat.** 6.8 ohm across 12.6 V dissipates about 23 W. Specified as a 50 W aluminium-clad resistor on a heatsink, switched in only during H2 sessions. A thermal fuse on the heatsink would be safer still; owner to decide.
4. **F3 stall mechanism.** A solenoid pushing a pin into a slotted disc on the shaft is proposed. If that is hard to build, the conservative fallback is a manual stall: the run script would prompt the technician to clamp the shaft. That would need a small change to `run_hardware.py` (prompt instead of relay). Owner to say which.
5. **Relay contact resistance.** Bypass contacts on F1, F2, H1 and H3 add tens of milliohms to healthy paths. The simulator's healthy values may then be off, and the engine may see a healthy bench as slightly faulty. Plan: after calibration, run healthy sessions, compare with the simulator, and if needed refit the healthy parameters (recorded in `docs/DECISIONS.md`). Not done yet: no hardware.
6. **Tap over-voltage.** A charger left connected can push the bus above 14 V; the dividers reach ADC full scale at 16.4 V and each output has a BAT54S clamp. The run checklist says to disconnect the charger. Owner to confirm the charger type.
7. **Board power from USB.** Both ESP32 boards are powered from the laptop. If a run uses a USB hub, it must be a powered hub; an unpowered hub may brown out the injector's relay coils.
