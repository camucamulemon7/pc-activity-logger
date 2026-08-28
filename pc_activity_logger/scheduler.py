from __future__ import annotations

import subprocess
import sys
from pathlib import Path


TASK_NAME = "PC Activity Logger"


def resource_path(filename: str) -> Path:
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / filename
    return Path(__file__).resolve().parents[1] / filename


def _powershell(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *arguments],
        check=check,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def is_registered() -> bool:
    result = _powershell(
        "-Command",
        f"Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop | Out-Null",
        check=False,
    )
    return result.returncode == 0


def register(config_path: Path) -> None:
    command = ["-File", str(resource_path("register-task.ps1")), "-Gui"]
    if getattr(sys, "frozen", False):
        command.extend(["-Executable", sys.executable])
    command.extend(["-Config", str(config_path)])
    result = _powershell(*command, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())


def unregister() -> None:
    result = _powershell(
        "-File",
        str(resource_path("unregister-task.ps1")),
        "-Confirm:$false",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
