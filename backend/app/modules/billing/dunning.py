"""
DunningService (T-130): schedules the payment-failure reminder sequence
(day-1/day-3/day-7 emails, sent by the `send_dunning_email` ARQ job in
app.workers.jobs.billing) as three deferred jobs tracked on a DunningCampaign
row, so a later payment success or subscription cancellation can revoke
whichever haven't fired yet via app.core.jobs.revoke_job.

start() closes out any existing active campaign for the subscription before
opening a new one: a second invoice.payment_failed for the same subscription
(a redelivered webhook, or a genuinely new failed invoice before the first
campaign's sequence finished) must not leave two overlapping sets of
reminder emails in flight.
"""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.modules.billing.models import DunningCampaign
from app.modules.billing.repository import DunningCampaignRepository

# (step name the send_dunning_email job matches on, delay before it fires).
STEPS: tuple[tuple[str, timedelta], ...] = (
    ("day1", timedelta(days=1)),
    ("day3", timedelta(days=3)),
    ("day7", timedelta(days=7)),
)


class DunningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._campaigns = DunningCampaignRepository(session)

    async def start(self, subscription_id: UUID, invoice_id: str) -> DunningCampaign:
        existing = await self._campaigns.get_active_by_subscription_id(subscription_id)
        if existing is not None:
            await self._revoke_and_close(existing, status="canceled")

        campaign = await self._campaigns.create(
            subscription_id=subscription_id, invoice_id=invoice_id, scheduled_job_ids=[]
        )

        job_ids: list[str] = []
        for step, delay in STEPS:
            job_id = await jobs.enqueue_job(
                "send_dunning_email",
                campaign_id=campaign.id,
                step=step,
                _defer_by=delay,
            )
            if job_id is not None:
                job_ids.append(job_id)
        await self._campaigns.set_scheduled_job_ids(campaign, job_ids)
        return campaign

    async def cancel(self, subscription_id: UUID) -> None:
        campaign = await self._campaigns.get_active_by_subscription_id(subscription_id)
        if campaign is None:
            return
        await self._revoke_and_close(campaign, status="canceled")

    async def complete(self, subscription_id: UUID) -> None:
        campaign = await self._campaigns.get_active_by_subscription_id(subscription_id)
        if campaign is None:
            return
        await self._revoke_and_close(campaign, status="completed")

    async def _revoke_and_close(self, campaign: DunningCampaign, *, status: str) -> None:
        for job_id in campaign.scheduled_job_ids:
            await jobs.revoke_job(job_id)
        await self._campaigns.close(campaign, status=status)
