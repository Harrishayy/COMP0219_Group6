**Project Architecture Memo: Lever-Amplified Drag Transducer**

**1. Core Engineering Philosophy**
Our wind speed sensor abandons the traditional "flat plate directly on a load cell" approach due to three fatal flaws in that standard design: boundary layer interference (slow air at the bottom of the tunnel), severe vortex shedding (mechanical vibration that ruins the MSE grade), and a lack of torque generation at the minimum 2 m/s requirement.

Instead, our design acts as a mechanical lever. By keeping the mounting hardware on the floor and extending a rigid mast 15 cm into the air, we place our aerodynamic target in the dead-center of the tunnel (the cleanest, fastest air). The rigid mast mechanically amplifies the tiny wind forces by 7.5x before they reach the load cell, guaranteeing clear readings above the electronic noise floor at low speeds.

**2. Mechanical Stack (Bottom to Top)**

* **The Base & Anchor:** A heavy laser-cut acrylic or MDF baseplate is bolted to the tunnel's mounting platform. A steel L-bracket is secured to this baseplate.


* **The Sensor:** A 1 kg parallel-beam aluminum load cell is mounted entirely vertically. The bottom half is secured to the L-bracket using horizontal side bolts. The load cell is rigidly anchored and cannot move.


* **The Coupler:** A custom 100% solid-infill 3D-printed adapter block (PLA+ or PETG) is secured to the top half of the load cell using horizontal bolts.


* **The Lever Arm:** A 15 cm perfectly rigid carbon fiber tube. It is seated deep into the 3D-printed adapter. **Crucial note:** This mast must not bend. It acts as a solid crowbar to transfer 100% of the wind's pushing force into a twisting force (bending moment) exclusively at the load cell's center flexure.


* **The Target:** Mounted at the top of the mast, placing its center exactly 15 cm above the baseplate, directly in the tunnel's "sweet spot."



**3. Target Geometry Options (The Aerodynamic Trade-off)**
We have two viable options for the aerodynamic target at the top of the mast. Both solve the vibration issues of a flat square plate, but offer different advantages.

* **Option A: The Smooth Sphere (60-80mm OD)**
* **Aerodynamics:** Lowest possible mechanical noise. The air parts symmetrically around the sphere, entirely eliminating the alternating vortex shedding that causes flat plates to vibrate violently.
* **Drag Coefficient (CD):** 0.47.
* **Pros:** Produces the cleanest, smoothest digital data line. Easiest to filter, practically guaranteeing a high accuracy (MSE) grade.
* **Cons:** Generates lower overall physical force, relying heavily on the 15 cm mast to amplify the signal at 2 m/s.


* Option B: The Hollow Hemisphere (80mm OD)


* **Aerodynamics:** The open "bowl" faces directly into the incoming wind flow. It catches a massive amount of air while still lacking the sharp 90-degree corners of a square plate.


* **Drag Coefficient (CD):** 1.42.
* **Pros:** Generates nearly 3 times the physical pushing force of the smooth sphere. When combined with the 15 cm mast, this guarantees massive signal clarity even at speeds below 2 m/s.
* **Cons:** Will shed slightly more vortices off its circular rim than a closed sphere, introducing a small amount of high-frequency noise into the data stream that our Nucleo will need to filter.



**4. Electronic & Processing Pipeline**

* **Analog Isolation:** The incredibly sensitive load cell wires route directly into the NAU7802 ADC, which is mounted *immediately* outside the wind tunnel to prevent the wires from acting as noise antennas. The NAU7802 samples at 320 SPS and outputs a clean digital I2C signal.
* **Environmental Calibration:** The X-NUCLEO-IKS4A1 shield sits safely on the desk (outside the tunnel to prevent aerodynamic blockage). It feeds real-time static pressure and ambient temperature data to the Nucleo.
* **The Math:** The Nucleo-F411RE uses the shield data to calculate real-time air density via the Ideal Gas Law: =PRT. It then reads the amplified torque from the NAU7802 (=Fd), derives the wind force, and calculates the exact air velocity using the drag equation: v=2FCDA. The result is streamed via USART2 at 200 Hz.
