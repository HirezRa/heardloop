# Backup service: nightly pg_dump + media archive, restore on demand (see docs/GUIDE.md, section 10).
# Same major version as the `db` service, so pg_dump and pg_restore match the server.
FROM postgres:18-alpine

RUN apk add --no-cache openssl tzdata

COPY backup.sh restore.sh entrypoint.sh /usr/local/bin/
RUN chmod 0555 /usr/local/bin/backup.sh /usr/local/bin/restore.sh /usr/local/bin/entrypoint.sh

# The image's own unprivileged user. Compose adds read_only, tmpfs /tmp, cap_drop ALL, no-new-privileges.
USER postgres

# The base image's entrypoint starts a database server; this service never does.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD []
