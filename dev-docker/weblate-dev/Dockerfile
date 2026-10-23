# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

FROM weblate/weblate:2026.8.0.0@sha256:81acc89b2a9742015f2ffdee77130d1c5fcdbf3eeae203b3ff665bc6d2f7a8aa

##########
# WARNING!
# This dockerfile is meant to be used in the development process
# and WILL perform very poorly in production.
#
# For production-ready dockerfile see:
# https://github.com/WeblateOrg/docker
#########

#WORKDIR /srv

ARG USER_ID=1000
ARG GROUP_ID=1000

USER root
ENV PATH=/app/venv/bin:/opt/python/bin:/usr/local/bin:/usr/bin:/bin:/sbin:/usr/sbin
ENV UV_CACHE_DIR=/app/data/uv-cache
ENV UV_LINK_MODE=copy
# Make sure user exists in the container
RUN if ! getent group "${GROUP_ID}"; then \
            groupadd --gid "${GROUP_ID}" developer; \
        fi \
    &&  if ! getent passwd "${USER_ID}"; then \
            useradd --gid "${GROUP_ID}" --uid "${USER_ID}" --groups root,tty,weblate --shell /bin/bash developer; \
        fi
# Following should match what dev container in docker-base repo includes
RUN apt-get update && apt-get install -y \
    pkg-config \
    make \
    gcc \
    g++ \
    libicu-dev \
    libxml2-dev \
    libacl1-dev \
    liblz4-dev \
    libzstd-dev \
    libxxhash-dev \
    libmariadb-dev \
    libxmlsec1-dev \
    libsasl2-dev \
    libldap2-dev \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    zlib1g-dev \
    libjpeg62-dev

# List should match weblate/weblate
RUN chown -R "${USER_ID}:${GROUP_ID}" /etc/nginx/sites-available/ /var/log/nginx/ /var/lib/nginx /app/data /app/cache /run /home/weblate /etc/localtime /etc/supervisor/conf.d /tmp /home

# Celery cold shutdown for producer tasks (acks_late auto_translate* on the
# "translate" queue and fix_failing_checks on "celery"): stopsignal=QUIT makes
# the worker wait CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT, then cancel the running
# task and restore the unacknowledged message to its queue instead of losing
# it to the 4 h visibility timeout on a stack restart. stopwaitsecs=60 stays
# between the soft shutdown timeout (20 s) and the container stop_grace_period
# (90 s). Mirrors deploy/Dockerfile; the other celery-*.conf programs keep
# SIGTERM.
RUN printf '%s\n' 'stopsignal=QUIT' 'stopwaitsecs=60' \
    >> /etc/supervisor/conf.d/celery-translate.conf \
    && printf '%s\n' 'stopsignal=QUIT' 'stopwaitsecs=60' \
    >> /etc/supervisor/conf.d/celery-celery.conf

# Start with fresh venv
# Using the original would work, but chown on it is super slow, see https://github.com/docker/for-linux/issues/388
RUN rm -rf /app/venv && mkdir /app/venv && chown "${USER_ID}:${GROUP_ID}" /app/venv

COPY --link --chmod=0755 start-dev /app/bin/

ENTRYPOINT ["/app/bin/start-dev"]
CMD ["runserver"]
