"""
Verifies T-033's GDPR schemas: DeleteMeRequest only accepts the literal
string "DELETE" (this is the only thing standing between a misclick and an
account deletion, so it must reject anything else -- including a
differently-cased match), and the other request/response shapes round-trip
their fields.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.gdpr.schemas import (
    ConsentRecordPublic,
    DataExportJobPublic,
    DeleteMeRequest,
    ExportJobCreated,
    RecordConsentRequest,
)

# --- DeleteMeRequest -----------------------------------------------------------


def test_delete_me_request_accepts_exact_confirm_value() -> None:
    req = DeleteMeRequest(confirm="DELETE")

    assert req.confirm == "DELETE"


@pytest.mark.parametrize("value", ["delete", "Delete", "yes", ""])
def test_delete_me_request_rejects_anything_but_delete(value: str) -> None:
    with pytest.raises(ValidationError):
        DeleteMeRequest(confirm=value)


def test_delete_me_request_requires_confirm() -> None:
    with pytest.raises(ValidationError):
        DeleteMeRequest.model_validate({})


# --- ExportJobCreated / DataExportJobPublic -------------------------------------


def test_export_job_created_shape() -> None:
    job_id = uuid4()

    created = ExportJobCreated(job_id=job_id)

    assert created.job_id == job_id


def test_data_export_job_public_constructs_from_orm_like_object() -> None:
    class _FakeJob:
        id = uuid4()
        status = "pending"
        download_url = None
        expires_at = None
        created_at = datetime(2026, 6, 1, tzinfo=UTC)

    public = DataExportJobPublic.model_validate(_FakeJob())

    assert public.status == "pending"


# --- RecordConsentRequest / ConsentRecordPublic ---------------------------------


def test_record_consent_request_accepts_well_formed_payload() -> None:
    req = RecordConsentRequest(consent_type="marketing", granted=True, version="2026-06-01")

    assert req.granted is True


def test_record_consent_request_rejects_empty_consent_type() -> None:
    with pytest.raises(ValidationError):
        RecordConsentRequest(consent_type="", granted=True, version="v1")


def test_consent_record_public_constructs_from_orm_like_object() -> None:
    class _FakeRecord:
        id = uuid4()
        consent_type = "marketing"
        granted = True
        version = "v1"
        ts = datetime(2026, 6, 1, tzinfo=UTC)

    public = ConsentRecordPublic.model_validate(_FakeRecord())

    assert public.consent_type == "marketing"
