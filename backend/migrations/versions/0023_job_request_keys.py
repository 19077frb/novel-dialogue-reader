"""Keep all admitted request keys associated with their original task."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_request_keys",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=True),
    )
    op.create_index("ix_job_request_keys_job_id", "job_request_keys", ["job_id"])
    op.execute("""
        INSERT INTO job_request_keys (idempotency_key, job_id, request_digest)
        SELECT idempotency_key, id, request_digest FROM jobs WHERE idempotency_key IS NOT NULL
    """)
    # Keep the old Job column compatible with callers that insert Jobs directly.
    # Plain INSERT deliberately rejects any attempt to take an existing alias,
    # including another request for the same job with a different digest.
    op.execute("""
        CREATE TRIGGER jobs_register_request_key_insert AFTER INSERT ON jobs
        WHEN NEW.idempotency_key IS NOT NULL
        BEGIN
            INSERT INTO job_request_keys (idempotency_key, job_id, request_digest)
            VALUES (NEW.idempotency_key, NEW.id, NEW.request_digest);
        END
    """)
    # Existing keys remain receipts when the legacy column changes. A key can
    # never be reassigned, even to its own job through a different original key.
    op.execute("""
        CREATE TRIGGER jobs_register_request_key_update AFTER UPDATE OF idempotency_key ON jobs
        WHEN NEW.idempotency_key IS NOT NULL AND NEW.idempotency_key IS NOT OLD.idempotency_key
        BEGIN
            INSERT INTO job_request_keys (idempotency_key, job_id, request_digest)
            VALUES (NEW.idempotency_key, NEW.id, NEW.request_digest);
        END
    """)
    op.execute("""
        CREATE TRIGGER jobs_register_request_digest_update AFTER UPDATE OF request_digest ON jobs
        WHEN NEW.idempotency_key IS NOT NULL AND NEW.idempotency_key IS OLD.idempotency_key
        BEGIN
            UPDATE job_request_keys SET request_digest = NEW.request_digest
            WHERE idempotency_key = NEW.idempotency_key AND job_id = NEW.id;
        END
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER jobs_register_request_digest_update")
    op.execute("DROP TRIGGER jobs_register_request_key_update")
    op.execute("DROP TRIGGER jobs_register_request_key_insert")
    op.drop_index("ix_job_request_keys_job_id", table_name="job_request_keys")
    op.drop_table("job_request_keys")
