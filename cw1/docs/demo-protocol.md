# Live demo test protocol

From the course email of 4 Nov 2026 (the original is saved as [`demo-protocol.png`](demo-protocol.png)). Our rehearsals
replicate this so they can be compared against it.

The sensor runs in the wind tunnel while the ground-truth sensor records airspeed. Two protocols run back to back
and take about two minutes in total. **They may run in reverse order on the day, and the reversed sequence is used for
the second demo assignment.** The email says not to tune to the exact sequence or timings: the speeds are measured on
the day and the timings are approximate. Teams are ranked by MSE against the ground truth (the brief).

## Speed levels

| Level | Tunnel state | Speed (m/s) | From the graph |
| --- | --- | --- | --- |
| Full speed | Fan at maximum, all panel seals in | measured on the day | 1.00 · full |
| Level 2 | One panel seal removed | measured on the day | ≈ 0.80 · full |
| Level 1 | Two panel seals removed | measured on the day | ≈ 0.67 · full |

The email left the speed blanks empty. Record the values from each rehearsal here:

| Date | Tunnel | Full | L2 | L1 | Run file |
| --- | --- | --- | --- | --- | --- |
| | | | | | |

Removing a panel seal lets air leak out, so the test-section speed falls. The 2025 demo ground-truth logs set the shape
of the curve. The fan reaches 90 % of full speed about 3 s after switch-on. The air stops about 20 s after switch-off.

## Protocol 1: steps between speed levels (15 s each)

| Step | Action | Hold | Nominal t (s) |
| --- | --- | --- | --- |
| 1 | Fan on at maximum, all panels sealed | full, 15 s | 0–15 |
| 2 | Remove one panel seal | L2, 15 s | 15–30 |
| 3 | Remove a second panel seal | L1, 15 s | 30–45 |
| 4 | Put one panel back | L2, 15 s | 45–60 |
| 5 | Put the second panel back | full, 15 s | 60–75 |
| 6 | Fan off, wait until the air is still | coast to 0, about 20 s | 75–~96 |

Steps between levels settle in about 1–2 s. The fan-off coast decays smoothly to zero and has a long tail below 2 m/s.

## Protocol 2: fast on/off pulses at full speed (3 s each)

| Step | Action | Nominal t (s) | Speed on the graph |
| --- | --- | --- | --- |
| 1 | Fan on, 3 s | ~96–99 | rises to ≈ 0.89 · full, never reaches full |
| 2 | Fan off, 3 s | ~99–102 | falls to ≈ 0.48 · full, never reaches zero |
| 3 | Fan on, 3 s | ~102–105 | rises to ≈ 0.94 · full |
| 4 | Fan off, coast to rest | ~105–125 | decays to 0 over about 20 s |

The fan spins up in about 3 s and takes much longer to stop, so the pulses never give clean steps. The sensor has to
handle two kinds of change: slow changes between steady levels, and fast changes where the speed never settles.

## What to compare on a rehearsal

- **Level speeds**: full, L2 and L1 in m/s, and the L2/full and L1/full ratios against 0.80 and 0.67.
- **Error per segment**: MSE and bias on each hold, each coast and each pulse. Zero offset and gain dominate the total.
  Reading high in the near-zero coast tails costs more than reading low.
- **Timing**: the spin-up time to 90 %, the coast time to still air, and the pulse peaks and dip against the fractions above.
- **Order**: rehearse in both orders, Protocol 1 then 2 and 2 then 1.

Run assessed rehearsals through the Wind Tunnel Logger (`pnpm logger`, files in `data/tunnel/`) so they are logged the
same way as the demo. Compare them with `calib tunnel` or the Wind speed page's Tunnel runs panel.
