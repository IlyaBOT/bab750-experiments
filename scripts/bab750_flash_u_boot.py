#!/usr/bin/env python3

"""Build and flash U-Boot for the Eltec BAB-750 over serial + TFTP."""

from __future__ import annotations

import argparse
import fcntl
import os
import pathlib
import re
import select
import shlex
import shutil
import struct
import subprocess
import sys
import termios
import time


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
UBOOT_DIR = ROOT_DIR / "u-boot-lab"
TFTPBOOT_DIR = ROOT_DIR / "bab750-tftp" / "tftpboot"
UBOOT_BIN = UBOOT_DIR / "u-boot.bin"
RESET_MARKER_RE = re.compile(
    r"\*\*\*\s*ELTEC Elektronik, Mainz\s*\*\*\*|"
    r"BAB-PPC Monitor Version 1\.2\.1/2|"
    r"Init MPU/MSR/FPU/Segment registers\.",
    re.IGNORECASE,
)
MEMORY_TEST_RE = re.compile(r"Press any key to skip memory test", re.IGNORECASE)
VXWORKS_BANNER_RE = re.compile(
    r"VxWorks System Boot|"
    r"Copyright 1984-1996\s+Wind River Systems, Inc\.|"
    r"CPU:\s*ELTEC BAB-PPC",
    re.IGNORECASE,
)
NVRAM_INVALID_RE = re.compile(r"Invalid revision info copy in nvram\s*!", re.IGNORECASE)
NVRAM_PRESS_KEY_RE = re.compile(r"Press key:", re.IGNORECASE)
PROMPT_RE = re.compile(r"=>\s")
UBOOT_BANNER_RE = re.compile(r"\bU-Boot\b", re.IGNORECASE)
UBOOT_AUTOBOOT_RE = re.compile(
    r"(?:Hit any key to stop autoboot|Press\s+'Enter'\s+or\s+'Space'\s+to stop autoboot):\s*\d+",
    re.IGNORECASE,
)
UBOOT_TFTP_AUTORUN_RE = re.compile(
    r"Filename\s+'[^']+'\.\s*|Load address:\s*0x[0-9a-fA-F]+",
    re.IGNORECASE,
)
BOOTROM_VXWORKS_PROMPT_RE = re.compile(r"\[VxWorks Boot\]:", re.IGNORECASE)
BOOTROM_AUTOSTART_RE = re.compile(r"auto-booting\.\.\.", re.IGNORECASE)
LOADING_RE = re.compile(r"\bLoading\b", re.IGNORECASE)
TFTP_SIZE_RE = re.compile(r"Bytes transferred = \d+ \(([0-9a-fA-F]+) hex\)")
FLASH_SECTOR_BOUNDARY_ERROR_RE = re.compile(
    r"Error:\s*end address not on sector boundary",
    re.IGNORECASE,
)
FLASH_NOT_ERASED_RE = re.compile(
    r"Copy to Flash\.\.\.\s*Flash not Erased",
    re.IGNORECASE,
)
BLUE = "\033[94m"
ORANGE = "\033[38;5;208m"
RED = "\033[91m"
RESET = "\033[0m"
SERIAL_LINE_OPEN = False
MODEM_LINE_BITS = {
    "rts": termios.TIOCM_RTS,
    "dtr": termios.TIOCM_DTR,
}


class ResetDetected(RuntimeError):
    def __init__(self, position: int) -> None:
        super().__init__("Machine reset detected")
        self.position = position


def emit_script_message(message: str, *, color: str | None = BLUE) -> None:
    global SERIAL_LINE_OPEN

    payload = message.rstrip("\n")
    prefix = "\n" if SERIAL_LINE_OPEN else ""
    color_prefix = color if color and sys.stderr.isatty() else ""
    color_suffix = RESET if color_prefix else ""

    sys.stdout.flush()
    os.write(
        sys.stderr.fileno(),
        f"{prefix}{color_prefix}{payload}{color_suffix}\n".encode("utf-8", errors="replace"),
    )
    SERIAL_LINE_OPEN = False


def log(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    emit_script_message(f"[{timestamp}] {message}", color=BLUE)


def warn(message: str) -> None:
    emit_script_message(f"[WARN] {message}", color=ORANGE)


def error(message: str) -> None:
    emit_script_message(f"[ERROR] {message}", color=RED)


def find_reset_marker(console: "SerialConsole", start: int) -> int | None:
    haystack = console.buffer_since(start)
    match = RESET_MARKER_RE.search(haystack)
    if not match:
        return None
    return start + match.start()


def raise_if_reset(console: "SerialConsole", start: int | None) -> None:
    if start is None:
        return
    reset_position = find_reset_marker(console, start)
    if reset_position is not None:
        raise ResetDetected(reset_position)


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def resolve_cross_compile(explicit: str | None) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)

    candidates.extend(
        [
            "/opt/powerpc-linux-gnu/bin/powerpc-linux-gnu-",
            str(ROOT_DIR / "powerpc-linux-gnu" / "bin" / "powerpc-linux-gnu-"),
        ]
    )

    for prefix in candidates:
        compiler = pathlib.Path(f"{prefix}gcc")
        if compiler.exists():
            return prefix

    searched = "\n".join(f"  - {candidate}gcc" for candidate in candidates)
    raise SystemExit(
        "PowerPC cross-compiler not found.\n"
        "Pass --cross-compile explicitly or install one of:\n"
        f"{searched}"
    )


def resolve_serial_port(raw: str) -> pathlib.Path:
    raw_path = pathlib.Path(raw)
    candidates = []

    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(pathlib.Path("/dev") / raw)

    base = raw_path.name
    lowered = base.lower()
    if lowered.startswith("ttyusb") and base != "ttyUSB" + base[6:]:
        normalized = "ttyUSB" + base[6:]
        candidates.append(pathlib.Path("/dev") / normalized)

    seen = set()
    unique_candidates = []
    for candidate in candidates:
        if candidate not in seen:
            unique_candidates.append(candidate)
            seen.add(candidate)

    for candidate in unique_candidates:
        if candidate.exists():
            return candidate

    searched = "\n".join(f"  - {candidate}" for candidate in unique_candidates)
    raise SystemExit(
        "Serial device not found.\n"
        f"Searched:\n{searched}"
    )


def run_command(cmd: list[str], cwd: pathlib.Path, env: dict[str, str]) -> None:
    log(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def collect_toolchain_runtime_lib_dirs(toolchain_root: pathlib.Path) -> list[pathlib.Path]:
    runtime_lib_dirs = [toolchain_root / "lib"]
    runtime_lib_dirs.extend(sorted(toolchain_root.glob("*/powerpc-linux-gnu/lib")))
    return [path for path in runtime_lib_dirs if path.exists()]


def prepare_wrapped_cross_prefix(cross_compile: str) -> str:
    tool_prefix = pathlib.Path(cross_compile)
    toolchain_bin = tool_prefix.parent
    toolchain_root = toolchain_bin.parent
    runtime_lib_dirs = collect_toolchain_runtime_lib_dirs(toolchain_root)
    wrapper_dir = pathlib.Path("/tmp") / f"{tool_prefix.name}wrappers"
    wrapper_dir.mkdir(parents=True, exist_ok=True)

    for real_tool in toolchain_bin.glob(f"{tool_prefix.name}*"):
        if not (real_tool.is_file() or real_tool.is_symlink()):
            continue

        wrapper_path = wrapper_dir / real_tool.name
        exports = []
        if runtime_lib_dirs:
            lib_path = ":".join(str(path) for path in runtime_lib_dirs)
            exports.append(
                f"export LD_LIBRARY_PATH={shlex.quote(lib_path)}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
            )
        if real_tool.name.startswith(f"{tool_prefix.name}gcc"):
            exports.append(
                f"export COMPILER_PATH={shlex.quote(str(toolchain_bin))}${{COMPILER_PATH:+:$COMPILER_PATH}}"
            )

        script = [
            "#!/bin/sh",
            *exports,
            f"exec {shlex.quote(str(real_tool))} \"$@\"",
            "",
        ]
        wrapper_path.write_text("\n".join(script), encoding="utf-8")
        wrapper_path.chmod(0o755)

    return str(wrapper_dir / tool_prefix.name)


def build_toolchain_env(cross_compile: str) -> tuple[dict[str, str], str]:
    env = os.environ.copy()
    wrapped_prefix = prepare_wrapped_cross_prefix(cross_compile)
    env["CROSS_COMPILE"] = wrapped_prefix
    return env, wrapped_prefix


def build_u_boot(cross_compile: str, jobs: int) -> pathlib.Path:
    env, wrapped_prefix = build_toolchain_env(cross_compile)
    if wrapped_prefix != cross_compile:
        log(f"Using wrapped cross-compiler prefix: {wrapped_prefix}")

    run_command(["make", "distclean"], cwd=UBOOT_DIR, env=env)
    run_command(["make", "BAB7xx_config"], cwd=UBOOT_DIR, env=env)
    run_command(["make", f"-j{jobs}"], cwd=UBOOT_DIR, env=env)

    if not UBOOT_BIN.exists():
        raise SystemExit(f"Build finished, but {UBOOT_BIN} was not created")

    size = UBOOT_BIN.stat().st_size
    log(f"Built {UBOOT_BIN} ({size} bytes)")
    return UBOOT_BIN


def copy_to_tftp(source: pathlib.Path) -> pathlib.Path:
    TFTPBOOT_DIR.mkdir(parents=True, exist_ok=True)
    target = TFTPBOOT_DIR / source.name
    shutil.copy2(source, target)
    log(f"Copied {source} -> {target}")
    return target


class SerialConsole:
    def __init__(
        self,
        port: pathlib.Path,
        baudrate: int,
        display_name: str | None = None,
        *,
        reset_line: str | None = None,
        reset_active_state: bool = True,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.display_name = display_name or port.name
        self.reset_line = reset_line
        self.reset_active_state = reset_active_state
        self.fd: int | None = None
        self._buffer = ""
        self._original_attrs = None

    def __enter__(self) -> "SerialConsole":
        self._open_port()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is None:
            return
        if self.reset_line is not None:
            self.set_reset_asserted(False)
        if self._original_attrs is not None:
            termios.tcsetattr(self.fd, termios.TCSANOW, self._original_attrs)
        os.close(self.fd)
        self.fd = None

    def _configure_port(self) -> None:
        assert self.fd is not None
        attrs = termios.tcgetattr(self.fd)
        self._original_attrs = attrs[:]

        attrs[0] = 0
        attrs[1] = 0
        attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
        attrs[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
        if hasattr(termios, "CRTSCTS"):
            attrs[2] &= ~termios.CRTSCTS
        attrs[3] = 0
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0

        speed_name = f"B{self.baudrate}"
        if not hasattr(termios, speed_name):
            raise SystemExit(f"Unsupported baudrate: {self.baudrate}")
        speed = getattr(termios, speed_name)
        attrs[4] = speed
        attrs[5] = speed
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)

    def _open_port(self) -> None:
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        if self.reset_line is not None:
            self.set_reset_asserted(False)
        self._configure_port()
        if self.reset_line is not None:
            self.set_reset_asserted(False)

    def reconnect(self) -> None:
        if self.fd is not None:
            if self.reset_line is not None:
                self.set_reset_asserted(False)
            if self._original_attrs is not None:
                termios.tcsetattr(self.fd, termios.TCSANOW, self._original_attrs)
            os.close(self.fd)
            self.fd = None
        self._open_port()

    def mark(self) -> int:
        return len(self._buffer)

    def fileno(self) -> int:
        assert self.fd is not None
        return self.fd

    def buffer_since(self, start: int = 0) -> str:
        return self._buffer[max(0, start):]

    def _append(self, chunk: str) -> None:
        global SERIAL_LINE_OPEN

        self._buffer += chunk
        if len(self._buffer) > 131072:
            self._buffer = self._buffer[-131072:]
        print(chunk, end="", flush=True)
        SERIAL_LINE_OPEN = not chunk.endswith(("\n", "\r"))

    def _set_modem_line(self, line_name: str, active: bool) -> None:
        assert self.fd is not None
        bit = MODEM_LINE_BITS[line_name]
        operation = termios.TIOCMBIS if active else termios.TIOCMBIC
        try:
            fcntl.ioctl(self.fd, operation, struct.pack("I", bit))
        except OSError:
            state = struct.unpack("I", fcntl.ioctl(self.fd, termios.TIOCMGET, struct.pack("I", 0)))[0]
            if active:
                state |= bit
            else:
                state &= ~bit
            fcntl.ioctl(self.fd, termios.TIOCMSET, struct.pack("I", state))

    def set_reset_asserted(self, asserted: bool) -> None:
        if self.reset_line is None:
            return
        physical_active = self.reset_active_state if asserted else not self.reset_active_state
        self._set_modem_line(self.reset_line, physical_active)

    def pulse_reset(self, duration: float, settle_delay: float) -> None:
        if self.reset_line is None:
            return
        log(
            f"Pulsing reset via {self.reset_line.upper()} "
            f"for {duration:.3f}s (active state: {'asserted' if self.reset_active_state else 'deasserted'})"
        )
        self.set_reset_asserted(True)
        time.sleep(duration)
        self.set_reset_asserted(False)
        time.sleep(settle_delay)

    def read_once(self, timeout: float) -> str:
        assert self.fd is not None
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return ""
        chunk = os.read(self.fd, 4096)
        if not chunk:
            return ""
        decoded = chunk.decode("utf-8", errors="replace")
        self._append(decoded)
        return decoded

    def send_raw(self, data: bytes, description: str, *, log_send: bool = True) -> None:
        assert self.fd is not None
        if log_send:
            log(f"Serial send: {description}")
        os.write(self.fd, data)

    def send_enter(self, *, log_send: bool = True) -> None:
        self.send_raw(b"\r", "<Enter>", log_send=log_send)

    def send_space(self, count: int = 3, delay: float = 0.2) -> None:
        for _ in range(count):
            self.send_raw(b" ", "<Space>")
            time.sleep(delay)

    def send_ctrl_c(self, *, log_send: bool = True) -> None:
        self.send_raw(b"\x03", "Ctrl+C", log_send=log_send)

    def send_line(self, line: str) -> None:
        self.send_raw(line.encode("ascii") + b"\r", line)

    def wait_for_any(
        self,
        patterns: dict[str, re.Pattern[str]],
        timeout: float,
        *,
        start: int | None = None,
        idle_timeout: float | None = None,
        description: str,
    ) -> tuple[str, str]:
        deadline = time.monotonic() + timeout
        last_rx = time.monotonic()
        start_at = 0 if start is None else max(0, start)

        while True:
            chunk = self.read_once(0.2)
            if chunk:
                last_rx = time.monotonic()
                haystack = self._buffer[start_at:]
                for name, pattern in patterns.items():
                    if pattern.search(haystack):
                        return name, haystack
            elif idle_timeout is not None and time.monotonic() - last_rx >= idle_timeout:
                return "idle", self._buffer[start_at:]

            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out while waiting for {description}")

    def wait_for_quiet(
        self,
        quiet_period: float,
        timeout: float,
        *,
        description: str,
        reset_watch_start: int | None = None,
    ) -> None:
        deadline = time.monotonic() + timeout
        last_rx = time.monotonic()
        saw_data = True
        if reset_watch_start is None:
            reset_watch_start = self.mark()

        while True:
            raise_if_reset(self, reset_watch_start)
            chunk = self.read_once(0.2)
            if chunk:
                saw_data = True
                last_rx = time.monotonic()
            elif saw_data and time.monotonic() - last_rx >= quiet_period:
                return

            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out while waiting for {description}")


def wait_for_reset_marker(console: SerialConsole, timeout: float) -> int:
    if console.reset_line is None:
        emit_script_message(
            "Press RESET on the board now.\n"
            "The script is waiting for reboot markers on the serial output:"
        )
    else:
        emit_script_message("Waiting for reboot markers on the serial output:")

    start = console.mark()
    deadline = time.monotonic() + timeout

    while True:
        reset_position = find_reset_marker(console, start)
        if reset_position is not None:
            log("Reset marker detected")
            return reset_position
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out while waiting for reset markers")
        console.read_once(0.2)


def wait_for_bootrom_loading(
    console: SerialConsole,
    args: argparse.Namespace,
    *,
    start: int | None = None,
) -> None:
    log("Waiting for boot ROM output after RESET")
    deadline = time.monotonic() + args.bootrom_timeout
    start = console.mark() if start is None else start
    reset_watch_start: int | None = None
    last_rx = time.monotonic()
    next_enter = None
    memory_test_seen = False
    vxworks_banner_seen = False
    nvram_invalid_seen = False
    nvram_handled = False
    enter_spam_started = False
    load_requested = False
    warned_autostart = False

    while True:
        raise_if_reset(console, reset_watch_start)
        haystack = console.buffer_since(start)

        if LOADING_RE.search(haystack):
            log("Loading detected")
            break

        if not memory_test_seen and MEMORY_TEST_RE.search(haystack):
            memory_test_seen = True
            log("Memory-test prompt detected")
            if reset_watch_start is None:
                reset_watch_start = console.mark()

        if not nvram_invalid_seen and NVRAM_INVALID_RE.search(haystack):
            nvram_invalid_seen = True
            warn(
                "Invalid revision info is stored in NVRAM. "
                "Copying the current revision info to NVRAM."
            )
            if reset_watch_start is None:
                reset_watch_start = console.mark()

        if nvram_invalid_seen and not nvram_handled and NVRAM_PRESS_KEY_RE.search(haystack):
            nvram_handled = True
            console.send_line("C")

        if not vxworks_banner_seen and VXWORKS_BANNER_RE.search(haystack):
            vxworks_banner_seen = True
            log("VxWorks banner detected")
            if reset_watch_start is None:
                reset_watch_start = console.mark()

        if not enter_spam_started and memory_test_seen and vxworks_banner_seen:
            enter_spam_started = True
            next_enter = time.monotonic()
            log("Starting <Enter> spam to stop VxWorks autostart")

        if not load_requested and BOOTROM_VXWORKS_PROMPT_RE.search(haystack):
            log("VxWorks boot prompt detected, sending 'l'")
            load_requested = True
            next_enter = None
            console.send_line("l")

        if not warned_autostart and BOOTROM_AUTOSTART_RE.search(haystack):
            warn("Failed to stop the autostart process!")
            warned_autostart = True
            next_enter = None

        if next_enter is not None and not load_requested and not warned_autostart and time.monotonic() >= next_enter:
            console.send_enter()
            next_enter = time.monotonic() + args.bootrom_enter_interval

        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out while waiting for VxWorks prompt or Loading")

        chunk = console.read_once(0.2)
        if chunk:
            last_rx = time.monotonic()
        elif (
            memory_test_seen
            and not vxworks_banner_seen
            and not load_requested
            and time.monotonic() - last_rx >= args.memory_test_halt_timeout
        ):
            error(f"Connection halted! Attempting to reconnect to {console.display_name}...")
            console.reconnect()
            last_rx = time.monotonic()

    log("Waiting for Loading to finish")
    console.wait_for_quiet(
        args.loading_quiet_period,
        args.loading_timeout,
        description="Loading to go quiet",
        reset_watch_start=reset_watch_start,
    )


def wait_for_u_boot_prompt(console: SerialConsole, args: argparse.Namespace) -> None:
    log("Waiting for U-Boot to start")
    deadline = time.monotonic() + args.uboot_timeout
    start = console.mark()
    # RAM-started U-Boot prints the same ELTEC startup banner as a real reset.
    # Ignore those markers in this phase to avoid false restart detection.
    reset_watch_start = None
    banner_logged = False
    autoboot_attempted = False
    nvram_invalid_seen = False
    nvram_handled = False
    ignore_next_prompt = False

    while True:
        raise_if_reset(console, reset_watch_start)
        haystack = console.buffer_since(start)

        if not nvram_invalid_seen and NVRAM_INVALID_RE.search(haystack):
            nvram_invalid_seen = True
            warn(
                "Invalid revision info is stored in NVRAM. "
                "Copying the current revision info to NVRAM."
            )

        if nvram_invalid_seen and not nvram_handled and NVRAM_PRESS_KEY_RE.search(haystack):
            nvram_handled = True
            ignore_next_prompt = True
            console.send_line("C")
            start = console.mark()
            continue

        if PROMPT_RE.search(haystack):
            if ignore_next_prompt:
                log("Ignoring temporary prompt after revision-info copy")
                ignore_next_prompt = False
                start = console.mark()
                continue
            return

        if UBOOT_TFTP_AUTORUN_RE.search(haystack):
            if interrupt_uboot_tftp_boot(console, args, start):
                return
            raise TimeoutError("Timed out while waiting for U-Boot prompt after interrupting TFTP boot")

        if not autoboot_attempted and UBOOT_AUTOBOOT_RE.search(haystack):
            autoboot_attempted = True
            if stop_uboot_autoboot(console, args, start):
                return
            if UBOOT_TFTP_AUTORUN_RE.search(console.buffer_since(start)):
                if interrupt_uboot_tftp_boot(console, args, start):
                    return
                raise TimeoutError("Timed out while waiting for U-Boot prompt after interrupting TFTP boot")

        if not banner_logged and UBOOT_BANNER_RE.search(haystack):
            log("U-Boot banner detected, waiting for initialization to finish")
            banner_logged = True

        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out while waiting for U-Boot prompt")

        console.read_once(0.2)


def wait_for_prompt_with_spam(
    console: SerialConsole,
    start: int,
    attempts: int,
    interval: float,
    *,
    send_fn,
    tftp_escape: bool = False,
    reset_watch_start: int | None = None,
) -> bool:
    if reset_watch_start is None:
        watch_resets = False
    else:
        watch_resets = True

    for attempt in range(attempts):
        send_fn(log_send=(attempt == 0))
        step_deadline = time.monotonic() + interval
        while True:
            if watch_resets:
                raise_if_reset(console, reset_watch_start)
            haystack = console.buffer_since(start)
            if PROMPT_RE.search(haystack):
                return True
            if tftp_escape and UBOOT_TFTP_AUTORUN_RE.search(haystack):
                return False
            if time.monotonic() >= step_deadline:
                break
            console.read_once(0.05)
    return PROMPT_RE.search(console.buffer_since(start)) is not None


def stop_uboot_autoboot(console: SerialConsole, args: argparse.Namespace, start: int) -> bool:
    log("U-Boot autoboot window detected, sending <Enter>")
    reset_watch_start = console.mark()

    if wait_for_prompt_with_spam(
        console,
        start,
        1,
        args.uboot_enter_interval,
        send_fn=console.send_enter,
        tftp_escape=True,
        reset_watch_start=reset_watch_start,
    ):
        return True

    log("U-Boot prompt not received yet, continuing <Enter> spam")
    return wait_for_prompt_with_spam(
        console,
        start,
        args.uboot_enter_attempts,
        args.uboot_enter_interval,
        send_fn=console.send_enter,
        tftp_escape=True,
        reset_watch_start=reset_watch_start,
    )


def interrupt_uboot_tftp_boot(console: SerialConsole, args: argparse.Namespace, start: int) -> bool:
    warn("Failed to stop the U-Boot autoboot process!")
    warn("Trying to interrupt the tftp boot process...")
    reset_watch_start = console.mark()

    return wait_for_prompt_with_spam(
        console,
        start,
        args.uboot_ctrl_c_attempts,
        args.uboot_ctrl_c_interval,
        send_fn=console.send_ctrl_c,
        reset_watch_start=reset_watch_start,
    )


def run_uboot_command(
    console: SerialConsole,
    command: str,
    timeout: float,
) -> str:
    start = console.mark()
    reset_watch_start = console.mark()
    console.send_line(command)
    deadline = time.monotonic() + timeout

    while True:
        raise_if_reset(console, reset_watch_start)
        haystack = console.buffer_since(start)
        if PROMPT_RE.search(haystack):
            return haystack
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out while waiting for prompt after '{command}'")
        console.read_once(0.2)


def sleep_with_reset_watch(console: SerialConsole, delay: float) -> None:
    deadline = time.monotonic() + delay
    reset_watch_start = console.mark()

    while True:
        raise_if_reset(console, reset_watch_start)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        console.read_once(min(0.2, remaining))


def parse_tftp_size_hex(command_output: str, fallback_size: int) -> str:
    matches = TFTP_SIZE_RE.findall(command_output)
    if matches:
        return matches[-1].lower()
    log("Could not parse size from tftpboot output, falling back to local u-boot.bin size")
    return format(fallback_size, "x")


def flash_output_has_sector_boundary_error(command_output: str) -> bool:
    return FLASH_SECTOR_BOUNDARY_ERROR_RE.search(command_output) is not None


def flash_output_has_not_erased_error(command_output: str) -> bool:
    return FLASH_NOT_ERASED_RE.search(command_output) is not None


def validate_flash_programming(
    protect_output: str,
    erase_output: str,
    copy_output: str,
) -> None:
    sector_boundary_error = (
        flash_output_has_sector_boundary_error(protect_output)
        or flash_output_has_sector_boundary_error(erase_output)
    )
    flash_not_erased = flash_output_has_not_erased_error(copy_output)

    if sector_boundary_error:
        error("Flash address range error: end address is not on a sector boundary.")

    if flash_not_erased:
        if sector_boundary_error:
            error("Flash write failed: target flash was not erased.")
        else:
            error("Flash write failed: Copy to Flash reported 'Flash not Erased'.")
        raise SystemExit(1)

    if sector_boundary_error:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build BAB-750 U-Boot, copy it to TFTP, then flash it over ttyUSB0."
    )
    parser.add_argument(
        "--serial-port",
        default="ttyUSB0",
        help="serial device name or full path (default: ttyUSB0)",
    )
    parser.add_argument(
        "--baudrate",
        type=positive_int,
        default=9600,
        help="serial speed (default: 9600)",
    )
    parser.add_argument(
        "--cross-compile",
        help="cross-compiler prefix, e.g. /opt/powerpc-linux-gnu/bin/powerpc-linux-gnu-",
    )
    parser.add_argument(
        "--reset-line",
        choices=sorted(MODEM_LINE_BITS),
        help="optional modem-control line to pulse for RESET (rts or dtr)",
    )
    parser.add_argument(
        "--reset-line-active-state",
        choices=("asserted", "deasserted"),
        default="asserted",
        help=(
            "whether the chosen modem line should be in the asserted or deasserted state "
            "while the board RESET pin is held active (default: asserted)"
        ),
    )
    parser.add_argument(
        "--auto-reset",
        action="store_true",
        help="pulse the configured reset line before waiting for boot markers",
    )
    parser.add_argument(
        "--reset-pulse",
        type=positive_float,
        default=0.25,
        help="seconds to hold RESET active when --auto-reset is used (default: 0.25)",
    )
    parser.add_argument(
        "--reset-settle-delay",
        type=positive_float,
        default=0.5,
        help="seconds to wait after releasing RESET (default: 0.5)",
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=os.cpu_count() or 1,
        help="parallel make jobs (default: nproc)",
    )
    parser.add_argument(
        "--serverip",
        default="192.168.1.101",
        help="U-Boot serverip value (default: 192.168.1.101)",
    )
    parser.add_argument(
        "--ipaddr",
        default="192.168.1.123",
        help="U-Boot ipaddr value (default: 192.168.1.123)",
    )
    parser.add_argument(
        "--load-address",
        default="400000",
        help="load address for tftpboot/cp.b (default: 400000)",
    )
    parser.add_argument(
        "--flash-start",
        default="ff900000",
        help="flash start address (default: ff900000)",
    )
    parser.add_argument(
        "--flash-end",
        default="ff9fffff",
        help="flash end address (default: ff9fffff)",
    )
    parser.add_argument(
        "--go-address",
        default="1000100",
        help="address for the 'g' command after Loading (default: 1000100)",
    )
    parser.add_argument(
        "--bootrom-timeout",
        type=positive_float,
        default=300.0,
        help="seconds to wait for boot ROM output after RESET (default: 300)",
    )
    parser.add_argument(
        "--loading-timeout",
        type=positive_float,
        default=300.0,
        help="seconds to wait for Loading and its completion (default: 300)",
    )
    parser.add_argument(
        "--loading-quiet-period",
        type=positive_float,
        default=1.5,
        help="seconds of serial silence that mean Loading is done (default: 1.5)",
    )
    parser.add_argument(
        "--post-loading-delay",
        type=positive_float,
        default=5.0,
        help="seconds to wait after Loading before 'g' (default: 5)",
    )
    parser.add_argument(
        "--uboot-timeout",
        type=positive_float,
        default=180.0,
        help="seconds to wait for U-Boot to appear after 'g' (default: 180)",
    )
    parser.add_argument(
        "--prompt-timeout",
        type=positive_float,
        default=30.0,
        help="seconds to wait for the U-Boot prompt after each command (default: 30)",
    )
    parser.add_argument(
        "--bootrom-enter-interval",
        type=positive_float,
        default=0.5,
        help="interval between repeated <Enter> presses while stopping boot ROM autostart (default: 0.5)",
    )
    parser.add_argument(
        "--memory-test-halt-timeout",
        type=positive_float,
        default=5.0,
        help="seconds without serial output during memory test before reconnecting the port (default: 5)",
    )
    parser.add_argument(
        "--uboot-enter-interval",
        type=positive_float,
        default=0.2,
        help="interval between repeated <Enter> presses while stopping U-Boot autoboot (default: 0.2)",
    )
    parser.add_argument(
        "--uboot-enter-attempts",
        type=positive_int,
        default=10,
        help="extra <Enter> attempts after the first U-Boot autoboot interrupt try (default: 10)",
    )
    parser.add_argument(
        "--uboot-ctrl-c-interval",
        type=positive_float,
        default=0.2,
        help="interval between repeated Ctrl+C presses while interrupting TFTP autoboot (default: 0.2)",
    )
    parser.add_argument(
        "--uboot-ctrl-c-attempts",
        type=positive_int,
        default=10,
        help="maximum Ctrl+C attempts while interrupting TFTP autoboot (default: 10)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.auto_reset and not args.reset_line:
        raise SystemExit("--auto-reset requires --reset-line")

    cross_compile = resolve_cross_compile(args.cross_compile)
    serial_port = resolve_serial_port(args.serial_port)
    serial_display_name = pathlib.Path(args.serial_port).name if os.sep in args.serial_port else args.serial_port
    reset_active_state = args.reset_line_active_state == "asserted"

    log(f"Using cross-compiler prefix: {cross_compile}")
    artifact = build_u_boot(cross_compile, args.jobs)
    copy_to_tftp(artifact)

    log("Make sure the TFTP container/server is already running before continuing")
    with SerialConsole(
        serial_port,
        args.baudrate,
        display_name=serial_display_name,
        reset_line=args.reset_line,
        reset_active_state=reset_active_state,
    ) as console:
        log(f"Connected to {serial_port} at {args.baudrate} baud")
        if args.reset_line is not None:
            log(
                f"{args.reset_line.upper()} reset control enabled "
                f"({args.reset_line_active_state} line state asserts RESET)"
            )
        restart_from: int | None = None

        while True:
            try:
                if restart_from is None:
                    if args.auto_reset:
                        console.pulse_reset(args.reset_pulse, args.reset_settle_delay)
                    cycle_start = wait_for_reset_marker(console, args.bootrom_timeout)
                else:
                    log("Reset marker detected")
                    cycle_start = restart_from
                    restart_from = None

                wait_for_bootrom_loading(console, args, start=cycle_start)

                log(f"Waiting {args.post_loading_delay:.1f}s before starting U-Boot from RAM")
                sleep_with_reset_watch(console, args.post_loading_delay)
                console.send_line(f"g {args.go_address}")

                wait_for_u_boot_prompt(console, args)
                log("U-Boot prompt acquired, starting flash commands")

                run_uboot_command(console, f"setenv serverip {args.serverip}", args.prompt_timeout)
                run_uboot_command(console, f"setenv ipaddr {args.ipaddr}", args.prompt_timeout)
                tftp_output = run_uboot_command(
                    console,
                    f"tftpboot {args.load_address} u-boot.bin",
                    max(args.prompt_timeout, 120.0),
                )
                size_hex = parse_tftp_size_hex(tftp_output, artifact.stat().st_size)
                protect_output = run_uboot_command(
                    console,
                    f"protect off {args.flash_start} {args.flash_end}",
                    args.prompt_timeout,
                )
                erase_output = run_uboot_command(
                    console,
                    f"erase {args.flash_start} {args.flash_end}",
                    max(args.prompt_timeout, 180.0),
                )
                copy_output = run_uboot_command(
                    console,
                    f"cp.b {args.load_address} {args.flash_start} {size_hex}",
                    max(args.prompt_timeout, 120.0),
                )
                validate_flash_programming(protect_output, erase_output, copy_output)
                break
            except ResetDetected as exc:
                warn("Machine was rebooted before the script finished, restarting from the beginning.")
                restart_from = exc.position

    log("Flash programming finished")
    emit_script_message("Switch the memory addressing mode, then reboot the machine.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        error(f"Command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}")
        raise SystemExit(1)
    except TimeoutError as exc:
        error(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        emit_script_message("Interrupted.")
        raise SystemExit(130)
    except Exception as exc:
        error(f"Unexpected error: {exc}")
        raise SystemExit(1)
