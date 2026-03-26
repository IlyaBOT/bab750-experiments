#!/usr/bin/env python3

"""Prepare a vendor-style Linux 2.4 multi-image for the ELTEC BAB-750."""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import textwrap
import urllib.request


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_KERNEL = pathlib.Path("/tmp/linux-2.4.18-eltec-1.0.19-2/vmlinux")
DEFAULT_OBJCOPY = pathlib.Path("/tmp/linux-bab750-build/.toolwrap/powerpc-linux-gnu-objcopy")
DEFAULT_MKIMAGE = ROOT_DIR / "u-boot-lab" / "tools" / "mkimage"
DEFAULT_TFTP_DIR = ROOT_DIR / "bab750-tftp" / "tftpboot"
DEFAULT_BUILD_DIR = ROOT_DIR / "bab750-tftp" / "vendor-2.4-build"
DEFAULT_COMMANDS = ROOT_DIR / "bab750-tftp" / "uboot-netboot-vendor-2.4.txt"
BUSYBOX_URL = (
    "https://archive.debian.org/debian/pool/main/b/busybox/"
    "busybox-static_0.60.5-2.2_powerpc.deb"
)

ROOTFS_LINUXRC = textwrap.dedent(
    """\
    #!/bin/sh
    PATH=/bin:/sbin
    mount -t proc proc /proc
    mount -t devfs devfs /dev 2>/dev/null
    clear 2>/dev/null

    echo "BAB750 Linux 2.4 vendor ramfs"
    echo
    uname -a
    if [ -r /proc/cpuinfo ]; then
      echo
      echo "--- /proc/cpuinfo ---"
      cat /proc/cpuinfo
    fi
    if [ -r /proc/pci ]; then
      echo
      echo "--- /proc/pci ---"
      cat /proc/pci
    fi
    if [ -r /proc/interrupts ]; then
      echo
      echo "--- /proc/interrupts ---"
      cat /proc/interrupts
    fi
    echo
    echo "Serial shell is ready. Type commands or exit to continue into /sbin/init."
    exec /bin/sh
    """
)

ROOTFS_INITTAB = textwrap.dedent(
    """\
    ::sysinit:/etc/rc.sysinit
    ::respawn:/bin/sh
    ::ctrlaltdel:/sbin/reboot
    ::shutdown:/bin/umount -a -r
    """
)

ROOTFS_RCSYSINIT = textwrap.dedent(
    """\
    #!/bin/sh
    PATH=/bin:/sbin
    mount -t proc proc /proc
    mount -t devfs devfs /dev 2>/dev/null

    echo
    echo "BAB750 vendor Linux 2.4 userspace is up."
    echo "Useful checks: cat /proc/pci ; cat /proc/interrupts ; ifconfig -a ; dmesg"
    echo
    """
)

COMMANDS_TEMPLATE = textwrap.dedent(
    """\
    # Vendor Linux 2.4 / ELinOS-style ramfs boot for the ELTEC BAB-750
    setenv bootargs
    setenv serverip {serverip}
    setenv ipaddr {ipaddr}
    setenv netmask {netmask}
    tftpboot {loadaddr} 750nfs.img
    bootm {loadaddr}
    """
)


def run(cmd: list[str], *, cwd: pathlib.Path | None = None) -> None:
    print("+", " ".join(str(part) for part in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def write_text(path: pathlib.Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def download_busybox(download_dir: pathlib.Path) -> pathlib.Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    deb_path = download_dir / pathlib.Path(BUSYBOX_URL).name
    if not deb_path.exists():
        print(f"Downloading {BUSYBOX_URL}")
        with urllib.request.urlopen(BUSYBOX_URL) as response:
            deb_path.write_bytes(response.read())
    return deb_path


def extract_busybox(deb_path: pathlib.Path, work_dir: pathlib.Path) -> pathlib.Path:
    extract_dir = work_dir / "busybox-extract"
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True)
    run(
        [
            "bash",
            "-lc",
            f"ar p {deb_path} data.tar.gz | tar -xzf - -C {extract_dir} ./bin/busybox",
        ]
    )
    busybox = extract_dir / "bin" / "busybox"
    if not busybox.exists():
        raise SystemExit(f"BusyBox extraction failed: {busybox} not found")
    busybox.chmod(0o755)
    return busybox


def stage_rootfs(rootfs_dir: pathlib.Path, busybox: pathlib.Path) -> None:
    shutil.rmtree(rootfs_dir, ignore_errors=True)
    for subdir in ("bin", "sbin", "etc", "proc", "dev", "tmp", "root", "mnt"):
        (rootfs_dir / subdir).mkdir(parents=True, exist_ok=True)

    shutil.copy2(busybox, rootfs_dir / "bin" / "busybox")
    (rootfs_dir / "bin" / "busybox").chmod(0o755)

    for applet in (
        "sh",
        "mount",
        "umount",
        "echo",
        "cat",
        "ls",
        "dmesg",
        "uname",
        "ps",
        "ifconfig",
        "route",
        "reboot",
        "halt",
        "init",
        "sleep",
    ):
        target = rootfs_dir / "bin" / applet
        target.unlink(missing_ok=True)
        target.symlink_to("/bin/busybox")

    for symlink in ("init", "ifconfig", "reboot"):
        target = rootfs_dir / "sbin" / symlink
        target.unlink(missing_ok=True)
        target.symlink_to("/bin/busybox")

    write_text(rootfs_dir / "linuxrc", ROOTFS_LINUXRC, mode=0o755)
    write_text(rootfs_dir / "etc" / "inittab", ROOTFS_INITTAB)
    write_text(rootfs_dir / "etc" / "rc.sysinit", ROOTFS_RCSYSINIT, mode=0o755)
    write_text(rootfs_dir / "etc" / "fstab", "proc /proc proc defaults 0 0\nnone /dev devfs defaults 0 0\n")
    write_text(rootfs_dir / "etc" / "passwd", "root::0:0:root:/root:/bin/sh\n")
    write_text(rootfs_dir / "etc" / "group", "root:x:0:\n")
    write_text(rootfs_dir / "etc" / "hosts", "127.0.0.1 localhost\n")


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
        tar --numeric-owner --owner=0 --group=0 -czf "$2" .
        """
    )
    run(["fakeroot", "--", "bash", "-lc", script, "_", str(rootfs_dir), str(tarball)])


def build_multi_image(
    kernel: pathlib.Path,
    objcopy: pathlib.Path,
    mkimage: pathlib.Path,
    build_dir: pathlib.Path,
    tftp_dir: pathlib.Path,
) -> pathlib.Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    tftp_dir.mkdir(parents=True, exist_ok=True)

    rootfs_tar = build_dir / "750nfs-rootfs.tgz"
    kernel_bin = build_dir / "vmlinux.bin"
    kernel_gz = build_dir / "vmlinux.bin.gz"
    image_path = tftp_dir / "750nfs.img"

    if not kernel.exists():
        raise SystemExit(f"Kernel image not found: {kernel}")
    if not objcopy.exists():
        raise SystemExit(f"Cross objcopy not found: {objcopy}")
    if not mkimage.exists():
        raise SystemExit(f"mkimage not found: {mkimage}")

    run([str(objcopy), "-O", "binary", str(kernel), str(kernel_bin)])
    if kernel_gz.exists():
        kernel_gz.unlink()
    run(["gzip", "-9f", str(kernel_bin)])
    run(
        [
            str(mkimage),
            "-A",
            "ppc",
            "-O",
            "linux",
            "-T",
            "multi",
            "-C",
            "gzip",
            "-a",
            "0",
            "-e",
            "0",
            "-n",
            "BAB750 Linux 2.4 multi",
            "-d",
            f"{kernel_gz}:{rootfs_tar}",
            str(image_path),
        ]
    )
    run([str(mkimage), "-l", str(image_path)])
    return image_path


def write_commands(path: pathlib.Path, *, serverip: str, ipaddr: str, netmask: str, loadaddr: str) -> None:
    write_text(
        path,
        COMMANDS_TEMPLATE.format(
            serverip=serverip,
            ipaddr=ipaddr,
            netmask=netmask,
            loadaddr=loadaddr,
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=str(DEFAULT_KERNEL), help=f"path to the linked vendor vmlinux (default: {DEFAULT_KERNEL})")
    parser.add_argument("--objcopy", default=str(DEFAULT_OBJCOPY), help=f"path to the working PowerPC objcopy wrapper (default: {DEFAULT_OBJCOPY})")
    parser.add_argument("--mkimage", default=str(DEFAULT_MKIMAGE), help=f"path to mkimage (default: {DEFAULT_MKIMAGE})")
    parser.add_argument("--tftp-dir", default=str(DEFAULT_TFTP_DIR), help=f"TFTP output directory (default: {DEFAULT_TFTP_DIR})")
    parser.add_argument("--build-dir", default=str(DEFAULT_BUILD_DIR), help=f"working build directory (default: {DEFAULT_BUILD_DIR})")
    parser.add_argument("--commands-file", default=str(DEFAULT_COMMANDS), help=f"output path for the U-Boot commands file (default: {DEFAULT_COMMANDS})")
    parser.add_argument("--serverip", default="192.168.1.101", help="server IP to write into the U-Boot commands file")
    parser.add_argument("--ipaddr", default="192.168.1.123", help="board IP to write into the U-Boot commands file")
    parser.add_argument("--netmask", default="255.255.255.0", help="netmask to write into the U-Boot commands file")
    parser.add_argument("--loadaddr", default="1000000", help="U-Boot TFTP load address to write into the commands file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    kernel = pathlib.Path(args.kernel).expanduser().resolve()
    objcopy = pathlib.Path(args.objcopy).expanduser()
    mkimage = pathlib.Path(args.mkimage).expanduser().resolve()
    tftp_dir = pathlib.Path(args.tftp_dir).expanduser().resolve()
    build_dir = pathlib.Path(args.build_dir).expanduser().resolve()
    commands_file = pathlib.Path(args.commands_file).expanduser().resolve()

    busybox_deb = download_busybox(build_dir / "downloads")
    busybox = extract_busybox(busybox_deb, build_dir)

    rootfs_dir = build_dir / "rootfs"
    rootfs_tar = build_dir / "750nfs-rootfs.tgz"

    stage_rootfs(rootfs_dir, busybox)
    make_rootfs_tarball(rootfs_dir, rootfs_tar)
    image_path = build_multi_image(kernel, objcopy, mkimage, build_dir, tftp_dir)
    write_commands(
        commands_file,
        serverip=args.serverip,
        ipaddr=args.ipaddr,
        netmask=args.netmask,
        loadaddr=args.loadaddr,
    )

    print()
    print(f"Prepared vendor Linux 2.4 image: {image_path}")
    print(f"Prepared commands file:        {commands_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
