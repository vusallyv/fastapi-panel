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

## Staff groups

Turn on groups to manage permissions from the panel, with no column on your user model:

```python
ModelAuth(User, superuser_field="is_superuser", groups=True)
```

A **Staff groups** page appears under System. Each group has a name, a grid of models and actions to tick, and its members. Someone in several groups gets all of their permissions, on top of any `permissions_field`. Changes apply on the member's next request.

Only superusers can see or change groups, even if a user holds the `*` permission, because anyone who could edit a group could give themselves everything. The same goes for your own user admin: don't let non-superusers edit `is_superuser` or `permissions`.

The grid sets the same permission strings as above, so it has no effect on a `ModelAdmin` whose `has_*_permission` methods ignore them. Groups need `ModelAuth`, and the two tables described in [Migrations](getting-started.md#4-migrations). Without those tables, non-superusers get an error page rather than extra access. Deleting a user through the panel removes their memberships, but deleting one elsewhere doesn't. If your database can reuse user ids, delete the rows in `fastapi_panel_group_member` too.

## Adding staff and setting passwords

When a `ModelAdmin` edits the same model as `ModelAuth`, its form gets a **Password** field that hashes through your backend. It's required when adding a user, and leaving it empty on an existing user keeps their password. The stored hash is never shown.

Only superusers can replace an existing user's password, since that would let someone log in as them. If there's no `superuser_field`, everyone who can edit the user can. Changing a password logs that user out, including you if you change your own here. To leave the field out, add the column to `exclude`.

## Your own roles

For anything else, subclass the backend. Load what you need in `get_user`, because the database session is closed by the time `has_perm` runs:

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

## Activity

The Activity page needs the `activity.view` permission, so with `ModelAuth` that means superusers or a `permissions_field` that lists it. It only shows entries for models the person may view. Row-level rules from `get_queryset` don't apply to the log, so don't give this permission to someone who should only see some rows of a model.

## Password reset

Pass a function that delivers the link, and the panel's public address:

```python
def send_reset(user, url):
    send_email(user.email, "Reset your password", f"Open this link to choose a new password: {url}")


site.mount(
    app,
    engine=engine,
    auth=ModelAuth(User),
    secret_key=key,
    password_reset=send_reset,
    public_url="https://example.com/admin",
)
```

A **Forgot your password?** link appears on the login page. `send_reset` can be sync or async, and runs after the response, so a slow mail server can't reveal which accounts exist. If it raises, the error goes to the `fastapi_panel` logger. People type their username, which is their email if that's your `username_field`.

Links work for one hour and stop working once the password changes. Changing it also ends that user's open sessions. Each username and IP can request 3 links an hour.

`public_url` is required because a link built from the request would use its `Host` header, which an attacker can set to their own site. `StaticAuth` has no reset, since its passwords live in your configuration.

Without email, or if you've lost access, use the command line:

```bash
python -m fastapi_panel changepassword main --username ana
```

## Sessions

Sessions live in a signed cookie, so there's no session table. A session ends when it expires (`session_age`, 12 hours by default), on logout, when the password changes, or when the user is deactivated. The last two apply on the very next request.

Logout clears the cookie in that browser. A stolen cookie stays valid until one of the other conditions is met, so changing the password is how you revoke every session of one user, and changing `secret_key` revokes everyone's.
