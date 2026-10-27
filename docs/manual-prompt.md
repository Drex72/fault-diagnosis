# Prompt: hardware setup manual (docs website)

Give the prompt below to an LLM that can write files (and, ideally, generate images). Attach these files from this repository with it; they are the only source of facts the manual may use:

- `README.md`
- `docs/HARDWARE.md` (parts, values, circuit, pin maps, injector relays)
- `docs/OWNER_TODO.md` (the build order, commands and expected results)
- `docs/RUNBOOK.md` (offline checklist, dashboard walkthrough, troubleshooting)
- `docs/ARCHITECTURE.md` (how the parts fit together, safety layers)
- `docs/OPEN_QUESTIONS.md` (known risks)
- `config/policy.json` (the limits the host enforces)
- Optional: screenshots of the dashboard pages (Live session, Experiments, Models, Calibration, Project tracker)

---

```text
You are writing the hardware setup manual for my final-year project: a 12 V DC fault-diagnosis
testbench (lead-acid battery, motor, lamp, resistive load, switched test load, voltage taps,
INA226 current sensors) controlled by an ESP32, with a second ESP32 that injects faults through
relays, and a laptop dashboard that runs the diagnosis. The software is finished; the hardware is
not built yet. I will follow your manual to buy, build, flash, check, calibrate and run it.

SOURCE OF TRUTH
The attached files are the only source of facts. Every part value, rating, pin number, GPIO,
address, fuse, wire size, command, file name, expected output and limit must come from them,
exactly as written. Do not invent or "improve" any value, pin or command. If something I need is
not in the files, write "Not specified in the project docs" at that spot and list it on a final
page called "Gaps and questions". Do not fill gaps with typical values.

WHO IT IS FOR
Me: an electrical and electronics engineering student who can solder, read a schematic and use a
multimeter, building this bench for the first time. I want it simple and straightforward, not
overwhelming.

OUTPUT: A DOCS WEBSITE
Build it with MkDocs and the Material theme, so it has a sidebar with sections and subsections,
a table of contents per page, and full-text search across every page. It must work fully offline
(the lab has no internet): enable the "search" and "offline" plugins, set theme.font to false,
and use no CDN or external asset. Use admonitions for warnings and tips, task lists for
checklists, and content tabs where macOS and Windows commands differ (the project runs on macOS;
show macOS first). Give me the complete file tree and the full content of every file
(mkdocs.yml, every page, every image or diagram file), then the exact commands to install,
preview (mkdocs serve) and build (mkdocs build) it.

SITE STRUCTURE (one short page per item; split anything longer than about two screens)
1. Home: what I am building (one paragraph and one overview picture), the stages in order with a
   rough time for each, and the five safety rules in one box.
2. Before you start: shopping list as checkboxes grouped by board, sensors, power parts, loads,
   fault parts and wiring (quantities and values from the files); lab equipment to borrow; the
   one-time software install.
3. Stage 1, boards first: flash the bench ESP32, run the firmware self-test on the bare board,
   flash the injector ESP32, run the relay check, label the cables.
4. Stage 2, build the bench (battery disconnected throughout), one page each: instrumentation
   board; power wiring and fuses; voltage taps; star ground; injector relays to the fault sites;
   checks before first power-up; first power-up.
5. Stage 3, calibrate: the calibration script, what it asks for at each step, where the meter
   leads go, then the noise refit.
6. Stage 4, the dashboard: how to start it; then each page and exactly what to look at
   (connection status and firmware version, calibration warning, step table, hypothesis chart,
   human-probe panel, the red Safe state button and how to clear it, the "link lost" banner).
   Only describe what the files say the dashboard shows.
7. Stage 5, run tests and experiments: software tests, the healthy check, the development run,
   the final run (once only, and why), where results go, what to send the maintainer.
8. Troubleshooting: symptom, cause, fix, as a searchable table, plus a link from each stage's
   "If not" notes to the matching row.
9. Reference: bench circuit topology, voltage tap table, current sensor table, bench pin map,
   injector relay table, fault list (what each fault is and how it is made), firmware safety
   limits, command cheat sheet, glossary of every abbreviation used.
10. Gaps and questions.

EVERY STAGE PAGE FOLLOWS THE SAME PATTERN
- Goal: one sentence.
- You need: parts and tools for this stage only.
- Steps: numbered, one action per step, the action in bold, the exact command in a code block.
- What it should look like now: the pictures for this stage (see IMAGES).
- Check: what I should see (the exact expected output or reading from the files), as a checklist.
- If not: the likely cause and a link to Troubleshooting.
- Stop gate (where the files say so): a red warning box saying not to continue, and why. For
  example, do not continue if heartbeat_cutoff, safe_state_latches, boot_safe_state or
  watchdog_reset fail in the self-test.
- Next: a link to the next page.

IMAGES (for every stage)
a) An accurate wiring diagram, drawn as SVG (or a Mermaid diagram), from the tables in
   docs/HARDWARE.md: real component names, values, GPIO numbers, addresses, fuse ratings and
   terminal names (src, bus, ret, motor tap, lamp tap, load tap). Only what is connected so far,
   with this stage's new connections highlighted. This is the picture I wire from.
b) One illustrative picture of how the bench looks at the end of the stage (a realistic
   top-down photo-style view of the boards, wiring and loads on a bench board). If you can
   generate images, generate it; if you cannot, put a placeholder on the page and write a
   detailed image prompt for it in a file images/prompts.md. Caption every such picture:
   "Illustration only. Wire from the diagram and the tables." It must match the facts: both
   ESP32 boards powered from the laptop's USB (never from the battery), one common ground at
   battery negative, blade fuses in inline holders, no mains (230 V) anywhere.
Every image needs alt text describing what it shows, so search finds it too.

STYLE
- Plain, short sentences. Second person ("Connect...", "You should see..."). No marketing tone.
- Explain each term the first time it appears, or link to the glossary.
- Units always with the number (12 V, 0.01 ohm, 115200 baud).
- Safety warnings stand out, but appear only where they apply; do not repeat every warning on
  every page.
- Use plain hyphens, colons or commas, never em dashes.

FINAL CHECK (do this before you give me the files)
List every value, pin and command the manual uses with the attached file it came from. Fix any
that do not match the files exactly, and move anything you could not source to "Gaps and
questions".
```
