import typer
from apscheduler.schedulers.blocking import BlockingScheduler
from budget_guard.db import init_db
from budget_guard.factory import billing_provider, limiter_provider
from budget_guard.services.poller import BillingPoller
from budget_guard.config import settings

app = typer.Typer()

def poller(): return BillingPoller(billing_provider(), limiter_provider())

@app.command()
def init():
    init_db()
    typer.echo("database initialized")

@app.command()
def poll(billing_cycle: str = ""):
    init_db()
    typer.echo(poller().run_once(billing_cycle or None))

@app.command()
def worker():
    init_db()
    scheduler = BlockingScheduler()
    scheduler.add_job(lambda: poller().run_once(), "interval", minutes=settings.poll_interval_minutes, max_instances=1, coalesce=True)
    poller().run_once()
    scheduler.start()
