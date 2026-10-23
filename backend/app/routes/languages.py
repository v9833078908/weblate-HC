# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth import require_actor
from app.services import languages

router = APIRouter(prefix="/api/producer")


@router.get("/languages/")
def catalog(request: Request, actor=Depends(require_actor)):
    return {"results": languages.catalog_for_settings(request.app.state.settings)}
