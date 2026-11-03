from __future__ import annotations

import hmac
import importlib
import inspect as inspect_module
import pkgutil
import secrets
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader, select_autoescape
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, views
from .db import Database
from .log import AdminLog
from .options import ImproperlyConfigured, ModelAdmin
from .security import SECURITY_HEADERS, LoginThrottle, Signer

COOKIE_NAME = "fastapi_panel_session"
RESERVED_SLUGS = {"login", "logout", "password", "static"}
MIN_SECRET_LENGTH = 32
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 4096
MAX_FORM_FIELDS = 20_000


class AdminSite:
    def __init__(
        self,
        *,
        title: str = "Admin",
        header: str | None = None,
        index_title: str = "Dashboard",
        template_dirs: Iterable[str | Path] = (),
        extra_css: Iterable[str] = (),
        audit_log: bool = True,
    ) -> None:
        self.title = title
        self.header = header or title
        self.index_title = index_title
        self.extra_css = list(extra_css)
        self.audit_log = audit_log
        self._registry: dict[str, ModelAdmin] = {}
        self._pages: list[dict[str, Any]] = []
        self._log_table_ready = not audit_log
        self.templates = Environment(
            loader=ChoiceLoader(
                [FileSystemLoader([str(d) for d in template_dirs]), PackageLoader("fastapi_panel", "templates")]
            ),
            autoescape=select_autoescape(default=True, default_for_string=True),
        )

    # Registration

    def register(self, model: type, admin_class: type[ModelAdmin] = ModelAdmin) -> ModelAdmin:
        admin = admin_class(model, self)
        if admin.slug in self._registry or admin.slug in RESERVED_SLUGS:
            raise ImproperlyConfigured(f"The URL name {admin.slug!r} is taken. Set `slug` on {admin_class.__name__}.")
        self._registry[admin.slug] = admin
        return admin

    def unregister(self, model: type) -> None:
        self._registry = {slug: a for slug, a in self._registry.items() if a.model is not model}

    def page(
        self,
        path: str,
        *,
        title: str,
        group: str = "Pages",
        methods: Iterable[str] = ("GET",),
        permission: str | None = None,
    ) -> Callable[[Callable], Callable]:
        """Add a custom page. The view is called as `fn(session, request)` and returns a Response."""

        def decorate(fn: Callable) -> Callable:
            self._pages.append(
                {
                    "path": path,
                    "title": title,
                    "group": group,
                    "methods": list(methods),
                    "permission": permission,
                    "view": fn,
                }
            )
            return fn

        return decorate

    def admin_for_model(self, model: type) -> ModelAdmin | None:
        return next((admin for admin in self._registry.values() if admin.model is model), None)

    def queryset_for(self, model: type, request: Request) -> Any:
        admin = self.admin_for_model(model)
        stmt = admin.get_queryset(request) if admin else select(model)
        keys = inspect(model).primary_key
        return stmt.order_by(*keys)

    def _admin(self, request: Request) -> ModelAdmin:
        admin = self._registry.get(request.path_params["slug"])
        if admin is None:
            raise HTTPException(404, "Not found.")
        return admin

    # Mounting

    def mount(
        self,
        app: FastAPI | Starlette,
        *,
        engine: Any,
        auth: Any,
        secret_key: str,
        path: str = "/admin",
        session_age: int = 12 * 3600,
        secure_cookies: bool | None = None,
        trusted_origins: Iterable[str] = (),
        max_body_size: int = 10 * 1024 * 1024,
    ) -> None:
        if len(secret_key) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"secret_key must be at least {MIN_SECRET_LENGTH} characters. "
                'Generate one: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        self.db = Database(engine)
        self.auth = auth
        self.signer = Signer(secret_key)
        self.session_age = session_age
        self.secure_cookies = secure_cookies
        self.trusted_origins = set(trusted_origins)
        self.max_body_size = max_body_size
        self.throttle = LoginThrottle()
        for admin in self._registry.values():
            admin.check()
        app.mount(path, Starlette(routes=self._routes(), exception_handlers={404: self._not_found}))

    def _routes(self) -> list[Any]:
        routes: list[Any] = [
            self._route("/", self._index),
            self._route("/login", self._login, methods=["GET", "POST"], public=True),
            self._route("/logout", self._logout, methods=["POST"]),
            self._route("/password", self._change_password, methods=["GET", "POST"]),
            Mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"),
        ]
        for page in self._pages:
            routes.append(self._route(page["path"], self._page_view(page), methods=page["methods"]))
        for slug, admin in self._registry.items():
            for path, view, methods in admin.get_routes():
                routes.append(self._route(f"/{slug}{path}", view, methods=methods))
        routes += [
            self._route("/{slug}", self._model_view(views.changelist), methods=["GET", "POST"]),
            self._route("/{slug}/add", self._model_view(views.change_form), methods=["GET", "POST"]),
            self._route("/{slug}/autocomplete", self._model_view(views.autocomplete)),
            self._route("/{slug}/{pk}/change", self._object_view(views.change_form), methods=["GET", "POST"]),
            self._route("/{slug}/{pk}/delete", self._object_view(views.delete_view), methods=["GET", "POST"]),
            self._route("/{slug}/{pk}/history", self._object_view(views.history_view)),
        ]
        return routes

    def _route(self, path: str, view: Callable, *, methods: Iterable[str] = ("GET",), public: bool = False) -> Route:
        async def endpoint(request: Request) -> Response:
            return await self._handle(request, view, public)

        return Route(path, endpoint, methods=list(methods))

    def _model_view(self, view: Callable) -> Callable:
        return lambda session, request: view(session, request, self._admin(request))

    def _object_view(self, view: Callable) -> Callable:
        return lambda session, request: view(session, request, self._admin(request), request.path_params["pk"])

    def _page_view(self, page: dict[str, Any]) -> Callable:
        def view(session: Any, request: Request) -> Response:
            if page["permission"] and not self.auth.has_perm(request.state.user, page["permission"]):
                raise HTTPException(403, "You don't have permission to do that.")
            return page["view"](session, request)

        return view

    # The one path every request takes: session, CSRF, auth, the view, then headers and the cookie.

    async def _handle(self, request: Request, view: Callable, public: bool) -> Response:
        session_data = self._read_session(request)
        request.state.session = session_data
        before = dict(session_data)
        request.state.user = None
        request.state.form = None
        request.state.relation_options = {}
        try:
            if request.method == "POST":
                await self._guard_post(request)
            await self._ensure_log_table()
            await self._load_user(request)
            if request.state.user is None and not public:
                response: Response = self._redirect_to_login(request)
            elif inspect_module.iscoroutinefunction(view):
                response = await view(request)
            else:
                response = await self.db.run(view, request)
        except HTTPException as exc:
            response = self._error_page(request, exc.status_code, exc.detail)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.state.session != before or not request.cookies.get(COOKIE_NAME):
            self._write_session(request, response)
        return response

    def _read_session(self, request: Request) -> dict[str, Any]:
        raw = request.cookies.get(COOKIE_NAME)
        data = self.signer.loads(raw) if raw else None
        if not isinstance(data, dict):
            data = {}
        if data.get("uid") and data.get("exp", 0) < time.time():
            data = {}
        data.setdefault("csrf", secrets.token_urlsafe(32))
        return data

    def _write_session(self, request: Request, response: Response) -> None:
        secure = self.secure_cookies if self.secure_cookies is not None else request.url.scheme == "https"
        response.set_cookie(
            COOKIE_NAME,
            self.signer.dumps(request.state.session),
            max_age=self.session_age,
            path=request.scope["root_path"] or "/",
            httponly=True,
            samesite="lax",
            secure=secure,
        )

    async def _guard_post(self, request: Request) -> None:
        length = request.headers.get("content-length", "0")
        if length.isdigit() and int(length) > self.max_body_size:
            raise HTTPException(413, "That request is too large.")

        origin = request.headers.get("origin")
        if origin and origin != "null":
            parts = urlsplit(origin)
            if parts.netloc != request.url.netloc and origin not in self.trusted_origins:
                raise HTTPException(403, "This request came from another site and was blocked.")
        elif origin == "null" or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "This request came from another site and was blocked.")

        form = await request.form(max_fields=MAX_FORM_FIELDS)
        request.state.form = form
        token = form.get("csrf_token", "")
        if not isinstance(token, str) or not hmac.compare_digest(token, request.state.session["csrf"]):
            raise HTTPException(403, "Your session expired or this form is stale. Reload the page and try again.")

    async def _load_user(self, request: Request) -> None:
        session_data = request.state.session
        uid = session_data.get("uid")
        if not uid:
            return
        user = await self.db.run(self.auth.get_user, uid)
        expected = session_data.get("hash", "")
        if (
            user is None
            or not self.auth.is_allowed(user)
            or not hmac.compare_digest(self.auth.session_hash(user), expected)
        ):
            for key in ("uid", "hash", "exp"):
                session_data.pop(key, None)
            return
        request.state.user = user

    async def _ensure_log_table(self) -> None:
        if self._log_table_ready:
            return

        def create(session: Any) -> None:
            AdminLog.__table__.create(session.connection(), checkfirst=True)
            session.commit()

        try:
            await self.db.run(create)
        except DBAPIError:
            pass  # Another worker won the race, or the table is managed by migrations.
        self._log_table_ready = True

    def _redirect_to_login(self, request: Request) -> Response:
        root = request.scope["root_path"]
        target = request.url.path if request.method == "GET" else f"{root}/"
        return RedirectResponse(f"{root}/login?next={views.quote(target, safe='')}", status_code=303)

    def _error_page(self, request: Request, status: int, detail: str) -> Response:
        response = self.render(
            request, "fastapi_panel/error.html", {"title": f"Error {status}", "status": status, "detail": detail}
        )
        response.status_code = status
        return response

    async def _not_found(self, request: Request, exc: Exception) -> Response:
        return await self._handle(request, _raise_not_found, public=True)

    # Rendering

    def render(
        self, request: Request, template: str, context: dict[str, Any] | None = None, status_code: int = 200
    ) -> HTMLResponse:
        session_data = request.state.session
        messages = session_data.pop("messages", [])
        user = request.state.user
        root = request.scope["root_path"]
        html = self.templates.get_template(template).render(
            {
                "request": request,
                "site": self,
                "root": root,
                "static": f"{root}/static",
                "version": __version__,
                "csrf_token": session_data["csrf"],
                "messages": messages,
                "user": user,
                "user_label": self.auth.user_label(user) if user is not None else "",
                "can_change_password": self.auth.can_change_password,
                "nav": self._navigation(request) if user is not None else [],
                **(context or {}),
            }
        )
        return HTMLResponse(html, status_code=status_code)

    def _navigation(self, request: Request) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        current = request.url.path
        root = request.scope["root_path"]
        for admin in self._registry.values():
            if admin.has_module_permission(request):
                url = f"{root}/{admin.slug}"
                can_add = admin.has_add_permission(request)
                groups.setdefault(admin.group_name, []).append(
                    {
                        "label": admin.name_plural,
                        "url": url,
                        "add_url": f"{url}/add" if can_add else None,
                        "active": current == url or current.startswith(f"{url}/"),
                    }
                )
        for page in self._pages:
            if not page["permission"] or self.auth.has_perm(request.state.user, page["permission"]):
                url = f"{root}{page['path']}"
                groups.setdefault(page["group"], []).append(
                    {"label": page["title"], "url": url, "add_url": None, "active": current == url}
                )
        return [
            {"name": name, "items": sorted(items, key=lambda item: item["label"])}
            for name, items in sorted(groups.items())
        ]

    def message(self, request: Request, text: str, level: str = "success") -> None:
        # Kept short: these travel in the cookie until the next page renders.
        queue = request.state.session.setdefault("messages", [])
        queue.append({"text": text[:300], "level": level})
        del queue[:-5]

    # Built-in pages

    def _index(self, session: Any, request: Request) -> Response:
        recent = []
        if self.audit_log:
            stmt = (
                select(AdminLog)
                .where(AdminLog.user_id == self.auth.user_id(request.state.user))
                .order_by(AdminLog.created_at.desc(), AdminLog.id.desc())
                .limit(10)
            )
            recent = list(session.scalars(stmt))
        return self.render(
            request,
            "fastapi_panel/index.html",
            {"title": self.index_title, "recent": recent, "registry": self._registry},
        )

    async def _login(self, request: Request) -> Response:
        root = request.scope["root_path"]
        raw_next = request.query_params.get("next", "")
        target = raw_next if _is_safe_next(raw_next, root) else f"{root}/"
        if request.state.user is not None:
            return RedirectResponse(target, status_code=303)
        error = ""
        username = ""
        status = 200
        if request.method == "POST":
            form = request.state.form
            username = str(form.get("username", ""))[:255]
            password = str(form.get("password", ""))[:MAX_PASSWORD_LENGTH]
            client = request.client.host if request.client else "unknown"
            key = f"{client}|{username.lower()}"
            if self.throttle.blocked(key):
                error = "Too many failed attempts. Try again in a few minutes."
                status = 429
            else:
                user = await self.db.run(self.auth.find_user, username)
                valid = await run_in_threadpool(self.auth.check_password, user, password)
                if valid and user is not None and self.auth.is_allowed(user):
                    self.throttle.reset(key)
                    # A new session, including a new CSRF token, so nothing set before login carries over.
                    request.state.session = {
                        "csrf": secrets.token_urlsafe(32),
                        "uid": self.auth.user_id(user),
                        "hash": self.auth.session_hash(user),
                        "exp": time.time() + self.session_age,
                    }
                    return RedirectResponse(target, status_code=303)
                self.throttle.fail(key)
                error = "Please enter a correct username and password."
                status = 400
        return self.render(
            request,
            "fastapi_panel/login.html",
            {"title": "Log in", "error": error, "username": username, "next": raw_next if target == raw_next else ""},
            status_code=status,
        )

    def _logout(self, session: Any, request: Request) -> Response:
        request.state.session = {"csrf": secrets.token_urlsafe(32)}
        return RedirectResponse(f"{request.scope['root_path']}/login", status_code=303)

    async def _change_password(self, request: Request) -> Response:
        if not self.auth.can_change_password:
            raise HTTPException(404, "Not found.")
        errors: dict[str, str] = {}
        if request.method == "POST":
            form = request.state.form
            current = str(form.get("current", ""))[:MAX_PASSWORD_LENGTH]
            new = str(form.get("new", ""))[:MAX_PASSWORD_LENGTH]
            if not await run_in_threadpool(self.auth.check_password, request.state.user, current):
                errors["current"] = "That's not your current password."
            if len(new) < MIN_PASSWORD_LENGTH:
                errors["new"] = f"Use at least {MIN_PASSWORD_LENGTH} characters."
            elif new != form.get("confirm", ""):
                errors["confirm"] = "The two passwords don't match."
            if not errors:
                hashed_user = await self.db.run(self._store_password, request, new)
                request.state.session["hash"] = self.auth.session_hash(hashed_user)
                self.message(request, "Your password was changed.")
                return RedirectResponse(f"{request.scope['root_path']}/", status_code=303)
        return self.render(request, "fastapi_panel/password.html", {"title": "Change password", "errors": errors})

    def _store_password(self, session: Any, request: Request, password: str) -> Any:
        user = self.auth.get_user(session, self.auth.user_id(request.state.user))
        self.auth.set_password(session, user, password)
        session.commit()
        return user


def _raise_not_found(session: Any, request: Request) -> Response:
    raise HTTPException(404, "This page doesn't exist.")


def _is_safe_next(target: str, root: str) -> bool:
    if not target.startswith(f"{root}/") or target.startswith("//") or "\\" in target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def autodiscover(*packages: str, module: str = "admin") -> None:
    """Import every `admin` module under the given packages, so their registrations run."""
    for name in packages:
        package = importlib.import_module(name)
        if not hasattr(package, "__path__"):
            continue
        for info in pkgutil.walk_packages(package.__path__, f"{name}."):
            if info.name.rsplit(".", 1)[-1] == module:
                importlib.import_module(info.name)


site = AdminSite()


def register(*models: type, site: AdminSite = site) -> Callable[[type[ModelAdmin]], type[ModelAdmin]]:
    """`@register(Product)` above a ModelAdmin, like in Django."""

    def decorate(admin_class: type[ModelAdmin]) -> type[ModelAdmin]:
        for model in models:
            site.register(model, admin_class)
        return admin_class

    return decorate
