#!/usr/bin/env python
"""Windows UI tool for benchmarking SCPI ``write + *OPC?`` round-trip latency
of a Rohde & Schwarz radar target simulator (AREG800A).

The GUI mirrors :mod:`radar_ui_tool` so both tools feel consistent.  The
benchmark itself replicates :mod:`benchmark_opc_latency` and reports the
average / min / max / stdev round-trip time (ms) directly inside the window.

Author: Kawhi.He
"""

from __future__ import annotations

import ipaddress
import statistics
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from queue import Empty, Queue
from tkinter import messagebox, ttk

from RsInstrument import RsInstrument


DEFAULT_IP = "10.66.156.12"
DEFAULT_SPEED_MPS = 20.0
DEFAULT_R_START = 100.0
DEFAULT_R_END = 10.0
DEFAULT_RCS_DBSM = 10.0
DEFAULT_T_RES_MS = 100
DEFAULT_SOURCE = 1
DEFAULT_OBJ_INDEX = 1


@dataclass
class LatencySample:
    """One measured SCPI ``write + *OPC?`` round trip.

    Args:
        command: SCPI command sent before the ``*OPC?`` query.
        elapsed_ms: Round-trip time in milliseconds.
    """

    command: str
    elapsed_ms: float


@dataclass
class BenchmarkResult:
    """Aggregated latency statistics for a benchmark run.

    Args:
        samples: Every :class:`LatencySample` collected during the run.
    """

    samples: list[LatencySample] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Number of collected samples."""

        return len(self.samples)

    @property
    def values_ms(self) -> list[float]:
        """Latency values (ms) as a flat list."""

        return [s.elapsed_ms for s in self.samples]

    @property
    def avg_ms(self) -> float:
        """Arithmetic mean of the latency samples (ms)."""

        return statistics.fmean(self.values_ms) if self.samples else 0.0

    @property
    def min_ms(self) -> float:
        """Minimum latency observed (ms)."""

        return min(self.values_ms) if self.samples else 0.0

    @property
    def max_ms(self) -> float:
        """Maximum latency observed (ms)."""

        return max(self.values_ms) if self.samples else 0.0

    @property
    def stdev_ms(self) -> float:
        """Population standard deviation of the latency samples (ms)."""

        return statistics.pstdev(self.values_ms) if len(self.samples) > 1 else 0.0


@dataclass
class BenchmarkConfig:
    """User-selected benchmark parameters.

    Args:
        ip: Instrument IPv4 address (LAN, hislip0).
        speed_mps: Target speed magnitude in m/s.  Applied as a negative
            Doppler speed to model an approaching target.
        r_start: Starting range in meters, must be greater than ``r_end``.
        r_end: Ending range in meters.
        rcs_dbsm: Radar cross section in dBsm.
        t_res_s: Time resolution (seconds) between two consecutive range
            updates.  Also acts as the pacing sleep when ``sleep_between``
            is true.
        sleep_between: When true, sleep ``t_res_s`` between range updates
            to mimic the real UI/demo pacing; when false, fire updates as
            fast as ``*OPC?`` allows.
        source: SCPI ``SOURce`` index.
        obj_index: SCPI ``OBJect`` index.
    """

    ip: str
    speed_mps: float
    r_start: float
    r_end: float
    rcs_dbsm: float
    t_res_s: float
    sleep_between: bool
    source: int = DEFAULT_SOURCE
    obj_index: int = DEFAULT_OBJ_INDEX


class BenchmarkRunner:
    """Executes one benchmark run and streams progress through callbacks.

    Args:
        config: The :class:`BenchmarkConfig` for the run.
        on_log: Callback invoked with a human-readable log line.
        on_sample: Callback invoked after every collected sample with the
            latest :class:`LatencySample` plus a ``(done, total)`` progress
            tuple.
        on_done: Callback invoked exactly once when the run finishes,
            passing the final :class:`BenchmarkResult` or ``None`` when the
            run failed.  Any raised exception is delivered through
            ``on_error`` first.
        on_error: Callback invoked with an ``Exception`` if the run fails.
        stop_event: Shared :class:`threading.Event` used to abort the run.
    """

    def __init__(
        self,
        config: BenchmarkConfig,
        on_log,
        on_sample,
        on_done,
        on_error,
        stop_event: threading.Event,
    ) -> None:
        self._config = config
        self._on_log = on_log
        self._on_sample = on_sample
        self._on_done = on_done
        self._on_error = on_error
        self._stop_event = stop_event

    def run(self) -> None:
        """Perform the benchmark synchronously in the current thread."""

        cfg = self._config
        approach_speed = -abs(cfg.speed_mps)
        speed_kmh = approach_speed * 3.6
        total_distance = cfg.r_start - cfg.r_end
        step_distance = abs(approach_speed) * cfg.t_res_s
        steps = max(1, int(total_distance / step_distance)) if step_distance > 0 else 1
        total_samples = steps + 5  # 4 setup + N range + 1 disable

        self._on_log(
            "Scenario: approaching target | "
            f"speed={cfg.speed_mps:.1f} m/s ({speed_kmh:+.1f} km/h) | "
            f"range {cfg.r_start:.1f} m -> {cfg.r_end:.1f} m | "
            f"RCS={cfg.rcs_dbsm:.1f} dBsm | "
            f"t_res={cfg.t_res_s * 1000:.0f} ms | steps={steps} | "
            f"sleep_between_steps={cfg.sleep_between}"
        )
        self._on_log(f"Connecting to TCPIP::{cfg.ip}::hislip0 ...")

        result = BenchmarkResult()
        instr: RsInstrument | None = None
        try:
            instr = RsInstrument(
                f"TCPIP::{cfg.ip}::hislip0",
                reset=False,
                id_query=False,
                options="SelectVisa='rs', LoggingMode=Off, LoggingToConsole=False",
            )
            instr.read_termination = "\n"
            idn = instr.query("*IDN?").strip()
            self._on_log(f"Connected: {idn}")

            base = f":SOURce{cfg.source}:AREGenerator:OBJect{cfg.obj_index}"

            setup_commands = [
                f"{base}:DOPPler:SPEed {speed_kmh}",
                f"{base}:RANGe {cfg.r_start}",
                f"{base}:RCS {cfg.rcs_dbsm}",
                f"{base}:STATe 1",
            ]
            for command in setup_commands:
                if self._stop_event.is_set():
                    raise RuntimeError("Benchmark aborted by user")
                self._timed_write_opc(instr, command, result, total_samples)

            for i in range(steps):
                if self._stop_event.is_set():
                    raise RuntimeError("Benchmark aborted by user")
                r_now = cfg.r_start + approach_speed * cfg.t_res_s * i
                if r_now < cfg.r_end:
                    r_now = cfg.r_end
                self._timed_write_opc(
                    instr, f"{base}:RANGe {r_now:.3f}", result, total_samples
                )
                if cfg.sleep_between:
                    time.sleep(cfg.t_res_s)

            self._timed_write_opc(instr, f"{base}:STATe 0", result, total_samples)
            self._on_done(result)
        except Exception as exc:  # noqa: BLE001 - forward everything to UI
            self._on_error(exc)
            self._on_done(None)
        finally:
            if instr is not None:
                try:
                    instr.close()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass

    def _timed_write_opc(
        self,
        instr: RsInstrument,
        command: str,
        result: BenchmarkResult,
        total_samples: int,
    ) -> None:
        """Send one SCPI command, wait for ``*OPC?`` and record the latency.

        Args:
            instr: Connected :class:`RsInstrument` handle.
            command: SCPI command to send before the ``*OPC?`` query.
            result: Aggregator receiving the new sample.
            total_samples: Expected total number of samples in the run,
                used to report progress.
        """

        start = time.perf_counter()
        instr.write(command)
        status = instr.query("*OPC?")
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        sample = LatencySample(command=command, elapsed_ms=elapsed_ms)
        result.samples.append(sample)
        self._on_sample(sample, len(result.samples), total_samples)
        if int(status) != 1:
            raise RuntimeError(f"Command failed (*OPC? != 1): {command}")


class BenchmarkUI:
    """Tkinter desktop UI for the SCPI latency benchmark.

    Args:
        root: The Tk root window supplied by :func:`main`.
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Radar Simulator SCPI Latency Benchmark")
        self.root.geometry("980x760")
        self.root.minsize(920, 720)
        self.root.configure(bg="#eaf0f7")

        self.ip_var = tk.StringVar(value=DEFAULT_IP)
        self.speed_var = tk.StringVar(value=f"{DEFAULT_SPEED_MPS:g}")
        self.r_start_var = tk.StringVar(value=f"{DEFAULT_R_START:g}")
        self.r_end_var = tk.StringVar(value=f"{DEFAULT_R_END:g}")
        self.rcs_var = tk.StringVar(value=f"{DEFAULT_RCS_DBSM:g}")
        self.t_res_ms_var = tk.StringVar(value=str(DEFAULT_T_RES_MS))
        self.sleep_var = tk.BooleanVar(value=True)

        self.status_var = tk.StringVar(value="Idle")
        self.progress_var = tk.StringVar(value="0 / 0")
        self.samples_var = tk.StringVar(value="-")
        self.avg_var = tk.StringVar(value="-")
        self.min_var = tk.StringVar(value="-")
        self.max_var = tk.StringVar(value="-")
        self.stdev_var = tk.StringVar(value="-")

        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._log_queue: Queue[str] = Queue()
        self._ui_queue: Queue = Queue()

        self._build_styles()
        self._build_layout()
        self._poll_log_queue()
        self._poll_ui_queue()

    # ------------------------------------------------------------------ UI

    def _build_styles(self) -> None:
        """Configure the ttk styles used throughout the window."""

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("App.TFrame", background="#eaf0f7")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure(
            "CardTitle.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
            background="#ffffff",
            foreground="#1e293b",
        )
        style.configure(
            "Field.TLabel",
            font=("Microsoft YaHei UI", 10),
            background="#ffffff",
            foreground="#334155",
        )
        style.configure(
            "Metric.TLabel",
            font=("Consolas", 14, "bold"),
            background="#ffffff",
            foreground="#0f172a",
        )
        style.configure(
            "MetricCaption.TLabel",
            font=("Microsoft YaHei UI", 9),
            background="#ffffff",
            foreground="#64748b",
        )
        style.configure(
            "Start.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
            background="#0f766e",
            foreground="#ffffff",
            borderwidth=0,
        )
        style.map(
            "Start.TButton",
            background=[("active", "#0d9488"), ("disabled", "#9ca3af")],
            foreground=[("disabled", "#f8fafc")],
        )
        style.configure(
            "Stop.TButton",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
            background="#b91c1c",
            foreground="#ffffff",
            borderwidth=0,
        )
        style.map(
            "Stop.TButton",
            background=[("active", "#dc2626"), ("disabled", "#9ca3af")],
            foreground=[("disabled", "#f8fafc")],
        )
        style.configure(
            "TEntry",
            fieldbackground="#f8fafc",
            foreground="#0f172a",
            bordercolor="#cbd5e1",
            lightcolor="#cbd5e1",
            darkcolor="#cbd5e1",
            padding=6,
        )
        style.configure(
            "Sleep.TCheckbutton",
            font=("Microsoft YaHei UI", 10),
            background="#ffffff",
            foreground="#1f2937",
        )

    def _make_card(self, parent: tk.Widget, padx: int = 12, pady: int = 10) -> tk.Frame:
        """Create a rounded-white card frame used to group widgets.

        Args:
            parent: Parent container.
            padx: Horizontal inner padding.
            pady: Vertical inner padding.

        Returns:
            The configured :class:`tk.Frame` acting as the card.
        """

        return tk.Frame(
            parent,
            bg="#ffffff",
            bd=1,
            highlightthickness=1,
            highlightbackground="#dbe4ef",
            highlightcolor="#dbe4ef",
            padx=padx,
            pady=pady,
        )

    def _build_layout(self) -> None:
        """Build the full window layout."""

        container = ttk.Frame(self.root, style="App.TFrame", padding=12)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        # --- Connection + action row ---
        conn_card = self._make_card(container)
        conn_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        conn_card.columnconfigure(1, weight=1)

        ttk.Label(conn_card, text="Connection", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8)
        )
        ttk.Label(conn_card, text="Simulator IP", style="Field.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        ttk.Entry(conn_card, textvariable=self.ip_var, width=30).grid(
            row=1, column=1, sticky="ew", padx=(10, 12)
        )
        action_frame = ttk.Frame(conn_card, style="Card.TFrame")
        action_frame.grid(row=1, column=2, sticky="e")
        self.start_btn = ttk.Button(
            action_frame,
            text="Start Benchmark",
            style="Start.TButton",
            command=self.start_benchmark,
        )
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(
            action_frame,
            text="Stop",
            style="Stop.TButton",
            command=self.stop_benchmark,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(10, 0))

        # --- Scenario config ---
        config_card = self._make_card(container)
        config_card.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        for col in range(4):
            config_card.columnconfigure(col, weight=1)
        ttk.Label(config_card, text="Scenario", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8)
        )

        self._add_field(config_card, 1, 0, "Speed (m/s)", self.speed_var)
        self._add_field(config_card, 1, 2, "RCS (dBsm)", self.rcs_var)
        self._add_field(config_card, 2, 0, "Start Range (m)", self.r_start_var)
        self._add_field(config_card, 2, 2, "End Range (m)", self.r_end_var)
        self._add_field(config_card, 3, 0, "Update Period (ms)", self.t_res_ms_var)

        sleep_chk = ttk.Checkbutton(
            config_card,
            text="Sleep between range updates (mimic real pacing)",
            variable=self.sleep_var,
            style="Sleep.TCheckbutton",
        )
        sleep_chk.grid(row=3, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=4)

        # --- Metrics ---
        metrics_card = self._make_card(container)
        metrics_card.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        for col in range(6):
            metrics_card.columnconfigure(col, weight=1)
        ttk.Label(metrics_card, text="Results", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 8)
        )
        self._add_metric(metrics_card, 1, 0, "Samples", self.samples_var)
        self._add_metric(metrics_card, 1, 1, "Average (ms)", self.avg_var)
        self._add_metric(metrics_card, 1, 2, "Min (ms)", self.min_var)
        self._add_metric(metrics_card, 1, 3, "Max (ms)", self.max_var)
        self._add_metric(metrics_card, 1, 4, "Stdev (ms)", self.stdev_var)
        self._add_metric(metrics_card, 1, 5, "Progress", self.progress_var)

        status_frame = ttk.Frame(metrics_card, style="Card.TFrame")
        status_frame.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(10, 0))
        status_frame.columnconfigure(1, weight=1)
        ttk.Label(status_frame, text="Status:", style="MetricCaption.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(status_frame, textvariable=self.status_var, style="Field.TLabel").grid(
            row=0, column=1, sticky="w", padx=(6, 0)
        )
        self.progress_bar = ttk.Progressbar(
            status_frame, orient="horizontal", mode="determinate", maximum=100
        )
        self.progress_bar.grid(row=0, column=2, sticky="ew", padx=(12, 0))
        status_frame.columnconfigure(2, weight=2)

        # --- Log ---
        log_card = self._make_card(container, padx=10, pady=10)
        log_card.grid(row=3, column=0, sticky="nsew")
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)
        ttk.Label(log_card, text="Log", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 6))
        self.log_text = tk.Text(
            log_card,
            height=18,
            font=("Consolas", 10),
            wrap="word",
            state=tk.DISABLED,
            bg="#0b1220",
            fg="#dbeafe",
            insertbackground="#dbeafe",
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _add_field(
        self, parent: tk.Widget, row: int, col: int, label: str, variable: tk.StringVar
    ) -> None:
        """Place a label + entry pair inside a config card.

        Args:
            parent: Parent card frame.
            row: Grid row.
            col: Grid column for the label (entry uses ``col + 1``).
            label: Display text.
            variable: Bound :class:`tk.StringVar`.
        """

        ttk.Label(parent, text=label, style="Field.TLabel").grid(
            row=row, column=col, sticky="w", pady=4, padx=(0, 6)
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=col + 1, sticky="ew", pady=4, padx=(0, 12)
        )

    def _add_metric(
        self, parent: tk.Widget, row: int, col: int, caption: str, variable: tk.StringVar
    ) -> None:
        """Place one large metric readout inside the results card.

        Args:
            parent: Parent card frame.
            row: Grid row of the caption.
            col: Grid column.
            caption: Small caption text above the number.
            variable: :class:`tk.StringVar` displayed as the value.
        """

        cell = ttk.Frame(parent, style="Card.TFrame")
        cell.grid(row=row, column=col, sticky="nsew", padx=6)
        ttk.Label(cell, text=caption, style="MetricCaption.TLabel").pack(anchor="w")
        ttk.Label(cell, textvariable=variable, style="Metric.TLabel").pack(anchor="w")

    # -------------------------------------------------------------- helpers

    def _append_log(self, text: str) -> None:
        """Enqueue a log line prefixed with a wall-clock timestamp."""

        timestamp = time.strftime("%H:%M:%S")
        self._log_queue.put(f"[{timestamp}] {text}\n")

    def _poll_log_queue(self) -> None:
        """Drain the log queue and append to the Text widget every 120 ms."""

        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg)
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except Empty:
            pass
        self.root.after(120, self._poll_log_queue)

    def _poll_ui_queue(self) -> None:
        """Drain UI-thread updates (progress + final result) every 80 ms."""

        try:
            while True:
                event = self._ui_queue.get_nowait()
                kind = event[0]
                if kind == "sample":
                    _, done, total, _sample = event
                    self.samples_var.set(str(done))
                    self.progress_var.set(f"{done} / {total}")
                    if total > 0:
                        self.progress_bar["value"] = min(100.0, done * 100.0 / total)
                elif kind == "result":
                    _, result = event
                    self._render_result(result)
                elif kind == "status":
                    _, text = event
                    self.status_var.set(text)
                elif kind == "buttons_idle":
                    self.start_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
        except Empty:
            pass
        self.root.after(80, self._poll_ui_queue)

    def _render_result(self, result: BenchmarkResult) -> None:
        """Populate the metrics card with the final aggregated result."""

        self.samples_var.set(str(result.count))
        self.avg_var.set(f"{result.avg_ms:.2f}")
        self.min_var.set(f"{result.min_ms:.2f}")
        self.max_var.set(f"{result.max_ms:.2f}")
        self.stdev_var.set(f"{result.stdev_ms:.2f}")

        self._append_log("=" * 56)
        self._append_log("SCPI write + *OPC? round-trip latency")
        self._append_log(
            f"samples={result.count} | avg={result.avg_ms:.2f} ms | "
            f"min={result.min_ms:.2f} ms | max={result.max_ms:.2f} ms | "
            f"stdev={result.stdev_ms:.2f} ms"
        )
        if result.samples:
            self._append_log("first 5 samples:")
            for s in result.samples[:5]:
                self._append_log(f"  {s.elapsed_ms:8.3f} ms  <-  {s.command}")
            self._append_log("last 5 samples:")
            for s in result.samples[-5:]:
                self._append_log(f"  {s.elapsed_ms:8.3f} ms  <-  {s.command}")
        self._append_log("=" * 56)

    def _reset_result_display(self) -> None:
        """Clear the metrics readout before starting a new run."""

        for var in (self.samples_var, self.avg_var, self.min_var, self.max_var, self.stdev_var):
            var.set("-")
        self.progress_var.set("0 / 0")
        self.progress_bar["value"] = 0

    @staticmethod
    def _validate_ip(text: str) -> str:
        """Return a normalized IPv4 string or raise :class:`ValueError`."""

        try:
            return str(ipaddress.ip_address(text.strip()))
        except ValueError as exc:
            raise ValueError("Invalid IP address format") from exc

    @staticmethod
    def _read_float(text: str, label: str) -> float:
        """Parse a UI float field or raise :class:`ValueError` with context."""

        try:
            return float(text.strip())
        except ValueError as exc:
            raise ValueError(f"{label} must be a number") from exc

    def _build_config(self) -> BenchmarkConfig:
        """Assemble a :class:`BenchmarkConfig` from the current UI state."""

        ip = self._validate_ip(self.ip_var.get())
        speed = self._read_float(self.speed_var.get(), "Speed")
        r_start = self._read_float(self.r_start_var.get(), "Start range")
        r_end = self._read_float(self.r_end_var.get(), "End range")
        rcs = self._read_float(self.rcs_var.get(), "RCS")
        t_res_ms = self._read_float(self.t_res_ms_var.get(), "Update period")

        if speed <= 0:
            raise ValueError("Speed must be a positive magnitude in m/s")
        if r_start <= r_end:
            raise ValueError("Start range must be greater than end range")
        if t_res_ms <= 0:
            raise ValueError("Update period must be greater than 0 ms")

        return BenchmarkConfig(
            ip=ip,
            speed_mps=speed,
            r_start=r_start,
            r_end=r_end,
            rcs_dbsm=rcs,
            t_res_s=t_res_ms / 1000.0,
            sleep_between=bool(self.sleep_var.get()),
        )

    # ------------------------------------------------------------- actions

    def start_benchmark(self) -> None:
        """Validate inputs and launch a benchmark worker thread."""

        if self._worker and self._worker.is_alive():
            self._append_log("Benchmark is already running")
            return
        try:
            config = self._build_config()
        except ValueError as exc:
            messagebox.showerror("Input Error", str(exc))
            self._append_log(f"Input validation failed: {exc}")
            return

        self._reset_result_display()
        self._stop_event.clear()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._ui_queue.put(("status", "Running"))

        self._worker = threading.Thread(
            target=self._run_benchmark_thread, args=(config,), daemon=True
        )
        self._worker.start()

    def stop_benchmark(self) -> None:
        """Request the current benchmark run to abort."""

        self._stop_event.set()
        self._append_log("Stopping benchmark ...")
        self._ui_queue.put(("status", "Stopping"))
        self.stop_btn.configure(state=tk.DISABLED)

    # ---------------------------------------------------- worker callbacks

    def _run_benchmark_thread(self, config: BenchmarkConfig) -> None:
        """Worker thread body: run the benchmark and marshal results."""

        runner = BenchmarkRunner(
            config=config,
            on_log=self._append_log,
            on_sample=self._on_sample,
            on_done=self._on_done,
            on_error=self._on_error,
            stop_event=self._stop_event,
        )
        try:
            runner.run()
        finally:
            self._ui_queue.put(("buttons_idle",))

    def _on_sample(self, sample: LatencySample, done: int, total: int) -> None:
        """Handle a per-sample callback from the runner (worker thread)."""

        self._ui_queue.put(("sample", done, total, sample))

    def _on_done(self, result: BenchmarkResult | None) -> None:
        """Handle end-of-run callback from the runner (worker thread)."""

        if result is None:
            self._ui_queue.put(("status", "Error"))
            return
        self._ui_queue.put(("result", result))
        self._ui_queue.put(("status", "Done"))

    def _on_error(self, exc: Exception) -> None:
        """Handle error callback from the runner (worker thread)."""

        self._append_log(f"Benchmark failed: {exc}")


def main() -> None:
    """Entry point of the benchmark UI tool."""

    root = tk.Tk()
    app = BenchmarkUI(root)

    def _on_close() -> None:
        """Signal the worker to stop before destroying the window."""

        app.stop_benchmark()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
