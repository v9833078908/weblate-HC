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

COPY . /usr/src/hcgameloc

RUN set -eux; \
    cd /usr/src/hcgameloc/weblate; \
    env DJANGO_SETTINGS_MODULE=weblate.settings_example \
        /app/venv/bin/django-admin compilemessages; \
    cd /usr/src/hcgameloc; \
    uv pip uninstall --no-config --python /app/venv/bin/python weblate; \
    uv pip install --no-config --python /app/venv/bin/python '.[all]'; \
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
    /app/venv/bin/python -c "import importlib.util as u, weblate, loc_kit_ingest; assert u.find_spec('weblate_customization.machinery') and u.find_spec('weblate_customization.checks'); print(weblate.__file__)"

USER 1000
