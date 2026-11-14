# ForgeSaaS

A multi-tenant SaaS template: people sign in, belong to organizations, and work inside one organization at a time.

## Language

**Organization**:
The tenant: the unit that owns data, members, billing and settings. Every piece of user data belongs to exactly one.
_Avoid_: Workspace, team, account, tenant (in user-facing copy)

**Member**:
A user's place in one organization, carrying a role.
_Avoid_: Seat (except when counting billable members on a plan)

**Grant**:
A single permission given to one member on top of their role, such as uploading files or seeing billing. Only a few permissions can be granted; managing members, billing or settings comes with a role.
_Avoid_: Custom role
