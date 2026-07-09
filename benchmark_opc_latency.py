"""Benchmark the SCPI write + ``*OPC?`` round-trip latency of the R&S radar target simulator.

Scenario
--------
Approaching target, speed = 20 m/s (negative Doppler), range sweeps from
100 m down to 10 m, RCS = 10 dBsm, ``t_res`` = 0.1 s (2 m per step).

For every SCPI ``write`` immediately followed by an ``*OPC?`` query, the
elapsed time is recorded with :func:`time.perf_counter`. Setup commands,
per-step range updates and the final disable command are all included in
the statistics so the reported average reflects the real cost of talking
to the simulator during a full run.

Author: Kawhi.He
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field

from RsInstrument import RsInstrument


DEFAULT_IP = "10.66.156.12"


@dataclass
class LatencySample:
    """One measured SCPI ``write`` + ``*OPC?`` round trip.

    Attributes
    ----------
    command:
        The SCPI command that was written before querying ``*OPC?``.
    elapsed_ms:
        Round-trip time in milliseconds.
    """

    command: str
    elapsed_ms: float


@dataclass
class BenchmarkResult:
    """Aggregated latency statistics for a benchmark run.

    Attributes
    ----------
    samples:
        Every individual :class:`LatencySample` collected during the run.
    """

    samples: list[LatencySample] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def values_ms(self) -> list[float]:
        return [s.elapsed_ms for s in self.samples]

    @property
    def avg_ms(self) -> float:
        return statistics.fmean(self.values_ms) if self.samples else 0.0

    @property
    def min_ms(self) -> float:
        return min(self.values_ms) if self.samples else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.values_ms) if self.samples else 0.0

    @property
    def stdev_ms(self) -> float:
        return statistics.pstdev(self.values_ms) if len(self.samples) > 1 else 0.0


def timed_write_opc(instr: RsInstrument, command: str, result: BenchmarkResult) -> None:
    """Send one SCPI command, wait for ``*OPC?`` and record the round trip.

    Parameters
    ----------
    instr:
        Connected :class:`RsInstrument` handle.
    command:
        SCPI command to send before the ``*OPC?`` query.
    result:
        Aggregator that receives the latency sample.

    Raises
    ------
    RuntimeError
        If the instrument does not report ``*OPC? == 1``.
    """

    start = time.perf_counter()
    instr.write(command)
    status = instr.query("*OPC?")
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    result.samples.append(LatencySample(command=command, elapsed_ms=elapsed_ms))
    if int(status) != 1:
        raise RuntimeError(f"Command failed (*OPC? != 1): {command}")


def run_benchmark(
    ip: str = DEFAULT_IP,
    speed_mps: float = 20.0,
    r_start: float = 100.0,
    r_end: float = 10.0,
    rcs_dbsm: float = 10.0,
    t_res: float = 0.1,
    source: int = 1,
    obj_index: int = 1,
    sleep_between_steps: bool = True,
) -> BenchmarkResult:
    """Run the approaching-target benchmark and return latency statistics.

    Parameters
    ----------
    ip:
        Instrument IPv4 address (LAN / hislip).
    speed_mps:
        Target speed magnitude in m/s. It is applied as a **negative**
        Doppler speed to model an approaching target.
    r_start, r_end:
        Start and end range in meters. ``r_start`` must be greater than
        ``r_end`` for an approaching scenario.
    rcs_dbsm:
        Radar cross section in dBsm.
    t_res:
        Time resolution in seconds between two consecutive range updates.
    source, obj_index:
        SCPI ``SOURce`` and ``OBJect`` indices used in the commands.

    Returns
    -------
    BenchmarkResult
        Collected samples plus average / min / max / stdev in ms.
    """

    if r_start <= r_end:
        raise ValueError("r_start must be greater than r_end for an approaching scenario")
    if speed_mps <= 0:
        raise ValueError("speed_mps must be a positive magnitude")
    if t_res <= 0:
        raise ValueError("t_res must be positive")

    approach_speed = -abs(speed_mps)  # negative Doppler = approaching
    speed_kmh = approach_speed * 3.6
    total_distance = r_start - r_end
    steps = max(1, int(total_distance / (abs(approach_speed) * t_res)))

    print(
        "Scenario: approaching target | "
        f"speed={speed_mps:.1f} m/s ({speed_kmh:+.1f} km/h) | "
        f"range {r_start:.1f} m -> {r_end:.1f} m | "
        f"RCS={rcs_dbsm:.1f} dBsm | t_res={t_res:.3f} s | steps={steps} | "
        f"sleep_between_steps={sleep_between_steps}"
    )
    print(f"Connecting to TCPIP::{ip}::hislip0 ...")

    result = BenchmarkResult()
    instr = RsInstrument(
        f"TCPIP::{ip}::hislip0",
        reset=False,
        id_query=False,
        options="SelectVisa='rs', LoggingMode=Off, LoggingToConsole=False",
    )
    instr.read_termination = "\n"

    try:
        idn = instr.query("*IDN?").strip()
        print(f"Connected: {idn}")

        base = f":SOURce{source}:AREGenerator:OBJect{obj_index}"

        # --- initial setup ---
        timed_write_opc(instr, f"{base}:DOPPler:SPEed {speed_kmh}", result)
        timed_write_opc(instr, f"{base}:RANGe {r_start}", result)
        timed_write_opc(instr, f"{base}:RCS {rcs_dbsm}", result)
        timed_write_opc(instr, f"{base}:STATe 1", result)

        # --- range sweep ---
        for i in range(steps):
            r_now = r_start + approach_speed * t_res * i  # decreasing
            if r_now < r_end:
                r_now = r_end
            timed_write_opc(instr, f"{base}:RANGe {r_now:.3f}", result)
            if sleep_between_steps:
                time.sleep(t_res)  # excluded from measurement

        # --- teardown ---
        timed_write_opc(instr, f"{base}:STATe 0", result)
    finally:
        instr.close()

    return result


def print_report(result: BenchmarkResult) -> None:
    """Pretty-print aggregated latency statistics for a benchmark run.

    Parameters
    ----------
    result:
        The :class:`BenchmarkResult` produced by :func:`run_benchmark`.
    """

    print()
    print("=" * 60)
    print("SCPI write + *OPC? round-trip latency")
    print("=" * 60)
    print(f"samples : {result.count}")
    print(f"average : {result.avg_ms:8.3f} ms")
    print(f"min     : {result.min_ms:8.3f} ms")
    print(f"max     : {result.max_ms:8.3f} ms")
    print(f"stdev   : {result.stdev_ms:8.3f} ms")
    print("-" * 60)
    print("first 5 samples:")
    for s in result.samples[:5]:
        print(f"  {s.elapsed_ms:8.3f} ms  <-  {s.command}")
    print("last 5 samples:")
    for s in result.samples[-5:]:
        print(f"  {s.elapsed_ms:8.3f} ms  <-  {s.command}")
    print("=" * 60)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", default=DEFAULT_IP, help="Instrument IP address")
    parser.add_argument("--speed", type=float, default=20.0, help="Speed magnitude in m/s")
    parser.add_argument("--r-start", type=float, default=100.0, help="Start range in meters")
    parser.add_argument("--r-end", type=float, default=10.0, help="End range in meters")
    parser.add_argument("--rcs", type=float, default=10.0, help="RCS in dBsm")
    parser.add_argument("--t-res", type=float, default=0.1, help="Time resolution in seconds")
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Skip the time.sleep(t_res) pacing between range updates (max-throughput mode)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_benchmark(
            ip=args.ip,
            speed_mps=args.speed,
            r_start=args.r_start,
            r_end=args.r_end,
            rcs_dbsm=args.rcs,
            t_res=args.t_res,
            sleep_between_steps=not args.no_sleep,
        )
    except Exception as exc:  # noqa: BLE001 - report and fail cleanly
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1
    print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
