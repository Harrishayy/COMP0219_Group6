// Competition firmware: estimates airspeed on the Nucleo and streams it at 200 Hz over the
// ST-LINK virtual COM port (115200 baud). Protocol summary (full spec in README):
//
//   chain            model -> median-3 -> lead -> [Butterworth | EMA] -> clamp >= 0
//   default stream   one velocity per line, m/s, e.g. "5.123"
//   '#' lines        informational, never data
//   MODE V | MODE D  velocity only | debug CSV (prints a "#cols,..." header)
//   HDR              re-print the "#cols,..." header
//   GET              "#cal {json}": every live parameter plus method, build and environment
//   SET <name> <v>   change a live parameter (RAM only, lost on reset)
//   TARE | ENV | STATS | HELP
#include <Arduino.h>
#include <Wire.h>

#include "air_density.h"
#include "airspeed_models.h"
#include "analog_in.h"
#include "board_config.h"
#include "calibration.h"
#include "env_sensors.h"
#include "filters.h"

#if AIRSPEED_METHOD == METHOD_CUP_ENCODER
#include "quad_encoder.h"
#include "rate_estimator.h"
#elif AIRSPEED_METHOD == METHOD_CUP_HALL
#include "hall_sensor.h"
#elif AIRSPEED_METHOD == METHOD_DRAG_PLATE
#include "load_cell.h"
#else
#error "Unknown AIRSPEED_METHOD, see include/calibration.h"
#endif

namespace {

// ---- Live parameters ---------------------------------------------------------------------

struct Params {
  float rotor_slope = CAL_ROTOR_SLOPE;
  float rotor_offset = CAL_ROTOR_OFFSET;
  float rotor_min_hz = CAL_ROTOR_MIN_HZ;
  float rotor_window_ms = CAL_ROTOR_WINDOW_MS;
  float sqrt_k = CAL_SQRT_K;
  float sqrt_x0 = CAL_SQRT_X0;
  float lc_offset = CAL_LC_OFFSET;
  float lc_n_per_count = CAL_LC_N_PER_COUNT;
  float lead_tau = CAL_LEAD_TAU;
  float lpf_hz = CAL_LPF_HZ;
  float filter_kind = CAL_FILTER_KIND;
  float ema_alpha = CAL_EMA_ALPHA;
} g_params;

struct ParamEntry {
  const char* name;
  float* value;
};

const ParamEntry kParams[] = {
    {"rotor_slope", &g_params.rotor_slope},   {"rotor_offset", &g_params.rotor_offset},
    {"rotor_min_hz", &g_params.rotor_min_hz}, {"rotor_window_ms", &g_params.rotor_window_ms},
    {"sqrt_k", &g_params.sqrt_k},             {"sqrt_x0", &g_params.sqrt_x0},
    {"lc_offset", &g_params.lc_offset},       {"lc_n_per_count", &g_params.lc_n_per_count},
    {"lead_tau", &g_params.lead_tau},         {"lpf_hz", &g_params.lpf_hz},
    {"filter_kind", &g_params.filter_kind},   {"ema_alpha", &g_params.ema_alpha},
};

const char* method_name() {
  switch (AIRSPEED_METHOD) {
    case METHOD_CUP_ENCODER: return "cup_encoder";
    case METHOD_CUP_HALL: return "cup_hall";
    case METHOD_DRAG_PLATE: return "drag_plate";
    default: return "unknown";
  }
}

// ---- Hardware ----------------------------------------------------------------------------

EnvSensors g_env(Wire);
HardwareTimer g_tick_timer(TIM5);
volatile uint32_t g_ticks = 0;

#if AIRSPEED_METHOD == METHOD_CUP_ENCODER
QuadEncoder g_encoder;
airmath::RateEstimator<64> g_encoder_rate;  // 64 ticks = 320 ms of history
#elif AIRSPEED_METHOD == METHOD_CUP_HALL
HallTachometer g_tacho;
#elif AIRSPEED_METHOD == METHOD_DRAG_PLATE
LoadCell g_load_cell;
#endif

// ---- Signal chain ------------------------------------------------------------------------

airmath::LeadCompensator g_lead;
airmath::Median3 g_median;
airmath::ButterworthLowPass g_lpf;
airmath::Ema g_ema;
bool g_use_ema = false;

// TARE changes the load cell's offset behind g_params' back; pull it in before reporting
// or re-applying parameters so a later SET can't silently undo a tare.
void sync_from_hardware() {
#if AIRSPEED_METHOD == METHOD_DRAG_PLATE
  if (!g_load_cell.taring()) g_params.lc_offset = static_cast<float>(g_load_cell.offset());
#endif
}

void apply_filter_params() {
  g_lead.configure(g_params.lead_tau, board::kSampleHz);
  g_lpf.configure(g_params.lpf_hz, board::kSampleHz);
  // Clamp rather than reject so the reported value is always the one in use.
  g_params.filter_kind = g_params.filter_kind >= 0.5f ? 1.0f : 0.0f;
  if (!(g_params.ema_alpha > 0.0f)) g_params.ema_alpha = 1e-4f;
  if (g_params.ema_alpha > 1.0f) g_params.ema_alpha = 1.0f;
  g_ema.set_alpha(g_params.ema_alpha);
  bool use_ema = g_params.filter_kind == 1.0f;
  if (use_ema != g_use_ema) {
    g_ema.reset();  // re-prime on switch so neither filter starts from a stale state
    g_lpf.reset();
  }
  g_use_ema = use_ema;
#if AIRSPEED_METHOD == METHOD_DRAG_PLATE
  g_load_cell.set_offset(static_cast<int32_t>(g_params.lc_offset));
  g_load_cell.set_scale(g_params.lc_n_per_count);
#endif
}

void transducer_begin() {
#if AIRSPEED_METHOD == METHOD_CUP_ENCODER
  g_encoder.begin(board::kEncoderFilter);
#elif AIRSPEED_METHOD == METHOD_CUP_HALL
  g_tacho.begin(board::kPinHallDigital, board::kHallPulsesPerRev);
#elif AIRSPEED_METHOD == METHOD_DRAG_PLATE
  if (!g_load_cell.begin(Wire, static_cast<int32_t>(g_params.lc_offset), g_params.lc_n_per_count)) {
    Serial.println("#err NAU7802 not found at 0x2A: force will read 0");
  }
#endif
}

// Raw transducer quantity the model consumes, sampled once per tick.
float read_feature() {
#if AIRSPEED_METHOD == METHOD_CUP_ENCODER
  g_encoder_rate.push(micros(), g_encoder.count());
  float window_us = g_params.rotor_window_ms * 1000.0f;
  return g_encoder_rate.rate(static_cast<uint32_t>(window_us)) / board::kEncoderCountsPerRev;
#elif AIRSPEED_METHOD == METHOD_CUP_HALL
  return g_tacho.rev_per_sec();
#elif AIRSPEED_METHOD == METHOD_DRAG_PLATE
  g_load_cell.poll();  // 320 SPS: a fresh conversion on most ticks, else hold the last one
  return g_load_cell.force_n();
#endif
}

float model(float feature, float rho) {
#if AIRSPEED_METHOD == METHOD_CUP_ENCODER || AIRSPEED_METHOD == METHOD_CUP_HALL
  (void)rho;
  return airmath::speed_linear(feature, g_params.rotor_slope, g_params.rotor_offset,
                               g_params.rotor_min_hz);
#else
  return airmath::speed_sqrt_density(feature, g_params.sqrt_k, g_params.sqrt_x0, rho);
#endif
}

// ---- Output ------------------------------------------------------------------------------

enum class Mode : uint8_t { kVelocity, kDebug };
Mode g_mode = Mode::kVelocity;
uint32_t g_seq = 0;

struct Stats {
  uint32_t overruns = 0;   // ticks that arrived while the previous one was still running
  uint32_t max_busy_us = 0;
} g_stats;

void print_header() { Serial.println("#cols,n,v,feature,rho,temp_c,pressure_pa"); }

void emit(float v, float feature, float rho) {
  if (g_mode == Mode::kVelocity) {
    Serial.println(v, 3);
    return;
  }
  // ~45 bytes/line * 200 Hz = 9 kB/s, under the 11.5 kB/s that 115200 baud allows.
  const EnvReading& env = g_env.reading();
  Serial.print(g_seq);
  Serial.print(',');
  Serial.print(v, 3);
  Serial.print(',');
  Serial.print(feature, 5);
  Serial.print(',');
  Serial.print(rho, 4);
  Serial.print(',');
  Serial.print(env.temp_c, 2);
  Serial.print(',');
  Serial.println(env.pressure_pa, 0);
}

void tick() {
  float rho = g_env.density();
  float feature = read_feature();
  float v = model(feature, rho);
  // Median before lead: the lead turns each quantisation step of a rotor's rate into a
  // spike train the median can no longer remove, and the LPF then turns that into bias.
  v = g_median.update(v);
  v = g_lead.update(v);
  v = g_use_ema ? g_ema.update(v) : g_lpf.update(v);
  if (v < 0.0f) v = 0.0f;
  emit(v, feature, rho);
  ++g_seq;
}

// ---- Commands ----------------------------------------------------------------------------

void print_cal() {
  sync_from_hardware();
  Serial.print("#cal {\"method\":\"");
  Serial.print(method_name());
  Serial.print("\",\"build\":\"" __DATE__ " " __TIME__ "\",\"sample_hz\":");
  Serial.print(board::kSampleHz, 1);
  for (const ParamEntry& p : kParams) {
    Serial.print(",\"");
    Serial.print(p.name);
    Serial.print("\":");
    Serial.print(*p.value, 7);
  }
  const EnvReading& env = g_env.reading();
  Serial.print(",\"rho\":");
  Serial.print(g_env.density(), 4);
  Serial.print(",\"temp_c\":");
  Serial.print(isnan(env.temp_c) ? 0.0f : env.temp_c, 2);
  Serial.print(",\"env_sensors\":");
  Serial.print(g_env.present_mask());
  Serial.println("}");
}

void print_help() {
  Serial.println("#cmds: MODE V | MODE D | HDR | GET | SET <name> <value> | TARE | ENV | STATS | HELP");
  Serial.print("#params:");
  for (const ParamEntry& p : kParams) {
    Serial.print(' ');
    Serial.print(p.name);
  }
  Serial.println();
}

void handle_set(char* args) {
  char* name = strtok(args, " \t");
  char* value = strtok(nullptr, " \t");
  if (!name || !value) {
    Serial.println("#err usage: SET <name> <value>");
    return;
  }
  char* end = nullptr;
  float v = strtof(value, &end);
  if (end == value || !isfinite(v)) {
    Serial.println("#err bad value");
    return;
  }
  for (const ParamEntry& p : kParams) {
    if (strcasecmp(name, p.name) == 0) {
      sync_from_hardware();
      *p.value = v;
      apply_filter_params();
      Serial.print("#ok ");
      Serial.print(p.name);
      Serial.print('=');
      Serial.println(*p.value, 7);  // after clamping
      return;
    }
  }
  Serial.print("#err unknown param ");
  Serial.println(name);
}

void handle_line(char* line) {
  while (*line == ' ') ++line;
  char* args = line;
  while (*args && *args != ' ') ++args;
  if (*args) *args++ = '\0';

  if (strcasecmp(line, "MODE") == 0) {
    if (toupper(args[0]) == 'D') {
      g_mode = Mode::kDebug;
      print_header();
    } else if (toupper(args[0]) == 'V') {
      g_mode = Mode::kVelocity;
    } else {
      Serial.println("#err usage: MODE V|D");
    }
  } else if (strcasecmp(line, "HDR") == 0) {
    print_header();
  } else if (strcasecmp(line, "GET") == 0) {
    print_cal();
  } else if (strcasecmp(line, "SET") == 0) {
    handle_set(args);
  } else if (strcasecmp(line, "TARE") == 0) {
#if AIRSPEED_METHOD == METHOD_DRAG_PLATE
    g_load_cell.start_tare();
    Serial.println("#ok taring (keep the plate still, ~0.5 s)");
#else
    Serial.println("#err TARE only applies to the drag plate");
#endif
  } else if (strcasecmp(line, "ENV") == 0) {
    g_env.print_status(Serial);
  } else if (strcasecmp(line, "STATS") == 0) {
    Serial.print("#stats n=");
    Serial.print(g_seq);
    Serial.print(" overruns=");
    Serial.print(g_stats.overruns);
    Serial.print(" max_busy_us=");
    Serial.println(g_stats.max_busy_us);
  } else if (strcasecmp(line, "HELP") == 0 || strcmp(line, "?") == 0) {
    print_help();
  } else if (*line) {
    Serial.print("#err unknown command ");
    Serial.println(line);
  }
}

void poll_serial() {
  static char buf[64];
  static uint8_t len = 0;
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      buf[len] = '\0';
      if (len) handle_line(buf);
      len = 0;
    } else if (len < sizeof(buf) - 1) {
      buf[len++] = c;
    }
  }
}

void on_tick() { g_ticks = g_ticks + 1; }

}  // namespace

void setup() {
  pinMode(board::kPinLed, OUTPUT);
  Serial.begin(board::kSerialBaud);
  Wire.begin();
  Wire.setClock(board::kI2cClockHz);
  analog_in::begin();

  g_env.begin(board::env_config());
  transducer_begin();
  apply_filter_params();

  g_tick_timer.setOverflow(static_cast<uint32_t>(board::kSampleHz), HERTZ_FORMAT);
  g_tick_timer.attachInterrupt(on_tick);
  g_tick_timer.resume();
}

void loop() {
  static uint32_t handled = 0;
  uint32_t ticks = g_ticks;
  if (ticks != handled) {
    if (ticks - handled > 1) g_stats.overruns += ticks - handled - 1;
    handled = ticks;
    uint32_t t0 = micros();
    tick();
    uint32_t busy = micros() - t0;
    if (busy > g_stats.max_busy_us) g_stats.max_busy_us = busy;
    if ((g_seq & 0x7F) == 0) digitalToggle(board::kPinLed);  // ~1.5 Hz heartbeat
    return;
  }
  // Idle time between ticks: slow sensors and commands.
  g_env.update(millis());
  poll_serial();
}
