"""SQLAlchemy models.

Alembic's `env.py` imports this module for autogenerate, so every model must be
re-exported from here or it will be silently missing from migrations.

Ported from ED360 with tenancy stripped (rule R1). ED360's `organization`,
`billing`, and `subscription` modules are deliberately absent — Ignition is a
single-tenant platform. `payment.py` keeps `Payment` but drops `Subscription`.
"""

from .academic import BlogPost, Country, CountryGuide, Intake, Program, University
from .application import Application, ApplicationStatusHistory
from .appointment import Appointment
from .attendance import AttendancePolicy, AttendanceRecord
from .department import Department, EmployeeEmploymentEvent
from .document import ApplicationDocument, Document, StudentEnglishTest
from .finance import (
    BudgetCategory,
    CostOfLivingCategory,
    CountryCostOfLiving,
    CurrencyRate,
    LoanDisbursement,
    LoanDocument,
    StudentBudget,
    StudentFundingSource,
    StudentLoan,
    StudentSavingsGoal,
)
from .interview import (
    InterviewAnswer,
    InterviewFeedbackBand,
    InterviewQuestion,
    InterviewSession,
    InterviewType,
)
from .lead import Lead, LeadActivity, LeadFollowUp
from .leave import LeaveRequest, LeaveType
from .message import Message
from .notification import Notification
from .payment import Payment
from .payroll import PayrollRun, Payslip, PayslipLineItem, SalaryStructure
from .student_checklist import ChecklistTemplateItem, StudentChecklistItem
from .student_history import StudentEducationHistory, StudentWorkExperience
from .student_portal import StudentCompareCourse, StudentSavedCourse, StudentSavedUniversity
from .student_preferences import StudentDashboardSettings
from .student_progress import (
    PointsRule,
    ProgressMilestone,
    StudentMilestone,
    StudentPointsLedger,
)
from .system import ActivityLog, UserSession
from .task import Task
from .user import EmployeeProfile, StudentProfile, User
from .visa import (
    DepartureChecklistItem,
    VisaAppointment,
    VisaCase,
    VisaDocumentRequirement,
    VisaFee,
    VisaStage,
)
from .workflow import (
    ApplicationChecklistItem,
    ApplicationWorkflow,
    ApplicationWorkflowStep,
    WorkflowStage,
    WorkflowStageDocumentRequirement,
    WorkflowStepActivity,
    WorkflowTemplate,
)

__all__ = [
    # Identity
    "User",
    "StudentProfile",
    "StudentEducationHistory",
    "StudentWorkExperience",
    "EmployeeProfile",
    "Department",
    "EmployeeEmploymentEvent",
    # HR
    "AttendancePolicy",
    "AttendanceRecord",
    "LeaveType",
    "LeaveRequest",
    "SalaryStructure",
    "PayrollRun",
    "Payslip",
    "PayslipLineItem",
    # CRM
    "Lead",
    "LeadActivity",
    "LeadFollowUp",
    # Catalog
    "Country",
    "University",
    "Program",
    "Intake",
    "CountryGuide",
    "BlogPost",
    # Education domain
    "Document",
    "ApplicationDocument",
    "StudentEnglishTest",
    "Application",
    "ApplicationStatusHistory",
    "Appointment",
    "Task",
    "Payment",
    "Notification",
    "Message",
    # Workflow engine
    "WorkflowTemplate",
    "WorkflowStage",
    "WorkflowStageDocumentRequirement",
    "ApplicationWorkflow",
    "ApplicationWorkflowStep",
    "WorkflowStepActivity",
    "ApplicationChecklistItem",
    # Student portal (Phase 4)
    "StudentSavedCourse",
    "StudentSavedUniversity",
    "StudentCompareCourse",
    # Phase 6 — progress and points
    "ProgressMilestone",
    "StudentMilestone",
    "PointsRule",
    "StudentPointsLedger",
    # Phase 6 — journey checklist
    "ChecklistTemplateItem",
    "StudentChecklistItem",
    # Phase 6 — interviews
    "InterviewType",
    "InterviewQuestion",
    "InterviewFeedbackBand",
    "InterviewSession",
    "InterviewAnswer",
    # Phase 6 — visa and pre-departure
    "VisaCase",
    "VisaStage",
    "VisaAppointment",
    "VisaDocumentRequirement",
    "VisaFee",
    "DepartureChecklistItem",
    # Phase 6 — finance
    "StudentFundingSource",
    "StudentLoan",
    "LoanDocument",
    "LoanDisbursement",
    "StudentBudget",
    "BudgetCategory",
    "StudentSavingsGoal",
    "CountryCostOfLiving",
    "CostOfLivingCategory",
    "CurrencyRate",
    # Phase 6 — dashboard preferences
    "StudentDashboardSettings",
    # Audit
    "UserSession",
    "ActivityLog",
]
