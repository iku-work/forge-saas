"""Tables that core code paths reach for *only when an optional module is on*.

Most test files here build their own small table subset rather than migrating
the whole schema, which is fast and keeps each file's dependencies legible.
That works right up until an optional module puts a query on a path those
files already exercise:

  * T-320 put `webhooks.emit()` on tenancy's and billing's write paths, so a
    role change reads `webhook_endpoint`.
  * T-331 put `password_login_blocked()` on the password login path, so every
    successful password check reads `sso_connection` joined to `membership`.

Both guards return before querying when their module is off, which is correct
and is why the committed configuration never noticed either of them. Turn the
modules on -- as CI's `module-matrix` job does on its Phase-4-modules-on leg --
and 49 tests across five auth files fail on a missing table that has nothing
to do with what they are testing.

So: splat `MODULE_HOT_PATH_TABLES` into any `_TABLES` list whose file drives a
login, a tenancy write or a billing write. Creating them costs a
configuration that never reads them nothing, and it means a file's coverage
does not silently depend on which modules happen to be enabled.

The list is a dependency *closure*, not just the two tables above:
`sso_connection` and `webhook_endpoint` both carry a foreign key to
`organization`, which carries one to `plan`; `membership` carries one to
`user` and `organization`, and `webhook_delivery` one to `webhook_endpoint`.
`create_all` needs every target, so they are all here.

That closure overlaps whatever the calling file already listed, which is why
this is a function rather than a list to splat: `create_all` does **not**
deduplicate within one call, and passing `user` twice raises
`DuplicateTableError` rather than quietly doing the right thing.
"""

from sqlalchemy import Table

from app.modules.auth.models import User
from app.modules.billing.models import Plan
from app.modules.sso.models import SsoConnection
from app.modules.tenancy.models import Membership, Organization
from app.modules.webhooks.models import WebhookDelivery, WebhookEndpoint

_MODULE_HOT_PATH_TABLES = [
    User.__table__,
    # `organization` carries a foreign key to `plan`, so the closure reaches
    # one level further than the two tables this module is named for.
    Plan.__table__,
    Organization.__table__,
    Membership.__table__,
    SsoConnection.__table__,
    WebhookEndpoint.__table__,
    WebhookDelivery.__table__,
]


def with_module_hot_path_tables(*tables: Table) -> list[Table]:
    """The caller's tables plus the ones an enabled module reaches for, once
    each and in a stable order (the caller's first, so a reader still sees
    what the file is actually about at the top of the list)."""
    merged: dict[str, Table] = {}
    for table in (*tables, *_MODULE_HOT_PATH_TABLES):
        merged.setdefault(table.name, table)
    return list(merged.values())
