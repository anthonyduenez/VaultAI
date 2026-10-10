from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from vaultai import db
from vaultai.actions import load_action_center
from vaultai.api import Conn
from vaultai.domain import ActionCenter

router = APIRouter()


@router.get("/actions", response_model=ActionCenter)
def action_center(conn: Conn) -> ActionCenter:
    return load_action_center(conn, today=date.today(), now=db.now())
