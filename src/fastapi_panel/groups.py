from __future__ import annotations

import re
from typing import Any

from .forms import Field, ValidationError
from .log import StaffGroup, StaffGroupMember
from .options import ModelAdmin, display

ACTIONS = ("view", "add", "change", "delete")
PERMISSION = re.compile(r"^(\*|[\w-]+\.(\*|[\w-]+))$")


class PermissionsField(Field):
    """A grid of model and action checkboxes, stored as the panel's usual permission strings."""

    widget = "permissions"

    def prepare(self, session: Any, request: Any, site: Any) -> None:
        self.actions = ACTIONS
        admins = sorted(
            (admin for admin in site._registry.values() if not isinstance(admin, StaffGroupAdmin)),
            key=lambda admin: (admin.group_name, admin.name_plural),
        )
        self.rows = [{"label": admin.name_plural, "slug": admin.slug} for admin in admins]
        self.others = [(page["permission"], page["title"]) for page in site._pages if page["permission"]]
        self.known = {f"{row['slug']}.{action}" for row in self.rows for action in ACTIONS}
        self.known.update(value for value, _ in self.others)

    def format(self, value: Any) -> list[str]:
        return sorted(value or [])

    def extract(self, data: Any) -> list[str]:
        return data.getlist(self.html_name)

    def clean(self, raw: Any) -> list[str]:
        # Strings outside the grid, like `order.*` written by a script, stay until someone unticks them.
        if not all(PERMISSION.match(value) for value in raw):
            raise ValidationError("That isn't a valid permission.")
        return sorted(set(raw))


class MembersField(Field):
    widget = "dual"

    def prepare(self, session: Any, request: Any, site: Any) -> None:
        self.session = session
        self.auth = site.auth
        self.choices = self.auth.staff_choices(session)

    def read(self, obj: Any) -> list[str]:
        ids = [member.user_id for member in obj.members]
        # Someone later deactivated, or past the choices limit, must not fall out of the group on save.
        listed = {value for value, _ in self.choices}
        for user_id in ids:
            if user_id not in listed:
                user = self.auth.get_user(self.session, user_id)
                self.choices.append((user_id, self.auth.user_label(user) if user else f"Deleted user {user_id}"))
        return ids

    def format(self, value: Any) -> list[str]:
        return sorted(value or [])

    def extract(self, data: Any) -> list[str]:
        return data.getlist(self.html_name)

    def clean(self, raw: Any) -> list[str]:
        allowed = {value for value, _ in self.choices}
        if not all(value in allowed for value in raw):
            raise ValidationError("Only staff can be added to a group.")
        return list(dict.fromkeys(raw))

    def write(self, obj: Any, value: Any) -> None:
        current = {member.user_id: member for member in obj.members}
        obj.members = [current.get(user_id) or StaffGroupMember(user_id=user_id) for user_id in value]


class StaffGroupAdmin(ModelAdmin):
    slug = "staff-groups"
    group = "System"
    verbose_name = "Staff group"
    fields = ("name", "permissions", "members")
    list_display = ("name", "member_count", "permission_count")
    search_fields = ("name",)
    ordering = ("name",)
    builtin_actions = ("delete_selected",)
    field_overrides = {
        "permissions": PermissionsField(help_text="Change also allows viewing."),
        "members": MembersField(label="Members", required=False),
    }

    @display("Members")
    def member_count(self, obj: Any) -> int:
        return len(obj.members)

    @display("Permissions")
    def permission_count(self, obj: Any) -> int:
        return len(obj.permissions)

    # Superusers only: anyone else who could edit a group could give themselves every permission.
    def _superuser(self, request: Any, obj: Any = None) -> bool:
        return self.site.auth.is_superuser(request.state.user)

    has_view_permission = has_add_permission = has_change_permission = has_delete_permission = _superuser


__all__ = ["StaffGroup", "StaffGroupAdmin"]
