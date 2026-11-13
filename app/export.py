from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

try:
    from icalendar import Calendar, Event as ICalEvent
    _ICAL_AVAILABLE = True
except ImportError:
    _ICAL_AVAILABLE = False

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    _REPORTLAB_AVAILABLE = True
except ImportError:
    _REPORTLAB_AVAILABLE = False


def parse_iso(slot_str: str) -> datetime:
    return datetime.fromisoformat(slot_str)


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

def generate_csv(schedule: Dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Event ID",
        "Event Title",
        "Department",
        "Venue Assigned",
        "Start Time",
        "End Time",
        "Duration (Hours)",
        "Soft Cost",
        "Primary Justification",
    ])

    for a in schedule.get("assignments", []):
        start_dt = parse_iso(a["slots"][0])
        end_dt = parse_iso(a["slots"][-1]) + timedelta(hours=1)
        expl_text = " | ".join(a.get("explanation", []))
        writer.writerow([
            a["event_id"],
            a["event_name"],
            a.get("department_name", "General"),
            a["venue_name"],
            start_dt.strftime("%Y-%m-%d %H:%M"),
            end_dt.strftime("%Y-%m-%d %H:%M"),
            len(a["slots"]),
            a["soft_cost"],
            expl_text,
        ])

    return output.getvalue()


# ---------------------------------------------------------------------------
# iCalendar (.ics) Export
# ---------------------------------------------------------------------------

def generate_ics(schedule: Dict[str, Any]) -> bytes:
    if not _ICAL_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="iCalendar export is unavailable in this environment (icalendar package not installed).",
        )
    cal = Calendar()
    cal.add("prodid", "-//Lovely Professional University//Event Scheduler//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "LPU Academic Schedule")

    for a in schedule.get("assignments", []):
        ievent = ICalEvent()
        start_dt = parse_iso(a["slots"][0])
        end_dt = parse_iso(a["slots"][-1]) + timedelta(hours=1)

        ievent.add("summary", f"{a['event_name']} ({a.get('department_name', 'LPU')})")
        ievent.add("dtstart", start_dt)
        ievent.add("dtend", end_dt)
        ievent.add("location", a["venue_name"])
        ievent.add("description", "\n".join(a.get("explanation", [])))
        ievent.add("status", "CONFIRMED")
        cal.add_component(ievent)

    return cal.to_ical()


# ---------------------------------------------------------------------------
# PDF Export (ReportLab)
# ---------------------------------------------------------------------------

def generate_pdf(schedule: Dict[str, Any]) -> bytes:
    if not _REPORTLAB_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="PDF export is unavailable in this environment (reportlab package not installed).",
        )
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    story: List[Any] = []
    styles = getSampleStyleSheet()

    # Custom typography styles
    title_style = ParagraphStyle(
        "LPUTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0B1220"),
    )
    subtitle_style = ParagraphStyle(
        "LPUSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#64748B"),
    )
    cell_style = ParagraphStyle(
        "LPUCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#0F172A"),
    )
    header_cell_style = ParagraphStyle(
        "LPUHeaderCell",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.white,
    )

    # Header with LPU Branding
    logo_path = Path(__file__).resolve().parent / "static" / "logo.png"
    if logo_path.exists():
        try:
            story.append(Image(str(logo_path), width=48, height=48))
            story.append(Spacer(1, 8))
        except Exception:
            pass

    story.append(Paragraph("Lovely Professional University (LPU)", title_style))
    story.append(Paragraph("Multi-Department Event Scheduling & Resource Optimization Matrix", subtitle_style))
    story.append(Paragraph("Generated via Constraint Satisfaction • Heuristic Search • Explainable AI", subtitle_style))
    story.append(Spacer(1, 14))

    # Summary Statistics Box
    assignments = schedule.get("assignments", [])
    unresolved = schedule.get("unresolved", [])
    total_soft_cost = schedule.get("total_soft_cost", 0)

    summary_data = [
        [
            Paragraph("<b>Total Scheduled Events:</b>", cell_style),
            Paragraph(str(len(assignments)), cell_style),
            Paragraph("<b>Unresolved Clashes:</b>", cell_style),
            Paragraph(str(len(unresolved)), cell_style),
            Paragraph("<b>Total Soft-Cost:</b>", cell_style),
            Paragraph(str(total_soft_cost), cell_style),
        ]
    ]
    summary_table = Table(summary_data, colWidths=[120, 50, 110, 50, 90, 50])
    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F5F9")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(summary_table)
    story.append(Spacer(1, 16))

    # Timetable Table
    table_data = [
        [
            Paragraph("Event Title", header_cell_style),
            Paragraph("Department", header_cell_style),
            Paragraph("Venue Assigned", header_cell_style),
            Paragraph("Time Slot", header_cell_style),
            Paragraph("Cost", header_cell_style),
            Paragraph("Explainability Summary", header_cell_style),
        ]
    ]

    for a in assignments:
        start_str = parse_iso(a["slots"][0]).strftime("%a %b %d, %H:%M")
        end_str = (parse_iso(a["slots"][-1]) + timedelta(hours=1)).strftime("%H:%M")
        time_display = f"{start_str} - {end_str}"
        first_reason = a["explanation"][0] if a.get("explanation") else "Constraint verified"

        table_data.append([
            Paragraph(f"<b>{a['event_name']}</b>", cell_style),
            Paragraph(a.get("department_name", "General"), cell_style),
            Paragraph(a["venue_name"], cell_style),
            Paragraph(time_display, cell_style),
            Paragraph(str(a["soft_cost"]), cell_style),
            Paragraph(first_reason, cell_style),
        ])

    col_widths = [110, 80, 100, 100, 35, 115]
    sched_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    sched_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ])
    )
    story.append(sched_table)

    # Unresolved Conflicts Section
    if unresolved:
        story.append(Spacer(1, 16))
        story.append(Paragraph("<b>Unresolved Resource Conflicts & Diagnoses</b>", subtitle_style))
        story.append(Spacer(1, 6))

        un_data = [
            [
                Paragraph("Event", header_cell_style),
                Paragraph("Priority", header_cell_style),
                Paragraph("Conflict Attribution & Resolution Reason", header_cell_style),
            ]
        ]
        for u in unresolved:
            reasons_text = "<br/>• ".join(u.get("reasons", []))
            un_data.append([
                Paragraph(f"<b>{u['event_name']}</b>", cell_style),
                Paragraph(u.get("priority", "normal").upper(), cell_style),
                Paragraph(f"• {reasons_text}", cell_style),
            ])

        un_table = Table(un_data, colWidths=[120, 60, 360], repeatRows=1)
        un_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DC2626")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#FCA5A5")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#FEF2F2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ])
        )
        story.append(un_table)

    doc.build(story)
    return buffer.getvalue()
