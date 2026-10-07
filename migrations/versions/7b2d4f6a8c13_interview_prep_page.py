"""interview_prep_page

Points the UK journey's two interview stages at the student portal's
Interview Preparation page.

- `interview_prep`: each resource that still has no url gets its guide's
  (`/interviews/guides/<slug>`).
- `interview_recording`: `requires_practice` lists the three practice interview
  types the student must finish first, and `allow_text` lets them hand in their
  scores as text.
- Both: the description, but only where it is still the seeded wording.

Only these keys are touched, so anything staff edited in the template builder
stays as they left it.

Revision ID: 7b2d4f6a8c13
Revises: 3c5e7a9b1d24
Create Date: 2026-10-03 12:00:00.000000

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b2d4f6a8c13"
down_revision: str | Sequence[str] | None = "3c5e7a9b1d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UK_JOURNEY_SLUG = "uk-student-journey"

GUIDE_URLS = {
    "Common interview questions": "/interviews/guides/common-questions",
    "Course & university research": "/interviews/guides/course-research",
    "Finance & sponsor explanation": "/interviews/guides/finance-sponsor",
    "Practise with a mock interview": "/interviews/guides/mock-interview",
}
OLD_MOCK_URL = "/interviews"

PRACTICE_INTERVIEW_KEYS = ["pre_cas", "credibility", "academic"]

DESCRIPTIONS = {
    "interview_prep": (
        "Use the resources, prepare your answers and send them to your counsellor.",
        "Read the guides on the Interview Preparation page, then send your counsellor your written answers.",
    ),
    "interview_recording": (
        "Record yourself answering the practice questions and hand in the video.",
        "Complete all three recorded practice interviews, then send your scores to your counsellor.",
    ),
}


def _stages(bind: sa.Connection) -> list[sa.Row]:
    return list(
        bind.execute(
            sa.text(
                """
                SELECT s.id, s.key, s.description, s.config
                FROM workflow_stages s
                JOIN workflow_templates t ON t.id = s.template_id
                WHERE t.slug = :slug AND s.key IN ('interview_prep', 'interview_recording')
                """
            ),
            {"slug": UK_JOURNEY_SLUG},
        )
    )


def _save(bind: sa.Connection, stage_id: object, description: str | None, config: dict) -> None:
    bind.execute(
        sa.text(
            "UPDATE workflow_stages SET description = :description, config = CAST(:config AS jsonb) WHERE id = :id"
        ),
        {"id": stage_id, "description": description, "config": json.dumps(config)},
    )


def upgrade() -> None:
    bind = op.get_bind()
    for row in _stages(bind):
        config = dict(row.config or {})
        old, new = DESCRIPTIONS[row.key]
        description = new if row.description == old else row.description

        if row.key == "interview_prep":
            resources = []
            for resource in config.get("resources") or []:
                resource = dict(resource)
                url = resource.get("url") or ""
                if (not url or url == OLD_MOCK_URL) and resource.get("title") in GUIDE_URLS:
                    resource["url"] = GUIDE_URLS[resource["title"]]
                resources.append(resource)
            config["resources"] = resources
        else:
            config["requires_practice"] = PRACTICE_INTERVIEW_KEYS
            config["allow_text"] = True

        _save(bind, row.id, description, config)


def downgrade() -> None:
    bind = op.get_bind()
    for row in _stages(bind):
        config = dict(row.config or {})
        old, new = DESCRIPTIONS[row.key]
        description = old if row.description == new else row.description

        if row.key == "interview_prep":
            reverse = {url: title for title, url in GUIDE_URLS.items()}
            resources = []
            for resource in config.get("resources") or []:
                resource = dict(resource)
                if resource.get("url") in reverse:
                    resource["url"] = OLD_MOCK_URL if resource["url"].endswith("mock-interview") else ""
                resources.append(resource)
            config["resources"] = resources
        else:
            config.pop("requires_practice", None)
            config["allow_text"] = False

        _save(bind, row.id, description, config)
