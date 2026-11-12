from typing import Literal


DesktopPage = Literal[
    "today", "inbox", "channels", "tasks", "knowledge", "content", "saved",
    "trash", "settings", "internal",
]
MESSAGE_PAGES = frozenset({"today", "inbox", "saved", "trash"})


def required_pages(path: str, method: str) -> frozenset[str]:
    """Translate API capabilities shared by the desktop views into page grants."""
    if not path.startswith("/api/v1/"):
        return frozenset()
    resource = path.removeprefix("/api/v1/").split("/", 1)[0]
    if resource in {"tasks", "staff"}:
        return frozenset({"tasks"})
    if resource == "knowledge":
        return frozenset({"knowledge"})
    if resource in {"content", "video"}:
        return frozenset({"content"})
    if resource in {"articles", "health-relay"}:
        return MESSAGE_PAGES
    if resource == "channels":
        return MESSAGE_PAGES | {"channels"} if method == "GET" else frozenset({"channels"})
    if resource == "channel-subscriptions":
        return frozenset({"channels"})
    return frozenset()


def can_access(allowed_pages: list[str] | None, role: str, pages: frozenset[str]) -> bool:
    return role == "admin" or allowed_pages is None or not pages or not pages.isdisjoint(allowed_pages)
