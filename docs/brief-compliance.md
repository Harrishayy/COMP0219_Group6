# Brief compliance checklist

Cross-check of the Stage 1 brief (`docs/project-brief-part1.pdf`) against this repo. Tick a box only once it
has been verified on the real board or in the tunnel.

## 1. Hard requirements

| # | Requirement (brief) | Where the repo stands | Done |
| --- | --- | --- | --- |
| R1 | **Contact-based** transducer: it must physically interact with the air (no camera or remote methods; those are Stage 2) | Drag plate on a load cell qualifies | [ ] |
| R2 | Estimates **instantaneous airspeed, 2–10 m/s**, including **steps and ramps** (direction not needed) | Chain has median-3, lead, EMA/Butterworth for the dynamics; still to be tested in the tunnel | [ ] |
| R3 | **Everything runs on the Nucleo-F411RE**; the PC only reads the stream | The PC previews, fits and records; the board computes | [ ] |
| R4 | One **floating-point value at 200 Hz, 115200 baud**, over USB virtual COM | `MODE V` (velocity only) is the boot default | [ ] |
| R5 | **No dropped samples** (scored) | Check `STATS` shows `overruns=0` after a long run | [ ] |
| R6 | Fits a **30 × 30 × 30 cm** envelope and **mounts to the 30 cm square plate** | Mechanical: check physically | [ ] |
| R7 | **Safety**: guards on rotating parts, magnets secured with epoxy or heat-shrink, no sharp edges | Matters mostly for the cup/encoder variants; deburr the plate regardless | [ ] |
| R8 | **Calibration file on demand** at the demo | `GET` on the board; toolkit exports JSON, C header and SET script. Decide which to show and rehearse it | [ ] |

## 2. Assessment

**Practical performance (70%)**

- [ ] Accuracy rank by **MSE** against the Dwyer 616KD pitot (sampled at 200 Hz)
- [ ] 200 Hz / 115200 streaming **without drops**

**Report (30%)**

- [ ] Clarity and depth; correct modelling and calibration; quality of experiments and plots
- [ ] **Originality** of the mechanical transducer and/or embedded processing (creative mechanics and filtering are rewarded)

## 3. Deliverables

- [ ] **Live demo** in the group's lab timeslot: device on the plate, live 200 Hz stream, calibration file on request
- [ ] **Code submission**: a buildable PlatformIO (or CubeIDE) project, **no PC-side processing**
- [ ] **6-page IEEE-style report** (template to follow) covering:
  - [ ] Design rationale
  - [ ] Modelling (drag → force → speed, density correction)
  - [ ] Calibration method
  - [ ] Error budget
  - [ ] Dynamic results: **MSE, bias, σ**
  - [ ] Limitations
  - [ ] Originality

Demo and submission dates: to be announced.

## 4. Open risks before the demo

- [ ] **Stream cleanliness.** At boot the firmware prints `#cmds: …` lines (`firmware/src/apps/airspeed.cpp`,
  `setup`). The marking script expects bare floats. Find out whether it skips non-numeric lines, or make sure
  nothing but numbers is sent in `MODE V`.
- [ ] **Load-cell rate.** The brief's hardware list names the **NAU7802 Qwiic Scale** (320 samples/s). The
  M5Stack Weight I2C (HX711) gives only about 10 samples/s. The output still goes out at 200 Hz, but the value
  only changes 10 times a second, which costs MSE on steps and ramps. Use the NAU7802 for the competition run.
- [ ] **Submission build.** `airspeed_drag_plate` is the env to submit. Confirm it builds from a clean clone
  (`cd firmware && pio run -e airspeed_drag_plate`) and that `calibration_generated.h` is committed with the
  final fit.
- [ ] **Report evidence.** Copy tunnel recordings from `calibration/runs/` (git-ignored) into `data/` so the
  results section has step and ramp plots plus MSE, bias and σ.
- [ ] **Density compensation.** The brief supplies the TMP36, MCP9808 and IKS4A1 barometer for this. The
  chain already uses ρ: explain it in the modelling section and include it in the error budget.

## 5. Design review: lever-amplified drag transducer (mast + target on a vertical load cell)

### Compliance

| Requirement | Verdict | Note |
| --- | --- | --- |
| R1 contact-based | Pass | A drag body in the flow |
| R2 2–10 m/s, dynamics | Depends on the target and the mast resonance | See forces and risks below |
| R3 all on the Nucleo | Pass | ρ = p/(R·T) and v = √(2F / (ρ·C_D·A)) both run on the board; the firmware already has this model (`speed_sqrt_density`, `k = 2/(C_D·A)`) |
| R4/R5 200 Hz, 115200 | Pass | USART2 is the ST-LINK virtual COM port; NAU7802 at 320 SPS is faster than 200 Hz |
| R6 envelope and plate | Likely pass | About 19 cm tall (15 cm mast + target radius). The baseplate must bolt to the 30 cm plate |
| R7 safety | Pass | No rotating parts or magnets. Sand the cut ends of the carbon tube and the acrylic/MDF edges |
| Hardware list | **Check** | The brief supplies a **5 kg** cell. The memo uses a **1 kg** cell, and "Innovation Lab passive/consumable parts" doesn't clearly cover one. Confirm with staff |

### Corrections needed before this goes in the report

- [ ] **The mast does not amplify the reading 7.5×.** A parallel-beam (binocular) cell is built to measure the
  shear force F and to *reject* the moment: its bridge reads the difference in bending moment between its two
  flexures, which is F × (flexure spacing) wherever F is applied. A 15 cm mast raises the moment on the mount,
  but the output stays about the same. Real amplification needs a pivot (fulcrum) with the cell on the short
  arm. The mast's real job is to put the target in clean, fast air above the boundary layer, so state that as
  its purpose.
- [ ] **A sphere still sheds vortices.** At Re ≈ 8,000–55,000 the wake is unsteady, though less periodic than
  behind a flat plate. Say "weaker, less coherent shedding", not "entirely eliminating".
- [ ] **The memo's formulas are garbled.** Write ρ = p / (R_specific · T) and v = √(2F / (ρ · C_D · A)).
- [ ] **The mast drags too.** An 8 mm × 15 cm tube makes about 0.09 N at 10 m/s, roughly as much as a 70 mm
  sphere. Calibration absorbs it, but it weights the reading by the velocity profile along the mast. Fixes:
  a thinner mast, or a **fixed shroud** (a stationary tube around the mast, on the baseplate, not touching it).
  The shroud is also an originality point.

### Drag forces (ρ = 1.2 kg/m³)

| Target | C_D | F at 2 m/s | F at 10 m/s |
| --- | --- | --- | --- |
| Sphere 60 mm | 0.47 | 0.33 gf | 8.1 gf |
| Sphere 70 mm | 0.47 | 0.44 gf | 11.1 gf |
| Sphere 80 mm | 0.47 | 0.58 gf | 14.5 gf |
| **Hemisphere 80 mm, open side upwind** | 1.42 | **1.75 gf** | **43.7 gf** |
| (Mast 8 mm × 15 cm, for comparison) | ~1.2 | 0.35 gf | 8.8 gf |

Speed resolution is δv ≈ v · δF / (2F), so the 2 m/s end is where the error comes from. With load-cell noise
δF = 0.1 gf, the 70 mm sphere gives δv ≈ 0.23 m/s at 2 m/s and the 80 mm hemisphere gives δv ≈ 0.06 m/s. On a
5 kg cell the noise is about five times larger in grams, and the sphere becomes marginal.

**Recommendation: Option B (hemisphere),** with the low-pass filter set from the measured rim and mast noise.
Its 4× signal advantage outweighs the extra shedding, and filtering is something we already do on the board.

### Other risks

- [ ] **Mast resonance.** A tall mast with a mass on top, on a compliant cell, has a low natural frequency that
  the tunnel's turbulence and fan will excite. Do a tap test in `MODE D`, find the peak, and set the
  Butterworth cutoff below it. The lag this adds costs MSE on steps, so trade it off with the lead stage.
- [ ] **Cell orientation.** The cell's sensing axis must point along the airflow, with gravity along the cell's
  long axis so the target's weight doesn't read as force. A 1° tilt of a 50 g top mass reads ≈ 0.9 gf, more
  than the sphere's whole signal at 2 m/s. Tare with the fan off, at the start of every run.
- [ ] **Tunnel centreline.** Measure the height of the tunnel's centreline above the mounting plate, and the
  duct cross-section (for blockage), before fixing the 15 cm figure.

### Next steps

1. [ ] Confirm with staff whether the 1 kg cell is allowed. Otherwise design around the kit's 5 kg cell.
2. [ ] Measure the tunnel's centreline height and cross-section.
3. [ ] Mount the bare cell vertically, flash `loadcell_test`, and measure the noise floor δF in grams at 320 SPS.
   This decides sphere vs hemisphere with real numbers.
4. [ ] Build the hemisphere target, the mast and (if time allows) the shroud. Tap-test for resonance.
5. [ ] Scale the cell with a known mass (`CAL_LC_N_PER_COUNT`), then fit `CAL_SQRT_K` and `CAL_SQRT_X0`
   against the pitot across 2–10 m/s, including steps and ramps.
6. [ ] Tune the filter and lead for MSE, export `calibration_generated.h`, and keep the runs in `data/` for the
   report.
