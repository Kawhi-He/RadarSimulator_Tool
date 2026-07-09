#!/usr/bin/env python
"""Windows UI tool for radar simulator control.

Author: Kawhi.He
"""

from __future__ import annotations

import ipaddress
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from queue import Empty, Queue
from tkinter import messagebox, ttk

from RsInstrument import RsInstrument


DEFAULT_IP = "10.66.156.12"
DEFAULT_SOURCE = 1
# Radar frame period (100 ms == 10 Hz). Kept for backward compatibility.
DEFAULT_TIME_RESOLUTION = 0.1
# SCPI RANGe update period used by the dynamic loop.  Aligned with the radar
# sampling period (100 ms == 10 Hz).  Writes are fired without the *OPC?
# handshake so the SCPI round-trip latency does not stretch the effective
# update period; the instrument's own apply-rate provides back-pressure.
DYNAMIC_UPDATE_PERIOD = 0.1

# SCPI query for the AREG "Radar Power" traffic-light status shown on the
# instrument front panel.  The response is a short string such as ``OK`` /
# ``WARN`` / ``ERR`` which the UI maps to green / yellow / red.
RADAR_STATUS_QUERY = ":SOURce1:AREGenerator:CHANnel1:CONDition?"
# How often the background monitor polls the radar condition (seconds).
RADAR_STATUS_POLL_PERIOD = 0.5
# One-shot SCPI command triggered by the "Adjust" button next to the LED.
RADAR_ADJUST_LEVEL_CMD = ":SOURce1:AREGenerator:MAPPing1:ADJust:LEVel"


@dataclass
class StaticTargetConfig:
    """Configuration for one static target.

    Args:
        distance_m: Target distance in meters.
        speed_ms: Doppler speed in meters per second.
        rcs_db: Target RCS in dB.
    """

    distance_m: float
    speed_ms: float
    rcs_db: float


@dataclass
class DynamicTargetConfig:
    """Configuration for one dynamic looping target.

    Args:
        start_distance_m: Motion start distance in meters.
        end_distance_m: Motion end distance in meters.
        speed_ms: Doppler speed in meters per second.
        rcs_db: Target RCS in dB.
        update_period_s: SCPI RANGe update period in seconds.
    """

    start_distance_m: float
    end_distance_m: float
    speed_ms: float
    rcs_db: float
    update_period_s: float = 0.1


@dataclass
class MotionTargetConfig:
    """Configuration for one preset motion target.

    Args:
        distance_m: Fixed target distance in meters.
        min_speed_ms: Minimum speed in m/s.
        max_speed_ms: Maximum speed in m/s.
        speed_step_ms: Speed update step in m/s.
        step_period_s: Update period in seconds.
        rcs_db: Target RCS in dB.
    """

    distance_m: float
    min_speed_ms: float
    max_speed_ms: float
    speed_step_ms: float
    step_period_s: float
    rcs_db: float


class RadarSimulatorClient:
    """Simple SCPI client for configuring radar object 1.

    Args:
        ip: Simulator IP address.
        source: SCPI source index.
    """

    def __init__(self, ip: str, source: int = DEFAULT_SOURCE) -> None:
        self.ip = ip
        self.source = source
        self._instr: RsInstrument | None = None

    @staticmethod
    def speed_ms_to_kmh(speed_ms: float) -> float:
        """Convert speed from m/s to km/h.

        Args:
            speed_ms: Speed in m/s.

        Returns:
            Speed in km/h.
        """

        return speed_ms * 3.6

    def connect(self) -> None:
        """Connect to radar simulator.

        Returns:
            None.
        """

        self._instr = RsInstrument(
            f"TCPIP::{self.ip}::hislip0",
            reset=False,
            id_query=False,
            options="SelectVisa='rs', LoggingMode=Off, LoggingToConsole=False",
        )
        self._instr.read_termination = "\n"

    def close(self) -> None:
        """Close simulator connection.

        Returns:
            None.
        """

        if self._instr is not None:
            self._instr.close()
            self._instr = None

    def write_and_wait(self, command: str) -> None:
        """Send a SCPI command and wait until operation is complete.

        Args:
            command: SCPI command string.

        Returns:
            None.
        """

        if self._instr is None:
            raise RuntimeError("Instrument is not connected")
        self._instr.write(command)
        status = self._instr.query("*OPC?")
        if int(status) != 1:
            raise RuntimeError(f"SCPI command failed: {command}")

    def write_no_wait(self, command: str) -> None:
        """Send a SCPI command without waiting for OPC.

        Args:
            command: SCPI command string.

        Returns:
            None.
        """

        if self._instr is None:
            raise RuntimeError("Instrument is not connected")
        self._instr.write(command)

    def enable_object(self, speed_ms: float, distance_m: float, rcs_db: float) -> None:
        """Enable object 1 with basic parameters.

        Args:
            speed_ms: Doppler speed in m/s.
            distance_m: Distance in meters.
            rcs_db: RCS in dB.

        Returns:
            None.
        """

        prefix = f":SOURce{self.source}:AREGenerator:OBJect1"
        self.write_and_wait(f"{prefix}:DOPPler:SPEed {self.speed_ms_to_kmh(speed_ms)}")
        self.write_and_wait(f"{prefix}:RANGe {distance_m}")
        self.write_and_wait(f"{prefix}:RCS {rcs_db}")
        self.write_and_wait(f"{prefix}:STATe 1")

    def set_range(self, distance_m: float) -> None:
        """Update object 1 range.

        Args:
            distance_m: Distance in meters.

        Returns:
            None.
        """

        self.write_and_wait(f":SOURce{self.source}:AREGenerator:OBJect1:RANGe {distance_m}")

    def set_range_fast(self, distance_m: float) -> None:
        """Update object 1 range without waiting for OPC.

        Args:
            distance_m: Distance in meters.

        Returns:
            None.
        """

        self.write_no_wait(f":SOURce{self.source}:AREGenerator:OBJect1:RANGe {distance_m}")

    def set_speed(self, speed_ms: float) -> None:
        """Update object 1 speed.

        Args:
            speed_ms: Doppler speed in m/s.

        Returns:
            None.
        """

        speed_kmh = self.speed_ms_to_kmh(speed_ms)
        self.write_and_wait(f":SOURce{self.source}:AREGenerator:OBJect1:DOPPler:SPEed {speed_kmh}")

    def disable_object(self) -> None:
        """Disable object 1.

        Returns:
            None.
        """

        self.write_and_wait(f":SOURce{self.source}:AREGenerator:OBJect1:STATe 0")


class RadarSimulatorUI:
    """Tkinter desktop UI for controlling radar simulator.

    Returns:
        None.
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Radar Simulator Tool")
        self.root.geometry("980x740")
        self.root.minsize(920, 700)
        self.root.configure(bg="#eaf0f7")

        self.mode_var = tk.StringVar(value="static")
        self.ip_var = tk.StringVar(value=DEFAULT_IP)
        self.status_var = tk.StringVar(value="Idle")

        self.static_distance_var = tk.StringVar(value="20")
        self.static_speed_var = tk.StringVar(value="-10")
        self.static_speed_unit_var = tk.StringVar(value="m/s")
        self.static_rcs_var = tk.StringVar(value="30")

        self.dynamic_start_var = tk.StringVar(value="120")
        self.dynamic_end_var = tk.StringVar(value="4")
        self.dynamic_speed_var = tk.StringVar(value="-5")
        self.dynamic_speed_unit_var = tk.StringVar(value="m/s")
        self.dynamic_rcs_var = tk.StringVar(value="10")

        self._client: RadarSimulatorClient | None = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._log_queue: Queue[str] = Queue()

        # Background thread that polls the instrument condition light.  Uses
        # its own RsInstrument session so it never contends with the
        # simulation worker for a single SCPI socket.
        self._status_monitor_thread: threading.Thread | None = None
        self._status_monitor_stop = threading.Event()
        # Set by the "Adjust" button; the monitor thread issues the SCPI
        # command on its next iteration so the UI stays responsive.
        self._adjust_pending = threading.Event()
        self._radar_status_text = tk.StringVar(value="N/A")

        self._build_styles()
        self._build_layout()
        self._update_mode_sections()
        self._poll_log_queue()
        self._set_status("idle", "Idle")
        self._start_status_monitor()

    def _build_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("App.TFrame", background="#eaf0f7")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure(
            "Header.TLabel",
            font=("Microsoft YaHei UI", 18, "bold"),
            background="#eaf0f7",
            foreground="#0f172a",
        )
        style.configure(
            "Hint.TLabel",
            font=("Microsoft YaHei UI", 10),
            background="#eaf0f7",
            foreground="#334155",
        )
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
            "StatusIdle.TLabel",
            font=("Microsoft YaHei UI", 10, "bold"),
            background="#dbeafe",
            foreground="#1e3a8a",
            padding=(10, 4),
        )
        style.configure(
            "StatusRunning.TLabel",
            font=("Microsoft YaHei UI", 10, "bold"),
            background="#dcfce7",
            foreground="#166534",
            padding=(10, 4),
        )
        style.configure(
            "StatusError.TLabel",
            font=("Microsoft YaHei UI", 10, "bold"),
            background="#fee2e2",
            foreground="#991b1b",
            padding=(10, 4),
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
            "Mode.TRadiobutton",
            font=("Microsoft YaHei UI", 10),
            background="#ffffff",
            foreground="#1f2937",
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
            "TCombobox",
            fieldbackground="#f8fafc",
            foreground="#0f172a",
            bordercolor="#cbd5e1",
            lightcolor="#cbd5e1",
            darkcolor="#cbd5e1",
            padding=4,
        )

    def _make_card(self, parent: tk.Widget, padx: int = 12, pady: int = 9) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg="#ffffff",
            bd=1,
            highlightthickness=1,
            highlightbackground="#dbe4ef",
            highlightcolor="#dbe4ef",
            padx=padx,
            pady=pady,
        )
        return card

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, style="App.TFrame", padding=12)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        # Keep top configuration area compact and let log section consume
        # most of the remaining height.
        container.rowconfigure(2, weight=0)
        container.rowconfigure(3, weight=3)

        conn_card = self._make_card(container)
        conn_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        conn_card.columnconfigure(1, weight=1)

        ttk.Label(conn_card, text="Connection", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w", columnspan=2, pady=(0, 8)
        )
        ttk.Label(conn_card, text="Simulator IP", style="Field.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        ttk.Entry(conn_card, textvariable=self.ip_var, width=30).grid(
            row=1, column=1, sticky="ew", padx=(10, 0)
        )

        # Radar Power condition indicator - a colored dot plus a text label
        # plus a one-shot "Adjust" button that triggers the AREG level
        # adjustment.  All three live on the same row as the IP entry.
        status_holder = tk.Frame(conn_card, bg="#ffffff")
        status_holder.grid(row=1, column=2, sticky="e", padx=(16, 0))
        ttk.Label(
            status_holder,
            text="Radar Power:",
            style="Field.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side=tk.LEFT)
        # Enlarged 28x28 canvas so the LED reads clearly from a distance.
        self._radar_status_canvas = tk.Canvas(
            status_holder,
            width=28,
            height=28,
            bg="#ffffff",
            highlightthickness=0,
        )
        self._radar_status_canvas.pack(side=tk.LEFT, padx=(10, 8))
        self._radar_status_dot = self._radar_status_canvas.create_oval(
            3, 3, 25, 25, fill="#9ca3af", outline="#6b7280", width=2
        )
        ttk.Label(
            status_holder,
            textvariable=self._radar_status_text,
            style="Field.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
            width=9,
            anchor="w",
        ).pack(side=tk.LEFT)
        self.adjust_btn = ttk.Button(
            status_holder,
            text="Adjust",
            style="Start.TButton",
            command=self.trigger_adjust_level,
        )
        self.adjust_btn.pack(side=tk.LEFT, padx=(12, 0))

        mode_card = self._make_card(container)
        mode_card.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        mode_card.columnconfigure(2, weight=1)
        ttk.Label(mode_card, text="Target Mode", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w", columnspan=2, pady=(0, 8)
        )
        ttk.Radiobutton(
            mode_card,
            text="Static Target",
            variable=self.mode_var,
            value="static",
            style="Mode.TRadiobutton",
            command=self._update_mode_sections,
        ).grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(
            mode_card,
            text="Dynamic Target (Loop)",
            variable=self.mode_var,
            value="dynamic",
            style="Mode.TRadiobutton",
            command=self._update_mode_sections,
        ).grid(row=1, column=1, sticky="w", padx=(24, 0))
        ttk.Radiobutton(
            mode_card,
            text="Motion Target (Preset)",
            variable=self.mode_var,
            value="motion",
            style="Mode.TRadiobutton",
            command=self._update_mode_sections,
        ).grid(row=1, column=2, sticky="w", padx=(24, 0))

        mode_action_frame = ttk.Frame(mode_card, style="Card.TFrame")
        mode_action_frame.grid(row=1, column=3, sticky="e")
        self.start_btn = ttk.Button(
            mode_action_frame,
            text="Simulation starts",
            style="Start.TButton",
            command=self.start_simulation,
        )
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(
            mode_action_frame,
            text="Simulation stops",
            style="Stop.TButton",
            command=self.stop_simulation,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(10, 0))

        config_frame = ttk.Frame(container, style="App.TFrame")
        config_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        config_frame.columnconfigure(0, weight=1, uniform="target_col")
        config_frame.columnconfigure(1, weight=1, uniform="target_col")

        self.static_frame = self._make_card(config_frame)
        self.static_frame.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Label(self.static_frame, text="Static Target Config", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w", columnspan=2, pady=(0, 8)
        )
        self._build_static_fields(self.static_frame)

        self.dynamic_frame = self._make_card(config_frame)
        self.dynamic_frame.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(self.dynamic_frame, text="Dynamic Target Config", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w", columnspan=2, pady=(0, 8)
        )
        self._build_dynamic_fields(self.dynamic_frame)

        log_frame = self._make_card(container, padx=10, pady=10)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text="Log", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 6))
        self.log_text = tk.Text(
            log_frame,
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

    def _build_static_fields(self, parent: tk.Widget) -> None:
        parent.columnconfigure(0, minsize=128)
        parent.columnconfigure(1, weight=1)
        for row in range(1, 6):
            parent.rowconfigure(row, minsize=38)

        ttk.Label(parent, text="Distance (m)", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=4
        )
        ttk.Entry(parent, textvariable=self.static_distance_var).grid(
            row=1, column=1, sticky="ew", pady=4
        )

        ttk.Label(parent, text="Doppler Speed", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=4
        )
        speed_frame = ttk.Frame(parent)
        speed_frame.grid(row=2, column=1, sticky="ew", pady=4)
        speed_frame.columnconfigure(0, weight=1)
        ttk.Entry(speed_frame, textvariable=self.static_speed_var).grid(row=0, column=0, sticky="ew")
        ttk.Combobox(
            speed_frame,
            textvariable=self.static_speed_unit_var,
            values=["m/s", "km/h"],
            width=8,
            state="readonly",
        ).grid(
            row=0, column=1, padx=(8, 0)
        )

        ttk.Label(parent, text="RCS (dB)", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", pady=4
        )
        ttk.Entry(parent, textvariable=self.static_rcs_var).grid(
            row=3, column=1, sticky="ew", pady=4
        )

        # Keep static panel row structure symmetric with dynamic panel.
        for spacer_row in (4, 5):
            tk.Label(parent, text="", bg="#ffffff").grid(
                row=spacer_row, column=0, sticky="w", pady=4
            )
            spacer = tk.Frame(parent, bg="#ffffff", height=32)
            spacer.grid(row=spacer_row, column=1, sticky="ew", pady=4)
            spacer.grid_propagate(False)

    def _build_dynamic_fields(self, parent: tk.Widget) -> None:
        parent.columnconfigure(0, minsize=128)
        parent.columnconfigure(1, weight=1)
        for row in range(1, 6):
            parent.rowconfigure(row, minsize=38)

        ttk.Label(parent, text="Start Distance (m)", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", pady=4
        )
        ttk.Entry(parent, textvariable=self.dynamic_start_var).grid(
            row=1, column=1, sticky="ew", pady=4
        )

        ttk.Label(parent, text="End Distance (m)", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Entry(parent, textvariable=self.dynamic_end_var).grid(
            row=2, column=1, sticky="ew", pady=4
        )

        ttk.Label(parent, text="Doppler Speed", style="Field.TLabel").grid(
            row=3, column=0, sticky="w", pady=4
        )
        speed_frame = ttk.Frame(parent)
        speed_frame.grid(row=3, column=1, sticky="ew", pady=4)
        speed_frame.columnconfigure(0, weight=1)
        ttk.Entry(speed_frame, textvariable=self.dynamic_speed_var).grid(row=0, column=0, sticky="ew")
        ttk.Combobox(
            speed_frame,
            textvariable=self.dynamic_speed_unit_var,
            values=["m/s", "km/h"],
            width=8,
            state="readonly",
        ).grid(
            row=0, column=1, padx=(8, 0)
        )

        ttk.Label(parent, text="RCS (dB)", style="Field.TLabel").grid(
            row=4, column=0, sticky="w", pady=4
        )
        ttk.Entry(parent, textvariable=self.dynamic_rcs_var).grid(
            row=4, column=1, sticky="ew", pady=4
        )

        # Keep dynamic panel row structure symmetric with the static panel.
        tk.Label(parent, text="", bg="#ffffff").grid(
            row=5, column=0, sticky="w", pady=4
        )
        spacer = tk.Frame(parent, bg="#ffffff", height=32)
        spacer.grid(row=5, column=1, sticky="ew", pady=4)
        spacer.grid_propagate(False)

    def _set_status(self, status: str, text: str) -> None:
        self.status_var.set(text)

    def _set_state_recursive(self, root_widget: tk.Widget, state: str) -> None:
        for child in root_widget.winfo_children():
            try:
                if isinstance(child, ttk.Combobox):
                    child.configure(state="readonly" if state == tk.NORMAL else tk.DISABLED)
                else:
                    child.configure(state=state)
            except tk.TclError:
                pass
            self._set_state_recursive(child, state)

    def _update_mode_sections(self) -> None:
        mode = self.mode_var.get()
        static_active = mode == "static"
        dynamic_active = mode == "dynamic"
        self._set_state_recursive(self.static_frame, tk.NORMAL if static_active else tk.DISABLED)
        self._set_state_recursive(self.dynamic_frame, tk.NORMAL if dynamic_active else tk.DISABLED)

    def _append_log(self, text: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self._log_queue.put(f"[{timestamp}] {text}\n")

    def _poll_log_queue(self) -> None:
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

    @staticmethod
    def _validate_ip(ip_text: str) -> str:
        try:
            return str(ipaddress.ip_address(ip_text.strip()))
        except ValueError as exc:
            raise ValueError("Invalid IP address format") from exc

    @staticmethod
    def _to_ms(value: float, unit: str) -> float:
        return value / 3.6 if unit == "km/h" else value

    @staticmethod
    def _read_float(text: str, label: str) -> float:
        try:
            return float(text.strip())
        except ValueError as exc:
            raise ValueError(f"{label} must be a number") from exc

    def _build_static_config(self) -> StaticTargetConfig:
        distance_m = self._read_float(self.static_distance_var.get(), "Static distance")
        speed_val = self._read_float(self.static_speed_var.get(), "Static speed")
        rcs_db = self._read_float(self.static_rcs_var.get(), "Static RCS")
        speed_ms = self._to_ms(speed_val, self.static_speed_unit_var.get())
        return StaticTargetConfig(distance_m=distance_m, speed_ms=speed_ms, rcs_db=rcs_db)

    def _build_dynamic_config(self) -> DynamicTargetConfig:
        start_distance_m = self._read_float(self.dynamic_start_var.get(), "Dynamic start distance")
        end_distance_m = self._read_float(self.dynamic_end_var.get(), "Dynamic end distance")
        speed_val = self._read_float(self.dynamic_speed_var.get(), "Dynamic speed")
        rcs_db = self._read_float(self.dynamic_rcs_var.get(), "Dynamic RCS")
        speed_ms = self._to_ms(speed_val, self.dynamic_speed_unit_var.get())
        if speed_ms == 0:
            raise ValueError("Dynamic speed cannot be 0")
        return DynamicTargetConfig(
            start_distance_m=start_distance_m,
            end_distance_m=end_distance_m,
            speed_ms=speed_ms,
            rcs_db=rcs_db,
            update_period_s=DYNAMIC_UPDATE_PERIOD,
        )

    @staticmethod
    def _build_motion_config() -> MotionTargetConfig:
        return MotionTargetConfig(
            distance_m=10.0,
            min_speed_ms=-27.7,
            max_speed_ms=27.7,
            speed_step_ms=1.0,
            step_period_s=1.0,
            rcs_db=20.0,
        )

    def start_simulation(self) -> None:
        if self._worker and self._worker.is_alive():
            self._append_log("Simulation is already running")
            return

        try:
            ip = self._validate_ip(self.ip_var.get())
            mode = self.mode_var.get()
            if mode == "static":
                config = self._build_static_config()
            elif mode == "dynamic":
                config = self._build_dynamic_config()
            else:
                config = self._build_motion_config()
        except ValueError as exc:
            messagebox.showerror("Input Error", str(exc))
            self._append_log(f"Input validation failed: {exc}")
            return

        self._stop_event.clear()
        self._set_status("running", "Running")
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self._worker = threading.Thread(target=self._run_simulation, args=(ip, mode, config), daemon=True)
        self._worker.start()

    def stop_simulation(self) -> None:
        self._stop_event.set()
        self._append_log("Stopping simulation...")
        self._set_status("idle", "Stopping")
        self.stop_btn.configure(state=tk.DISABLED)

    def _run_simulation(
        self,
        ip: str,
        mode: str,
        config: StaticTargetConfig | DynamicTargetConfig | MotionTargetConfig,
    ) -> None:
        self._append_log(f"Connecting to {ip} ...")
        try:
            self._client = RadarSimulatorClient(ip)
            self._client.connect()
            self._append_log("Connection successful")

            if mode == "static":
                self._run_static_once(config)
            elif mode == "dynamic":
                self._run_dynamic_loop(config)
            else:
                self._run_motion_profile(config)
        except Exception as exc:  # noqa: BLE001
            self._set_status("error", "Error")
            self._append_log(f"Simulation error: {exc}")
        finally:
            try:
                if self._client is not None:
                    self._client.disable_object()
                    self._append_log("Object disabled")
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"Disable object failed: {exc}")
            finally:
                if self._client is not None:
                    self._client.close()
                    self._append_log("Connection closed")
                self._client = None

            self.root.after(0, self._set_idle_buttons)

    def _set_idle_buttons(self) -> None:
        self._set_status("idle", "Idle")
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Radar condition indicator (Radar Power light)
    # ------------------------------------------------------------------
    @staticmethod
    def _map_radar_condition(raw: str) -> tuple[str, str, str]:
        """Map a raw SCPI condition reply to (label, dot_color, outline).

        Args:
            raw: Raw response string from ``:SOURce1:AREGenerator:CHANnel1:CONDition?``.

        Returns:
            A tuple of ``(label, fill_color, outline_color)`` used to render
            the indicator dot and its text label.
        """

        token = (raw or "").strip().strip('"').upper()
        # AREG typically replies with short tokens; be tolerant to variants.
        if token in {"OK", "GREEN", "GOOD", "NORMAL", "PASS", "0"}:
            return "OK", "#22c55e", "#15803d"
        if token in {"WARN", "WARNING", "YELLOW", "1"}:
            return "Warning", "#eab308", "#a16207"
        if token in {"ERR", "ERROR", "RED", "FAIL", "2"}:
            return "Error", "#ef4444", "#991b1b"
        if not token:
            return "N/A", "#9ca3af", "#6b7280"
        # Unknown but non-empty reply - surface it so we can debug quickly.
        return token[:8], "#9ca3af", "#6b7280"

    def _apply_radar_condition(self, label: str, fill: str, outline: str) -> None:
        """Update the indicator dot and label on the Tk main thread.

        Args:
            label: Text to show next to the dot.
            fill: Dot fill color (hex).
            outline: Dot outline color (hex).

        Returns:
            None.
        """

        self._radar_status_text.set(label)
        try:
            self._radar_status_canvas.itemconfigure(
                self._radar_status_dot, fill=fill, outline=outline
            )
        except tk.TclError:
            pass

    def _start_status_monitor(self) -> None:
        """Start the background thread that polls the radar condition.

        Returns:
            None.
        """

        if self._status_monitor_thread and self._status_monitor_thread.is_alive():
            return
        self._status_monitor_stop.clear()
        self._status_monitor_thread = threading.Thread(
            target=self._status_monitor_loop, daemon=True
        )
        self._status_monitor_thread.start()

    def _stop_status_monitor(self) -> None:
        """Signal the status monitor thread to stop.

        Returns:
            None.
        """

        self._status_monitor_stop.set()

    def trigger_adjust_level(self) -> None:
        """Request the AREG level adjust command on the monitor thread.

        The actual SCPI command is issued from :meth:`_status_monitor_loop`
        so we reuse its RsInstrument session and never touch the SCPI socket
        from the UI thread.

        Returns:
            None.
        """

        if self._adjust_pending.is_set():
            self._append_log("Adjust already pending, ignoring duplicate click")
            return
        self._adjust_pending.set()
        self._append_log("Adjust requested")

    def _status_monitor_loop(self) -> None:
        """Poll the instrument condition at a fixed period.

        The monitor owns its own :class:`RsInstrument` session to avoid
        contending with the simulation worker for a single SCPI socket.
        It re-uses the connection across iterations and only reconnects
        on error or when the target IP changes.

        Returns:
            None.
        """

        instr: RsInstrument | None = None
        active_ip: str | None = None
        last_label: str | None = None

        while not self._status_monitor_stop.is_set():
            ip_text = self.ip_var.get().strip()
            try:
                ip = str(ipaddress.ip_address(ip_text))
            except ValueError:
                ip = None

            # Rebuild the SCPI session when the IP changes or after a fault.
            if ip != active_ip and instr is not None:
                try:
                    instr.close()
                except Exception:  # noqa: BLE001
                    pass
                instr = None
                active_ip = None

            if ip is None:
                self.root.after(0, self._apply_radar_condition, "N/A", "#9ca3af", "#6b7280")
                if self._adjust_pending.is_set():
                    self._adjust_pending.clear()
                    self._append_log("Adjust ignored: invalid IP")
            else:
                try:
                    if instr is None:
                        instr = RsInstrument(
                            f"TCPIP::{ip}::hislip0",
                            reset=False,
                            id_query=False,
                            options="SelectVisa='rs', LoggingMode=Off, LoggingToConsole=False",
                        )
                        instr.read_termination = "\n"
                        instr.visa_timeout = 2000
                        active_ip = ip

                    raw = instr.query(RADAR_STATUS_QUERY)
                    label, fill, outline = self._map_radar_condition(raw)
                    if label != last_label:
                        last_label = label
                    self.root.after(0, self._apply_radar_condition, label, fill, outline)

                    # Fire the level-adjust command if the UI requested it.
                    # We consume the flag first so a fault below doesn't
                    # leave it stuck.
                    if self._adjust_pending.is_set():
                        self._adjust_pending.clear()
                        try:
                            instr.write(RADAR_ADJUST_LEVEL_CMD)
                            # *OPC? forces the box to finish the adjustment
                            # before we mark the request as done.
                            instr.query("*OPC?")
                            self._append_log("Adjust level: done")
                        except Exception as exc:  # noqa: BLE001
                            self._append_log(f"Adjust level failed: {exc}")
                            raise
                except Exception:  # noqa: BLE001
                    # Drop the session; next iteration reconnects.
                    if instr is not None:
                        try:
                            instr.close()
                        except Exception:  # noqa: BLE001
                            pass
                    instr = None
                    active_ip = None
                    last_label = None
                    self.root.after(0, self._apply_radar_condition, "N/A", "#9ca3af", "#6b7280")

            if self._status_monitor_stop.wait(RADAR_STATUS_POLL_PERIOD):
                break

        if instr is not None:
            try:
                instr.close()
            except Exception:  # noqa: BLE001
                pass

    def _run_static_once(self, config: StaticTargetConfig | DynamicTargetConfig) -> None:
        if not isinstance(config, StaticTargetConfig):
            raise TypeError("Invalid static config")

        self._append_log(
            "Static target -> "
            f"distance={config.distance_m:.2f}m, speed={config.speed_ms:.2f}m/s, rcs={config.rcs_db:.2f}dB"
        )
        self._client.enable_object(config.speed_ms, config.distance_m, config.rcs_db)
        self._append_log("Static target configured, waiting for stop")
        while not self._stop_event.is_set():
            time.sleep(0.1)

    def _run_dynamic_loop(self, config: StaticTargetConfig | DynamicTargetConfig) -> None:
        """Sweep the target distance from ``start`` to ``end`` exactly once.

        Each RANGe update is issued with the ``*OPC?`` handshake
        (``set_range`` -> ``write_and_wait``) so the caller only proceeds to
        the next step after the instrument confirms the previous command.
        The per-step latency is measured with :func:`time.perf_counter` and
        the average is logged when the sweep finishes (or is stopped early).

        The sweep runs a single pass: after reaching ``end_distance_m`` the
        method returns instead of restarting.

        Args:
            config: Dynamic target configuration built from the UI inputs.

        Returns:
            None.
        """

        if not isinstance(config, DynamicTargetConfig):
            raise TypeError("Invalid dynamic config")

        start_m = float(config.start_distance_m)
        end_m = float(config.end_distance_m)
        speed_ms = float(config.speed_ms)
        update_period = float(config.update_period_s)
        if speed_ms == 0 or start_m == end_m:
            raise ValueError("Dynamic speed and travel distance must be non-zero")
        if update_period <= 0:
            raise ValueError("Dynamic update period must be positive")

        # Direction is fully determined by the endpoints, so users may enter
        # the speed as either +5 or -5; the sign is normalised here.
        speed_magnitude = abs(speed_ms)
        direction = 1.0 if end_m > start_m else -1.0
        travel = abs(end_m - start_m)
        step_distance = speed_magnitude * update_period
        total_steps = max(1, int(travel / step_distance))

        self._append_log(
            "Dynamic sweep (single pass) -> "
            f"start={start_m:.2f}m, end={end_m:.2f}m, "
            f"speed={speed_magnitude:.2f}m/s ({'approach' if direction < 0 else 'recede'}), "
            f"rcs={config.rcs_db:.2f}dB, update={update_period * 1000:.0f}ms, "
            f"step={step_distance:.3f}m, steps={total_steps}"
        )

        # Prime the simulator at the exact starting point with the correct
        # doppler.  ``enable_object`` uses write_and_wait so the first sample
        # captured by the radar is guaranteed to be at ``start_m``.
        self._client.enable_object(
            direction * speed_magnitude, start_m, config.rcs_db
        )

        latencies_ms: list[float] = []
        next_tick = time.perf_counter() + update_period

        for step in range(1, total_steps + 1):
            if self._stop_event.is_set():
                break

            # Sleep until the next scheduled tick so the sweep advances at
            # the user-configured cadence rather than as fast as SCPI allows.
            now = time.perf_counter()
            sleep_for = next_tick - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            next_tick += update_period

            distance_m = start_m + direction * speed_magnitude * step * update_period
            if (direction < 0 and distance_m < end_m) or (direction > 0 and distance_m > end_m):
                distance_m = end_m

            call_start = time.perf_counter()
            self._client.set_range(distance_m)
            latencies_ms.append((time.perf_counter() - call_start) * 1000.0)

        if latencies_ms:
            avg_ms = sum(latencies_ms) / len(latencies_ms)
            min_ms = min(latencies_ms)
            max_ms = max(latencies_ms)
            self._append_log(
                "Dynamic sweep done -> "
                f"samples={len(latencies_ms)}, avg={avg_ms:.2f}ms, "
                f"min={min_ms:.2f}ms, max={max_ms:.2f}ms"
            )
        else:
            self._append_log("Dynamic sweep done -> no RANGe updates issued")

    @staticmethod
    def _iter_dynamic_ranges(start_m: float, end_m: float, speed_ms: float, t_res: float):
        """Yield intermediate ranges between two endpoints (legacy helper).

        Retained for backward compatibility with external callers/tests.  The
        dynamic loop no longer uses it because iterating by step count causes
        cumulative timing drift; see :meth:`_run_dynamic_loop`.

        Args:
            start_m: Starting distance in meters.
            end_m: Target distance in meters.
            speed_ms: Signed speed in m/s (sign is ignored, direction is
                inferred from ``start_m`` and ``end_m``).
            t_res: Time resolution between successive samples in seconds.

        Yields:
            Successive distance values in meters, ending exactly at ``end_m``.
        """

        if speed_ms == 0 or start_m == end_m or t_res <= 0:
            return
        speed_magnitude = abs(speed_ms)
        direction = 1.0 if end_m > start_m else -1.0
        delta = direction * speed_magnitude * t_res
        steps = max(1, int(abs(end_m - start_m) / abs(delta)))
        for i in range(1, steps + 1):
            yield start_m + i * delta
        # Guarantee we finish exactly on the endpoint.
        if (start_m + steps * delta) != end_m:
            yield end_m

    def _run_motion_profile(
        self, config: StaticTargetConfig | DynamicTargetConfig | MotionTargetConfig
    ) -> None:
        if not isinstance(config, MotionTargetConfig):
            raise TypeError("Invalid motion config")

        self._append_log(
            "Motion target -> "
            f"distance={config.distance_m:.2f}m, rcs={config.rcs_db:.2f}dB, "
            f"speed=[{config.min_speed_ms:.2f}, {config.max_speed_ms:.2f}]m/s, "
            f"step={config.speed_step_ms:.2f}m/s per {config.step_period_s:.1f}s"
        )

        speed_ms = config.min_speed_ms
        direction = 1.0
        self._client.enable_object(speed_ms, config.distance_m, config.rcs_db)

        while not self._stop_event.is_set():
            time.sleep(config.step_period_s)
            if self._stop_event.is_set():
                return

            next_speed = speed_ms + direction * config.speed_step_ms
            if next_speed >= config.max_speed_ms:
                next_speed = config.max_speed_ms
                direction = -1.0
            elif next_speed <= config.min_speed_ms:
                next_speed = config.min_speed_ms
                direction = 1.0

            self._client.set_speed(next_speed)
            speed_ms = next_speed


def main() -> None:
    """Entry point of UI tool.

    Returns:
        None.
    """

    root = tk.Tk()
    app = RadarSimulatorUI(root)

    def _on_close() -> None:
        app.stop_simulation()
        app._stop_status_monitor()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
