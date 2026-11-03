# Authentication and permissions

`site.mount` requires an `auth` backend. There's no anonymous mode.

## ModelAuth

```python
ModelAuth(
    User,
    username_field="email",
    password_field="hashed_password",
    active_field="is_active",  # None to skip
    staff_field="is_staff",  # None to skip
    superuser_field="is_superuser",
    permissions_field="permissions",
)
```

`active_field` and `staff_field` must exist on the model or be passed as `None`. That's deliberate. If your `users` table also holds customers, a missing staff check would let all of them log in, so the panel refuses to start until you decide.

## Permissions

Permissions are strings in the form `<slug>.<action>`, where the action is `view`, `add`, `change` or `delete`.

| Configuration | Who can do what |
|---|---|
| No `superuser_field`, no `permissions_field` | Everyone who can log in can do everything |
| `superuser_field` only | Superusers can do everything, other staff can do nothing |
| `permissions_field` | Superusers can do everything, others get what's in the list |

`permissions_field` points at something iterable on the user, usually a JSON column:

```python
["product.view", "product.change", "order.*"]
```

`order.*` covers all actions on orders and `*` covers everything. Having `change` implies `view`.

For roles or groups, subclass the backend. Load what you need in `get_user`, because the database session is closed by the time `has_perm` runs:

```python
class RoleAuth(ModelAuth):
    def get_user(self, session, user_id):
        user = super().get_user(session, user_id)
        if user is not None:
            user.granted = {p.code for role in user.roles for p in role.permissions}
        return user

    def has_perm(self, user, perm):
        return user.is_superuser or perm in user.granted
```

## Per-model and per-row rules

```python
class InvoiceAdmin(ModelAdmin):
    def get_queryset(self, request):
        user = request.state.user
        stmt = super().get_queryset(request)
        return stmt if user.is_superuser else stmt.where(Invoice.team_id == user.team_id)

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.status == "draft"
```

`get_queryset` is the safest place for row-level rules. Lists, edit pages, deletes, history, bulk actions, autocomplete and foreign key validation all go through it, and a row outside it returns 404.

Inline rows follow the parent's change permission by default. Override `has_add_permission`, `has_change_permission` and `has_delete_permission` on the inline to change that.

The current user is `request.state.user` in every hook and view.

## Your own backend

Single sign-on, LDAP or anything else: subclass `AuthBackend`.

```python
from fastapi_panel import AuthBackend


class LdapAuth(AuthBackend):
    def find_user(self, session, username): ...
    def get_user(self, session, user_id): ...
    def check_password(self, user, password): ...  # user may be None. Runs in a worker thread
    def user_id(self, user): ...
    def is_allowed(self, user): ...
    def has_perm(self, user, perm): ...
    def session_hash(self, user): ...  # sessions end when this value changes
```

## Sessions

Sessions live in a signed cookie, so there's no session table. A session ends when it expires (`session_age`, 12 hours by default), on logout, when the password changes, or when the user is deactivated. The last two apply on the very next request.

Logout clears the cookie in that browser. A stolen cookie stays valid until one of the other conditions is met, so changing the password is how you revoke every session of one user, and changing `secret_key` revokes everyone's.
