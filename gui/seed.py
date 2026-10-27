"""First-run contents of the project tracker (editable in the GUI afterwards).

The 14-day plan is a proposal built from docs/OWNER_TODO.md (no plan existed
in the repository); the step numbers refer to that file. Prices are left for
the owner: none are invented here.
"""

_PLAN = [
    ("Order the bench parts; request the lab supply, meter and soldering station", "OWNER_TODO steps 1 to 3"),
    ("Install PlatformIO, flash both boards, bare-board self-test, injector check", "steps 4 to 8"),
    ("Instrumentation board (ADS1115, multiplexer, INA226); self-test passes", "step 9"),
    ("Power wiring, fuses and the F1, H3 sites", "step 10"),
    ("Tap dividers, filters and clamps; star ground", "steps 11 and 12"),
    ("Injector relays to the fault sites; resistance checks", "step 13"),
    ("Pre-power checks, first power-up, wired self-test, photos", "steps 14 to 16"),
    ("Calibration with the lab supply and the meter", "step 17"),
    ("Noise refit and the healthy-bench check", "steps 18 to 20"),
    ("Development hardware run, part 1", "step 21"),
    ("Development hardware run, part 2, and review", "step 21"),
    ("Final hardware run with H3 and H4 (once)", "step 22"),
    ("Hardware results into docs/PROGRESS.md; compare with simulation", "step 23"),
    ("Demo rehearsal from cold with docs/RUNBOOK.md", "RUNBOOK"),
]

_BOM = [
    ("ESP32 DevKit v1 (38-pin, CP2102 or CH340) with data USB cable", 2),
    ("ADS1115 16-bit ADC module", 1),
    ("CD74HC4067 16-channel multiplexer module", 1),
    ("INA226 current sensor module", 2),
    ("Shunt 0.01 ohm 1 % 3 W", 3),
    ("Shunt 0.005 ohm 1 % 3 W (split source shunt for H4)", 2),
    ("Resistor 40.2 k 1 % (divider top)", 5),
    ("Resistor 10 k 1 % (divider bottom)", 5),
    ("Resistor 1.8 k 1 % (F5 sites)", 3),
    ("Capacitor 100 nF ceramic", 5),
    ("BAT54S dual Schottky (tap clamps)", 5),
    ("Resistor 4.7 k (I2C pull-ups)", 2),
    ("12 V DC motor, armature about 4 ohm", 1),
    ("12 V 10 W lamp and holder", 1),
    ("Resistor 24 ohm 10 W (resistive load)", 1),
    ("Resistor 4 ohm 50 W aluminium-clad (test load) with heatsink", 1),
    ("IRF9540N P-MOSFET, 2N2222 NPN, 1 k, 10 k, 100 k (test-load switch)", 1),
    ("Resistor 0.22 ohm 5 W (F1)", 1),
    ("Resistor 1.5 ohm 10 W (F2)", 1),
    ("Resistor 1.0 ohm 10 W (H1)", 1),
    ("Resistor 0.47 ohm 10 W (H3)", 1),
    ("Resistor 6.8 ohm 50 W aluminium-clad with heatsink (H2)", 1),
    ("Resistor 4.7 ohm 25 W (motor-channel calibration)", 1),
    ("8-channel 5 V relay module, SRD-05VDC-SL-C, active-low, JD-VCC jumper", 2),
    ("12 V push solenoid, about 10 mm stroke, with slotted shaft disc (F3 brake)", 1),
    ("12 V 1 A adapter for the solenoid", 1),
    ("Blade fuses and inline holders: 15 A, 10 A, 2 x 5 A, 3 A, 2 A, 1 A", 1),
    ("Terminal blocks or bus bars (src, bus, ret), wire, stripboard", 1),
    ("12 V lead-acid battery, 7 to 12 Ah (aged), and charger", 1),
]

_EQUIPMENT = [
    ("Adjustable DC bench supply, 0 to 15 V, 3 A or more", "calibration (OWNER_TODO step 17)"),
    ("Handheld digital multimeter with a 10 A range", "calibration and the human probe"),
    ("Soldering station and a bench for a few days", "building the bench"),
]

_ISSUES = [
    ("risk", "Test-load duty limit makes steady tests slow (18 s cool-down)", "docs/OPEN_QUESTIONS.md item 1", "low"),
    ("risk", "Compound faults with the test load may trip the 8 A limit", "docs/OPEN_QUESTIONS.md item 2", "medium"),
    ("risk", "H2 resistor dissipates about 23 W; needs heatsink, consider a thermal fuse", "item 3", "medium"),
    ("issue", "F3 stall mechanism: solenoid brake or manual stall?", "item 4: owner to decide", "medium"),
    ("risk", "Relay contact resistance shifts healthy values away from the simulator", "item 5", "medium"),
    ("risk", "Charger left connected can over-range the taps", "item 6", "low"),
    ("risk", "Unpowered USB hub may brown out the relay coils", "item 7", "low"),
    ("issue", "No board connected yet: hardware results not run", "docs/OWNER_TODO.md", "high"),
    ("issue", "Replace the TypeSafe API key used for the Jev run (it was shared in a chat)", "docs/OWNER_TODO.md step 29", "medium"),
    ("issue", "Optional: make a free Gemini key for the cloud comparison", "docs/OWNER_TODO.md steps 34 to 37", "low"),
]

SEED = {
    "milestones": [{"day": str(i + 1), "title": t, "detail": d, "status": "todo", "due": ""}
                   for i, (t, d) in enumerate(_PLAN)],
    "bom": [{"item": item, "qty": qty, "status": "needed", "supplier": "", "price_ngn": "", "date": "", "notes": ""}
            for item, qty in _BOM],
    "equipment": [{"item": item, "purpose": p, "status": "to request", "date": "", "notes": ""}
                  for item, p in _EQUIPMENT],
    "issues": [{"kind": k, "title": t, "detail": d, "severity": s, "status": "open", "date": "2026-10-26"}
               for k, t, d, s in _ISSUES],
}
