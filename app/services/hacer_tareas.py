import logging

from app.mappers.task_scheduler import (
    AGENT_SUBJECT_PREFIX,
    build_hacer_tareas_note,
    compute_task_due_date,
    is_business_day,
    is_business_hour,
    parse_task_agente,
)
from app.schemas.responses import HacerTareasResponse, TaskResult
from app.services.hubspot import HubSpotService

logger = logging.getLogger(__name__)


class HacerTareasService:
    def __init__(self, hubspot: HubSpotService) -> None:
        self._hubspot = hubspot

    async def run(self) -> HacerTareasResponse:
        tasks = await self._hubspot.search_tasks()

        # Client-side filter: only tasks with "Agente:" prefix
        agent_tasks = []
        for t in tasks:
            subject = (t.get("properties") or {}).get("hs_task_subject", "")
            agente_value = parse_task_agente(subject)
            if agente_value:
                agent_tasks.append((t["id"], subject, agente_value))

        results: list[TaskResult] = []
        activated = 0
        skipped = 0
        rescheduled = 0
        errors = 0

        for task_id, subject, agente_value in agent_tasks:
            try:
                result = await self._process_task(task_id, subject, agente_value)
                results.append(result)
                if result.status == "activated":
                    activated += 1
                elif result.status == "rescheduled":
                    rescheduled += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.exception("Error processing task %s", task_id)
                results.append(TaskResult(
                    task_id=task_id,
                    task_subject=subject,
                    agente_value=agente_value,
                    status="error",
                    message=str(exc),
                ))
                errors += 1

        return HacerTareasResponse(
            total_found=len(agent_tasks),
            activated=activated,
            skipped=skipped,
            rescheduled=rescheduled,
            errors=errors,
            results=results,
        )

    async def _process_task(
        self, task_id: str, subject: str, agente_value: str,
    ) -> TaskResult:
        # 1. Get associated company
        company_ids = await self._hubspot.get_task_company_ids(task_id)
        if not company_ids:
            return TaskResult(
                task_id=task_id,
                task_subject=subject,
                agente_value=agente_value,
                status="skipped",
                message="no_company",
            )

        company_id = company_ids[0]

        # 2. Get company to check country and agente
        company = await self._hubspot.get_company(company_id)
        country = company.properties.country

        # 3. Check business hour (outside hours → silent skip, cron retries)
        if not is_business_hour(country):
            logger.info(
                "Task %s skipped: outside_hours (country=%r, company=%s)",
                task_id, country, company_id,
            )
            return TaskResult(
                task_id=task_id,
                task_subject=subject,
                company_id=company_id,
                agente_value=agente_value,
                status="skipped",
                message="outside_hours",
            )

        # 4. Check business day (holiday/weekend → reschedule)
        if not is_business_day(country):
            new_due = compute_task_due_date(country)
            await self._hubspot.update_task(task_id, {"hs_timestamp": new_due})
            logger.info(
                "Task %s rescheduled: not a business day (country=%r, moved to %s)",
                task_id, country, new_due,
            )
            return TaskResult(
                task_id=task_id,
                task_subject=subject,
                company_id=company_id,
                agente_value=agente_value,
                status="rescheduled",
                message=f"moved to {new_due}",
            )

        # 5. Check if company already has an active agent
        if company.properties.agente:
            logger.info(
                "Task %s skipped: company_busy (agente=%r, company=%s)",
                task_id, company.properties.agente, company_id,
            )
            return TaskResult(
                task_id=task_id,
                task_subject=subject,
                company_id=company_id,
                agente_value=agente_value,
                status="skipped",
                message="company_busy",
            )

        # 6. Activate agent on company
        await self._hubspot.update_company(company_id, {"agente": agente_value})

        # 7. Mark task as COMPLETED
        await self._hubspot.update_task(task_id, {"hs_task_status": "COMPLETED"})

        # 8. Create note (best-effort)
        try:
            note_body = build_hacer_tareas_note(agente_value, subject)
            await self._hubspot.create_note(company_id, note_body)
        except Exception:
            logger.warning("Failed to create note for task %s", task_id)

        return TaskResult(
            task_id=task_id,
            task_subject=subject,
            company_id=company_id,
            agente_value=agente_value,
            status="activated",
        )
