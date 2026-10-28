# Formulas

Every equation the drag-plate sensor uses, from the load cell's counts to the speed the board streams, with the
numbers for our parts. The firmware is the truth: `firmware/lib/airmath/` (density, model, filters) and
`firmware/src/apps/airspeed.cpp` (the 200 Hz chain); `calibration/calib/models.py` mirrors it on the PC. Symbols are
SI (N, Pa, m, m², kg/m³, K) unless a unit is written.

| Symbol | Meaning | Where it lives |
| --- | --- | --- |
| v | airspeed, m/s | streamed at 200 Hz |
| F | force on the plate along the flow, N | debug column `feature` |
| F₀ | force the cell reads with no flow (mount preload, an old tare), N | `sqrt_x0` |
| ρ | air density, kg/m³ | debug column `rho` |
| q | dynamic pressure, Pa | |
| C_d | drag coefficient of the shape (no unit) | |
| A | reference (frontal) area, m² | |
| C_d·A | drag area, m² (the dashboard shows cm²: 1 cm² = 10⁻⁴ m²) | `sqrt_k = 2/(C_d·A)` |

## 1. Dynamic pressure and drag

```
q = ½·ρ·v²                         Pa
F = C_d·A·q = ½·ρ·v²·C_d·A          N
```

At ρ = 1.20 kg/m³: q = 2.4 Pa at 2 m/s, 15 Pa at 5 m/s, 60 Pa at 10 m/s. The force is q times the drag area, so a
10 × 10 cm plate (C_d·A = 117 cm² = 0.0117 m²) feels 0.028 N (2.9 g) at 2 m/s and 0.70 N (72 g) at 10 m/s.
Grams of load and newtons: `m [g] = 1000·F/g`, with g = 9.80665 m/s².

## 2. Speed from force (what the board computes)

Solve the drag equation for v and subtract the zero-flow force:

```
v = √( 2·(F − F₀) / (ρ·C_d·A) )     = √( sqrt_k·(F − F₀) / ρ ),   sqrt_k = 2/(C_d·A)  [1/m²]
```

`airmath::speed_sqrt_density`: if the argument under the root is negative (F < F₀, noise near zero) the speed is 0.
The board stores `sqrt_k` and `sqrt_x0` (= F₀), never C_d or A on their own: only the product C_d·A matters. To go
the other way, `C_d·A [cm²] = 2·10⁴ / sqrt_k`.

Known mass to speed (the bench check, a mass m pulling along the flow): `v = √( 2·m·g / (ρ·C_d·A) )`. With
C_d·A = 117 cm² and ρ = 1.20: 3 g → 2.0 m/s, 20 g → 5.3 m/s, 50 g → 8.4 m/s, 400 g → 23.6 m/s.

## 3. Drag coefficient and area by shape

Use the frontal area A, the area the wind sees, and C_d for that area. Values are for sharp-edged bodies square to
the flow at our Reynolds numbers (Hoerner, *Fluid-Dynamic Drag*, 1965; ±5 %):

| Shape | A | C_d | C_d·A for our size | sqrt_k | Force at 2 / 10 m/s |
| --- | --- | --- | --- | --- | --- |
| Square plate, side s | s² | 1.17 | 10 × 10 cm: 117 cm² | 171 | 2.9 g / 72 g |
| Rectangular plate a × b | a·b | 1.17 (a/b ≤ 3), 1.2 at 5, 1.3 at 10 | | | |
| Flat disc, diameter d | π·d²/4 | 1.17 | Ø10 cm: 92 cm² | 218 | 2.3 g / 56 g |
| Hollow hemisphere, open side to the wind (a cup) | π·d²/4 (rim) | 1.42 | Ø10 cm: 112 cm² | 179 | 2.7 g / 68 g |
| Hollow hemisphere, round side to the wind | π·d²/4 (rim) | 0.38 | Ø10 cm: 30 cm² | 670 | 0.7 g / 18 g |
| Solid hemisphere, flat face to the wind | π·d²/4 | 1.17 | | | |
| Sphere | π·d²/4 | 0.47 (Re < 2·10⁵) | Ø7 cm: 18 cm² · Ø8 cm: 24 cm² | 1106 · 847 | Ø8: 0.6 g / 14.5 g |
| Hollow hemisphere Ø8 cm, open side to the wind | 50.3 cm² | 1.42 | 71 cm² | 280 | 1.8 g / 44 g |

(ρ = 1.20 kg/m³.) What this means for a design:

- A hollow hemisphere with its open side to the wind gives about 20 % more force than a flat disc of the same
  diameter, and about the same as a square plate whose side equals the diameter. Turned round, it gives a quarter
  of the force: mount it the right way and keep it square to the flow.
- More force is more resolution (section 7). Force scales with area: a 15 × 15 cm plate gives 2.25 times the force
  of a 10 × 10 cm one (6.4 g at 2 m/s), and still fits the 30 cm envelope.
- These C_d values are a starting point. The tunnel fit (dashboard step "Fit the calibration curve") measures C_d·A
  on the real mount, including the stalk, the edges and the tunnel's blockage (section 8). Trust the fit over the
  table once you have one.

Reynolds number, to check C_d is in its flat region: `Re = ρ·v·L/μ`, with μ = 1.81·10⁻⁵ Pa·s at 20 °C and L the
plate's width. For L = 10 cm: Re ≈ 1.3·10⁴ at 2 m/s and 6.6·10⁴ at 10 m/s. Sharp-edged plates, discs and hollow
hemispheres keep a constant C_d over this range, so √F stays proportional to v. A round-edged body (a sphere, the
convex side of a hemisphere) does not: its C_d drifts with speed, and the "Cd is not constant" diagnosis appears.

## 4. Air density

The board computes ρ every tick from the air temperature T (°C), the pressure p and, when the SHT40 reads, the
relative humidity RH (`airmath::air_density`):

```
dry:     ρ = p / (R_d·T_K)                                   R_d = 287.058 J/(kg·K),  T_K = T + 273.15
humid:   p_sat = 610.94·exp( 17.625·T / (T + 243.04) )       Pa (Magnus)
         p_v = (RH/100)·p_sat,   p_d = p − p_v
         ρ = p_d/(R_d·T_K) + p_v/(R_v·T_K)                   R_v = 461.495 J/(kg·K)
```

T is the MCP9808 plus `mcp9808_offset`. p is the LPS22DF, or the constant `pressure_ref_pa` when no barometer reads
(set it to the day's *station* pressure, not the sea-level figure). Missing T falls back to 15 °C. Examples:
20 °C, 101 325 Pa, dry → 1.204; 50 % RH → 1.199; 22.5 °C, 100 870 Pa, 41 % → 1.184 kg/m³.

How errors in the inputs reach the speed (v ∝ ρ^−½):

```
Δρ/ρ ≈ Δp/p − ΔT/T_K          Δv/v = −½·Δρ/ρ
```

1 °C → 0.34 % on ρ → 0.17 % on v. 10 hPa → 1 % on ρ → 0.5 % on v. Humidity moves ρ by under 1 % at room
temperature.

## 5. Load cell: counts to newtons

```
F = (counts − lc_offset) · lc_n_per_count
```

- **Tare** (`TARE`) sets `lc_offset` to the mean counts with nothing on the cell and no wind.
- **Scale** from known masses m_i hung along the cell's sensing axis: the board reads F_i, and the true load is
  W_i = m_i·g. The least-squares gain is `G = Σ W_i·F_i / Σ W_i²`, and the new scale is
  `lc_n_per_count_new = lc_n_per_count_old / G` (dashboard step "Scale with known masses", `calib weights`).
- **Linearity**: the largest |F_i − G·W_i| as a percentage of the largest load. Expect under 0.1 %.

**Mount geometry.** Calibrate with the mass pulling where and how the wind pushes: at the centre of the plate, along
the flow (a thread over a pulley if the cell lies horizontal). Then any lever ratio, a stalk's bending or the cell's
sensitivity to where the load sits is inside `lc_n_per_count`.

- **A lever on a pivot.** If the body sits on an arm that turns about a hinge and presses on the cell, the cell sees
  `F_cell = F_drag·(L_body / L_cell)`, with L each one's distance from the pivot. An arm with L_body > L_cell
  multiplies the force, which is more resolution.
- **A mast on a bar load cell does not do that.** The 1 kg bar cell is a parallel-beam (single-point) cell: its gauges
  sit in pairs, so it reads the force across it and largely ignores the moment. That is why a kitchen scale reads the
  same wherever the load sits. A body on a mast rising from the cell's free end gives roughly the drag force itself,
  not drag × (mast length / cell length). The mast's value is putting the body in clean air in the middle of the
  tunnel, away from the floor's boundary layer. The moment it adds is what the cell rejects, and how well it rejects
  it is not specified, so the real ratio is whatever the masses say: hang them at the body's centre, along the flow,
  and the scale includes it. A tall mast also lowers the mount's stiffness and natural frequency f_n (section 6),
  so check the ringing.

## 6. The output chain (200 Hz, `tick()`)

Each sample, in this order, then clamped at 0:

```
median-3:   y_k = median(x_k, x_{k−1}, x_{k−2})                   removes single spikes; 1 sample (5 ms) delay
lead:       y_k = x_k + τ_lead·f_s·(x_k − x_{k−1})                 lead_tau; cancels a first-order lag τ_lead;
                                                                  amplifies noise by (1 + τ_lead·f_s)
EMA:        y_k = α·x_k + (1 − α)·y_{k−1}                          ema_alpha (filter_kind 1)
            time constant τ = −1 / (f_s·ln(1 − α))                 α 0.2 → 22 ms, α 0.05 → 97 ms
Butterworth 2nd order, cutoff f_c (filter_kind 0):                  lpf_hz; RBJ biquad, Q = 1/√2
            delay at low frequency ≈ √2 / (2π·f_c)                f_c 10 Hz → 23 ms, 5 Hz → 45 ms
```

f_s = 200 Hz. The score is MSE against the tunnel, so noise (σ²) and lag on speed changes both count: smoothing
lowers σ and adds delay. The dashboard's "Tune smoothing and lag" step fits α or f_c together with τ_lead. The
plate on its mount is a spring and mass, `f_n = (1/2π)·√(k_mount/m_plate)`. Keep the low-pass cutoff below f_n so
the ringing does not reach the output (the simulator assumes 6 Hz).

## 7. Resolution: what the load cell's noise costs in speed

Differentiate section 2:

```
Δv = ΔF / (ρ·C_d·A·v)              Δv/v = ½·ΔF/(F − F₀)
```

The same force noise costs more at low speed. With 0.5 g (4.9 mN) of noise, ρ = 1.20:

| Body | σ_v at 2 m/s | σ_v at 10 m/s |
| --- | --- | --- |
| Square plate 10 × 10 cm | 0.18 m/s | 0.035 m/s |
| Hollow hemisphere Ø10 cm, open side to wind | 0.18 m/s | 0.037 m/s |
| Hollow hemisphere Ø10 cm, round side to wind | 0.69 m/s | 0.14 m/s |
| Square plate 15 × 15 cm | 0.08 m/s | 0.016 m/s |

Smoothing divides this further, roughly by √(samples averaged), at the cost of lag (section 6).

## 8. Ground truth (the tunnel)

The tunnel's pitot and differential-pressure cell give `v_gt = C·√( 2·|Δp| / ρ )`, with C = 1. The Wind Tunnel
Logger zeroes Δp with the fan off (5 s mean; a mean beyond ±6 Pa is rejected for +3 Pa) and takes ρ from the
pressure and temperature typed into its window: `ρ = p / (287.05·T_K)`. The white tunnel's VDAS reports whole
pascals about 5 times a second, so its own resolution is

```
Δv_gt = Δp / (ρ·v)   → ±1 Pa is ±0.42 m/s at 2 m/s, ±0.17 at 5 m/s, ±0.08 at 10 m/s
```

Below about 4 m/s, a few tenths of a m/s of disagreement is the ground truth's resolution, not the sensor.

Scores (`calib tunnel`, the dashboard), over paired samples e = v_board − v_gt:

```
MSE = mean(e²)     bias = mean(e)     σ = std(e)     MSE = bias² + σ²
drift = slope of e against time (m/s per minute)
```

A constant percentage error at every speed is the drag area. If the board reads v_b where the truth is v_g, then
`sqrt_k_new = sqrt_k · s` with `s = Σ v_g²·v_b² / Σ v_b⁴` (speeds above 2 m/s). This holds only if the board ran its
current settings during the run.

**Blockage.** A body that fills more than about 5 % of the tunnel's cross-section C speeds the air up around it, so
it reads high. Maskell's correction for bluff bodies is about `q_true = q_measured / (1 + 2.5·C_d·A/C)`. Fitting C_d·A
in the same tunnel absorbs it: the fitted value is right for that tunnel. Measure the working section before using a
fitted C_d·A in another tunnel.

## 9. When something changes: what to redo

| What changed | What it changes in the formula | What to do |
| --- | --- | --- |
| Plate size or shape | C_d·A, so `sqrt_k = 2/(C_d·A)` | Wind speed step 1: pick the shape and enter its size, then "Send C_d·A to board". Refit the curve in the tunnel. Export the header so the board boots with it |
| Plate mounting (stalk, lever, angle to the flow) | the load the cell sees for the same drag, and F₀ | Scale with masses hung at the plate's centre along the flow (Load cell step 2), then measure F₀ (Wind speed step 2) |
| Load cell or amplifier (Weight I2C ↔ Qwiic Scale) | `lc_offset` and `lc_n_per_count` | Tare, scale with masses, check with another mass (Load cell steps 1–3) |
| The cell was knocked, warmed up, or the board was reset | `lc_offset`, F₀ | Tare, then measure F₀ with no wind |
| Thermometer, or its position | T, so ρ | Temperature steps: settle, calibrate the offset against a reference |
| Weather, or a different room | p, so ρ | Barometer reads it live; without one, enter the day's station pressure (Temperature step 5) |
| Smoothing or lag settings | output chain only, not the curve | Fit the response in the tunnel, push |
| Different tunnel | blockage (section 8) | Refit C_d·A in that tunnel |

Every setting set from the dashboard is a `SET` in the board's RAM and is lost on reset. Export the calibration
(Runs → Export) to write `firmware/include/calibration_generated.h`, then flash, so the board boots with it.
`firmware/include/calibration.h` holds the defaults. `CAL_SQRT_K` must stay equal to `sqrt_k` in
`calib/models.py` (`test_chain.py` checks this): a default of 1.0 means a 2 m² plate, which reads a few hundred
grams as 2 m/s.
