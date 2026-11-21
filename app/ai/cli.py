"""Offline commands that fill the AI caches ahead of time (no user involved)."""

import time

import click

from ..catalogue import catalogue_policies, is_closed
from . import client, comparison_ai, plan_summary
from .. import retrieval


BUSY_RETRIES = 4          # the free tier allows about one large request a minute
BUSY_WAIT_SECONDS = 30


def _until_not_busy(make):
    """Call make(); when the provider says it is busy (HTTP 429), wait and try again."""
    result = make()
    for _ in range(BUSY_RETRIES):
        if result.get("reason") != "busy":
            break
        time.sleep(BUSY_WAIT_SECONDS)
        result = make()
    return result


def register(app):
    @app.cli.command("generate-summaries")
    @click.option("--language", "languages", multiple=True, type=click.Choice(plan_summary.LANGUAGE_CODES),
                  help="Languages to generate (repeat). Default: all four.")
    @click.option("--plan", "slugs", multiple=True, help="Only this plan slug (repeat). Default: every plan.")
    @click.option("--force", is_flag=True, help="Regenerate even when a valid summary is cached.")
    def generate_summaries(languages, slugs, force):
        """Generate and cache the English summary (and translations) for every catalogue plan.

        Plans with no ingested contract wording are skipped and never reach the model."""
        if not client.is_configured():
            raise click.ClickException("GROQ_API_KEY and GROQ_MODEL are not set in .env. Nothing generated.")
        wanted = list(languages) or list(plan_summary.LANGUAGE_CODES)
        done = skipped = failed = 0
        for policy in catalogue_policies():
            if slugs and policy.slug not in slugs:
                continue
            if policy.slug and is_closed(policy):
                skipped += 1  # plans closed to new members are not summarised
                continue
            try:
                if policy.slug:  # a catalogue plan: contract paragraphs, or nothing
                    if not retrieval.retrieve(policy).paragraphs:
                        skipped += 1  # counted in the last line, not listed one by one
                        continue
                    english = _until_not_busy(lambda: plan_summary.contract_english_summary(policy, force=force))
                else:  # fictional demo plan (clauses)
                    english = plan_summary.english_summary(policy, force=force)
                click.echo(f"{policy.name}: en -> {english['source']}"
                           + (f" ({english['reason']})" if english["reason"] else ""))
                for code in wanted:
                    if code == "en":
                        continue
                    translated = plan_summary.translated_summary(policy, code, english, force=force)
                    click.echo(f"{policy.name}: {code} -> {'ai' if translated else 'unavailable'}")
                done += 1
            except Exception as error:  # one plan must never stop the rest
                click.echo(f"{policy.name}: failed ({type(error).__name__})")
                failed += 1
        click.echo(f"Summaries: {done} plans processed, {skipped} skipped (no contract wording or closed to new members), {failed} failed.")

    @app.cli.command("generate-comparison")
    @click.option("--plan", "slugs", multiple=True, help="Only this plan slug (repeat). Default: every plan with contract wording.")
    @click.option("--force", is_flag=True, help="Regenerate even when the stored findings are current.")
    def generate_comparison(slugs, force):
        """Propose, validate and store the comparison findings for plans with contract wording."""
        if not client.is_configured():
            raise click.ClickException("GROQ_API_KEY and GROQ_MODEL are not set in .env. Nothing generated.")
        plans = [p for p in catalogue_policies()
                 if p.slug and not is_closed(p) and (not slugs or p.slug in slugs)]
        if not plans:
            raise click.ClickException("No matching catalogue plans. Run flask import-catalogue first.")
        counts = {"generated": 0, "cached": 0, "skipped": 0, "failed": 0}
        for policy in plans:
            try:
                result = comparison_ai.generate_for_plan(policy, force=force)
            except Exception as error:
                result = {"status": "failed", "reason": type(error).__name__}
            counts[result["status"]] += 1
            if result["status"] == "skipped":
                continue  # most catalogue plans have no wording: one line at the end, not 46 lines
            detail = {
                "generated": f"{result.get('accepted', 0)} findings kept, {result.get('downgraded', 0)} left Unconfirmed",
                "cached": "already current",
                "failed": result.get("reason", ""),
            }[result["status"]]
            click.echo(f"{policy.name}: {result['status']} ({detail})")
        click.echo(f"Comparison: {counts['generated']} generated, {counts['cached']} current, "
                   f"{counts['failed']} failed, {counts['skipped']} skipped (no contract wording or closed to new members).")
