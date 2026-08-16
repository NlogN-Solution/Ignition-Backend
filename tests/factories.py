"""Object factories for tests.

Empty until Phase 1 lands the models. The intended surface, so tests written in
Phase 1 onward have a consistent vocabulary:

    async def user_factory(session, *, role=UserRole.STAFF, **overrides) -> User
    async def student_factory(session, **overrides) -> User            # role=STUDENT + StudentProfile
    async def application_factory(session, *, student, **overrides) -> Application
    async def document_factory(session, *, student, **overrides) -> Document
    def auth_headers(user) -> dict[str, str]                            # {"Authorization": "Bearer ..."}

Note what is deliberately absent: ED360's equivalents take an `organization`
argument. Ignition is single-tenant, so no factory here accepts one.
"""

__all__: list[str] = []
