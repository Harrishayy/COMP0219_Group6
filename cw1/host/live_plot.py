#!/usr/bin/env python3
"""Live scrolling plot of the board's stream.

    python host/live_plot.py                 # velocity
    python host/live_plot.py --mode d -c feature -c v
    python host/live_plot.py --app -c rho    # bench-tool firmware (e.g. env_monitor)
"""

from __future__ import annotations

import argparse
import collections
import threading

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from airspeed_tools import serialio


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--mode", choices=["v", "d"])
    ap.add_argument("--app", action="store_true", help="bench-tool firmware: send no commands")
    ap.add_argument("-c", "--column", action="append", help="column(s) to plot (default: v)")
    ap.add_argument("-w", "--window", type=float, default=10.0, help="seconds shown")
    args = ap.parse_args()
    wanted = args.column or ["v"]

    ser = serialio.open_port(args.port)
    if not args.app:
        if args.mode:
            serialio.send(ser, f"MODE {args.mode.upper()}")
        serialio.send(ser, "HDR")

    maxlen = int(args.window * 250)
    times = collections.deque(maxlen=maxlen)
    series = {c: collections.deque(maxlen=maxlen) for c in wanted}
    cols = serialio.Columns()
    lock = threading.Lock()

    def reader():
        for line in serialio.read_lines(ser):
            if line.values is None:
                cols.update(line)
                continue
            names = cols.for_values(line.values)
            with lock:
                times.append(line.host_time)
                for c in wanted:
                    series[c].append(line.values[names.index(c)] if c in names else float("nan"))

    threading.Thread(target=reader, daemon=True).start()

    fig, ax = plt.subplots(figsize=(10, 4))
    lines = {c: ax.plot([], [], label=c)[0] for c in wanted}
    ax.set_xlabel("time (s)")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)

    def update(_):
        with lock:
            if not times:
                return list(lines.values())
            t_end = times[-1]
            t = [x - t_end for x in times]
            for c, ln in lines.items():
                ln.set_data(t, list(series[c]))
        ax.set_xlim(-args.window, 0)
        ax.relim()
        ax.autoscale_view(scalex=False)
        return list(lines.values())

    _anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    main()
