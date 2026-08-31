from ninja import NinjaAPI

from api.routers import documents, events, meta, search, suggest

api = NinjaAPI(
    title="Gazette Search",
    version="1.0.0",
    urls_namespace="api-v1",
    # Every endpoint is read-only or an anonymous append-only event; there is
    # no session-authenticated state to protect. django-ninja 1.5 exempts its
    # views from the Django CSRF middleware and enforces CSRF only inside auth
    # callbacks, and we install none. The report and click endpoints are
    # rate-limited instead.
)

api.add_router("", meta.router, tags=["meta"])
api.add_router("", search.router, tags=["search"])
api.add_router("", suggest.router, tags=["suggest"])
api.add_router("", events.router, tags=["events"])
api.add_router("", documents.router, tags=["documents"])
