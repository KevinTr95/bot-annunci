"""Formattazione dei messaggi Telegram."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Optional

from .matcher import Match
from .models import Job
from .workmode import LABELS

SOURCE_LABEL = {"linkedin": "LinkedIn", "jobs.ch": "jobs.ch", "greenhouse": "sito carriere",
                "lever": "sito carriere", "workable": "sito carriere", "smartrecruiters": "sito carriere",
                "recruitee": "sito carriere", "personio": "sito carriere"}


def score_bar(score: int) -> str:
    full = max(0, min(5, round(score / 20)))
    return "🟩" * full + "⬜" * (5 - full)


def age_text(dt: Optional[datetime]) -> str:
    if not dt:
        return "data non indicata"
    mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
    if mins < 60:
        return f"{max(1, int(mins))} min fa"
    if mins < 24 * 60:
        return f"{int(mins // 60)} h fa"
    return f"{int(mins // 1440)} g fa"


def job_message(job: Job, m: Match) -> str:
    lines = [f"🆕 <b>{escape(job.title)}</b>", f"🏢 {escape(job.company)}"]
    where = escape(job.location or "località non indicata")
    if m.mode:
        where += f" · {LABELS[m.mode]}"
    lines.append(f"📍 {where}")
    if m.salary:
        lines.append(f"💰 {escape(m.salary.short())}")
    pct = f" · skill {m.skill_pct}%" if m.skill_pct is not None else ""
    lines.append(f"🎯 <b>{m.score}/100</b> {score_bar(m.score)}{pct}")
    if m.matched:
        lines.append("✅ " + escape(", ".join(m.matched[:8])))
    if m.similar:
        lines.append("🔸 " + escape(", ".join(f"{s} (≈ {k})" for s, k in m.similar[:4])))
    if m.missing:
        lines.append("❌ " + escape(", ".join(m.missing[:6])))
    if m.warnings:
        lines.append("⚠️ " + escape("; ".join(m.warnings)))
    meta = f"🕒 {age_text(job.posted_at)} · {SOURCE_LABEL.get(job.source, job.source)}"
    if job.extra.get("applicants"):
        meta += f" · 👥 {escape(job.extra['applicants'])}"
    lines.append(meta)
    return "\n".join(lines)
