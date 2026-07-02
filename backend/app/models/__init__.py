# Every feature module's models.py is imported here so that any import of
# app.models (directly, or transitively via app.models._mixins, which every
# module's models.py imports) registers every SQLModel table on
# SQLModel.metadata. alembic/env.py does NOT use this aggregator -- it has
# its own load_models_for_migration() that imports only enabled modules'
# models (T-062), so a disabled module's tables are never proposed by
# autogenerate. This file is for everything else: the app always runs with
# every module's model class importable, regardless of product.config.py --
# only routes and migrations are actually conditional.
from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.billing import models as billing_models  # noqa: F401
from app.modules.gdpr import models as gdpr_models  # noqa: F401
from app.modules.notifications import models as notifications_models  # noqa: F401
from app.modules.storage import models as storage_models  # noqa: F401
from app.modules.tenancy import models as tenancy_models  # noqa: F401
from app.workers.jobs import records as job_records  # noqa: F401
