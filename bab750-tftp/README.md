# BAB-750 Linux Netboot

This directory hosts the two network services used by the ELTEC BAB-750:

- `tftp` serves the Linux boot image to U-Boot.
- `nfs` exports `./nfsroot/adelie-full` as an NFSv4 root filesystem.

For the old BAB-750 U-Boot, the working path is the legacy `cuImage` flow:

```bash
python3 /home/ilyabot/bab750-experiments/scripts/bab750_prepare_legacy_linux_netboot.py --up
```

That script does three things:

- extracts `adelie-rootfs-full-ppc-*.txz` into `./nfsroot/adelie-full`
- builds `cuImage.bab750` from the local Linux source tree
- writes ready-to-paste U-Boot commands to `./uboot-netboot.txt`

To drive the serial console and start the Linux netboot automatically from an already flashed U-Boot:

```bash
python3 /home/ilyabot/bab750-experiments/scripts/bab750_boot_legacy_linux.py
```

If you wire `RTS` or `DTR` to the board reset circuit, the boot helper can pulse it:

```bash
python3 /home/ilyabot/bab750-experiments/scripts/bab750_boot_legacy_linux.py --reset-line rts --auto-reset
```

If you only want to start the services manually:

```bash
cd /home/ilyabot/bab750-experiments/bab750-tftp
docker compose up -d
```

The generated U-Boot command file lives at:

`/home/ilyabot/bab750-experiments/bab750-tftp/uboot-netboot.txt`

## Vendor Linux 2.4 smoke test

To sanity-check the BAB-750 with the old vendor kernel and a tiny ramfs shell:

```bash
python3 /home/ilyabot/bab750-experiments/scripts/bab750_prepare_vendor_linux_2_4_netboot.py
```

That produces:

- `./tftpboot/750nfs.img` — a classic `ppcboot_multi` image with the vendor `2.4` kernel
- `./uboot-netboot-vendor-2.4.txt` — ready-to-use U-Boot commands for that image

To drive the serial console with the existing helper, point it at the vendor command file:

```bash
python3 /home/ilyabot/bab750-experiments/scripts/bab750_boot_legacy_linux.py \
  --commands-file /home/ilyabot/bab750-experiments/bab750-tftp/uboot-netboot-vendor-2.4.txt
```
