from ninja import Router

from api.schemas import SuggestOut
from search.suggest import suggest as suggest_terms

router = Router()


@router.get("/suggest", response=SuggestOut)
def suggest(request, q: str = "", limit: int = 8):
    return {"suggestions": suggest_terms(q, limit=max(1, min(limit, 20)))}
