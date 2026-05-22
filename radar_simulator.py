"""Reusable R&S radar target simulator control logic."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Any

from RsInstrument import RsInstrument

from radar_scenarios import BrandProfile, ScenarioId


Number = int | float
DEFAULT_IP = "10.66.156.52"


class RadarTargetSimulator(AbstractContextManager["RadarTargetSimulator"]):
    """Controls one radar target simulator using SCPI commands over TCP/IP."""

    def __init__(
        self,
        profile: BrandProfile,
        ip: str = DEFAULT_IP,
        t_res: float = 0.1,
        source: int = 1,
    ) -> None:
        self.profile = profile
        self.ip = self._validate_ip(ip)
        self.t_res = self._validate_positive_number(t_res, "t_res")
        self.source = self._validate_positive_integer(source, "source")
        self._enabled_objects: set[int] = set()
        self._stop_event = threading.Event()
        self.instr = RsInstrument(
            f"TCPIP::{self.ip}::hislip0",
            reset=False,
            id_query=False,
            options="SelectVisa='rs', LoggingMode=Off, LoggingToConsole=False",
        )
        self.instr.read_termination = "\n"

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def _validate_ip(ip: str) -> str:
        if not isinstance(ip, str) or not ip.strip():
            raise ValueError("ip must be a non-empty string")
        return ip.strip()

    @staticmethod
    def _validate_positive_number(value: Number, name: str) -> float:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive number")
        return float(value)

    @staticmethod
    def _validate_positive_integer(value: int, name: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _mapping_channel(channel: int | str) -> str:
        if isinstance(channel, int) and not isinstance(channel, bool):
            return str(RadarTargetSimulator._validate_positive_integer(channel, "channel"))
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel must be a non-empty string or positive integer")

        value = channel.strip().upper()
        if value.startswith("A") and value[1:].isdigit():
            value = value[1:]
        if not value.isdigit():
            raise ValueError("channel must be a positive integer or A-prefixed channel such as A1")
        return str(RadarTargetSimulator._validate_positive_integer(int(value), "channel"))

    @staticmethod
    def _require_number(data: Mapping[str, Any], key: str) -> Number:
        value = data.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"scenario field {key!r} must be numeric")
        return value

    @staticmethod
    def _object_name(index: int) -> str:
        if index <= 0:
            raise ValueError("object index must be greater than 0")
        return f"OBJect{index}"

    @staticmethod
    def _speed_ms_to_kmh(speed_ms: Number) -> float:
        return float(speed_ms) * 3.6

    def _write_and_wait(self, command: str) -> None:
        self.instr.write(command)
        status = self.instr.query("*OPC?")
        if int(status) != 1:
            raise RuntimeError(f"Command failed: {command}")

    def adjust_level(self, channel: int | str = "A1") -> None:
        mapping = self._mapping_channel(channel)
        self._write_and_wait(f":SOURce{self.source}:AREGenerator:MAPPing{mapping}:ADJust:LEVel")

    def _set_object(self, index: int, *, speed: Number, range_m: Number, rcs: Number, angle: Number | None = None) -> None:
        obj = self._object_name(index)
        commands = [
            f":SOURce{self.source}:AREGenerator:{obj}:DOPPler:SPEed {self._speed_ms_to_kmh(speed)}",
            f":SOURce{self.source}:AREGenerator:{obj}:RANGe {range_m}",
            f":SOURce{self.source}:AREGenerator:{obj}:RCS {rcs}",
        ]
        if angle is not None:
            commands.append(f":SOURce{self.source}:AREGenerator:{obj}:ANGLe:HORizontal {angle}")
        commands.append(f":SOURce{self.source}:AREGenerator:{obj}:STATe 1")

        for command in commands:
            self._write_and_wait(command)
        self._enabled_objects.add(index)

    def set_object_range(self, index: int, range_m: Number) -> None:
        obj = self._object_name(index)
        self._write_and_wait(f":SOURce{self.source}:AREGenerator:{obj}:RANGe {range_m}")

    def set_object_speed(self, index: int, speed: Number) -> None:
        obj = self._object_name(index)
        self._write_and_wait(
            f":SOURce{self.source}:AREGenerator:{obj}:DOPPler:SPEed {self._speed_ms_to_kmh(speed)}"
        )

    def stop(self) -> None:
        self._stop_event.set()

    def reset_stop_flag(self) -> None:
        self._stop_event.clear()

    def run_dynamic(self, scenario_id: ScenarioId) -> None:
        scenario = self._get_scenario(self.profile.dynamic_scenarios, scenario_id, "dynamic scenario")
        if "speed_min" in scenario:
            self._run_speed_sweep(scenario)
            return
        self._run_range_motion(scenario)

    def run_fixed(self, target_id: int) -> None:
        target = self._get_scenario(self.profile.fixed_targets, target_id, "fixed target")
        angle = target.get("angle")
        if angle is not None and not isinstance(angle, (int, float)):
            raise ValueError("fixed target angle must be numeric")
        self._set_object(
            1,
            speed=self._require_number(target, "speed"),
            range_m=self._require_number(target, "range"),
            rcs=self._require_number(target, "rcs"),
            angle=angle,
        )

    def run_multi(self, multi_id: int) -> None:
        multi = self._get_scenario(self.profile.multi_targets, multi_id, "multi target")
        targets = multi.get("targets")
        if not isinstance(targets, tuple) or not targets:
            raise ValueError("multi target scenario must contain at least one target")

        self._enabled_objects.clear()
        for index, target in enumerate(targets, start=1):
            angle = target.get("angle")
            if angle is not None and not isinstance(angle, (int, float)):
                raise ValueError("target angle must be numeric")
            self._set_object(
                index,
                speed=self._require_number(target, "speed"),
                range_m=self._require_number(target, "range"),
                rcs=self._require_number(target, "rcs"),
                angle=angle,
            )

    def disable_all(self) -> None:
        for obj_idx in sorted(self._enabled_objects):
            self._write_and_wait(f":SOURce{self.source}:AREGenerator:OBJect{obj_idx}:STATe 0")
        self._enabled_objects.clear()

    def close(self) -> None:
        self.instr.close()

    @staticmethod
    def _get_scenario(
        scenarios: Mapping[ScenarioId, Mapping[str, Any]],
        scenario_id: ScenarioId,
        label: str,
    ) -> Mapping[str, Any]:
        if not isinstance(scenario_id, (int, str)) or isinstance(scenario_id, bool):
            raise ValueError(f"{label} id must be an integer or string")
        try:
            return scenarios[scenario_id]
        except KeyError as exc:
            valid_ids = ", ".join(str(key) for key in scenarios)
            raise ValueError(f"invalid {label} id {scenario_id}; valid ids: {valid_ids}") from exc

    def _run_range_motion(self, scenario: Mapping[str, Any]) -> None:
        r_start = self._require_number(scenario, "r_start")
        r_end = self._require_number(scenario, "r_end")
        speed = self._require_number(scenario, "speed")
        rcs = self._require_number(scenario, "rcs")
        if math.isclose(float(speed), 0.0):
            raise ValueError("dynamic scenario speed must not be zero")

        self._set_object(1, speed=speed, range_m=r_start, rcs=rcs)
        while not self._stop_event.is_set():
            for range_now in self._iter_ranges(float(r_start), float(r_end), float(speed)):
                if self._stop_event.is_set():
                    return
                self._write_and_wait(f":SOURce{self.source}:AREGenerator:OBJect1:RANGe {range_now}")
                time.sleep(self.t_res)
            self._write_and_wait(f":SOURce{self.source}:AREGenerator:OBJect1:RANGe {r_start}")

    def _run_speed_sweep(self, scenario: Mapping[str, Any]) -> None:
        speed_min = self._require_number(scenario, "speed_min")
        speed_max = self._require_number(scenario, "speed_max")
        if speed_min > speed_max:
            raise ValueError("speed_min must be less than or equal to speed_max")

        self._set_object(
            1,
            speed=speed_min,
            range_m=self._require_number(scenario, "range"),
            rcs=self._require_number(scenario, "rcs"),
        )
        while not self._stop_event.is_set():
            for speed in self._iter_speeds(float(speed_min), float(speed_max)):
                if self._stop_event.is_set():
                    return
                self._write_and_wait(f":SOURce{self.source}:AREGenerator:OBJect1:DOPPler:SPEed {self._speed_ms_to_kmh(speed)}")
                print(f"  Current speed: {speed:.1f} m/s ({self._speed_ms_to_kmh(speed):.1f} km/h)")
                time.sleep(self.t_res)

    def _iter_ranges(self, start: float, end: float, speed: float) -> Iterator[float]:
        steps = max(1, int(abs(end - start) / abs(speed) / self.t_res))
        yield from (start + speed * self.t_res * step for step in range(steps))

    @staticmethod
    def _iter_speeds(start: float, end: float, step: float = 1.0) -> Iterator[float]:
        current = start
        while current <= end:
            yield current
            current += step
