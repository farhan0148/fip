"""`flask generate-summaries`: fill the plan summary cache ahead of time (no user involved)."""

import click

from ..catalogue import catalogue_policies
from . import client, plan_summary


def register(app):
    @app.cli.command("generate-summaries")
    @click.option("--language", "languages", multiple=True, type=click.Choice(plan_summary.LANGUAGE_CODES),
                  help="Languages to generate (repeat). Default: all four.")
    @click.option("--force", is_flag=True, help="Regenerate even when a valid summary is cached.")
    def generate_summaries(languages, force):
        """Generate and cache the English summary (and translations) for every catalogue plan."""
        if not client.is_configured():
            raise click.ClickException("GROQ_API_KEY and GROQ_MODEL are not set in .env. Nothing generated.")
        wanted = list(languages) or list(plan_summary.LANGUAGE_CODES)
        for policy in catalogue_policies():
            english = plan_summary.english_summary(policy, force=force)
            click.echo(f"{policy.name}: en -> {english['source']}" + (f" ({english['reason']})" if english["reason"] else ""))
            for code in wanted:
                if code == "en":
                    continue
                translated = plan_summary.translated_summary(policy, code, english, force=force)
                click.echo(f"{policy.name}: {code} -> {'ai' if translated else 'unavailable'}")
