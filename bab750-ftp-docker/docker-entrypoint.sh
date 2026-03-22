#!/bin/sh
set -eu

mkdir -p /var/log
: > /var/log/vsftpd.log

echo "--- /etc/vsftpd.conf ---"
cat /etc/vsftpd.conf

echo "--- /srv/ftp ---"
ls -lah /srv/ftp || true

exec /usr/sbin/vsftpd /etc/vsftpd.conf
