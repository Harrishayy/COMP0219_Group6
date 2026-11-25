// Every wiring diagram, for scripts/check-wiring.mjs. A sensor module imports its own spec directly.
import lm35 from './lm35.js';
import mcp9808 from './mcp9808.js';

export default [mcp9808, lm35];
