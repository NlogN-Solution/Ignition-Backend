#!/usr/bin/env bash
# Copy the catalogue, website content and app configuration from one database
# into a brand-new one — and nothing about people.
#
#   SOURCE_DATABASE_URL=postgresql://...old...  \
#   TARGET_DATABASE_URL=postgresql://...new...  \
#   ./scripts/copy_reference_data.sh
#
# What moves: countries, universities, courses, intakes, routes, course
# profiles, scholarships, guides, blog posts, website pages/blocks/media, and
# the configuration the app runs on (application workflow templates, checklist
# templates, journey milestones, points rules, interview question banks, portal
# fee, currency rates, cost of living, leave types, attendance policy).
#
# What does not: users, students, staff, leads, applications, documents,
# messages, payments, notifications — every table that describes a person or
# something a person did. Departments are left behind too: they name staff.
#
# Three reference rows point at the user who created them (a page's author, a
# media upload's uploader, a workflow template's creator). Those users do not
# exist in the new database, so the columns are cleared on the way in; the
# foreign keys are dropped for the load and put back unchanged afterwards, all
# inside one transaction.
#
# Before running:
#   1. The SOURCE must be at the same migration as the code you deploy
#      (`alembic upgrade head` against it), so its columns match.
#   2. The TARGET must be new and migrated: `alembic upgrade head` against it,
#      and nothing else — no seed. The script refuses a target that already has
#      catalogue rows.
#   3. pg_dump must be the same major version as the server or newer. Neon runs
#      recent Postgres; if your local pg_dump is older, run this script inside
#      a matching image, e.g. `docker run --rm -it -v "$PWD":/w -w /w \
#      -e SOURCE_DATABASE_URL -e TARGET_DATABASE_URL postgres:17 \
#      ./scripts/copy_reference_data.sh`.
#
# The source is only ever read.

set -euo pipefail

# Debian/Ubuntu install each Postgres major side by side under
# /usr/lib/postgresql/<N>/bin, and the plain `pg_dump` on PATH may be an older
# one than the server needs. Put the newest installed client first.
newest_pg_bin=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -n 1 || true)
if [ -n "$newest_pg_bin" ]; then
  export PATH="$newest_pg_bin:$PATH"
fi
echo "==> Using $(pg_dump --version)"

: "${SOURCE_DATABASE_URL:?set SOURCE_DATABASE_URL to the database to copy FROM}"
: "${TARGET_DATABASE_URL:?set TARGET_DATABASE_URL to the new database to copy INTO}"

if [ "$SOURCE_DATABASE_URL" = "$TARGET_DATABASE_URL" ]; then
  echo "SOURCE and TARGET are the same database — refusing." >&2
  exit 1
fi

# Order does not matter here — pg_dump emits table data in foreign-key order.
TABLES=(
  # catalogue
  countries universities university_routes course_profiles programs intakes scholarships
  country_guides blog_posts
  # website CMS
  content_pages content_blocks media_assets
  # application workflow + checklist configuration
  workflow_templates workflow_stages workflow_stage_document_requirements checklist_template_items
  # student journey configuration
  progress_milestones points_rules
  interview_types interview_questions interview_feedback_bands
  portal_access_fees currency_rates country_cost_of_living cost_of_living_categories
  # HR configuration (no people in these)
  leave_types attendance_policies
)

table_args=()
for t in "${TABLES[@]}"; do table_args+=(--table="public.$t"); done

existing=$(psql "$TARGET_DATABASE_URL" -tAc "SELECT count(*) FROM countries" 2>/dev/null) || {
  echo "Could not read the target's countries table. Run 'alembic upgrade head' against the TARGET first." >&2
  exit 1
}
if [ "$existing" != "0" ]; then
  echo "The TARGET already has $existing countries. Use a new, migrated, unseeded database." >&2
  exit 1
fi

dump=$(mktemp)
trap 'rm -f "$dump"' EXIT

echo "==> Dumping reference data from SOURCE (read-only)"
pg_dump "$SOURCE_DATABASE_URL" --data-only --no-owner --no-privileges "${table_args[@]}" > "$dump"

echo "==> Loading into TARGET"
{
  echo "\\set ON_ERROR_STOP on"
  echo "BEGIN;"
  echo "ALTER TABLE content_pages DROP CONSTRAINT fk_content_pages_author_id_users;"
  echo "ALTER TABLE media_assets DROP CONSTRAINT fk_media_assets_uploaded_by_users;"
  echo "ALTER TABLE workflow_templates DROP CONSTRAINT fk_workflow_templates_created_by_users;"
  # A migration inserts a default portal fee into every new database; the
  # source's own fee rows replace it rather than sitting beside it.
  echo "DELETE FROM portal_access_fees;"
  # The dump resets search_path to empty; everything below it must be qualified.
  cat "$dump"
  echo "UPDATE public.content_pages SET author_id = NULL;"
  echo "UPDATE public.media_assets SET uploaded_by = NULL;"
  echo "UPDATE public.workflow_templates SET created_by = NULL;"
  echo "ALTER TABLE public.content_pages ADD CONSTRAINT fk_content_pages_author_id_users FOREIGN KEY (author_id) REFERENCES public.users(id) ON DELETE SET NULL;"
  echo "ALTER TABLE public.media_assets ADD CONSTRAINT fk_media_assets_uploaded_by_users FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE SET NULL;"
  echo "ALTER TABLE public.workflow_templates ADD CONSTRAINT fk_workflow_templates_created_by_users FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;"
  echo "COMMIT;"
} | psql "$TARGET_DATABASE_URL" --quiet

echo "==> Row counts (source -> target)"
for t in "${TABLES[@]}"; do
  s=$(psql "$SOURCE_DATABASE_URL" -tAc "SELECT count(*) FROM public.$t")
  d=$(psql "$TARGET_DATABASE_URL" -tAc "SELECT count(*) FROM public.$t")
  flag=""; [ "$s" != "$d" ] && flag="   <-- MISMATCH"
  printf '  %-38s %6s -> %6s%s\n' "$t" "$s" "$d" "$flag"
done
echo "Done. No users exist in the TARGET yet — create the three Ignition logins with: python -m scripts.seed --users-only"
