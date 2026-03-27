#!/usr/bin/env python3

"""Build the working vendor Linux 2.4 PReP netboot image for the ELTEC BAB-750."""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import textwrap


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_KERNEL_DIR = ROOT_DIR / "vendor-src" / "linux-2.4.18-eltec-1.0.19-2"
DEFAULT_TOOL_PREFIX = ROOT_DIR / "vendor-src" / "toolwrap" / "ppc_60x"
DEFAULT_MKIMAGE = ROOT_DIR / "u-boot-lab" / "tools" / "mkimage"
DEFAULT_TFTP_DIR = ROOT_DIR / "bab750-tftp" / "tftpboot"
DEFAULT_BUILD_DIR = ROOT_DIR / "bab750-tftp" / "vendor-2.4-build"
DEFAULT_COMMANDS = ROOT_DIR / "bab750-tftp" / "uboot-netboot-vendor-2.4-prep.txt"
DEFAULT_IMAGE_NAME = "750nfs-prep.uImage"
DEFAULT_LOADADDR = "1800000"

COMMANDS_TEMPLATE = textwrap.dedent(
    """\
    # Vendor Linux 2.4 PReP zImage.initrd wrapper for the ELTEC BAB-750
    setenv bootargs root=ramfs console=ttyS0,9600
    setenv serverip {serverip}
    setenv ipaddr {ipaddr}
    setenv netmask {netmask}
    tftpboot {loadaddr} {image_name}
    bootm {loadaddr}
    """
)


def run(cmd: list[str], *, cwd: pathlib.Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def write_text(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def stage_minimal_rootfs(rootfs_dir: pathlib.Path) -> None:
    shutil.rmtree(rootfs_dir, ignore_errors=True)
    for subdir in ("bin", "dev", "etc", "mnt", "proc", "root", "sbin", "tmp"):
        (rootfs_dir / subdir).mkdir(parents=True, exist_ok=True)


def make_rootfs_tarball(rootfs_dir: pathlib.Path, tarball: pathlib.Path) -> None:
    tarball.parent.mkdir(parents=True, exist_ok=True)
    script = textwrap.dedent(
        """\
        set -e
        cd "$1"
        mkdir -p dev
        mknod -m 600 dev/console c 5 1
        mknod -m 666 dev/null c 1 3
        mknod -m 666 dev/ttyS0 c 4 64
        mknod -m 666 dev/tty0 c 4 0
        tar --format=ustar --sort=name --mtime='@0' \
            --numeric-owner --owner=0 --group=0 \
            -czf "$2" .
        """
    )
    run(["fakeroot", "--", "bash", "-lc", script, "_", str(rootfs_dir), str(tarball)])


def build_vendor_prep_wrapper(
    kernel_dir: pathlib.Path,
    build_dir: pathlib.Path,
    tool_prefix: pathlib.Path,
    mkimage: pathlib.Path,
    tftp_dir: pathlib.Path,
    image_name: str,
    *,
    elinos_prefix: str,
    elinos_project: str,
) -> pathlib.Path:
    tarball = build_dir / "750nfs-rootfs.tgz"
    ramdisk_image = kernel_dir / "arch" / "ppc" / "boot" / "images" / "ramdisk.image.gz"
    prep_elf = kernel_dir / "arch" / "ppc" / "boot" / "images" / "zImage.initrd.elf"
    segment_path = tftp_dir / "750nfs-prep-seg1.bin"
    image_path = tftp_dir / image_name

    shutil.copy2(tarball, ramdisk_image)

    env = dict(os.environ)
    env.update(
        {
            "ELINOS_PREFIX": elinos_prefix,
            "ELINOS_PROJECT": elinos_project,
            "ELINOS_BIN_PREFIX": str(tool_prefix),
            "LINUX_ARCH": "ppc",
        }
    )
    run(["make", "-C", str(kernel_dir), "-j4", "zImage.initrd"], env=env)

    readelf = tool_prefix.with_name(tool_prefix.name + "-readelf")
    if not readelf.exists():
        raise SystemExit(f"readelf wrapper not found: {readelf}")

    headers = subprocess.check_output([str(readelf), "-l", str(prep_elf)], text=True)
    match = re.search(
        r"LOAD\s+0x([0-9a-fA-F]+)\s+0x00800000\s+0x00800000\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)",
        headers,
    )
    if not match:
        raise SystemExit(f"Could not locate the PReP LOAD segment in {prep_elf}")

    offset = int(match.group(1), 16)
    filesz = int(match.group(2), 16)
    memsz = int(match.group(3), 16)

    with prep_elf.open("rb") as src, segment_path.open("wb") as dst:
        src.seek(offset)
        dst.write(src.read(filesz))
    with segment_path.open("ab") as dst:
        dst.truncate(memsz)

    run(
        [
            str(mkimage),
            "-A",
            "ppc",
            "-O",
            "linux",
            "-T",
            "kernel",
            "-C",
            "none",
            "-a",
            "0x00800000",
            "-e",
            "0x00800000",
            "-n",
            "BAB750 Linux 2.4 prep wrapper",
            "-d",
            str(segment_path),
            str(image_path),
        ]
    )
    return image_path


def write_commands(path: pathlib.Path, *, serverip: str, ipaddr: str, netmask: str, loadaddr: str, image_name: str) -> None:
    write_text(
        path,
        COMMANDS_TEMPLATE.format(
            serverip=serverip,
            ipaddr=ipaddr,
            netmask=netmask,
            loadaddr=loadaddr,
            image_name=image_name,
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-dir", default=str(DEFAULT_KERNEL_DIR), help=f"vendor kernel tree (default: {DEFAULT_KERNEL_DIR})")
    parser.add_argument("--tool-prefix", default=str(DEFAULT_TOOL_PREFIX), help=f"PowerPC tool wrapper prefix without the trailing tool name (default: {DEFAULT_TOOL_PREFIX})")
    parser.add_argument("--mkimage", default=str(DEFAULT_MKIMAGE), help=f"path to mkimage (default: {DEFAULT_MKIMAGE})")
    parser.add_argument("--tftp-dir", default=str(DEFAULT_TFTP_DIR), help=f"TFTP output directory (default: {DEFAULT_TFTP_DIR})")
    parser.add_argument("--build-dir", default=str(DEFAULT_BUILD_DIR), help=f"working build directory (default: {DEFAULT_BUILD_DIR})")
    parser.add_argument("--commands-file", default=str(DEFAULT_COMMANDS), help=f"output path for the U-Boot commands file (default: {DEFAULT_COMMANDS})")
    parser.add_argument("--image-name", default=DEFAULT_IMAGE_NAME, help=f"name of the generated TFTP image (default: {DEFAULT_IMAGE_NAME})")
    parser.add_argument("--serverip", default="192.168.1.101", help="server IP to write into the U-Boot commands file")
    parser.add_argument("--ipaddr", default="192.168.1.123", help="board IP to write into the U-Boot commands file")
    parser.add_argument("--netmask", default="255.255.255.0", help="netmask to write into the U-Boot commands file")
    parser.add_argument("--loadaddr", default=DEFAULT_LOADADDR, help=f"U-Boot TFTP load address (default: {DEFAULT_LOADADDR})")
    parser.add_argument("--elinos-prefix", default="/opt/elinos", help="ELinOS installation prefix for the vendor kernel build")
    parser.add_argument(
        "--elinos-project",
        default=str(DEFAULT_BUILD_DIR / "elinos-project"),
        help="ELINOS_PROJECT path to export during the vendor kernel build",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    kernel_dir = pathlib.Path(args.kernel_dir).expanduser().resolve()
    tool_prefix = pathlib.Path(args.tool_prefix).expanduser().resolve()
    mkimage = pathlib.Path(args.mkimage).expanduser().resolve()
    tftp_dir = pathlib.Path(args.tftp_dir).expanduser().resolve()
    build_dir = pathlib.Path(args.build_dir).expanduser().resolve()
    commands_file = pathlib.Path(args.commands_file).expanduser().resolve()

    if not kernel_dir.exists():
        raise SystemExit(f"Kernel tree not found: {kernel_dir}")
    if not mkimage.exists():
        raise SystemExit(f"mkimage not found: {mkimage}")

    tftp_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    rootfs_dir = build_dir / "minroot"
    tarball = build_dir / "750nfs-rootfs.tgz"

    stage_minimal_rootfs(rootfs_dir)
    make_rootfs_tarball(rootfs_dir, tarball)
    image_path = build_vendor_prep_wrapper(
        kernel_dir,
        build_dir,
        tool_prefix,
        mkimage,
        tftp_dir,
        args.image_name,
        elinos_prefix=args.elinos_prefix,
        elinos_project=args.elinos_project,
    )
    write_commands(
        commands_file,
        serverip=args.serverip,
        ipaddr=args.ipaddr,
        netmask=args.netmask,
        loadaddr=args.loadaddr,
        image_name=args.image_name,
    )

    print()
    print(f"Prepared vendor Linux 2.4 image: {image_path}")
    print(f"Prepared commands file:        {commands_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
