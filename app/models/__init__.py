"""SQLAlchemy models.

Alembic's `env.py` imports this module for autogenerate, so every model must be
re-exported from here or it will be silently missing from migrations.

Ported from ED360 with tenancy stripped (rule R1). ED360's `organization`,
`billing`, and `subscription` modules are deliberately absent — Ignition is a
single-tenant platform. `payment.py` keeps `Payment` but drops `Subscription`.
"""

from .academic import BlogPost, Country, CountryGuide, Intake, Program, University
from .application import Application, ApplicationStatusHistory
from .apply_intent import ApplyIntent
from .appointment import Appointment
from .attendance import AttendancePolicy, AttendanceRecord
from .catalogue import CourseProfile, Scholarship, UniversityRoute
from .communication import (
    MessageAttachment,
    MessageAttachmentKind,
    MessageThread,
    ThreadMessage,
    ThreadVisibility,
)
from .content import ContentBlock, ContentPage, MediaAsset
from .department import Department, EmployeeEmploymentEvent
from .document import ApplicationDocument, Document, StudentEnglishTest
from .eligibility import EligibilityAssessment
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
from .milestone import ApplicationMilestone, MilestoneKind
from .notification import Notification
from .payment import Payment, PortalAccessFee
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
    "ActivityLog",
    "Application",
    "ApplicationChecklistItem",
    "ApplicationDocument",
    "ApplicationStatusHistory",
    "ApplicationWorkflow",
    "ApplicationWorkflowStep",
    "Appointment",
    "AttendancePolicy",
    "AttendanceRecord",
    "BlogPost",
    "BudgetCategory",
    "ChecklistTemplateItem",
    "ContentBlock",
    "ContentPage",
    "CostOfLivingCategory",
    "Country",
    "CountryCostOfLiving",
    "CountryGuide",
    "CourseProfile",
    "CurrencyRate",
    "Department",
    "DepartureChecklistItem",
    "Document",
    "EmployeeEmploymentEvent",
    "EmployeeProfile",
    "Intake",
    "InterviewAnswer",
    "InterviewFeedbackBand",
    "InterviewQuestion",
    "InterviewSession",
    "InterviewType",
    "EligibilityAssessment",
    "Lead",
    "LeadActivity",
    "LeadFollowUp",
    "LeaveRequest",
    "LeaveType",
    "LoanDisbursement",
    "LoanDocument",
    "MediaAsset",
    "ApplicationMilestone",
    "ApplyIntent",
    "Message",
    "MessageAttachment",
    "MessageAttachmentKind",
    "MessageThread",
    "MilestoneKind",
    "ThreadMessage",
    "ThreadVisibility",
    "Notification",
    "Payment",
    "PayrollRun",
    "Payslip",
    "PayslipLineItem",
    "PointsRule",
    "PortalAccessFee",
    "Program",
    "ProgressMilestone",
    "SalaryStructure",
    "Scholarship",
    "StudentBudget",
    "StudentChecklistItem",
    "StudentCompareCourse",
    "StudentDashboardSettings",
    "StudentEducationHistory",
    "StudentEnglishTest",
    "StudentFundingSource",
    "StudentLoan",
    "StudentMilestone",
    "StudentPointsLedger",
    "StudentProfile",
    "StudentSavedCourse",
    "StudentSavedUniversity",
    "StudentSavingsGoal",
    "StudentWorkExperience",
    "Task",
    "University",
    "UniversityRoute",
    "User",
    "UserSession",
    "VisaAppointment",
    "VisaCase",
    "VisaDocumentRequirement",
    "VisaFee",
    "VisaStage",
    "WorkflowStage",
    "WorkflowStageDocumentRequirement",
    "WorkflowStepActivity",
    "WorkflowTemplate",
]
