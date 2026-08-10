# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Production image for the HCGameLoc Weblate fork.
#
# Build context is the repository root:
#   docker build -f deploy/Dockerfile -t hcgameloc:latest .
#
# The upstream runtime image is reused for the system dependencies, the venv,
# nginx/supervisor wiring and the /app/bin/start entrypoint (it runs migrate
# and collectstatic on every boot). Only the Python code is replaced by this
# repository.

FROM weblate/weblate:2026.8.0.0@sha256:81acc89b2a9742015f2ffdee77130d1c5fcdbf3eeae203b3ff665bc6d2f7a8aa

USER root

ENV UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    VIRTUAL_ENV=/app/venv

# hc-srv15-localizer is a KVM guest with a "Common KVM processor" (qemu64,
# x86-64-v1: no SSE4.2/POPCNT). numpy >= 2.4 ships wheels built with an
# x86-64-v2 baseline and raises RuntimeError on import, and
# weblate.fonts.render imports matplotlib -> numpy while Django loads apps, so
# the stock weblate image cannot even run `django-admin` there. 2.3.5 is the
# last release with x86-64-v1 wheels for CPython 3.14. Remove the pin once the
# hypervisor exposes a modern CPU model (libvirt/Proxmox: cpu mode=host).
ARG NUMPY_VERSION=2.3.5

COPY . /usr/src/hcgameloc

RUN set -eux; \
    uv pip install --no-config --python /app/venv/bin/python "numpy==${NUMPY_VERSION}"; \
    cd /usr/src/hcgameloc/weblate; \
    env DJANGO_SETTINGS_MODULE=weblate.settings_example \
        /app/venv/bin/django-admin compilemessages; \
    cd /usr/src/hcgameloc; \
    uv pip uninstall --no-config --python /app/venv/bin/python weblate; \
    uv pip install --no-config --python /app/venv/bin/python '.[all]' "numpy==${NUMPY_VERSION}"; \
    PYLIB=/app/pylib; \
    mkdir -p "$PYLIB"; \
    cp -r weblate_customization/src/weblate_customization "$PYLIB/"; \
    cp -r loc_kit_ingest "$PYLIB/"; \
    rm -rf "$PYLIB/loc_kit_ingest/tests" "$PYLIB/loc_kit_ingest/pytest.ini"; \
    find "$PYLIB" -name __pycache__ -type d -prune -exec rm -rf {} +; \
    SITE_PACKAGES="$(/app/venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"; \
    echo "$PYLIB" > "$SITE_PACKAGES/hcgameloc-pylib.pth"; \
    cd /; \
    rm -rf /usr/src/hcgameloc; \
    /app/venv/bin/python -c "import importlib.util as u, numpy, matplotlib, weblate, loc_kit_ingest; assert u.find_spec('weblate_customization.machinery') and u.find_spec('weblate_customization.checks'); print(weblate.__file__, numpy.__version__)"

USER 1000
