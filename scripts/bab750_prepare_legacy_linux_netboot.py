#!/usr/bin/env python3

"""Prepare a legacy U-Boot TFTP/NFS Linux netboot for the ELTEC BAB-750."""

from __future__ import annotations

import argparse
import gzip
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import textwrap


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
TFTP_DIR = ROOT_DIR / "bab750-tftp"
TFTPBOOT_DIR = TFTP_DIR / "tftpboot"
NFSROOT_DIR = TFTP_DIR / "nfsroot"
EXPORTED_ROOTFS_DIR = NFSROOT_DIR / "adelie-full"
KERNEL_FRAGMENT = ROOT_DIR / "scripts" / "bab750_linux_kernel.fragment"
MKIMAGE = ROOT_DIR / "u-boot-lab" / "tools" / "mkimage"
DEFAULT_LINUX_SRC = pathlib.Path("/tmp/linux-6.6.58")
DEFAULT_LINUX_BUILD_DIR = pathlib.Path("/tmp/linux-bab750-build")
DEFAULT_KERNEL_IMAGE = "adelie-bab750-linux.cuImage"
DEFAULT_KERNEL_LOAD_ADDRESS = "2000000"
DEFAULT_SERVER_IP = "192.168.1.101"
DEFAULT_CLIENT_IP = "192.168.1.123"
DEFAULT_NETMASK = "255.255.255.0"
DEFAULT_GATEWAY = ""
DEFAULT_INTERFACE = "eth0"
DEFAULT_NFSROOT = "/adelie-full"
DEFAULT_UBOOT_COMMANDS = TFTP_DIR / "uboot-netboot.txt"


def log(message: str) -> None:
    print(f"[legacy-netboot] {message}", flush=True)


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


def parse_mkimage_header(image_path: pathlib.Path) -> tuple[str, str, str]:
    result = run_command(
        [str(MKIMAGE), "-l", str(image_path)],
        stdout=subprocess.PIPE,
    )
    output = result.stdout or ""
    name_match = re.search(r"^Image Name:\s+(.*)$", output, re.MULTILINE)
    load_match = re.search(r"^Load Address:\s+([0-9A-Fa-f]+)$", output, re.MULTILINE)
    entry_match = re.search(r"^Entry Point:\s+([0-9A-Fa-f]+)$", output, re.MULTILINE)
    if not name_match or not load_match or not entry_match:
        raise SystemExit(
            "Could not parse mkimage header metadata from:\n"
            f"  {image_path}\n"
            f"mkimage output was:\n{output}"
        )
    return (
        name_match.group(1).strip(),
        load_match.group(1).strip(),
        entry_match.group(1).strip(),
    )


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

    if not (EXPORTED_ROOTFS_DIR / "sbin" / "init").exists():
        raise SystemExit(f"Extracted rootfs does not look bootable: {EXPORTED_ROOTFS_DIR}")
    return EXPORTED_ROOTFS_DIR


def resolve_linux_source(explicit: str | None) -> pathlib.Path:
    linux_src = pathlib.Path(explicit).expanduser().resolve() if explicit else DEFAULT_LINUX_SRC
    if not (linux_src / "Makefile").exists():
        raise SystemExit(
            "Linux source tree not found.\n"
            f"Expected a kernel source checkout at: {linux_src}\n"
            "Pass --linux-src explicitly if it lives elsewhere."
        )
    return linux_src


def ensure_bab750_wrapper_link_address(linux_src: pathlib.Path) -> None:
    wrapper_path = linux_src / "arch" / "powerpc" / "boot" / "wrapper"
    if not wrapper_path.exists():
        raise SystemExit(f"Linux boot wrapper not found: {wrapper_path}")

    original = wrapper_path.read_text(encoding="utf-8")
    bab750_case = "    *-bab750)\n        link_address='0x1000000'\n        ;;\n"
    if bab750_case in original:
        return

    amigaone_case = "    *-amigaone)\n        link_address='0x800000'\n        ;;\n"
    if amigaone_case not in original:
        raise SystemExit(
            "Could not patch the PowerPC boot wrapper for BAB750.\n"
            f"Expected to find the AmigaOne link-address case in: {wrapper_path}"
        )

    patched = original.replace(amigaone_case, bab750_case + amigaone_case, 1)
    wrapper_path.write_text(patched, encoding="utf-8")
    log(f"Patched BAB750 wrapper link address in: {wrapper_path}")


def build_cross_tool_wrappers(cross_compile: str, build_dir: pathlib.Path) -> str:
    tool_prefix = pathlib.Path(cross_compile)
    tool_root = tool_prefix.parent.parent
    if not tool_root.exists():
        raise SystemExit(f"Toolchain root not found: {tool_root}")

    wrapper_dir = build_dir / ".toolwrap"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    toolwrap = wrapper_dir / "toolwrap.sh"
    toolwrap.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            set -eu
            TOOLROOT={shlex.quote(str(tool_root))}
            WRAPDIR={shlex.quote(str(wrapper_dir))}
            export LD_LIBRARY_PATH="$TOOLROOT/lib:$TOOLROOT/x86_64-pc-linux-gnu/powerpc-linux-gnu/lib${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"
            base=$(basename "$0")
            case "$base" in
              ld)
                exec "$TOOLROOT/bin/powerpc-linux-gnu-ld" "$@"
                ;;
              as|ar|nm|objcopy|objdump|ranlib|readelf|strip)
                exec "$TOOLROOT/bin/$base" "$@"
                ;;
              powerpc-linux-gnu-gcc|powerpc-linux-gnu-gcc-9.1.0|powerpc-linux-gnu-gcc-ar|powerpc-linux-gnu-gcc-nm|powerpc-linux-gnu-gcc-ranlib)
                export COMPILER_PATH="$WRAPDIR:$TOOLROOT/bin${{COMPILER_PATH:+:$COMPILER_PATH}}"
                exec "$TOOLROOT/bin/$base" "$@"
                ;;
              powerpc-linux-gnu-*)
                exec "$TOOLROOT/bin/$base" "$@"
                ;;
              *)
                echo "unknown wrapper target: $base" >&2
                exit 1
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    toolwrap.chmod(0o755)

    for name in ("ld", "as", "ar", "nm", "objcopy", "objdump", "ranlib", "readelf", "strip"):
        link = wrapper_dir / name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to("toolwrap.sh")

    for tool in sorted((tool_root / "bin").glob("powerpc-linux-gnu-*")):
        link = wrapper_dir / tool.name
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to("toolwrap.sh")

    return str(wrapper_dir / "powerpc-linux-gnu-")


def configure_legacy_kernel(
    linux_src: pathlib.Path,
    build_dir: pathlib.Path,
    cross_compile: str,
    reconfigure: bool,
) -> None:
    if reconfigure and build_dir.exists():
        log(f"Removing existing kernel build directory: {build_dir}")
        shutil.rmtree(build_dir)

    build_dir.mkdir(parents=True, exist_ok=True)
    config_path = build_dir / ".config"

    if not config_path.exists():
        run_command(
            [
                "make",
                "-C",
                str(linux_src),
                f"O={build_dir}",
                "ARCH=powerpc",
                f"CROSS_COMPILE={cross_compile}",
                "amigaone_defconfig",
            ]
        )

    run_command(
        [
            str(linux_src / "scripts" / "kconfig" / "merge_config.sh"),
            "-m",
            "-O",
            str(build_dir),
            str(config_path),
            str(KERNEL_FRAGMENT),
        ]
    )
    run_command(
        [
            "make",
            "-C",
            str(linux_src),
            f"O={build_dir}",
            "ARCH=powerpc",
            f"CROSS_COMPILE={cross_compile}",
            "olddefconfig",
        ]
    )


def build_legacy_cuimage(
    linux_src: pathlib.Path,
    build_dir: pathlib.Path,
    cross_compile: str,
    output_path: pathlib.Path,
) -> pathlib.Path:
    require_tool(MKIMAGE)
    env = os.environ.copy()
    env["PATH"] = f"{MKIMAGE.parent}:{env.get('PATH', '/usr/bin:/bin')}"

    run_command(
        [
            "make",
            "-C",
            str(linux_src),
            f"O={build_dir}",
            "ARCH=powerpc",
            f"CROSS_COMPILE={cross_compile}",
            f"-j{os.cpu_count() or 1}",
            "cuImage.bab750",
        ],
        env=env,
    )

    built_image = build_dir / "arch" / "powerpc" / "boot" / "cuImage.bab750"
    raw_wrapper_gzip = build_dir / "arch" / "powerpc" / "boot" / "cuImage.bab750.gz"
    raw_wrapper = build_dir / "arch" / "powerpc" / "boot" / "cuImage.bab750.raw"
    if not built_image.exists():
        raise SystemExit(f"Kernel image not produced: {built_image}")
    if not raw_wrapper_gzip.exists():
        raise SystemExit(
            "The gzipped BAB750 bootwrapper payload was not produced.\n"
            f"Expected: {raw_wrapper_gzip}"
        )

    image_name, load_address, entry_point = parse_mkimage_header(built_image)
    with gzip.open(raw_wrapper_gzip, "rb") as source, raw_wrapper.open("wb") as destination:
        shutil.copyfileobj(source, destination)
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
            entry_point,
            "-n",
            image_name,
            "-d",
            str(raw_wrapper),
            str(output_path),
        ]
    )
    log(
        "Installed kernel image without the outer U-Boot gzip layer: "
        f"{output_path} ({output_path.stat().st_size} bytes)"
    )
    return output_path


def build_bootargs(args: argparse.Namespace) -> str:
    gateway = args.gateway
    device = args.interface or DEFAULT_INTERFACE
    ip_config = (
        f"{args.client_ip}:{args.server_ip}:{gateway}:{args.netmask}::{device}:off"
    )
    return (
        "console=ttyS0,9600 "
        "root=/dev/nfs "
        f"nfsroot={args.server_ip}:{args.nfsroot},vers=4 "
        f"ip={ip_config}"
    )


def write_uboot_commands(
    output_path: pathlib.Path,
    args: argparse.Namespace,
    kernel_image: pathlib.Path,
) -> pathlib.Path:
    cleanup_vars = [
        "bootcmd",
        "bootfile",
        "fileaddr",
        "filesize",
        "gatewayip",
        "hostname",
        "rootpath",
        "bootargs",
        "netmask",
        "ipaddr",
        "serverip",
    ]

    commands = [f"setenv {name}" for name in cleanup_vars]
    commands.extend([
        f"setenv serverip {args.server_ip}",
        f"setenv ipaddr {args.client_ip}",
        f"setenv netmask {args.netmask}",
    ])
    if args.gateway:
        commands.append(f"setenv gatewayip {args.gateway}")

    commands.extend(
        [
            f"setenv bootargs '{build_bootargs(args)}'",
            f"tftpboot {args.kernel_load_address} {kernel_image.name}",
            f"bootm {args.kernel_load_address}",
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        "--linux-src",
        default=str(DEFAULT_LINUX_SRC),
        help=f"Linux kernel source tree used to build cuImage.bab750 (default: {DEFAULT_LINUX_SRC})",
    )
    parser.add_argument(
        "--linux-build-dir",
        default=str(DEFAULT_LINUX_BUILD_DIR),
        help=f"Out-of-tree kernel build directory (default: {DEFAULT_LINUX_BUILD_DIR})",
    )
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
        help="Delete and re-extract bab750-tftp/nfsroot/adelie-full before preparing NFS root.",
    )
    parser.add_argument(
        "--reconfigure-kernel",
        action="store_true",
        help="Delete the kernel build directory before configuring the legacy BAB-750 kernel.",
    )
    parser.add_argument(
        "--kernel-image-name",
        default=DEFAULT_KERNEL_IMAGE,
        help=f"TFTP kernel image name (default: {DEFAULT_KERNEL_IMAGE})",
    )
    parser.add_argument(
        "--server-ip",
        default=DEFAULT_SERVER_IP,
        help=f"Host IP used by U-Boot and the NFS root (default: {DEFAULT_SERVER_IP})",
    )
    parser.add_argument(
        "--client-ip",
        default=DEFAULT_CLIENT_IP,
        help=f"Static IP configured by the kernel (default: {DEFAULT_CLIENT_IP})",
    )
    parser.add_argument(
        "--netmask",
        default=DEFAULT_NETMASK,
        help=f"Static netmask configured by the kernel (default: {DEFAULT_NETMASK})",
    )
    parser.add_argument(
        "--gateway",
        default=DEFAULT_GATEWAY,
        help="Optional gateway configured by the kernel.",
    )
    parser.add_argument(
        "--interface",
        default=DEFAULT_INTERFACE,
        help=f"Kernel network interface name used in ip= (default: {DEFAULT_INTERFACE})",
    )
    parser.add_argument(
        "--nfsroot",
        default=DEFAULT_NFSROOT,
        help=f"NFSv4 export path mounted as root (default: {DEFAULT_NFSROOT})",
    )
    parser.add_argument(
        "--kernel-load-address",
        default=DEFAULT_KERNEL_LOAD_ADDRESS,
        help=(
            "temporary U-Boot RAM address used by tftpboot/bootm for the cuImage "
            f"container (default: {DEFAULT_KERNEL_LOAD_ADDRESS})"
        ),
    )
    parser.add_argument(
        "--commands-output",
        default=str(DEFAULT_UBOOT_COMMANDS),
        help=f"Where to write the ready-to-paste U-Boot commands (default: {DEFAULT_UBOOT_COMMANDS})",
    )
    parser.add_argument(
        "--up",
        action="store_true",
        help="Run docker compose up -d in bab750-tftp after the image is generated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_tool("tar")
    require_tool("docker")
    require_tool("make")
    require_tool("readelf")

    cross_compile = resolve_cross_compile(args.cross_compile)
    linux_src = resolve_linux_source(args.linux_src)
    build_dir = pathlib.Path(args.linux_build_dir).expanduser().resolve()
    tarball = discover_rootfs_tarball(args.rootfs_full_tarball)

    log(f"Using Linux source tree: {linux_src}")
    log(f"Using kernel build dir:  {build_dir}")
    log(f"Using rootfs tarball:    {tarball}")
    log(f"Using cross-tool prefix: {cross_compile}")

    source_root = prepare_exported_rootfs(tarball, args.force_extract)
    ensure_bab750_wrapper_link_address(linux_src)
    wrapped_cross_compile = build_cross_tool_wrappers(cross_compile, build_dir)
    configure_legacy_kernel(linux_src, build_dir, wrapped_cross_compile, args.reconfigure_kernel)
    kernel_image = build_legacy_cuimage(
        linux_src,
        build_dir,
        wrapped_cross_compile,
        TFTPBOOT_DIR / args.kernel_image_name,
    )

    commands_output = pathlib.Path(args.commands_output).expanduser().resolve()
    write_uboot_commands(commands_output, args, kernel_image)
    maybe_start_compose(args.up)

    log("")
    log("Legacy BAB-750 netboot assets are ready.")
    log(f"Kernel image: {kernel_image}")
    log(f"NFS rootfs:   {source_root}")
    log(f"U-Boot cmds:  {commands_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
