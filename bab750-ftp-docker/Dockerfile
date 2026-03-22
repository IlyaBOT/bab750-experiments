FROM debian:12-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends vsftpd ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/run/vsftpd/empty && \
    chown root:root /var/run/vsftpd/empty && \
    chmod 0555 /var/run/vsftpd/empty

COPY vsftpd.conf /etc/vsftpd.conf
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /srv/ftp

EXPOSE 21/tcp 20/tcp
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
