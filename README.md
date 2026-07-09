# RadarSimulator_Tool

Windows radar simulator desktop tools based on SCPI commands for the
Rohde & Schwarz AREG800A radar target simulator.

## Features

### Radar Simulator Tool (`radar_ui_tool.py`)

- Editable simulator IP, default `10.66.156.12`
- Live **Radar Power** condition indicator (colored LED + text) polled in
  the background via `:SOURce1:AREGenerator:CHANnel1:CONDition?`
- One-shot **Adjust** button that fires
  `:SOURce1:AREGenerator:MAPPing1:ADJust:LEVel` on the monitor thread
- Three target modes (one at a time):
	- **Static Target** — fixed distance, doppler and RCS
	- **Dynamic Target (Loop)** — single-pass distance sweep from start
	  to end at the configured update period (default 100 ms)
	- **Motion Target (Preset)** — fixed distance, speed ramps between
	  ±27.7 m/s with a 1 m/s step every 1 s
- Static target fields:
	- Distance (m)
	- Doppler speed (`m/s` or `km/h`)
	- RCS (dB)
- Dynamic target fields:
	- Start distance (m)
	- End distance (m)
	- Doppler speed (`m/s` or `km/h`) — sign is auto-normalised from the
	  start/end direction
	- RCS (dB)
- Dynamic sweep logs average / min / max SCPI RANGe update latency when
  the sweep finishes or is stopped
- Log panel for key messages (connections, errors, sweep metrics)
- `Simulation starts` and `Simulation stops` controls in the `Target Mode`
  section
- Clean compact layout for Windows desktop (Tkinter + ttk)

### SCPI Latency Benchmark

Two tools measure the SCPI `write + *OPC?` round-trip time for the
approaching-target scenario (range 100 m → 10 m, RCS 10 dBsm, 100 ms
step by default):

- `benchmark_opc_latency.py` — command-line runner that prints the
  aggregated latency report (samples, avg, min, max, stdev)
- `benchmark_opc_ui.py` — Tkinter UI mirroring the simulator tool with
  live progress, metric readouts and a scrollable log

## Files

- `radar_scpi_demo.py` — original simple SCPI demo
- `radar_simulator.py` — reusable `RadarTargetSimulator` context manager
- `radar_ui_tool.py` — Windows desktop UI tool (Tkinter)
- `benchmark_opc_latency.py` — CLI SCPI latency benchmark
- `benchmark_opc_ui.py` — Windows desktop UI for the latency benchmark
- `RadarSimulatorTool.spec` — PyInstaller spec for the simulator UI
- `RadarOpcBenchmarkTool.spec` — PyInstaller spec for the benchmark UI
- `tapp_versionfile.txt` — Windows version-info resource used by
  PyInstaller
- `requirements.txt` — Python dependencies

## Use Virtual Environment

### 1) Create venv

```powershell
python -m venv .venv
```

### 2) Activate venv (PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3) Install dependencies

```powershell
pip install -r requirements.txt
```

### 4) Run the tools

```powershell
# Simulator UI
python radar_ui_tool.py

# Latency benchmark UI
python benchmark_opc_ui.py

# Latency benchmark CLI (defaults: 100 m -> 10 m, 20 m/s, 10 dBsm, 100 ms)
python benchmark_opc_latency.py --ip 10.66.156.12
```

## Build Standalone Executables

PyInstaller spec files are provided for both UIs:

```powershell
pyinstaller RadarSimulatorTool.spec
pyinstaller RadarOpcBenchmarkTool.spec
```

The resulting `.exe` files are written under `dist/`.

## UI Notes

- Start/Stop buttons are located at the right side of the `Target Mode`
  area.
- The `Radar Power` LED updates every 500 ms and turns green (OK),
  yellow (Warning) or red (Error) based on the instrument reply.
- The **Adjust** button is one-shot; it is safe to press while a
  simulation is running because the SCPI command is issued from a
  dedicated background thread.
- The static and dynamic config panels are aligned symmetrically.
- The `Log` panel has enlarged display space for easier troubleshooting.
- Dynamic mode runs a single pass; press `Simulation stops` to abort
  early.

## Notes

- Ensure your PC can reach the simulator IP over the LAN (`hislip0`).
- If VISA or the instrument driver is missing, install R&S VISA and
  verify communication.
- The simulator tool sends SCPI commands to `OBJect1` under
  `SOURce1:AREGenerator`.
