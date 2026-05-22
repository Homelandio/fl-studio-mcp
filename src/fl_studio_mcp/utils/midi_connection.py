"""MIDI connection using Windows ctypes (no rtmidi needed)."""

from __future__ import annotations

import ctypes
import json
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


def _get_fl_hardware_dir() -> Path:
    base = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Settings"
    hardware_dir = base / "Hardware" / "FLStudioMCP"
    hardware_dir.mkdir(parents=True, exist_ok=True)
    return hardware_dir


def _list_output_ports():
    winmm = ctypes.windll.winmm

    class OUTCAPS(ctypes.Structure):
        _fields_ = [
            ('wMid', wintypes.WORD), ('wPid', wintypes.WORD),
            ('vDriverVersion', wintypes.DWORD), ('szPname', ctypes.c_wchar * 32),
            ('wTechnology', wintypes.WORD), ('wVoices', wintypes.WORD),
            ('wNotes', wintypes.WORD), ('wChannelMask', wintypes.WORD),
            ('dwSupport', wintypes.DWORD),
        ]

    ports = []
    for i in range(winmm.midiOutGetNumDevs()):
        caps = OUTCAPS()
        if winmm.midiOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            ports.append(caps.szPname)
    return ports


class MIDIConnection:
    TRIGGER_NOTE = 127

    def __init__(self) -> None:
        self._port_idx: int | None = None
        self._port_name: str | None = None
        self._connected = False
        self._error: str | None = None
        self._hmo = None
        self._winmm = ctypes.windll.winmm
        self._hardware_dir = _get_fl_hardware_dir()
        self._command_file = self._hardware_dir / "mcp_command.json"
        self._response_file = self._hardware_dir / "mcp_response.json"

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def connection_error(self) -> str | None:
        return self._error

    def connect(self) -> bool:
        if self._connected:
            return True
        ports = _list_output_ports()
        if not ports:
            self._error = "No MIDI output ports found"
            return False
        target_idx = None
        target_name = None
        for i, name in enumerate(ports):
            if "Yoke" in name:
                target_idx = i
                target_name = name
                break
        if target_idx is None:
            for i, name in enumerate(ports):
                if "loopMIDI" in name or "FL" in name.upper():
                    target_idx = i
                    target_name = name
                    break
        if target_idx is None:
            target_idx = 0
            target_name = ports[0]
        try:
            hmo = ctypes.c_void_p()
            r = self._winmm.midiOutOpen(ctypes.byref(hmo), target_idx, 0, 0, 0)
            if r != 0:
                self._error = f"midiOutOpen failed: {r}"
                return False
            self._hmo = hmo
            self._port_idx = target_idx
            self._port_name = target_name
            self._connected = True
            self._error = None
            return True
        except Exception as e:
            self._error = str(e)
            return False

    def disconnect(self) -> None:
        if self._hmo is not None:
            try:
                self._winmm.midiOutClose(self._hmo)
            except Exception:
                pass
            self._hmo = None
        self._connected = False

    def ensure_connected(self) -> None:
        if not self.is_connected:
            if not self.connect():
                raise RuntimeError(self._error or "Failed to connect")

    def send_command(self, action: str, params: dict[str, Any] | None = None,
                     timeout: float = 5.0) -> dict[str, Any]:
        self.ensure_connected()
        command = {"action": action, "params": params or {}}
        try:
            self._command_file.write_text(json.dumps(command, indent=2), encoding='utf-8')
        except Exception as e:
            return {"success": False, "error": f"Failed to write command file: {e}"}
        if self._response_file.exists():
            try:
                self._response_file.unlink()
            except Exception:
                pass
        try:
            msg = (127 << 16) | (self.TRIGGER_NOTE << 8) | 0x90
            self._winmm.midiOutShortMsg(self._hmo, msg)
        except Exception as e:
            return {"success": False, "error": f"Failed to send MIDI trigger: {e}"}
        return self._wait_for_response(timeout)

    def _wait_for_response(self, timeout: float) -> dict[str, Any]:
        start = time.time()
        while time.time() - start < timeout:
            if self._response_file.exists():
                try:
                    response = json.loads(self._response_file.read_text(encoding='utf-8'))
                    try:
                        self._response_file.unlink()
                    except Exception:
                        pass
                    return response
                except Exception as e:
                    return {"success": False, "error": f"Failed to read response: {e}"}
            time.sleep(0.02)
        return {"success": False, "error": f"Timeout after {timeout}s"}

    def get_status(self) -> dict[str, Any]:
        return {
            "connected": self.is_connected,
            "port_name": self._port_name,
            "available_ports": _list_output_ports(),
            "command_file": str(self._command_file),
            "response_file": str(self._response_file),
            "error": self._error,
        }


_connection: MIDIConnection | None = None

def get_connection() -> MIDIConnection:
    global _connection
    if _connection is None:
        _connection = MIDIConnection()
    return _connection

def reset_connection() -> None:
    global _connection
    if _connection is not None:
        _connection.disconnect()
        _connection = None
