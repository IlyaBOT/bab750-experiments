#!/usr/bin/env python3

"""Prepare Adelie Linux netboot assets for the Eltec BAB-750."""

from __future__ import annotations

import argparse
import os
import pathlib
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
TFTP_DIR = ROOT_DIR / "bab750-tftp"
TFTPBOOT_DIR = TFTP_DIR / "tftpboot"
NFSROOT_DIR = TFTP_DIR / "nfsroot"
EXPORTED_ROOTFS_DIR = NFSROOT_DIR / "adelie-full"
MKIMAGE = ROOT_DIR / "u-boot-lab" / "tools" / "mkimage"
VMLINUX_PREFIX = "vmlinux-"
DEFAULT_KERNEL_IMAGE = "adelie-linux-ppc.uImage"
DEFAULT_INITRD_IMAGE = "adelie-initramfs.uInitrd"
DEFAULT_UBOOT_COMMANDS = TFTP_DIR / "uboot-netboot.txt"
DEFAULT_KERNEL_LOAD_ADDRESS = "1400000"
DEFAULT_INITRD_LOAD_ADDRESS = "2c00000"
DEFAULT_SERVER_IP = "192.168.1.101"
DEFAULT_CLIENT_IP = "192.168.1.123"
DEFAULT_NETMASK = "255.255.255.0"
DEFAULT_GATEWAY = ""
DEFAULT_INTERFACE = ""
DEFAULT_NFSROOT = "/adelie-full"
LIBRARY_DIR_CANDIDATES = ("lib", "usr/lib")
RUNTIME_BINARIES = (
    "bin/dash",
    "bin/mount",
    "bin/ifconfig",
    "bin/route",
    "bin/sleep",
    "bin/kmod",
    "sbin/switch_root",
)
RUNTIME_MODULES = (
    "kernel/drivers/net/mii.ko.xz",
    "kernel/drivers/net/ethernet/dec/tulip/tulip.ko.xz",
    "kernel/net/sunrpc/sunrpc.ko.xz",
    "kernel/fs/nfs_common/grace.ko.xz",
    "kernel/fs/lockd/lockd.ko.xz",
    "kernel/fs/nfs/nfs.ko.xz",
    "kernel/net/dns_resolver/dns_resolver.ko.xz",
    "kernel/fs/nfs/nfsv4.ko.xz",
    "kernel/fs/nfs_common/nfs_acl.ko.xz",
    "kernel/fs/nfs/nfsv3.ko.xz",
)


def log(message: str) -> None:
    print(f"[prepare] {message}")


def run_command(
    cmd: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
    stdout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    log(f"Running: {' '.join(shlex.quote(part) for part in cmd)}")
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=stdout,
    )


def require_tool(path_or_name: pathlib.Path | str) -> pathlib.Path:
    if isinstance(path_or_name, pathlib.Path):
        if not path_or_name.exists():
            raise SystemExit(f"Required tool not found: {path_or_name}")
        return path_or_name

    resolved = shutil.which(path_or_name)
    if not resolved:
        raise SystemExit(f"Required tool not found in PATH: {path_or_name}")
    return pathlib.Path(resolved)


def resolve_cross_compile(explicit: str | None) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)

    candidates.extend(
        [
            "/opt/powerpc-linux-gnu/bin/powerpc-linux-gnu-",
            str(ROOT_DIR / "powerpc-linux-gnu" / "bin" / "powerpc-linux-gnu-"),
        ]
    )

    for prefix in candidates:
        if pathlib.Path(f"{prefix}objcopy").exists():
            return prefix

    searched = "\n".join(f"  - {candidate}objcopy" for candidate in candidates)
    raise SystemExit(
        "PowerPC binutils not found.\n"
        "Pass --cross-compile explicitly or install one of:\n"
        f"{searched}"
    )


def collect_toolchain_runtime_lib_dirs(toolchain_root: pathlib.Path) -> list[pathlib.Path]:
    runtime_lib_dirs = [toolchain_root / "lib"]
    runtime_lib_dirs.extend(sorted(toolchain_root.glob("*/powerpc-linux-gnu/lib")))
    return [path for path in runtime_lib_dirs if path.exists()]


def build_cross_env(cross_compile: str) -> dict[str, str]:
    tool_prefix = pathlib.Path(cross_compile)
    toolchain_root = tool_prefix.parent.parent
    env = os.environ.copy()
    runtime_lib_dirs = collect_toolchain_runtime_lib_dirs(toolchain_root)
    if runtime_lib_dirs:
        extra = ":".join(str(path) for path in runtime_lib_dirs)
        existing = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = f"{extra}:{existing}" if existing else extra
    return env


def discover_rootfs_tarball(explicit: str | None) -> pathlib.Path:
    if explicit:
        tarball = pathlib.Path(explicit).expanduser().resolve()
        if tarball.exists():
            return tarball
        raise SystemExit(f"Rootfs tarball not found: {tarball}")

    candidates = sorted(pathlib.Path.home().glob("Downloads/adelie-rootfs-full-ppc-*.txz"))
    if candidates:
        return candidates[-1].resolve()

    raise SystemExit(
        "Could not find adelie-rootfs-full-ppc tarball.\n"
        "Pass --rootfs-full-tarball explicitly."
    )


def prepare_exported_rootfs(tarball: pathlib.Path, force_extract: bool) -> pathlib.Path:
    if EXPORTED_ROOTFS_DIR.exists() and force_extract:
        log(f"Removing existing extracted rootfs: {EXPORTED_ROOTFS_DIR}")
        shutil.rmtree(EXPORTED_ROOTFS_DIR)

    if not EXPORTED_ROOTFS_DIR.exists():
        EXPORTED_ROOTFS_DIR.mkdir(parents=True, exist_ok=True)
        run_command(["tar", "-xf", str(tarball), "-C", str(EXPORTED_ROOTFS_DIR)])
    else:
        log(f"Using existing extracted rootfs: {EXPORTED_ROOTFS_DIR}")

    kernel_candidates = sorted(EXPORTED_ROOTFS_DIR.glob("boot/vmlinux-*"))
    if not kernel_candidates:
        raise SystemExit(f"No vmlinux-* found under {EXPORTED_ROOTFS_DIR / 'boot'}")

    return EXPORTED_ROOTFS_DIR


def detect_kernel_version(source_root: pathlib.Path) -> str:
    kernels = sorted(source_root.glob("boot/vmlinux-*"))
    if len(kernels) != 1:
        raise SystemExit(
            f"Expected exactly one boot/vmlinux-* inside {source_root}, found {len(kernels)}"
        )
    name = kernels[0].name
    if not name.startswith(VMLINUX_PREFIX):
        raise SystemExit(f"Unexpected kernel file name: {name}")
    return name[len(VMLINUX_PREFIX) :]


def read_first_load_paddr(kernel: pathlib.Path, cross_compile: str, env: dict[str, str]) -> str:
    objdump = pathlib.Path(f"{cross_compile}objdump")
    output = run_command([str(objdump), "-p", str(kernel)], env=env, stdout=subprocess.PIPE).stdout
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("LOAD"):
            continue
        match = re.search(r"paddr\s+0x([0-9a-fA-F]+)", stripped)
        if not match:
            break
        return match.group(1).lower()
    raise SystemExit(f"Could not determine first LOAD paddr from {kernel}")


def build_kernel_uimage(
    source_root: pathlib.Path,
    kernel_version: str,
    cross_compile: str,
    output_path: pathlib.Path,
) -> pathlib.Path:
    env = build_cross_env(cross_compile)
    objcopy = pathlib.Path(f"{cross_compile}objcopy")
    kernel = source_root / "boot" / f"vmlinux-{kernel_version}"
    if not kernel.exists():
        raise SystemExit(f"Kernel file not found: {kernel}")

    load_address = read_first_load_paddr(kernel, cross_compile, env)
    require_tool(MKIMAGE)

    with tempfile.TemporaryDirectory(prefix="bab750-kernel-") as tmpdir_name:
        tmpdir = pathlib.Path(tmpdir_name)
        stripped = tmpdir / "vmlinux-stripped"
        compressed = tmpdir / "vmlinux-stripped.gz"

        run_command([str(objcopy), "-S", str(kernel), str(stripped)], env=env)
        with compressed.open("wb") as compressed_handle:
            subprocess.run(["gzip", "-9", "-c", str(stripped)], check=True, stdout=compressed_handle)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                str(MKIMAGE),
                "-A",
                "ppc",
                "-O",
                "linux",
                "-T",
                "kernel",
                "-C",
                "none",
                "-a",
                load_address,
                "-e",
                load_address,
                "-n",
                f"Adele Linux {kernel_version}",
                "-d",
                str(stripped),
                str(output_path),
            ]
        )

    log(
        "Created kernel image: "
        f"{output_path} ({output_path.stat().st_size} bytes, uncompressed for old U-Boot bootm)"
    )
    return output_path


def normalize_relpath(value: pathlib.PurePosixPath | str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(posixpath.normpath(str(value)))


def resolve_symlink_relpath(rel_path: pathlib.PurePosixPath, link_target: str) -> pathlib.PurePosixPath:
    if link_target.startswith("/"):
        return normalize_relpath(link_target.lstrip("/"))
    return normalize_relpath(rel_path.parent / link_target)


def ensure_source_path(source_root: pathlib.Path, rel_path: pathlib.PurePosixPath) -> pathlib.Path:
    candidate = source_root / rel_path
    if not os.path.lexists(candidate):
        raise SystemExit(f"Missing source path in extracted rootfs: {candidate}")
    return candidate


def copy_relpath(
    source_root: pathlib.Path,
    rel_path: pathlib.PurePosixPath,
    dest_root: pathlib.Path,
    copied: set[pathlib.PurePosixPath],
) -> None:
    rel_path = normalize_relpath(rel_path)
    if rel_path in copied:
        return

    source = ensure_source_path(source_root, rel_path)
    destination = dest_root / rel_path
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.is_symlink():
        target = os.readlink(source)
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        os.symlink(target, destination)
        copied.add(rel_path)
        copy_relpath(source_root, resolve_symlink_relpath(rel_path, target), dest_root, copied)
        return

    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        copied.add(rel_path)
        return

    shutil.copy2(source, destination)
    copied.add(rel_path)


def resolve_real_source_path(
    source_root: pathlib.Path,
    rel_path: pathlib.PurePosixPath,
) -> tuple[pathlib.PurePosixPath, pathlib.Path]:
    rel_path = normalize_relpath(rel_path)
    source = ensure_source_path(source_root, rel_path)
    while source.is_symlink():
        rel_path = resolve_symlink_relpath(rel_path, os.readlink(source))
        source = ensure_source_path(source_root, rel_path)
    return rel_path, source


def is_elf_file(path: pathlib.Path) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(4) == b"\x7fELF"


def readelf_needed(path: pathlib.Path) -> tuple[set[str], str | None]:
    dynamic = run_command(["readelf", "-d", str(path)], stdout=subprocess.PIPE).stdout
    needed = {
        match.group(1)
        for match in re.finditer(r"Shared library: \[(.+?)\]", dynamic)
    }

    program_headers = run_command(["readelf", "-l", str(path)], stdout=subprocess.PIPE).stdout
    interpreter_match = re.search(r"Requesting program interpreter: (.+?)\]", program_headers)
    interpreter = interpreter_match.group(1) if interpreter_match else None
    return needed, interpreter


def find_library_provider(source_root: pathlib.Path, soname: str) -> pathlib.PurePosixPath:
    for prefix in LIBRARY_DIR_CANDIDATES:
        candidate = pathlib.PurePosixPath(prefix) / soname
        if os.path.lexists(source_root / candidate):
            return candidate
    raise SystemExit(f"Could not find runtime library {soname} in extracted rootfs")


def copy_binary_runtime(
    source_root: pathlib.Path,
    stage_root: pathlib.Path,
    rel_paths: tuple[str, ...],
) -> None:
    queue = [normalize_relpath(path) for path in rel_paths]
    copied: set[pathlib.PurePosixPath] = set()
    inspected: set[pathlib.PurePosixPath] = set()

    while queue:
        rel_path = queue.pop()
        copy_relpath(source_root, rel_path, stage_root, copied)
        real_relpath, real_path = resolve_real_source_path(source_root, rel_path)
        if real_relpath in inspected or not is_elf_file(real_path):
            continue

        inspected.add(real_relpath)
        needed, interpreter = readelf_needed(real_path)

        if interpreter:
            queue.append(normalize_relpath(interpreter.lstrip("/")))

        for soname in sorted(needed):
            queue.append(find_library_provider(source_root, soname))


def create_runtime_layout(stage_root: pathlib.Path) -> None:
    for relative in ("bin", "sbin", "dev", "proc", "sys", "run", "newroot", "etc", "tmp", "root"):
        (stage_root / relative).mkdir(parents=True, exist_ok=True)

    sh_path = stage_root / "bin" / "sh"
    if sh_path.exists() or sh_path.is_symlink():
        sh_path.unlink()
    os.symlink("dash", sh_path)

    for link_name in ("insmod", "modprobe"):
        link_path = stage_root / "sbin" / link_name
        if link_path.exists() or link_path.is_symlink():
            link_path.unlink()
        os.symlink("../bin/kmod", link_path)

    mtab_path = stage_root / "etc" / "mtab"
    if mtab_path.exists() or mtab_path.is_symlink():
        mtab_path.unlink()
    os.symlink("/proc/self/mounts", mtab_path)


def copy_kernel_modules(source_root: pathlib.Path, stage_root: pathlib.Path, kernel_version: str) -> None:
    copied: set[pathlib.PurePosixPath] = set()
    for relative in RUNTIME_MODULES:
        rel_path = pathlib.PurePosixPath("lib/modules") / kernel_version / relative
        copy_relpath(source_root, rel_path, stage_root, copied)


def render_init_script(args: argparse.Namespace, kernel_version: str) -> str:
    gateway = args.gateway or ""
    interface = args.interface or ""
    script = f"""\
#!/bin/sh
PATH=/bin:/sbin
export PATH

log() {{
    echo "[initramfs] $*"
}}

panic() {{
    echo "[initramfs] ERROR: $*"
    exec /bin/sh
}}

mount -t proc proc /proc || panic "failed to mount /proc"
mount -t sysfs sysfs /sys || panic "failed to mount /sys"
mount -t devtmpfs devtmpfs /dev 2>/dev/null || mount -t tmpfs tmpfs /dev || panic "failed to mount /dev"

if [ -c /dev/console ]; then
    exec </dev/console >/dev/console 2>&1
fi

SERVERIP="{args.server_ip}"
IPADDR="{args.client_ip}"
NETMASK="{args.netmask}"
GATEWAY="{gateway}"
IFACE="{interface}"
NFSROOT="{args.nfsroot}"
KERNEL_VERSION="{kernel_version}"

read -r cmdline </proc/cmdline || cmdline=""
for arg in $cmdline; do
    case "$arg" in
        bab750_serverip=*)
            SERVERIP=${{arg#bab750_serverip=}}
            ;;
        bab750_ip=*)
            IPADDR=${{arg#bab750_ip=}}
            ;;
        bab750_netmask=*)
            NETMASK=${{arg#bab750_netmask=}}
            ;;
        bab750_gateway=*)
            GATEWAY=${{arg#bab750_gateway=}}
            ;;
        bab750_iface=*)
            IFACE=${{arg#bab750_iface=}}
            ;;
        bab750_nfsroot=*)
            NFSROOT=${{arg#bab750_nfsroot=}}
            ;;
    esac
done

load_module() {{
    module_path="$1"
    if [ ! -f "$module_path" ]; then
        panic "missing module $module_path"
    fi
    log "loading $module_path"
    /sbin/insmod "$module_path" || panic "failed to load $module_path"
}}

load_module "/lib/modules/$KERNEL_VERSION/kernel/drivers/net/mii.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/drivers/net/ethernet/dec/tulip/tulip.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/net/sunrpc/sunrpc.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/fs/nfs_common/grace.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/fs/lockd/lockd.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/fs/nfs/nfs.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/net/dns_resolver/dns_resolver.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/fs/nfs/nfsv4.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/fs/nfs_common/nfs_acl.ko.xz"
load_module "/lib/modules/$KERNEL_VERSION/kernel/fs/nfs/nfsv3.ko.xz"

ifconfig lo 127.0.0.1 up || true

if [ -z "$IFACE" ]; then
    tries=0
    while [ "$tries" -lt 20 ]; do
        for path in /sys/class/net/*; do
            name=${{path##*/}}
            [ "$name" = "lo" ] && continue
            IFACE="$name"
            break 2
        done
        sleep 1
        tries=$((tries + 1))
    done
fi

[ -n "$IFACE" ] || panic "no network interface detected after tulip init"

log "configuring $IFACE as $IPADDR/$NETMASK"
ifconfig "$IFACE" "$IPADDR" netmask "$NETMASK" up || panic "failed to configure $IFACE"
if [ -n "$GATEWAY" ] && [ "$GATEWAY" != "0.0.0.0" ]; then
    route add default gw "$GATEWAY" dev "$IFACE" 2>/dev/null || route add default gw "$GATEWAY" || true
fi

log "mounting NFSv4 root $SERVERIP:$NFSROOT"
mount -i -t nfs4 -o vers=4,proto=tcp,rw "$SERVERIP:$NFSROOT" /newroot || panic "failed to mount NFS root $SERVERIP:$NFSROOT"

log "switching root to $SERVERIP:$NFSROOT"
exec /sbin/switch_root /newroot /sbin/init
panic "switch_root returned unexpectedly"
"""
    return textwrap.dedent(script)


def pack_initramfs(stage_root: pathlib.Path, raw_initramfs: pathlib.Path) -> None:
    require_tool("cpio")
    require_tool("gzip")
    shell_script = (
        f"find . -print0 | cpio --null -o --format=newc 2>/dev/null | "
        f"gzip -9 > {shlex.quote(str(raw_initramfs))}"
    )
    run_command(["bash", "-lc", shell_script], cwd=stage_root)


def build_initramfs(
    source_root: pathlib.Path,
    kernel_version: str,
    output_path: pathlib.Path,
    args: argparse.Namespace,
) -> pathlib.Path:
    require_tool(MKIMAGE)
    with tempfile.TemporaryDirectory(prefix="bab750-initramfs-") as tmpdir_name:
        tmpdir = pathlib.Path(tmpdir_name)
        stage_root = tmpdir / "stage"
        stage_root.mkdir()

        create_runtime_layout(stage_root)
        copy_binary_runtime(source_root, stage_root, RUNTIME_BINARIES)
        copy_kernel_modules(source_root, stage_root, kernel_version)

        init_path = stage_root / "init"
        init_path.write_text(render_init_script(args, kernel_version), encoding="utf-8")
        init_path.chmod(0o755)

        raw_initramfs = tmpdir / "initramfs.cpio.gz"
        pack_initramfs(stage_root, raw_initramfs)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                str(MKIMAGE),
                "-A",
                "ppc",
                "-O",
                "linux",
                "-T",
                "ramdisk",
                "-C",
                "gzip",
                "-n",
                "BAB-750 Adelie initramfs",
                "-d",
                str(raw_initramfs),
                str(output_path),
            ]
        )

    log(f"Created initramfs image: {output_path} ({output_path.stat().st_size} bytes)")
    return output_path


def write_uboot_commands(
    output_path: pathlib.Path,
    args: argparse.Namespace,
    kernel_image: pathlib.Path,
    initrd_image: pathlib.Path,
) -> pathlib.Path:
    commands = [
        f"setenv serverip {args.server_ip}",
        f"setenv ipaddr {args.client_ip}",
        f"setenv netmask {args.netmask}",
    ]
    if args.gateway:
        commands.append(f"setenv gatewayip {args.gateway}")

    bootargs = [
        "console=ttyS0,9600",
        f"bab750_serverip={args.server_ip}",
        f"bab750_ip={args.client_ip}",
        f"bab750_netmask={args.netmask}",
        f"bab750_nfsroot={args.nfsroot}",
    ]
    if args.gateway:
        bootargs.append(f"bab750_gateway={args.gateway}")
    if args.interface:
        bootargs.append(f"bab750_iface={args.interface}")

    commands.extend(
        [
            f"setenv bootargs '{' '.join(bootargs)}'",
            f"tftpboot {args.kernel_load_address} {kernel_image.name}",
            f"tftpboot {args.initrd_load_address} {initrd_image.name}",
            f"bootm {args.kernel_load_address} {args.initrd_load_address}",
        ]
    )

    output_path.write_text("\n".join(commands) + "\n", encoding="utf-8")
    log(f"Wrote U-Boot command file: {output_path}")
    return output_path


def maybe_start_compose(should_start: bool) -> None:
    if not should_start:
        return
    run_command(["docker", "compose", "up", "-d"], cwd=TFTP_DIR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rootfs-full-tarball",
        help="Path to adelie-rootfs-full-ppc-*.txz. Auto-detected from ~/Downloads by default.",
    )
    parser.add_argument(
        "--cross-compile",
        help="Cross-tool prefix ending in powerpc-linux-gnu-",
    )
    parser.add_argument(
        "--force-extract",
        action="store_true",
        help="Delete and re-extract bab750-tftp/nfsroot/adelie-full before building assets.",
    )
    parser.add_argument(
        "--kernel-image-name",
        default=DEFAULT_KERNEL_IMAGE,
        help=f"TFTP kernel image name (default: {DEFAULT_KERNEL_IMAGE})",
    )
    parser.add_argument(
        "--initrd-image-name",
        default=DEFAULT_INITRD_IMAGE,
        help=f"TFTP initramfs image name (default: {DEFAULT_INITRD_IMAGE})",
    )
    parser.add_argument(
        "--server-ip",
        default=DEFAULT_SERVER_IP,
        help=f"Host IP used by U-Boot and the initramfs (default: {DEFAULT_SERVER_IP})",
    )
    parser.add_argument(
        "--client-ip",
        default=DEFAULT_CLIENT_IP,
        help=f"Static IP configured inside initramfs (default: {DEFAULT_CLIENT_IP})",
    )
    parser.add_argument(
        "--netmask",
        default=DEFAULT_NETMASK,
        help=f"Static netmask configured inside initramfs (default: {DEFAULT_NETMASK})",
    )
    parser.add_argument(
        "--gateway",
        default=DEFAULT_GATEWAY,
        help="Optional gateway configured inside initramfs.",
    )
    parser.add_argument(
        "--interface",
        default=DEFAULT_INTERFACE,
        help="Optional Linux interface name. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--nfsroot",
        default=DEFAULT_NFSROOT,
        help=f"NFSv4 root path mounted by initramfs (default: {DEFAULT_NFSROOT})",
    )
    parser.add_argument(
        "--kernel-load-address",
        default=DEFAULT_KERNEL_LOAD_ADDRESS,
        help=f"U-Boot TFTP load address for the kernel (default: {DEFAULT_KERNEL_LOAD_ADDRESS})",
    )
    parser.add_argument(
        "--initrd-load-address",
        default=DEFAULT_INITRD_LOAD_ADDRESS,
        help=f"U-Boot TFTP load address for the initrd (default: {DEFAULT_INITRD_LOAD_ADDRESS})",
    )
    parser.add_argument(
        "--commands-output",
        default=str(DEFAULT_UBOOT_COMMANDS),
        help=f"Where to write the ready-to-paste U-Boot commands (default: {DEFAULT_UBOOT_COMMANDS})",
    )
    parser.add_argument(
        "--up",
        action="store_true",
        help="Run docker compose up -d in bab750-tftp after the images are generated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_tool("tar")
    require_tool("readelf")
    require_tool("docker")

    cross_compile = resolve_cross_compile(args.cross_compile)
    tarball = discover_rootfs_tarball(args.rootfs_full_tarball)
    log(f"Using rootfs tarball: {tarball}")
    log(f"Using cross-tool prefix: {cross_compile}")

    source_root = prepare_exported_rootfs(tarball, args.force_extract)
    kernel_version = detect_kernel_version(source_root)
    log(f"Detected Adelie kernel version: {kernel_version}")

    kernel_image = build_kernel_uimage(
        source_root,
        kernel_version,
        cross_compile,
        TFTPBOOT_DIR / args.kernel_image_name,
    )
    initrd_image = build_initramfs(
        source_root,
        kernel_version,
        TFTPBOOT_DIR / args.initrd_image_name,
        args,
    )

    commands_output = pathlib.Path(args.commands_output).expanduser().resolve()
    commands_output.parent.mkdir(parents=True, exist_ok=True)
    write_uboot_commands(commands_output, args, kernel_image, initrd_image)

    maybe_start_compose(args.up)

    log("")
    log("Netboot assets are ready.")
    log(f"Kernel image:  {kernel_image}")
    log(f"Initrd image:  {initrd_image}")
    log(f"NFS rootfs:    {source_root}")
    log(f"U-Boot cmds:   {commands_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
