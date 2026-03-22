1) Put the boot file into ./ftp-root/
   Example:
     cp ../tftpboot/ram-boot/u-boot ./ftp-root/BAB750.img

2) Start:
     docker compose up -d --build

3) Watch logs:
     docker logs -f bab750-ftp

4) Boot ROM fields for anonymous FTP:
   boot device          : dc
   processor number     : 0
   host name            : host
   file name            : /BAB750.img
   inet on ethernet (e) : 192.168.1.123
   host inet (h)        : 192.168.1.109
   user (u)             : anonymous
   ftp password (pw)    : x
   flags (f)            : 0x0

5) Inside U-Boot after RAM boot succeeds, reflash UserROM using your normal flow.
