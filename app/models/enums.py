from enum import Enum

# Ported from ED360. Strip rule R10 removed six multi-tenancy enums that have no
# single-tenant meaning: OrganizationStatus, OrgSubscriptionPlan,
# OrgSubscriptionStatus, BillingCycle, and the legacy per-user SubscriptionPlan /
# SubscriptionStatus. Everything else carries over unchanged.


class UserRole(str, Enum):
    STUDENT = "student"
    COUNSELLOR = "counsellor"
    FRONTDESK = "frontdesk"
    STAFF = "staff"
    FINANCE = "finance"
    MARKETING = "marketing"
    SUPPORT = "support"
    ADMISSIONS = "admissions"
    MANAGER = "manager"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"
    VIEWER = "viewer"


#: Every role except STUDENT. Students use the student portal and must never
#: reach a staff endpoint — see `require_staff` in app/api/auth.py.
STAFF_ROLES: frozenset[UserRole] = frozenset(role for role in UserRole if role is not UserRole.STUDENT)


class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class UserStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class InstitutionType(str, Enum):
    BACHELOR = "bachelor"
    DIPLOMA = "diploma"
    _10_2 = "10+2"
    MASTERS = "masters"
    #: Added for Phase 7 — the student portal's self-registration wizard
    #: offers "PhD" and "Other" alongside the four ED360 counsellors already
    #: used, and a PhD or non-standard applicant is a real signup, not an
    #: edge case worth rejecting.
    PHD = "phd"
    OTHER = "other"


class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    FOLLOW_UP = "follow_up"
    QUALIFIED = "qualified"
    CONVERTED = "converted"
    LOST = "lost"


class LeadSource(str, Enum):
    WEBSITE = "website"
    WALK_IN = "walk_in"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    GOOGLE = "google"
    TIKTOK = "tiktok"
    LINKEDIN = "linkedin"
    WHATSAPP = "whatsapp"
    PHONE = "phone"
    REFERRAL = "referral"
    EVENT = "event"
    OTHER = "other"


class DocumentType(str, Enum):
    PASSPORT = "passport"
    CITIZENSHIP = "citizenship"
    NATIONAL_ID = "national_id"
    ACADEMIC_TRANSCRIPT = "academic_transcript"
    ACADEMIC_CERTIFICATE = "academic_certificate"
    PROVISIONAL_CERTIFICATE = "provisional_certificate"
    CHARACTER_CERTIFICATE = "character_certificate"
    ENGLISH_TEST = "english_test"
    CV = "cv"
    STATEMENT_OF_PURPOSE = "statement_of_purpose"
    RECOMMENDATION_LETTER = "recommendation_letter"
    OFFER_LETTER = "offer_letter"
    #: Confirmation of Acceptance for Studies. Issued by the university after the
    #: offer is accepted, and the one document a UK Student visa cannot be
    #: applied for without — it has an `ApplicationStatus` of its own
    #: (`cas_received`) and needed a document type to match.
    CAS_LETTER = "cas_letter"
    VISA = "visa"
    FINANCIAL_DOCUMENT = "financial_document"
    MEDICAL_REPORT = "medical_report"
    PHOTO = "photo"
    OTHER = "other"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class LeadActivityType(str, Enum):
    LEAD_CREATED = "lead_created"
    CALL = "call"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    MEETING = "meeting"
    NOTE = "note"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    FOLLOW_UP = "follow_up"
    DOCUMENT_RECEIVED = "document_received"
    CONVERTED = "converted"
    LOST = "lost"


class ApplicationStatus(str, Enum):
    DRAFT = "draft"
    DOCUMENTS_PENDING = "documents_pending"
    READY_TO_SUBMIT = "ready_to_submit"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    OFFER_RECEIVED = "offer_received"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    #: Confirmation of Acceptance for Studies. A UK-specific step, and not an
    #: optional one: a student cannot apply for a Student visa without a CAS
    #: number, so every application that reaches a visa has passed through here.
    #: It sits between accepting the offer and applying for the visa because
    #: that is the order it happens in — the university issues the CAS after
    #: the offer is accepted and the deposit is settled.
    CAS_RECEIVED = "cas_received"
    VISA_PROCESSING = "visa_processing"
    VISA_APPROVED = "visa_approved"
    VISA_REJECTED = "visa_rejected"
    ENROLLED = "enrolled"
    WITHDRAWN = "withdrawn"
    REJECTED = "rejected"


class OfferType(str, Enum):
    """What kind of offer the university made.

    A conditional offer is not a place — it is a place *if* the student meets
    the remaining conditions — and a student reading "Offer received" with no
    qualifier will reasonably assume the former. Recorded alongside the offer
    date so the portal can say which.
    """

    CONDITIONAL = "conditional"
    UNCONDITIONAL = "unconditional"
    OTHER = "other"


class AppointmentStatus(str, Enum):
    REQUESTED = "requested"
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"


class AppointmentType(str, Enum):
    CONSULTATION = "consultation"
    DOCUMENT_REVIEW = "document_review"
    APPLICATION_REVIEW = "application_review"
    VISA_CONSULTATION = "visa_consultation"
    FOLLOW_UP = "follow_up"
    PHONE_CALL = "phone_call"
    VIDEO_CALL = "video_call"
    OFFICE_VISIT = "office_visit"
    OTHER = "other"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    FOLLOW_UP = "follow_up"
    DOCUMENT_COLLECTION = "document_collection"
    DOCUMENT_VERIFICATION = "document_verification"
    APPLICATION_SUBMISSION = "application_submission"
    VISA_PROCESSING = "visa_processing"
    PAYMENT_FOLLOW_UP = "payment_follow_up"
    APPOINTMENT = "appointment"
    CALL = "call"
    EMAIL = "email"
    OTHER = "other"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class PaymentPurpose(str, Enum):
    """What a payment was for.

    Ignition charges a one-time fee for access to the student portal, set per
    country (see `PortalAccessFee`). That is a different kind of money from an
    application fee or a tuition deposit, and access is granted by the presence
    of a completed `PORTAL_ACCESS` payment — so the distinction has to be a
    column, not a convention in `remarks`.
    """

    PORTAL_ACCESS = "portal_access"
    APPLICATION_FEE = "application_fee"
    TUITION_DEPOSIT = "tuition_deposit"
    OTHER = "other"


class PaymentMethod(str, Enum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    ESewa = "esewa"  # noqa: N815 - value is the contract; ED360 ships this casing
    KHALTI = "khalti"
    FONEPAY = "fonepay"
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    OTHER = "other"


class NotificationType(str, Enum):
    SYSTEM = "system"
    APPLICATION = "application"
    PAYMENT = "payment"
    APPOINTMENT = "appointment"
    TASK = "task"
    DOCUMENT = "document"
    LEAD = "lead"
    MESSAGE = "message"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"


class ActivityType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    ASSIGN = "assign"
    STATUS_CHANGE = "status_change"
    PAYMENT = "payment"
    #: The "log in as" feature that emitted this has been removed. Kept so
    #: existing activity_log rows with this value still deserialize.
    IMPERSONATE = "impersonate"
    OTHER = "other"


class DegreeLevel(str, Enum):
    CERTIFICATE = "certificate"
    DIPLOMA = "diploma"
    ADVANCED_DIPLOMA = "advanced_diploma"
    BACHELOR = "bachelor"
    POSTGRADUATE_DIPLOMA = "postgraduate_diploma"
    MASTER = "master"
    DOCTORATE = "doctorate"


class EnglishTestType(str, Enum):
    IELTS = "ielts"
    PTE = "pte"
    TOEFL = "toefl"
    DUOLINGO = "duolingo"
    OTHER = "other"


class WorkflowStepStatus(str, Enum):
    PENDING = "pending"
    CURRENT = "current"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class WorkflowActivityType(str, Enum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    NOTE_ADDED = "note_added"
    ASSIGNED = "assigned"
    DOCUMENT_LINKED = "document_linked"
    COMMENT = "comment"


class ApplicationWorkflowStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ChecklistItemStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    REJECTED = "rejected"
    WAIVED = "waived"


class LeadPriority(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class FollowUpMethod(str, Enum):
    PHONE_CALL = "phone_call"
    WHATSAPP = "whatsapp"
    EMAIL = "email"
    SMS = "sms"
    IN_PERSON = "in_person"
    VIDEO_MEETING = "video_meeting"


class FollowUpOutcome(str, Enum):
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    CALL_BACK_LATER = "call_back_later"
    INTERESTED = "interested"
    VERY_INTERESTED = "very_interested"
    DOCUMENTS_PENDING = "documents_pending"
    THINKING = "thinking"
    WRONG_NUMBER = "wrong_number"
    NOT_ELIGIBLE = "not_eligible"
    DUPLICATE = "duplicate"
    CONVERTED_TO_PROSPECT = "converted_to_prospect"
    CONVERTED_TO_CLIENT = "converted_to_client"


class LostReason(str, Enum):
    NO_RESPONSE_AFTER_7_ATTEMPTS = "no_response_after_7_attempts"
    NOT_INTERESTED = "not_interested"
    BUDGET_ISSUE = "budget_issue"
    CHOSE_ANOTHER_CONSULTANCY = "chose_another_consultancy"
    WRONG_NUMBER = "wrong_number"
    INVALID_LEAD = "invalid_lead"
    NOT_ELIGIBLE = "not_eligible"
    DUPLICATE_LEAD = "duplicate_lead"
    OTHER = "other"


class ConversionSource(str, Enum):
    AGREEMENT_SIGNED = "agreement_signed"
    SERVICE_PURCHASED = "service_purchased"
    REGISTRATION_COMPLETED = "registration_completed"
    PAYMENT_RECEIVED = "payment_received"
    OTHER = "other"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERN = "intern"
    FREELANCE = "freelance"


class EmploymentEventType(str, Enum):
    JOINED = "joined"
    PROMOTED = "promoted"
    DEPARTMENT_CHANGED = "department_changed"
    MANAGER_CHANGED = "manager_changed"
    DESIGNATION_CHANGED = "designation_changed"
    STATUS_CHANGED = "status_changed"
    PROBATION_COMPLETED = "probation_completed"
    CONTRACT_RENEWED = "contract_renewed"
    SALARY_CHANGED = "salary_changed"
    OTHER = "other"


class AttendanceStatus(str, Enum):
    """Only PRESENT/LATE/HALF_DAY/ON_LEAVE/HOLIDAY are ever written to a row by
    application code. ABSENT and WEEKEND are computed at query time (nobody has
    a row for that day) and only exist here so the same enum can describe a
    computed cell in the dashboard/summary response."""

    PRESENT = "present"
    LATE = "late"
    HALF_DAY = "half_day"
    ON_LEAVE = "on_leave"
    HOLIDAY = "holiday"
    ABSENT = "absent"
    WEEKEND = "weekend"


class AttendanceSource(str, Enum):
    WEB = "web"
    MANUAL = "manual"


class LeaveStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PayrollRunStatus(str, Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"
    PAID = "paid"


class PayslipLineType(str, Enum):
    ADDITION = "addition"
    DEDUCTION = "deduction"


class InterviewSessionStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class VisaCaseStatus(str, Enum):
    PREPARING = "preparing"
    LODGED = "lodged"
    APPROVED = "approved"
    REFUSED = "refused"


class VisaStageState(str, Enum):
    COMPLETED = "completed"
    CURRENT = "current"
    UPCOMING = "upcoming"


class VisaAppointmentStatus(str, Enum):
    NOT_BOOKED = "not_booked"
    BOOKED = "booked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class VisaDocumentRequirementStatus(str, Enum):
    MISSING = "missing"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class FundingSourceStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    AVAILABLE = "available"


class FundingVerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"


class LoanProcessingStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    PARTIALLY_DISBURSED = "partially_disbursed"
    FULLY_DISBURSED = "fully_disbursed"
    REJECTED = "rejected"


class LoanDocumentStatus(str, Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    VERIFIED = "verified"


class LoanDisbursementStatus(str, Enum):
    SCHEDULED = "scheduled"
    RELEASED = "released"


class SavingsGoalKind(str, Enum):
    SAVINGS = "savings"
    EMERGENCY_FUND = "emergency_fund"


# --- Public catalogue and CMS (CATALOGUE-CMS-PLAN.md) -----------------------
# The four catalogue enums mirror the public site's controlled vocabularies
# verbatim, em dashes and ampersands included: `Ignition-Landing`'s facets are
# closed const tuples (`data/universities/types.ts`, `data/courses/types.ts`)
# and the two must not drift. The values are therefore the display strings the
# site already renders, not snake_case keys.


class UkRegion(str, Enum):
    ENGLAND_NORTH = "England — North"
    ENGLAND_MIDLANDS = "England — Midlands"
    ENGLAND_SOUTH = "England — South"
    SCOTLAND = "Scotland"
    WALES = "Wales"
    NORTHERN_IRELAND = "Northern Ireland"


class CourseSubject(str, Enum):
    COMPUTING = "Computing"
    ENGINEERING = "Engineering"
    HEALTH = "Health"
    SCIENCES = "Sciences"
    BUSINESS = "Business"
    LAW = "Law"
    ARTS_DESIGN = "Arts & Design"
    SOCIAL_SCIENCES = "Social Sciences"
    EDUCATION = "Education"
    HUMANITIES = "Humanities"


class CourseLevel(str, Enum):
    """What a course *is*.

    Distinct from `DegreeLevel`, which is what an application is made at. Both
    are load-bearing and they are not merged: `DegreeLevel` is consumed by
    applications and the student portal, `CourseLevel` by the public
    catalogue's facets.
    """

    FOUNDATION = "Foundation"
    UNDERGRADUATE = "Undergraduate"
    TOP_UP = "Top-Up"
    INTEGRATED_MASTERS = "Integrated Masters"
    POSTGRADUATE = "Postgraduate"


class EntryRoute(str, Enum):
    """A column of a university's entry-criteria matrix.

    Unlike the three enums above these are keys rather than display strings:
    the label a university actually prints ("BNurs(Adult Nursing)",
    "Enhanced Extended Masters") is kept verbatim on
    `university_routes.label`, because 44 institutions spell the same route ten
    different ways and staff recognise their own wording.
    """

    UNDERGRADUATE = "undergraduate"
    INTERNATIONAL_YEAR_ONE = "international_year_one"
    INTERNATIONAL_FOUNDATION_YEAR = "international_foundation_year"
    PRE_MASTERS = "pre_masters"
    POSTGRADUATE = "postgraduate"
    TOP_UP = "top_up"
    EXTENDED_MASTERS = "extended_masters"
    MRES = "mres"
    DBA = "dba"
    NURSING = "nursing"


class ContentKind(str, Enum):
    PAGE = "page"
    GUIDE = "guide"
    POST = "post"
    FRAGMENT = "fragment"


class BlockType(str, Enum):
    """A typed content block, each rendering through a component the public
    site already has. `COMPONENT` is the escape hatch for interactive slots,
    and its allowlist lives in the landing rather than the database so no
    editor can name an arbitrary component.
    """

    PROSE = "prose"
    CARDS = "cards"
    TIMELINE = "timeline"
    CHECKLIST = "checklist"
    FAQ = "faq"
    CALLOUT = "callout"
    STATS = "stats"
    LIST = "list"
    CTA = "cta"
    COMPONENT = "component"


#: Every Postgres ENUM this schema needs, as (python_enum, type_name) pairs.
#: `enum_type(..., create_type=False)` means table DDL does NOT emit CREATE TYPE,
#: and `alembic revision --autogenerate` will not write them either — the initial
#: migration iterates this list to create them. Adding an enum above without
#: adding it here means a migration that fails on a clean database.
PG_ENUMS: list[tuple[type[Enum], str]] = [
    (ActivityType, "activity_type"),
    (ApplicationStatus, "application_status"),
    (ApplicationWorkflowStatus, "application_workflow_status"),
    (AppointmentStatus, "appointment_status"),
    (AppointmentType, "appointment_type"),
    (AttendanceSource, "attendance_source"),
    (BlockType, "content_block_type"),
    (AttendanceStatus, "attendance_status"),
    (ChecklistItemStatus, "checklist_item_status"),
    (ContentKind, "content_kind"),
    (CourseLevel, "course_level"),
    (CourseSubject, "course_subject"),
    (ConversionSource, "conversion_source"),
    (DegreeLevel, "degree_level"),
    (DocumentStatus, "document_status"),
    (DocumentType, "document_type"),
    (EmploymentEventType, "employment_event_type"),
    (EmploymentType, "employment_type"),
    (EnglishTestType, "english_test_type"),
    (EntryRoute, "entry_route"),
    (FollowUpMethod, "follow_up_method"),
    (FollowUpOutcome, "follow_up_outcome"),
    (FundingSourceStatus, "funding_source_status"),
    (FundingVerificationStatus, "funding_verification_status"),
    (InstitutionType, "institution_type"),
    (InterviewSessionStatus, "interview_session_status"),
    (LeadActivityType, "lead_activity_type"),
    (LeadPriority, "lead_priority"),
    (LeadSource, "lead_source"),
    (LeadStatus, "lead_status"),
    (LeaveStatus, "leave_status"),
    (LoanDisbursementStatus, "loan_disbursement_status"),
    (LoanDocumentStatus, "loan_document_status"),
    (LoanProcessingStatus, "loan_processing_status"),
    (LostReason, "lost_reason"),
    (NotificationChannel, "notification_channel"),
    (NotificationType, "notification_type"),
    (PaymentMethod, "payment_method"),
    (PaymentStatus, "payment_status"),
    (PayrollRunStatus, "payroll_run_status"),
    (PayslipLineType, "payslip_line_type"),
    (SavingsGoalKind, "savings_goal_kind"),
    (TaskPriority, "task_priority"),
    (TaskStatus, "task_status"),
    (TaskType, "task_type"),
    (UserRole, "user_role"),
    (UserStatus, "user_status"),
    (UkRegion, "uk_region"),
    (VisaAppointmentStatus, "visa_appointment_status"),
    (VisaCaseStatus, "visa_case_status"),
    (VisaDocumentRequirementStatus, "visa_document_requirement_status"),
    (VisaStageState, "visa_stage_state"),
    (WorkflowActivityType, "workflow_activity_type"),
    (WorkflowStepStatus, "workflow_step_status"),
]

class EligibilityIndicator(str, Enum):
    """One dimension of a preliminary eligibility assessment.

    Deliberately three-valued and deliberately hedged. This is a *preliminary*
    read on the information a student typed into a public form — nobody has
    verified a transcript, and no university has been asked. `LIKELY_MEETS` is
    the strongest thing it can say, and it means "worth a counsellor's time",
    never "eligible".

    `INSUFFICIENT_INFORMATION` is a real answer, not a failure: a student who
    has not taken an English test yet is not ineligible, they are early.
    """

    LIKELY_MEETS = "likely_meets"
    NEEDS_REVIEW = "needs_review"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class EligibilityOverall(str, Enum):
    """What the whole assessment adds up to.

    Only ever a routing decision — which queue this lands in and how quickly
    someone should call. The counsellor makes the actual recommendation, which
    is why there is no `NOT_ELIGIBLE` here: a public form cannot establish it,
    and a student reading it would take it as final.
    """

    PRELIMINARY_LIKELY_ELIGIBLE = "preliminary_likely_eligible"
    NEEDS_COUNSELLOR_REVIEW = "needs_counsellor_review"
    MORE_INFORMATION_REQUIRED = "more_information_required"


class DocumentReadiness(str, Enum):
    """How ready one class of document is, as the student reports it."""

    READY = "ready"
    IN_PROGRESS = "in_progress"
    NOT_AVAILABLE = "not_available"
    NOT_SURE = "not_sure"


class FundingSource(str, Enum):
    FAMILY = "family"
    LOAN = "loan"
    SCHOLARSHIP = "scholarship"
    SELF = "self"
    COMBINATION = "combination"


class EnglishEvidence(str, Enum):
    """How a student intends to satisfy the English requirement.

    Wider than `EnglishTestType` because two of these are not tests: a medium
    of instruction letter is documentary evidence, and "not taken yet" is a
    state most students are in when they first ask.
    """

    IELTS = "ielts"
    PTE = "pte"
    TOEFL = "toefl"
    OTHER_TEST = "other_test"
    MOI = "moi"
    NOT_TAKEN = "not_taken"


class ContactMethod(str, Enum):
    PHONE = "phone"
    WHATSAPP = "whatsapp"
    EMAIL = "email"

