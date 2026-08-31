"""Admin CRUD for the public catalogue (CATALOGUE-CMS-PLAN.md Phase 2).

Mounted without a prefix, like `routes/academic.py`, so the admin console calls
`/university-routes`, `/course-profiles`, `/scholarships` directly.

**Reads here are staff reads, not public ones.** The unauthenticated surface the
public site consumes is a separate router (`routes/public.py`, Phase 3) serving
published records only. Keeping them apart is what lets an editor see a
half-written university that no visitor can reach.

Writes are open to marketing as well as admins: `MODULE_ROLES` in the admin
console mirrors these sets, and the website module is explicitly a marketing
surface. Super_admin passes everywhere by way of `require_role`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import get_current_user, require_role
from ..api.exceptions import NotFoundException
from ..models.enums import UserRole
from ..schemas.catalogue import (
    CourseProfileCreate,
    CourseProfileList,
    CourseProfileRead,
    CourseProfileUpdate,
    ScholarshipCreate,
    ScholarshipList,
    ScholarshipRead,
    ScholarshipUpdate,
    UniversityRouteCreate,
    UniversityRouteList,
    UniversityRouteRead,
    UniversityRouteUpdate,
)
from ..services.catalogue_service import (
    CourseProfileService,
    ScholarshipService,
    UniversityRouteService,
    get_course_profile_service,
    get_scholarship_service,
    get_university_route_service,
)

router = APIRouter(tags=["Catalogue"])

#: Editing the public catalogue is a marketing job as much as an admin one.
_MANAGE_WEBSITE = require_role(UserRole.ADMIN, UserRole.MARKETING)


# --- University routes (the entry-criteria matrix) ---------------------------


@router.get("/university-routes", response_model=UniversityRouteList, summary="List entry routes")
async def list_university_routes(
    page: int = 1,
    limit: int = 20,
    university_id: UUID | None = None,
    route_key: str | None = None,
    is_published: bool | None = None,
    service: UniversityRouteService = Depends(get_university_route_service),
    _: object = Depends(get_current_user),
) -> UniversityRouteList:
    items, total = await service.list(
        page, limit, university_id=university_id, route_key=route_key, is_published=is_published
    )
    return UniversityRouteList(items=items, total=total, page=page, limit=limit)


@router.get("/university-routes/{route_id}", response_model=UniversityRouteRead, summary="Get entry route")
async def get_university_route(
    route_id: UUID,
    service: UniversityRouteService = Depends(get_university_route_service),
    _: object = Depends(get_current_user),
) -> UniversityRouteRead:
    route = await service.get(route_id)
    if route is None:
        raise NotFoundException("Entry route not found")
    return UniversityRouteRead.model_validate(route)


@router.post("/university-routes", response_model=UniversityRouteRead, summary="Create entry route")
async def create_university_route(
    payload: UniversityRouteCreate,
    service: UniversityRouteService = Depends(get_university_route_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> UniversityRouteRead:
    return UniversityRouteRead.model_validate(await service.create(payload.model_dump()))


@router.patch("/university-routes/{route_id}", response_model=UniversityRouteRead, summary="Update entry route")
async def update_university_route(
    route_id: UUID,
    payload: UniversityRouteUpdate,
    service: UniversityRouteService = Depends(get_university_route_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> UniversityRouteRead:
    route = await service.get(route_id)
    if route is None:
        raise NotFoundException("Entry route not found")
    return UniversityRouteRead.model_validate(await service.update(route, payload.model_dump(exclude_unset=True)))


@router.delete("/university-routes/{route_id}", response_model=UniversityRouteRead, summary="Delete entry route")
async def delete_university_route(
    route_id: UUID,
    service: UniversityRouteService = Depends(get_university_route_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> UniversityRouteRead:
    route = await service.get(route_id)
    if route is None:
        raise NotFoundException("Entry route not found")
    return UniversityRouteRead.model_validate(await service.delete(route))


# --- Course profiles (the editorial layer) -----------------------------------


@router.get("/course-profiles", response_model=CourseProfileList, summary="List course profiles")
async def list_course_profiles(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    subject: str | None = None,
    course_level: str | None = None,
    is_published: bool | None = None,
    service: CourseProfileService = Depends(get_course_profile_service),
    _: object = Depends(get_current_user),
) -> CourseProfileList:
    items, total = await service.list(
        page, limit, search=search, subject=subject, course_level=course_level, is_published=is_published
    )
    return CourseProfileList(items=items, total=total, page=page, limit=limit)


@router.get("/course-profiles/{profile_id}", response_model=CourseProfileRead, summary="Get course profile")
async def get_course_profile(
    profile_id: UUID,
    service: CourseProfileService = Depends(get_course_profile_service),
    _: object = Depends(get_current_user),
) -> CourseProfileRead:
    profile = await service.get(profile_id)
    if profile is None:
        raise NotFoundException("Course profile not found")
    return CourseProfileRead.model_validate(profile)


@router.post("/course-profiles", response_model=CourseProfileRead, summary="Create course profile")
async def create_course_profile(
    payload: CourseProfileCreate,
    service: CourseProfileService = Depends(get_course_profile_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> CourseProfileRead:
    return CourseProfileRead.model_validate(await service.create(payload.model_dump()))


@router.patch("/course-profiles/{profile_id}", response_model=CourseProfileRead, summary="Update course profile")
async def update_course_profile(
    profile_id: UUID,
    payload: CourseProfileUpdate,
    service: CourseProfileService = Depends(get_course_profile_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> CourseProfileRead:
    profile = await service.get(profile_id)
    if profile is None:
        raise NotFoundException("Course profile not found")
    return CourseProfileRead.model_validate(await service.update(profile, payload.model_dump(exclude_unset=True)))


@router.delete("/course-profiles/{profile_id}", response_model=CourseProfileRead, summary="Delete course profile")
async def delete_course_profile(
    profile_id: UUID,
    service: CourseProfileService = Depends(get_course_profile_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> CourseProfileRead:
    profile = await service.get(profile_id)
    if profile is None:
        raise NotFoundException("Course profile not found")
    return CourseProfileRead.model_validate(await service.delete(profile))


# --- Scholarships ------------------------------------------------------------


@router.get("/scholarships", response_model=ScholarshipList, summary="List scholarships")
async def list_scholarships(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    university_id: UUID | None = None,
    kind: str | None = None,
    is_published: bool | None = None,
    service: ScholarshipService = Depends(get_scholarship_service),
    _: object = Depends(get_current_user),
) -> ScholarshipList:
    items, total = await service.list(
        page, limit, search=search, university_id=university_id, kind=kind, is_published=is_published
    )
    return ScholarshipList(items=items, total=total, page=page, limit=limit)


@router.get("/scholarships/{scholarship_id}", response_model=ScholarshipRead, summary="Get scholarship")
async def get_scholarship(
    scholarship_id: UUID,
    service: ScholarshipService = Depends(get_scholarship_service),
    _: object = Depends(get_current_user),
) -> ScholarshipRead:
    scholarship = await service.get(scholarship_id)
    if scholarship is None:
        raise NotFoundException("Scholarship not found")
    return ScholarshipRead.model_validate(scholarship)


@router.post("/scholarships", response_model=ScholarshipRead, summary="Create scholarship")
async def create_scholarship(
    payload: ScholarshipCreate,
    service: ScholarshipService = Depends(get_scholarship_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ScholarshipRead:
    return ScholarshipRead.model_validate(await service.create(payload.model_dump()))


@router.patch("/scholarships/{scholarship_id}", response_model=ScholarshipRead, summary="Update scholarship")
async def update_scholarship(
    scholarship_id: UUID,
    payload: ScholarshipUpdate,
    service: ScholarshipService = Depends(get_scholarship_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ScholarshipRead:
    scholarship = await service.get(scholarship_id)
    if scholarship is None:
        raise NotFoundException("Scholarship not found")
    return ScholarshipRead.model_validate(await service.update(scholarship, payload.model_dump(exclude_unset=True)))


@router.delete("/scholarships/{scholarship_id}", response_model=ScholarshipRead, summary="Delete scholarship")
async def delete_scholarship(
    scholarship_id: UUID,
    service: ScholarshipService = Depends(get_scholarship_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ScholarshipRead:
    scholarship = await service.get(scholarship_id)
    if scholarship is None:
        raise NotFoundException("Scholarship not found")
    return ScholarshipRead.model_validate(await service.delete(scholarship))
