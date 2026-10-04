"""Motor de detección: hallazgos con trazabilidad y calidad de señal continua.

Hasta acá `ecg_event` alcanzaba porque los únicos eventos los escribían reglas
determinísticas sobre los bits de flags del hardware: siempre el mismo resultado
para la misma trama, sin versión ni revisión posible. Con un motor de detección
la fila deja de ser un hecho y pasa a ser **una afirmación de un modelo**, y eso
exige poder decir cuál, poder reescribirla sin duplicarla, y poder registrar que
un médico la miró.

Siete cosas:

- `ANOMALY` en `ecg_event_type`. Es lo único que un motor no supervisado puede
  afirmar sin mentir: "este latido no se parece a los tuyos". `PVC` sería un
  diagnóstico que ningún dato del sistema respalda.
- `ecg_event.study_id`, con backfill. Hoy llegar del evento al estudio exige un
  JOIN contra `ecg_batch` **más** un fallback por JSONB, sin índice en ninguno de
  los dos caminos. Queda NULLABLE a propósito: `ecg_batch.study_id` también lo
  es, así que el backfill puede no resolver todas las filas y un NOT NULL haría
  fallar la migración sobre cualquier base con datos demo.
- `model_version`. Es el predicado que define "esto lo escribió el motor": lo
  único que el motor reescribe (encabezados por morfología, fusión al cierre).
  Los hallazgos de `simulate-anomaly`, la Capa A y los seeds legacy lo tienen en
  NULL y quedan intactos.
- `dedupe_key` con índice único parcial. Escribir dos veces el mismo hallazgo no
  puede duplicarlo. Un lote que falla hace rollback de todo y uno `DONE` no se
  reprocesa, así que el índice es una red de seguridad, no el mecanismo.
- `validation_status` / `validated_by` / `validated_at` / `validation_note`. La
  UI de validación es de una fase posterior, pero la columna va ahora: cada
  "descartado" del médico es una etiqueta de ruido revisada por un especialista
  sobre NUESTRO hardware, y son las únicas que el proyecto va a tener. Sin la
  columna se pierden meses de ellas.
- `signal_quality_interval` + `study.ml_state`. La calidad no es un evento
  puntual sino una propiedad continua del registro; ver el docstring del modelo.
  Su clave natural es `(study_id, start_sample_index)`: el motor analiza por
  bloques de la corrida y no por lote, así que el lote es atribución y no clave.
- `study.ml_analyzed_samples`, el cursor del análisis por bloques. Mismo patrón
  que `filtered_samples_count`: todo lo anterior ya se analizó una sola vez.
  Los estudios existentes arrancan con el cursor al final de su señal.
- Dos índices sobre `ecg_batch` para el barrido de colas viejas del motor
  (`list_studies_with_stale_tail`), que corre cada minuto sobre cada estudio en
  curso: `(study_id, received_at)` para saber si llegó un lote reciente sin
  recorrer la historia del estudio, y uno parcial de los lotes sin terminar.

Revision ID: 32266feb49f2
Revises: d0e1f2a3b4c5
Create Date: 2026-10-01 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "32266feb49f2"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Mismo criterio que `c3d4e5f6a7b8`: Postgres acepta ADD VALUE dentro de la
    # transacción de Alembic mientras el valor nuevo no se use en la misma
    # transacción. Acá solo se declara; el uso es en runtime.
    op.execute("ALTER TYPE ecg_event_type ADD VALUE IF NOT EXISTS 'ANOMALY'")

    validation_enum = postgresql.ENUM(
        "pending",
        "confirmed",
        "rejected",
        "uncertain",
        name="ecg_event_validation",
        create_type=False,
    )
    validation_enum.create(op.get_bind(), checkfirst=True)
    quality_enum = postgresql.ENUM(
        "good",
        "marginal",
        "bad",
        "unknown",
        name="signal_quality_level",
        create_type=False,
    )
    quality_enum.create(op.get_bind(), checkfirst=True)

    # --- ecg_event ----------------------------------------------------------- #
    op.add_column("ecg_event", sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("ecg_event", sa.Column("model_version", sa.String(length=64), nullable=True))
    op.add_column("ecg_event", sa.Column("dedupe_key", sa.String(length=128), nullable=True))
    op.add_column(
        "ecg_event",
        sa.Column(
            "validation_status",
            validation_enum,
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "ecg_event", sa.Column("validated_by", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("ecg_event", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ecg_event", sa.Column("validation_note", sa.String(length=1024), nullable=True))
    op.create_foreign_key("fk_ecg_event_study", "ecg_event", "study", ["study_id"], ["id"])
    op.create_foreign_key(
        "fk_ecg_event_validated_by", "ecg_event", "doctor", ["validated_by"], ["id"]
    )
    op.create_check_constraint(
        "ck_ecg_event_validation_actor",
        "ecg_event",
        "validated_at IS NULL OR validated_by IS NOT NULL",
    )

    # Backfill por los dos caminos que ya usaba `list_ecg_events`: la FK del lote
    # y, para los seeds anteriores a esa columna, el `studyId` del JSONB.
    op.execute(
        """
        UPDATE ecg_event e
           SET study_id = COALESCE(
                 (SELECT b.study_id FROM ecg_batch b WHERE b.id = e.batch_id),
                 NULLIF(e.metadata ->> 'studyId', '')::uuid
               )
         WHERE e.study_id IS NULL
        """
    )

    op.create_index("ix_ecg_event_study_ts", "ecg_event", ["study_id", "timestamp_in_recording"])
    # No existía ninguno: `ecg_event.batch_id` es NOT NULL y es la columna de los
    # JOIN con `ecg_batch` (`list_ecg_events`, el estudio de una alerta) y de las
    # bajas de los seeds, que se resolvían con seq scan.
    op.create_index("ix_ecg_event_batch", "ecg_event", ["batch_id"])
    op.create_index(
        "uq_ecg_event_dedupe",
        "ecg_event",
        ["study_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND deleted_at IS NULL"),
    )

    # --- signal_quality_interval --------------------------------------------- #
    op.create_table(
        "signal_quality_interval",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_sample_index", sa.BigInteger(), nullable=False),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("level", quality_enum, nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("window_count", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.CheckConstraint("start_sample_index >= 0", name="ck_sqi_start"),
        sa.CheckConstraint("sample_count > 0", name="ck_sqi_count"),
        sa.CheckConstraint("window_count > 0", name="ck_sqi_windows"),
        sa.ForeignKeyConstraint(["study_id"], ["study.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["ecg_batch.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sqi_batch", "signal_quality_interval", ["batch_id"])
    # Único por estudio y no por lote: un bloque de análisis abarca muchos lotes,
    # y reescribir el mismo bloque no puede duplicar sus intervalos. Cubre
    # también el listado por estudio en orden de grabación.
    op.create_index(
        "uq_sqi_study_start",
        "signal_quality_interval",
        ["study_id", "start_sample_index"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- study.ml_state ------------------------------------------------------- #
    op.add_column(
        "study",
        sa.Column(
            "ml_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # Los estudios nuevos arrancan en cero. Los que ya existen arrancan **al
    # final de su señal**: el motor empieza a mirarlos desde el despliegue, como
    # habría hecho el análisis por lote. Arrancar en cero recorría la historia
    # entera —un estudio de quince días son ~4.300 bloques de S3 y CPU con la
    # fila tomada, disparados al abrirlo en el visor— y avisaba al paciente de
    # pausas de hace semanas. Analizar la historia es una decisión explícita
    # (volver el cursor a cero), no un efecto de desplegar.
    op.add_column(
        "study",
        sa.Column(
            "ml_analyzed_samples",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute("UPDATE study SET ml_analyzed_samples = samples_count")

    # --- ecg_batch ------------------------------------------------------------ #
    op.create_index("ix_ecg_batch_study_received", "ecg_batch", ["study_id", "received_at"])
    op.create_index(
        "ix_ecg_batch_study_unprocessed",
        "ecg_batch",
        ["study_id"],
        postgresql_where=sa.text("processing_status <> 'DONE'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ecg_batch_study_unprocessed", table_name="ecg_batch")
    op.drop_index("ix_ecg_batch_study_received", table_name="ecg_batch")
    op.drop_column("study", "ml_analyzed_samples")
    op.drop_column("study", "ml_state")

    op.drop_index("uq_sqi_study_start", table_name="signal_quality_interval")
    op.drop_index("ix_sqi_batch", table_name="signal_quality_interval")
    op.drop_table("signal_quality_interval")
    sa.Enum(name="signal_quality_level").drop(op.get_bind(), checkfirst=True)

    op.drop_index("uq_ecg_event_dedupe", table_name="ecg_event")
    op.drop_index("ix_ecg_event_batch", table_name="ecg_event")
    op.drop_index("ix_ecg_event_study_ts", table_name="ecg_event")
    op.drop_constraint("ck_ecg_event_validation_actor", "ecg_event", type_="check")
    op.drop_constraint("fk_ecg_event_validated_by", "ecg_event", type_="foreignkey")
    op.drop_constraint("fk_ecg_event_study", "ecg_event", type_="foreignkey")
    for column in (
        "validation_note",
        "validated_at",
        "validated_by",
        "validation_status",
        "dedupe_key",
        "model_version",
        "study_id",
    ):
        op.drop_column("ecg_event", column)
    sa.Enum(name="ecg_event_validation").drop(op.get_bind(), checkfirst=True)

    # `ANOMALY` se queda en `ecg_event_type`, mismo criterio que `a1b70fd51903` y
    # `c3d4e5f6a7b8`: quitar un valor exige recrear el tipo y reescribir cada
    # columna que lo usa. Un valor sin usar es inerte.
