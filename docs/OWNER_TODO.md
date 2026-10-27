# Owner to-do

Everything that needs hands, money or a meter. Work top to bottom; each step says what to do, where, and what to type. Wiring details are in `docs/HARDWARE.md`. The same items appear in the GUI's Project tracker page, where status can be updated.

Commands are run from the repository folder (`fault-diagnosis/`) on the laptop.

## A. Buy and borrow

1. **Buy the bench parts** (fill in supplier, price in NGN and date in the GUI tracker as you go):
   - 2 x ESP32 DevKit v1 (38-pin, CP2102 or CH340 USB) and 2 x data-capable micro-USB cables
   - 1 x ADS1115 module, 1 x CD74HC4067 16-channel multiplexer module, 2 x INA226 modules
   - Shunts: 3 x 0.01 ohm 1 % 3 W, 2 x 0.005 ohm 1 % 3 W (the split source shunt for H4)
   - Dividers: 5 x 40.2 k 1 %, 5 x 10 k 1 %, 3 x 1.8 k 1 %, 5 x 100 nF ceramic, 5 x BAT54S, 2 x 4.7 k (I2C pull-ups)
   - Loads: 12 V DC motor (armature about 4 ohm), 12 V 10 W lamp and holder, 24 ohm 10 W resistor
   - Test load: 4 ohm 50 W aluminium-clad resistor and a heatsink for it
   - Test-load switch: IRF9540N P-MOSFET, 2N2222 NPN, 1 k, 10 k, 100 k resistors
   - Fault elements: 0.22 ohm 5 W, 1.5 ohm 10 W, 1.0 ohm 10 W, 0.47 ohm 10 W, 6.8 ohm 50 W aluminium-clad with heatsink, 4.7 ohm 25 W (for the motor-channel calibration)
   - 2 x 8-channel 5 V relay modules (SRD-05VDC-SL-C, active-low inputs, JD-VCC jumper)
   - 12 V push solenoid (about 10 mm stroke) and a 12 V 1 A adapter for it, plus a slotted disc for the motor shaft (the F3 brake)
   - Blade fuses and inline holders: 15 A, 10 A, 2 x 5 A, 3 A, 2 A, 1 A
   - Terminal blocks or bus bars (src, bus, ret), 1.5 mm2 wire for power paths, thin wire for sense leads, stripboard
2. **The battery:** a 12 V lead-acid battery, 7 to 12 Ah. The experiment wants an aged one; also keep a charger to bring it to full charge before each run day (disconnected during runs).
3. **Request lab equipment** (record each request in the GUI tracker):
   - Adjustable DC bench supply, 0 to 15 V, at least 3 A (calibration)
   - Handheld digital multimeter with a 10 A range (calibration and the human probe); note its make and model
   - Soldering station, and access to a bench for a few days

## B. Boards first (no bench wiring yet)

4. **Install the firmware tools once** (needs internet only this once):
   `python3.12 -m venv .pio-venv && .pio-venv/bin/pip install platformio==6.1.18`
5. **Flash the bench ESP32.** Plug it in alone and find its port: `.pio-venv/bin/pio device list` (it looks like `/dev/cu.usbserial-0001` or `/dev/cu.SLAB_USBtoUART`). Then:
   `cd firmware/bench && ../../.pio-venv/bin/pio run -e esp32dev -t upload --upload-port <port> && cd ../..`
6. **Run the firmware self-test on the bare bench board** (nothing connected to it except USB):
   `.venv/bin/python scripts/fw_selftest.py --port <port> --watchdog`
   Expected: every line PASS except `firmware_self_test`, `adc_present` and `ina226_src_present`, which fail until the ADC and the source INA226 are wired (step 9). The result file goes to `results/fw-selftest/`. Do not go further if `heartbeat_cutoff`, `safe_state_latches`, `boot_safe_state` or `watchdog_reset` fail: report it as an issue in the tracker.
7. **Flash the injector ESP32** the same way, from `firmware/injector`. Then with only the relay boards attached (no bench):
   `.venv/bin/python scripts/injector_check.py --port <injector port> --hold 2`
   Every relay should click on and off in turn, and the script should end with "injector protocol OK".
8. **Label both USB cables** (bench, injector) so the ports are not swapped later.

## C. Build the bench (battery disconnected throughout)

9. **Instrumentation board:** wire the ADS1115, the multiplexer and both INA226 modules to the bench ESP32 as in `docs/HARDWARE.md` (pin map table). Tie multiplexer channel 15 to ground. Re-run step 6: now every line must PASS, including `firmware_self_test`.
10. **Power wiring:** battery post fuse, the F1 site, INA226 #1 split shunt, src terminal block, the H3 site, main fuse, bus. Then each branch with its fuse: motor (F2 site, motor tap, INA226 #2 shunt), lamp (F4 contact, lamp tap), load (load tap), test load (P-MOSFET high side), H2 branch. All loads return to the ret bar; ret goes to battery negative through the H1 site.
11. **Taps:** one divider per tap point (40.2 k top, 10 k bottom with the F5 1.8 k site on motor, lamp and load), 100 nF and BAT54S at each divider output, outputs to multiplexer channels 0 to 4 in the order src, bus, motor, lamp, load.
12. **Ground:** join the bench ESP32 GND and the injector ESP32 GND to battery negative at one point only (star point). Do not power either ESP32 from the battery.
13. **Injector relays to the fault sites** as in the injector table in `docs/HARDWARE.md`. With the battery still disconnected, run `scripts/injector_check.py --hold 10` and, for each fault, measure resistance across the site with the meter: it must change as the script says.
14. **Before the first power-up:** check with the meter (battery disconnected) that there is no short from bus to ret and from src to battery negative, and that every fuse is the right rating.
15. **First power-up:** connect the battery, leave the injector unplugged. Run step 6 again with the bench wired; it must pass. The test load must stay off (it does: nothing has asked for a load test).
16. **Take photos** (whole bench, tap board, shunts and sense leads, relay boards, star ground) and put them in `docs/photos/`.

## D. Calibrate

17. **Calibrate taps and current sensors** with the lab supply and the meter:
   `.venv/bin/python scripts/calibrate.py --port <bench port> --meter "<meter make and model>"`
   Disconnect the battery and connect the lab supply to the battery terminals. The script asks for the supply at 6, 10 and 13 V; at each, it tells you where to put the meter leads and asks you to type the reading (volts, or amps on the 10 A range for the current channels). For the motor current channel it asks you to swap the motor for the 4.7 ohm 25 W resistor. The record is saved in `calibration/` and loaded into the board. Repeat every 30 days or after any rewiring; hardware runs refuse an older calibration.
18. **Re-fit the noise values:** `.venv/bin/python scripts/refit_noise.py`, then follow `docs/RUNBOOK.md` ("After calibration") to apply them and record before and after in `docs/DECISIONS.md`.

## E. Run

19. **Charge the battery fully**, then disconnect the charger.
20. **Healthy check:** `.venv/bin/python scripts/run_hardware.py --port <bench port> --injector-port <injector port> --methods bayes --scenarios healthy --trials 2`. Expect "healthy" both times. If not, the simulator's healthy values do not match the bench (see `docs/OPEN_QUESTIONS.md`); report it before going on.
21. **Development hardware run** (no frozen held-out faults):
   `.venv/bin/python scripts/run_hardware.py --port <bench port> --injector-port <injector port> --methods fixed,tree,bayes,llm,hybrid --model qwen3:8b --trials 3`
   When the script asks for a human probe, put the meter leads where it says and type the reading within the time shown.
22. **Final hardware run** (includes H3 and H4, once only): the same command with `--final`.
23. Results go to `results/hardware/<date-time>/` with `summary.md`. Tell the maintainer, so `docs/PROGRESS.md` gets the hardware table.

## F. Optional: the Jev comparison (cloud, needs internet and a paid key)

24. **Get a TypeSafe API key** from the TypeSafe console (https://console.typesafe.ai). It is paid by use: $0.042 per million input tokens on 27 Oct 2026. Our rough estimate for the full comparison below is about 20 million tokens, so about one dollar; the short test prints the tokens it used. One guide said new sign-ups were paused in September 2026, so this may take a wait. Keep the key out of every file in this folder and out of chats.
25. **Put the key in the terminal you will run from** (only that terminal sees it): `export TYPESAFE_API_KEY=<your key>`. Check the internet is on.
26. **Short test** (21 sessions, the same as the Phase 1 benchmark):
   `.venv/bin/python scripts/bench_models.py --server jev --models jev-1.14.0`
   It stops at once with a clear message if the key is missing or refused.
27. **Full comparison in simulation** (D and E with Jev, the same 17 scenarios and seeds as the qwen3:8b run):
   `.venv/bin/python scripts/run_experiment.py --methods llm,hybrid --server jev --model jev-1.14.0 --trials 10 --final`
   If it stops (internet or service problem), run the same command with `--resume results/<that run's folder>`.
28. **Tell the maintainer** the two results folders (`results/model-bench/<date-time>/` and `results/<date-time>/`), so `docs/PROGRESS.md` gets the comparison.
29. **Replace the key.** Steps 24 to 28 were done on 27 Oct 2026 with the owner's key (`results/model-bench/20261027-103049`, `results/20261027-103259`; written up in `docs/PROGRESS.md`). That key was pasted into a chat, so in the TypeSafe console (https://console.typesafe.ai) revoke it and make a new one before any further Jev run. While there, check the usage page: it showed no requests during the run although Jev answered all of them. A tagged test request was sent at 09:52:38 UTC on 27 Oct 2026, request id `req_01a17cc6b35c76a38b1f3685a9b7832b`; if it does not appear either, the page is showing a different key or workspace.

To watch a Jev session in the dashboard, start it from the same terminal after step 25 (`.venv/bin/python -m gui --open`), then on Live session choose Model server "Jev (TypeSafe cloud)" and Model `jev-1.14.0`.
