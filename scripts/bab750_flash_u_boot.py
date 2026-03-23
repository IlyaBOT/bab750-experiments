#!/usr/bin/env python3

"""Build and flash U-Boot for the Eltec BAB-750 over serial + TFTP."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import select
import shutil
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
YELLOW = "\033[33m"
RESET = "\033[0m"
SERIAL_LINE_OPEN = False


def emit_script_message(message: str) -> None:
    global SERIAL_LINE_OPEN

    payload = message.rstrip("\n")
    prefix = "\n" if SERIAL_LINE_OPEN else ""
    color_prefix = YELLOW if sys.stderr.isatty() else ""
    color_suffix = RESET if color_prefix else ""

    sys.stdout.flush()
    os.write(
        sys.stderr.fileno(),
        f"{prefix}{color_prefix}{payload}{color_suffix}\n".encode("utf-8", errors="replace"),
    )
    SERIAL_LINE_OPEN = False


def log(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    emit_script_message(f"[{timestamp}] {message}")


def warn(message: str) -> None:
    emit_script_message(f"[WARN] {message}")


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


def build_u_boot(cross_compile: str, jobs: int) -> pathlib.Path:
    env = os.environ.copy()
    env["CROSS_COMPILE"] = cross_compile

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
    def __init__(self, port: pathlib.Path, baudrate: int) -> None:
        self.port = port
        self.baudrate = baudrate
        self.fd: int | None = None
        self._buffer = ""
        self._original_attrs = None

    def __enter__(self) -> "SerialConsole":
        self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self._configure_port()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is None:
            return
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

    def wait_for_quiet(self, quiet_period: float, timeout: float, *, description: str) -> None:
        deadline = time.monotonic() + timeout
        last_rx = time.monotonic()
        saw_data = True

        while True:
            chunk = self.read_once(0.2)
            if chunk:
                saw_data = True
                last_rx = time.monotonic()
            elif saw_data and time.monotonic() - last_rx >= quiet_period:
                return

            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out while waiting for {description}")


def wait_for_reset_marker(console: SerialConsole, timeout: float) -> None:
    emit_script_message(
        "Press RESET on the board now.\n"
        "The script is waiting for reboot markers on the serial output:"
    )

    start = console.mark()
    deadline = time.monotonic() + timeout

    while True:
        if RESET_MARKER_RE.search(console.buffer_since(start)):
            log("Reset marker detected")
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out while waiting for reset markers")
        console.read_once(0.2)


def wait_for_bootrom_loading(console: SerialConsole, args: argparse.Namespace) -> None:
    log("Waiting for boot ROM output after RESET")
    deadline = time.monotonic() + args.bootrom_timeout
    start = console.mark()
    next_enter = None
    memory_test_seen = False
    vxworks_banner_seen = False
    enter_spam_started = False
    load_requested = False
    warned_autostart = False

    while True:
        haystack = console.buffer_since(start)

        if LOADING_RE.search(haystack):
            log("Loading detected")
            break

        if not memory_test_seen and MEMORY_TEST_RE.search(haystack):
            memory_test_seen = True
            log("Memory-test prompt detected")

        if not vxworks_banner_seen and VXWORKS_BANNER_RE.search(haystack):
            vxworks_banner_seen = True
            log("VxWorks banner detected")

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

        console.read_once(0.2)

    log("Waiting for Loading to finish")
    console.wait_for_quiet(
        args.loading_quiet_period,
        args.loading_timeout,
        description="Loading to go quiet",
    )


def wait_for_u_boot_prompt(console: SerialConsole, args: argparse.Namespace) -> None:
    log("Waiting for U-Boot to start")
    deadline = time.monotonic() + args.uboot_timeout
    start = console.mark()
    banner_logged = False
    autoboot_attempted = False

    while True:
        haystack = console.buffer_since(start)

        if PROMPT_RE.search(haystack):
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
) -> bool:
    for attempt in range(attempts):
        send_fn(log_send=(attempt == 0))
        step_deadline = time.monotonic() + interval
        while True:
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

    if wait_for_prompt_with_spam(
        console,
        start,
        1,
        args.uboot_enter_interval,
        send_fn=console.send_enter,
        tftp_escape=True,
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
    )


def interrupt_uboot_tftp_boot(console: SerialConsole, args: argparse.Namespace, start: int) -> bool:
    warn("Failed to stop the U-Boot autoboot process!")
    warn("Trying to interrupt the tftp boot process...")

    return wait_for_prompt_with_spam(
        console,
        start,
        args.uboot_ctrl_c_attempts,
        args.uboot_ctrl_c_interval,
        send_fn=console.send_ctrl_c,
    )


def run_uboot_command(
    console: SerialConsole,
    command: str,
    timeout: float,
) -> str:
    start = console.mark()
    console.send_line(command)
    _, text = console.wait_for_any(
        {"prompt": PROMPT_RE},
        timeout,
        start=start,
        description=f"prompt after '{command}'",
    )
    return text


def parse_tftp_size_hex(command_output: str, fallback_size: int) -> str:
    matches = TFTP_SIZE_RE.findall(command_output)
    if matches:
        return matches[-1].lower()
    log("Could not parse size from tftpboot output, falling back to local u-boot.bin size")
    return format(fallback_size, "x")


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
        default="ff92ffff",
        help="flash end address (default: ff92ffff)",
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
    cross_compile = resolve_cross_compile(args.cross_compile)
    serial_port = resolve_serial_port(args.serial_port)

    log(f"Using cross-compiler prefix: {cross_compile}")
    artifact = build_u_boot(cross_compile, args.jobs)
    copy_to_tftp(artifact)

    log("Make sure the TFTP container/server is already running before continuing")
    with SerialConsole(serial_port, args.baudrate) as console:
        log(f"Connected to {serial_port} at {args.baudrate} baud")
        wait_for_reset_marker(console, args.bootrom_timeout)

        wait_for_bootrom_loading(console, args)

        log(f"Waiting {args.post_loading_delay:.1f}s before starting U-Boot from RAM")
        time.sleep(args.post_loading_delay)
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
        run_uboot_command(
            console,
            f"protect off {args.flash_start} {args.flash_end}",
            args.prompt_timeout,
        )
        run_uboot_command(
            console,
            f"erase {args.flash_start} {args.flash_end}",
            max(args.prompt_timeout, 180.0),
        )
        run_uboot_command(
            console,
            f"cp.b {args.load_address} {args.flash_start} {size_hex}",
            max(args.prompt_timeout, 120.0),
        )

    log("Flash programming finished")
    emit_script_message("Switch the memory addressing mode, then reboot the machine.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TimeoutError as exc:
        emit_script_message(f"[ERROR] {exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        emit_script_message("Interrupted.")
        raise SystemExit(130)
