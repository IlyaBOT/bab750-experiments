#!/usr/bin/env python3

"""Drive the BAB-750 serial console and start Linux from a flashed U-Boot."""

from __future__ import annotations

import argparse
import fcntl
import os
import pathlib
import re
import select
import struct
import sys
import termios
import time


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_COMMANDS_FILE = ROOT_DIR / "bab750-tftp" / "uboot-netboot.txt"
RESET_MARKER_RE = re.compile(
    r"\*\*\*\s*ELTEC Elektronik, Mainz\s*\*\*\*|"
    r"BAB-PPC Monitor Version 1\.2\.1/2|"
    r"Init MPU/MSR/FPU/Segment registers\.",
    re.IGNORECASE,
)
PROMPT_RE = re.compile(r"=>\s")
UBOOT_BANNER_RE = re.compile(r"\bU-Boot\b", re.IGNORECASE)
UBOOT_AUTOBOOT_RE = re.compile(
    r"(?:Hit any key to stop autoboot|Press\s+'Enter'\s+or\s+'Space'\s+to stop autoboot)",
    re.IGNORECASE,
)
UBOOT_TFTP_AUTORUN_RE = re.compile(
    r"BOOTP broadcast\s+\d+|"
    r"TFTP from server\s+[0-9.]+|"
    r"Filename\s+'[^']+'\.\s*|"
    r"Load address:\s*0x[0-9a-fA-F]+",
    re.IGNORECASE,
)
NVRAM_INVALID_RE = re.compile(r"Invalid revision info copy in nvram\s*!", re.IGNORECASE)
NVRAM_PRESS_KEY_RE = re.compile(r"Press key:", re.IGNORECASE)
ENV_OVERFLOW_RE = re.compile(r'environment overflow,\s*"filesize"\s+deleted', re.IGNORECASE)
TFTP_TRANSFERRED_RE = re.compile(r"Bytes transferred\s*=\s*\d+", re.IGNORECASE)
SERIAL_ERROR_LINE_RE = re.compile(
    r"(?im)^(.*(?:\[\s*ERROR\s*\]|##\s*Error:|(?<!#)Error:|ERROR:|"
    r"Program Check Exception|Machine Check Exception|Alignment Exception|"
    r"Instruction Access Exception|Data Access Exception).*)$"
)
LINUX_BOOT_RE = re.compile(
    r"Starting kernel|Linux version|Kernel command line|Freeing unused kernel memory|Run /sbin/init",
    re.IGNORECASE,
)
LINUX_SUCCESS_RE = re.compile(
    r"BAB750 PPC mininit is alive\.|"
    r"BAB750 rescue shell ready|"
    r"BAB750 vendor Linux 2\.4 userspace is up\.|"
    r"(?:^|[\r\n])bab750#\s|"
    r"(?:^|[\r\n])login:\s*$|"
    r"Welcome to Ad[eé]lie",
    re.IGNORECASE | re.MULTILINE,
)
KERNEL_FAILURE_RE = re.compile(
    r"Kernel panic|VFS: Unable to mount root fs|No working init found|"
    r"Unable to handle kernel|Oops:|BUG:|NFS:.*not responding|"
    r"Program Check Exception|Machine Check Exception|Alignment Exception|"
    r"Instruction Access Exception|Data Access Exception",
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


def resolve_serial_port(raw: str) -> pathlib.Path:
    raw_path = pathlib.Path(raw)
    candidates = [raw_path if raw_path.is_absolute() else pathlib.Path("/dev") / raw]

    base = raw_path.name
    lowered = base.lower()
    if lowered.startswith("ttyusb") and base != "ttyUSB" + base[6:]:
        candidates.append(pathlib.Path("/dev") / ("ttyUSB" + base[6:]))

    seen: set[pathlib.Path] = set()
    unique_candidates: list[pathlib.Path] = []
    for candidate in candidates:
        if candidate not in seen:
            unique_candidates.append(candidate)
            seen.add(candidate)

    for candidate in unique_candidates:
        if candidate.exists():
            return candidate

    searched = "\n".join(f"  - {candidate}" for candidate in unique_candidates)
    raise SystemExit(f"Serial device not found.\nSearched:\n{searched}")


def load_commands_file(path: pathlib.Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"U-Boot commands file not found: {path}")

    commands = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        commands.append(line)

    if not commands:
        raise SystemExit(f"No U-Boot commands found in: {path}")
    if not commands[-1].startswith("bootm "):
        raise SystemExit(f"The last command in {path} must be a bootm command")
    return commands


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


def report_serial_errors(haystack: str, emitted: set[str]) -> None:
    for match in SERIAL_ERROR_LINE_RE.finditer(haystack):
        line = match.group(1).strip()
        if not line or line in emitted:
            continue
        if ENV_OVERFLOW_RE.search(line):
            continue
        emitted.add(line)
        error(line)


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
        try:
            chunk = os.read(self.fd, 4096)
        except BlockingIOError:
            return ""
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

    def send_ctrl_c(self, *, log_send: bool = True) -> None:
        self.send_raw(b"\x03", "Ctrl+C", log_send=log_send)

    def send_line(self, line: str) -> None:
        self.send_raw(line.encode("ascii") + b"\r", line)


def attach_console(console: SerialConsole) -> None:
    if console.fd is None:
        return

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_is_tty = sys.stdin.isatty()
    original_stdin_attrs = None

    emit_script_message("Attached to the BAB750 console. Press Ctrl-] to disconnect.", color=BLUE)

    try:
        if stdin_is_tty:
            original_stdin_attrs = termios.tcgetattr(stdin_fd)
            tty_attrs = termios.tcgetattr(stdin_fd)
            tty_attrs[3] &= ~(termios.ICANON | termios.ECHO)
            tty_attrs[6][termios.VMIN] = 1
            tty_attrs[6][termios.VTIME] = 0
            termios.tcsetattr(stdin_fd, termios.TCSANOW, tty_attrs)

        while True:
            ready, _, _ = select.select([console.fd, stdin_fd], [], [])
            if console.fd in ready:
                chunk = os.read(console.fd, 4096)
                if chunk:
                    os.write(stdout_fd, chunk)
            if stdin_fd in ready:
                data = os.read(stdin_fd, 1024)
                if not data:
                    return
                if b"\x1d" in data:
                    emit_script_message("Detached from the BAB750 console.", color=BLUE)
                    return
                os.write(console.fd, data)
    finally:
        if original_stdin_attrs is not None:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, original_stdin_attrs)


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
    watch_resets = reset_watch_start is not None

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


def wait_for_u_boot_prompt(console: SerialConsole, args: argparse.Namespace, *, start: int) -> None:
    log("Waiting for U-Boot prompt after RESET")
    deadline = time.monotonic() + args.uboot_timeout
    banner_logged = False
    autoboot_attempted = False
    nvram_invalid_seen = False
    nvram_handled = False
    ignore_next_prompt = False
    emitted_errors: set[str] = set()

    while True:
        haystack = console.buffer_since(start)
        report_serial_errors(haystack, emitted_errors)

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
            emitted_errors.clear()
            continue

        if PROMPT_RE.search(haystack):
            if ignore_next_prompt:
                log("Ignoring temporary prompt after revision-info copy")
                ignore_next_prompt = False
                start = console.mark()
                emitted_errors.clear()
                continue
            return

        if UBOOT_TFTP_AUTORUN_RE.search(haystack):
            if interrupt_uboot_tftp_boot(console, args, start):
                return
            raise TimeoutError("Timed out while waiting for the U-Boot prompt after interrupting autoboot")

        if not autoboot_attempted and UBOOT_AUTOBOOT_RE.search(haystack):
            autoboot_attempted = True
            if stop_uboot_autoboot(console, args, start):
                return
            if UBOOT_TFTP_AUTORUN_RE.search(console.buffer_since(start)):
                if interrupt_uboot_tftp_boot(console, args, start):
                    return
                raise TimeoutError("Timed out while waiting for the U-Boot prompt after interrupting autoboot")

        if not banner_logged and UBOOT_BANNER_RE.search(haystack):
            banner_logged = True
            log("U-Boot banner detected")

        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out while waiting for the U-Boot prompt after reset")

        console.read_once(0.2)


def run_uboot_command(
    console: SerialConsole,
    command: str,
    timeout: float,
    *,
    idle_timeout: float | None = None,
    stage_description: str,
    accept_completion_without_prompt: bool = False,
) -> str:
    start = console.mark()
    reset_watch_start = console.mark()
    emitted_errors: set[str] = set()
    last_rx = time.monotonic()
    is_tftp_command = command.startswith("tftpboot ")
    saw_tftp_completion = False
    prompt_nudge_sent = False
    console.send_line(command)
    deadline = time.monotonic() + timeout

    while True:
        raise_if_reset(console, reset_watch_start)
        haystack = console.buffer_since(start)
        report_serial_errors(haystack, emitted_errors)

        if is_tftp_command and TFTP_TRANSFERRED_RE.search(haystack):
            saw_tftp_completion = True

        if PROMPT_RE.search(haystack):
            return haystack

        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out while waiting for the U-Boot prompt after {stage_description}")

        chunk = console.read_once(0.2)
        if chunk:
            last_rx = time.monotonic()
        elif idle_timeout is not None and time.monotonic() - last_rx >= idle_timeout:
            if is_tftp_command and saw_tftp_completion and not prompt_nudge_sent:
                log("TFTP transfer completed, but the U-Boot prompt did not appear yet. Sending <Enter> once.")
                console.send_enter(log_send=False)
                prompt_nudge_sent = True
                last_rx = time.monotonic()
                continue
            if is_tftp_command and saw_tftp_completion and accept_completion_without_prompt:
                warn("TFTP transfer finished, but the U-Boot prompt never appeared. Continuing with the loaded image anyway.")
                return haystack
            raise TimeoutError(
                f"Board hang detected while {stage_description}: "
                f"no new serial output for {idle_timeout:.0f} seconds"
            )


def monitor_linux_boot(console: SerialConsole, args: argparse.Namespace, boot_command: str) -> None:
    start = console.mark()
    reset_watch_start = console.mark()
    emitted_errors: set[str] = set()
    last_rx = time.monotonic()
    saw_linux_output = False

    console.send_line(boot_command)
    log(f"Starting Linux with: {boot_command}")

    while True:
        raise_if_reset(console, reset_watch_start)
        haystack = console.buffer_since(start)
        report_serial_errors(haystack, emitted_errors)

        if PROMPT_RE.search(haystack):
            raise SystemExit("bootm returned to the U-Boot prompt")

        if KERNEL_FAILURE_RE.search(haystack):
            raise SystemExit("Linux reported a boot failure on the serial console")

        if LINUX_SUCCESS_RE.search(haystack):
            log("Linux reached userspace successfully")
            return

        if LINUX_BOOT_RE.search(haystack):
            saw_linux_output = True

        chunk = console.read_once(0.2)
        if chunk:
            last_rx = time.monotonic()
            continue

        if time.monotonic() - last_rx >= args.hang_timeout:
            if saw_linux_output:
                raise TimeoutError(
                    f"Board hang detected while booting Linux: "
                    f"no new serial output for {args.hang_timeout:.0f} seconds"
                )
            raise TimeoutError(
                f"Board hang detected while starting Linux: "
                f"no new serial output for {args.hang_timeout:.0f} seconds"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
        "--commands-file",
        default=str(DEFAULT_COMMANDS_FILE),
        help=f"path to the prepared U-Boot command file (default: {DEFAULT_COMMANDS_FILE})",
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
        "--reset-timeout",
        type=positive_float,
        default=300.0,
        help="seconds to wait for reboot markers after RESET (default: 300)",
    )
    parser.add_argument(
        "--uboot-timeout",
        type=positive_float,
        default=180.0,
        help="seconds to wait for the U-Boot prompt after RESET (default: 180)",
    )
    parser.add_argument(
        "--prompt-timeout",
        type=positive_float,
        default=30.0,
        help="seconds to wait for the U-Boot prompt after normal commands (default: 30)",
    )
    parser.add_argument(
        "--tftp-timeout",
        type=positive_float,
        default=180.0,
        help="seconds to wait for a tftpboot command to finish (default: 180)",
    )
    parser.add_argument(
        "--hang-timeout",
        type=positive_float,
        default=10.0,
        help="seconds without serial output before the board is considered hung (default: 10)",
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
    parser.add_argument(
        "--attach",
        action="store_true",
        help="stay attached to the serial console after Linux reaches the prompt",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.auto_reset and not args.reset_line:
        raise SystemExit("--auto-reset requires --reset-line")

    serial_port = resolve_serial_port(args.serial_port)
    commands_file = pathlib.Path(args.commands_file).expanduser().resolve()
    commands = load_commands_file(commands_file)
    serial_display_name = pathlib.Path(args.serial_port).name if os.sep in args.serial_port else args.serial_port
    reset_active_state = args.reset_line_active_state == "asserted"

    with SerialConsole(
        serial_port,
        args.baudrate,
        display_name=serial_display_name,
        reset_line=args.reset_line,
        reset_active_state=reset_active_state,
    ) as console:
        log(f"Connected to {serial_port} at {args.baudrate} baud")
        log(f"Using U-Boot command file: {commands_file}")
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
                    cycle_start = wait_for_reset_marker(console, args.reset_timeout)
                else:
                    log("Reset marker detected")
                    cycle_start = restart_from
                    restart_from = None

                wait_for_u_boot_prompt(console, args, start=cycle_start)
                log("U-Boot prompt acquired, starting Linux netboot commands")

                for command in commands[:-1]:
                    timeout = args.tftp_timeout if command.startswith("tftpboot ") else args.prompt_timeout
                    idle_timeout = args.hang_timeout if command.startswith("tftpboot ") else None
                    stage_description = (
                        "downloading the Linux image via TFTP"
                        if command.startswith("tftpboot ")
                        else f"running '{command}'"
                    )
                    output = run_uboot_command(
                        console,
                        command,
                        timeout,
                        idle_timeout=idle_timeout,
                        stage_description=stage_description,
                        accept_completion_without_prompt=command.startswith("tftpboot "),
                    )
                    if ENV_OVERFLOW_RE.search(output):
                        warn(
                            "U-Boot could not store filesize in its tiny environment, "
                            "but the TFTP transfer completed."
                        )

                monitor_linux_boot(console, args, commands[-1])
                if args.attach:
                    attach_console(console)
                return 0
            except ResetDetected as exc:
                warn("Machine was rebooted before Linux boot finished, restarting from the beginning.")
                restart_from = exc.position


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TimeoutError as exc:
        error(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        emit_script_message("Interrupted.")
        raise SystemExit(130)
    except Exception as exc:
        error(f"Unexpected error: {exc}")
        raise SystemExit(1)
