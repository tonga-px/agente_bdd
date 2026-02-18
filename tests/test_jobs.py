"""Tests for JobStore, including cooldown logic."""

from datetime import datetime, timedelta, timezone

from app.jobs import Job, JobStatus, JobStore


def test_recently_completed_job_within_cooldown():
    """A completed job within cooldown window is returned."""
    store = JobStore()
    job = store.create_job(company_id="C1", task_type="enrichment")
    store.mark_running(job.job_id)
    store.mark_completed(job.job_id, None)

    recent = store.recently_completed_job("enrichment", "C1")
    assert recent is not None
    assert recent.job_id == job.job_id


def test_recently_completed_job_outside_cooldown():
    """A completed job older than cooldown is not returned."""
    store = JobStore()
    job = store.create_job(company_id="C1", task_type="enrichment")
    store.mark_running(job.job_id)
    store.mark_completed(job.job_id, None)

    # Manually backdate finished_at
    job.finished_at = datetime.now(timezone.utc) - timedelta(minutes=31)

    recent = store.recently_completed_job("enrichment", "C1")
    assert recent is None


def test_recently_completed_job_different_company():
    """Cooldown is per company — different company_id returns None."""
    store = JobStore()
    job = store.create_job(company_id="C1", task_type="enrichment")
    store.mark_running(job.job_id)
    store.mark_completed(job.job_id, None)

    recent = store.recently_completed_job("enrichment", "C2")
    assert recent is None


def test_recently_completed_job_different_task():
    """Cooldown is per task_type — different task returns None."""
    store = JobStore()
    job = store.create_job(company_id="C1", task_type="enrichment")
    store.mark_running(job.job_id)
    store.mark_completed(job.job_id, None)

    recent = store.recently_completed_job("prospeccion", "C1")
    assert recent is None


def test_recently_completed_job_failed_also_blocks():
    """A failed job within cooldown also blocks re-processing."""
    store = JobStore()
    job = store.create_job(company_id="C1", task_type="enrichment")
    store.mark_running(job.job_id)
    store.mark_failed(job.job_id, "Some error")

    recent = store.recently_completed_job("enrichment", "C1")
    assert recent is not None
    assert recent.status == JobStatus.failed


def test_recently_completed_job_active_not_returned():
    """Active (pending/running) jobs are not returned by recently_completed_job."""
    store = JobStore()
    job = store.create_job(company_id="C1", task_type="enrichment")
    store.mark_running(job.job_id)

    recent = store.recently_completed_job("enrichment", "C1")
    assert recent is None


def test_has_active_job_basic():
    """Basic has_active_job still works."""
    store = JobStore()
    job = store.create_job(company_id="C1", task_type="enrichment")

    assert store.has_active_job("enrichment", "C1") is not None
    assert store.has_active_job("enrichment", "C2") is None
