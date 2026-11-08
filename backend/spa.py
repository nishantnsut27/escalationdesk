"""Serve the app shell only for recognized frontend routes."""
from starlette.staticfiles import StaticFiles


APP_PAGES = {"login", "signup", "demo-login", "forgot", "account", "privacy", "dashboard",
             "collections", "errors", "investigations", "incidents", "connections", "import", "logs", "demo",
             "overview", "history", "evidence", "imports"}
DETAIL_PAGES = {"collections", "investigations", "incidents", "demo", "history", "evidence"}


def is_app_path(path):
    parts = path.strip("/").split("/")
    return parts == [""] or (parts[0] in APP_PAGES and
                             (len(parts) == 1 or (parts[0] in DETAIL_PAGES and len(parts) == 2)))


class AppStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Use the encoded path to keep IDs containing an escaped slash in one segment.
        raw_path = scope.get("raw_path", scope["path"].encode()).decode("ascii", errors="replace")
        if scope["method"] in {"GET", "HEAD"} and is_app_path(raw_path):
            return await super().get_response("index.html", scope)
        return await super().get_response(path, scope)
