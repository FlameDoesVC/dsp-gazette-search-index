from ninja import Router
from ninja.errors import HttpError

from api.logging import log_click
from api.schemas import AcceptedOut, ClickIn

router = Router()


@router.post("/events/click", response={202: AcceptedOut})
def click(request, payload: ClickIn):
    if payload.position < 0:
        raise HttpError(400, "position must be >= 0")
    log_click(payload.query_id, payload.document_id, payload.position)
    return 202, {"status": "accepted"}
