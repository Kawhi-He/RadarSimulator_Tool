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


DEFAULT_IP = "10.66.156.52"
DEFAULT_SOURCE = 1
DEFAULT_TIME_RESOLUTION = 0.1


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
    """

    start_distance_m: float
    end_distance_m: float
    speed_ms: float
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
        self.root.geometry("980x700")
        self.root.minsize(920, 660)
        self.root.configure(bg="#eaf0f7")

        self.mode_var = tk.StringVar(value="static")
        self.ip_var = tk.StringVar(value=DEFAULT_IP)
        self.status_var = tk.StringVar(value="Idle")

        self.static_distance_var = tk.StringVar(value="20")
        self.static_speed_var = tk.StringVar(value="-10")
        self.static_speed_unit_var = tk.StringVar(value="m/s")
        self.static_rcs_var = tk.StringVar(value="30")

        self.dynamic_start_var = tk.StringVar(value="150")
        self.dynamic_end_var = tk.StringVar(value="20")
        self.dynamic_speed_var = tk.StringVar(value="-10")
        self.dynamic_speed_unit_var = tk.StringVar(value="m/s")
        self.dynamic_rcs_var = tk.StringVar(value="30")

        self._client: RadarSimulatorClient | None = None
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._log_queue: Queue[str] = Queue()

        self._build_styles()
        self._build_layout()
        self._update_mode_sections()
        self._poll_log_queue()
        self._set_status("idle", "Idle")

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

        mode_action_frame = ttk.Frame(mode_card, style="Card.TFrame")
        mode_action_frame.grid(row=1, column=2, sticky="e")
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
        for row in range(1, 5):
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
        tk.Label(parent, text="", bg="#ffffff").grid(row=4, column=0, sticky="w", pady=4)
        spacer = tk.Frame(parent, bg="#ffffff", height=32)
        spacer.grid(row=4, column=1, sticky="ew", pady=4)
        spacer.grid_propagate(False)

    def _build_dynamic_fields(self, parent: tk.Widget) -> None:
        parent.columnconfigure(0, minsize=128)
        parent.columnconfigure(1, weight=1)
        for row in range(1, 5):
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
        static_active = self.mode_var.get() == "static"
        self._set_state_recursive(self.static_frame, tk.NORMAL if static_active else tk.DISABLED)
        self._set_state_recursive(self.dynamic_frame, tk.DISABLED if static_active else tk.NORMAL)

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
        )

    def start_simulation(self) -> None:
        if self._worker and self._worker.is_alive():
            self._append_log("Simulation is already running")
            return

        try:
            ip = self._validate_ip(self.ip_var.get())
            mode = self.mode_var.get()
            config = self._build_static_config() if mode == "static" else self._build_dynamic_config()
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

    def _run_simulation(self, ip: str, mode: str, config: StaticTargetConfig | DynamicTargetConfig) -> None:
        self._append_log(f"Connecting to {ip} ...")
        try:
            self._client = RadarSimulatorClient(ip)
            self._client.connect()
            self._append_log("Connection successful")

            if mode == "static":
                self._run_static_once(config)
            else:
                self._run_dynamic_loop(config)
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
        if not isinstance(config, DynamicTargetConfig):
            raise TypeError("Invalid dynamic config")

        self._append_log(
            "Dynamic loop -> "
            f"start={config.start_distance_m:.2f}m, end={config.end_distance_m:.2f}m, "
            f"speed={config.speed_ms:.2f}m/s, rcs={config.rcs_db:.2f}dB"
        )
        self._client.enable_object(config.speed_ms, config.start_distance_m, config.rcs_db)

        while not self._stop_event.is_set():
            for distance_m in self._iter_dynamic_ranges(
                config.start_distance_m,
                config.end_distance_m,
                config.speed_ms,
                DEFAULT_TIME_RESOLUTION,
            ):
                if self._stop_event.is_set():
                    return
                self._client.set_range(distance_m)
                time.sleep(DEFAULT_TIME_RESOLUTION)

            # Loop behavior: once end is reached, jump back to start and continue.
            self._client.set_range(config.start_distance_m)
            self._append_log("Dynamic loop restart")

    @staticmethod
    def _iter_dynamic_ranges(start_m: float, end_m: float, speed_ms: float, t_res: float):
        delta = speed_ms * t_res
        if delta == 0:
            return
        steps = max(1, int(abs(end_m - start_m) / abs(delta)))
        for i in range(steps):
            yield start_m + i * delta


def main() -> None:
    """Entry point of UI tool.

    Returns:
        None.
    """

    root = tk.Tk()
    app = RadarSimulatorUI(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_simulation(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
