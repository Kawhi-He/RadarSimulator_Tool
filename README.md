# RadarSimulator_Tool

Windows radar simulator desktop tool based on SCPI commands.

## Features

- Editable simulator IP, default `10.66.156.52`
- One mode at a time: `Static` or `Dynamic`
- Static target fields:
	- Distance (m)
	- Doppler speed (`m/s` or `km/h`)
	- RCS (dB)
- Dynamic target fields:
	- Start distance (m)
	- End distance (m)
	- Doppler speed (`m/s` or `km/h`)
	- RCS (dB)
- Dynamic loop simulation:
	- After reaching end distance, it jumps back to start distance and repeats continuously
- Log panel for key messages (for example, connection failures)
- `Simulation starts` and `Simulation stops` controls in the `Target Mode` section
- Clean compact layout for Windows desktop (Tkinter + ttk)

## Files

- `radar_scpi_demo.py`: original simple SCPI demo
- `radar_ui_tool.py`: Windows desktop UI tool (Tkinter)
- `requirements.txt`: Python dependencies

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

### 4) Run UI tool

```powershell
python radar_ui_tool.py
```

## UI Notes

- Start/Stop buttons are located at the right side of the `Target Mode` area.
- The static and dynamic config panels are aligned symmetrically.
- The `Log` panel has enlarged display space for easier troubleshooting.
- Dynamic mode runs in a loop until stopped manually.

## Notes

- Ensure your PC can reach the simulator IP.
- If VISA or instrument driver is missing, install R&S VISA and verify communication.
- The tool sends SCPI commands to `OBJect1` under `SOURce1:AREGenerator`.
