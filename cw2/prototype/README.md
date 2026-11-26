# The Coursework 2 prototype: a ping pong ball pendulum

Two pillars on the mounting plate, one upstream and one downstream, joined by a crossbar on top. A straw hangs from
a hinge at the middle of the crossbar, and a ping pong ball (40 mm, ~2.7 g) sits on its end. The wind pushes the
ball downstream. The camera on the plate looks side-on, so the swing is in the image plane, and the Pi measures how
far the ball has moved from where it hangs in still air.

What the software needs from the build:

- **A clear view of the whole swing.** Nothing should cross the ball's path in the image, from rest to ~60°, and
  nothing as bright as the ball should touch it. Use a matte dark background behind the ball (the far tunnel wall,
  or black card), and constant light on the camera side.
  - The light must not be PWM-dimmed: that bands the image.
  - Keep the light off the ball's background, so the ball stands out.
- **The pillars far enough apart** that the ball never reaches the downstream one. At 10 m/s a bare ball swings to
  about 53°, which puts it 0.8 × the straw length downstream.
- **Size.** A 15 cm straw (hinge to ball centre) swings ~3° at 2 m/s and ~53° at 10 m/s.
  - In the camera that is still tens of pixels at 2 m/s.
  - The pendulum's own frequency is ~1.3 Hz.
- **Damping, if you can.** A bare ball on a straw is very lightly damped (ζ ≈ 0.05 in still air), so turbulence
  makes it flutter at ~1.3 Hz. In the simulator that flutter, not the camera, sets the MSE. Damping to ζ ≈ 0.3–0.5
  cut the simulated MSE by about a third (`docs/algorithms.md` §6). Both of these stay passive:
  - a light vane on the straw in a cup of oil;
  - a small aluminium plate on the straw moving past a fixed magnet (eddy currents), with the magnet epoxied, per
    the brief's safety rules.

  Too much damping makes it slow; aim for the ball to overshoot once at most when flicked (`aircam pluck`).

Write down here, once it exists:
- the straw length from the hinge to the ball centre (mm), which is `arm` in the config before the fit refines it;
- the ball's mass and the straw's;
- the camera's distance and the ball's size in pixels (`aircam setup` prints it);
- photos, and CAD (STL/STEP).

Then run the tests in `../docs/algorithms.md` §7.
