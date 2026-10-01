"""Adjudication of the FLoRA / Metascience Observatory disagreements.

forrtproject/fred-data PR #143 lists 159 rows where FLoRA's pipeline and the
Metascience Observatory give different answers about the same replication.
Trusted and Senior validators judge each one twice in the validator app, an
admin approves the final answer, and approved answers can be published to FLoRA
through Source Records.

An isolated feature, so that it can fail without taking the app with it:

* its tables live in their own PostgreSQL schema, `adjudication` (schema.sql),
  applied by bootstrap.setup(), not by db_schema.sql, and reference nothing
  outside it;
* it is switched on by ADJUDICATION_ENABLED and stays off when its setup fails;
* app.py loads it inside one try block, and docs/adjudication.js is loaded
  after docs/app.js and reached only through window.Adjudication?.….
"""

from .api import create_router
from .bootstrap import SetupStatus, feature_enabled, setup

__all__ = ["SetupStatus", "create_router", "feature_enabled", "setup"]
