from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user
from io import BytesIO
from datetime import datetime, timedelta
import pandas as pd
from reportlab.pdfgen import canvas

from models import (
    db,
    Hospital,
    Department,
    Role,
    User,
    WastePackage,
    Transport,
    WasteOnTransport,
    Disposal,
    StatusEvent,
    GpsPoint,
    Incident,
)
from auth import require_roles
from status_flow import latest_status, advance_waste, advance_transport, FlowError
from utils import (
    route_points_from_geojson,
    min_distance_to_polyline_m,
    default_buffer_m,
    overdue_threshold,
)

bp = Blueprint("views", __name__)


@bp.route("/")
@login_required
def index():
    return render_template("index.html")


@bp.route("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    stype = request.args.get("type") or "all"
    results = {"wastes": [], "transports": [], "hospitals": []}
    if not q:
        return render_template("search.html", q=q, results=results)

    if stype in ("all", "waste"):
        ws = WastePackage.query.filter(WastePackage.waste_id.like(f"%{q}%")).all()
        results["wastes"] = [
            {
                "waste_id": w.waste_id,
                "type": w.waste_type,
                "weight": float(w.weight_kg),
                "hospital_id": w.hospital_id,
                "dept_id": w.dept_id,
                "status": latest_status("waste", w.waste_id),
            }
            for w in ws
        ]
    if stype in ("all", "transport"):
        ts = Transport.query.filter(Transport.transport_id.like(f"%{q}%")).all()
        results["transports"] = [
            {
                "transport_id": t.transport_id,
                "by": t.transport_by,
                "plate": t.vehicle_plate,
                "status": latest_status("transport", t.transport_id),
            }
            for t in ts
        ]
    if stype in ("all", "hospital"):
        hs = Hospital.query.filter(
            (Hospital.hospital_id.like(f"%{q}%")) | (Hospital.name.like(f"%{q}%"))
        ).all()
        results["hospitals"] = [
            {"hospital_id": h.hospital_id, "name": h.name} for h in hs
        ]

    return render_template("search.html", q=q, results=results)


@bp.route("/waste/<waste_id>")
@login_required
def waste_detail(waste_id):
    w = WastePackage.query.get_or_404(waste_id)
    events = (
        StatusEvent.query.filter_by(ref_type="waste", ref_id=waste_id)
        .order_by(StatusEvent.at.desc())
        .all()
    )
    disp = Disposal.query.filter_by(waste_id=waste_id).first()
    trans = (
        db.session.query(Transport)
        .join(WasteOnTransport, WasteOnTransport.transport_id == Transport.transport_id)
        .filter(WasteOnTransport.waste_id == waste_id)
        .first()
    )
    return render_template(
        "waste_detail.html", w=w, events=events, disp=disp, trans=trans
    )


@bp.route("/transport/<transport_id>")
@login_required
def transport_detail(transport_id):
    t = Transport.query.get_or_404(transport_id)
    wastes = (
        db.session.query(WastePackage)
        .join(WasteOnTransport, WasteOnTransport.waste_id == WastePackage.waste_id)
        .filter(WasteOnTransport.transport_id == transport_id)
        .all()
    )
    events = (
        StatusEvent.query.filter_by(ref_type="transport", ref_id=transport_id)
        .order_by(StatusEvent.at.desc())
        .all()
    )
    gps = (
        GpsPoint.query.filter_by(transport_id=transport_id)
        .order_by(GpsPoint.at.asc())
        .all()
    )
    return render_template(
        "transport_detail.html", t=t, wastes=wastes, events=events, gps=gps
    )


@bp.route("/dashboard")
@login_required
@require_roles("manager", "staff", "transport")
def dashboard():
    # time filters
    from_str = request.args.get("from")
    to_str = request.args.get("to")
    now = datetime.utcnow()
    default_from = now - timedelta(days=30)
    dt_from = datetime.fromisoformat(from_str) if from_str else default_from
    dt_to = datetime.fromisoformat(to_str) if to_str else now

    wastes = WastePackage.query.filter(
        (WastePackage.collected_time == None) | (WastePackage.collected_time >= dt_from)
    ).all()

    # KPIs
    total = len(wastes)
    by_type = {}
    completed = 0
    for w in wastes:
        by_type[w.waste_type] = by_type.get(w.waste_type, 0) + 1
        if (
            StatusEvent.query.filter_by(
                ref_type="waste", ref_id=w.waste_id, status="Completed"
            ).count()
            > 0
        ):
            completed += 1

    percent_completed = round(100 * completed / max(total, 1), 1)

    # Time series (by day)
    df = pd.DataFrame(
        [
            {
                "day": (w.collected_time.date() if w.collected_time else now.date()),
                "count": 1,
            }
            for w in wastes
        ]
    )
    ts = (
        df.groupby("day")["count"].sum().reset_index().to_dict(orient="records")
        if not df.empty
        else []
    )

    # Incidents generation (on-the-fly)
    incidents = []
    buffer_m = default_buffer_m()
    # route deviation
    transports = Transport.query.all()
    for t in transports:
        route = route_points_from_geojson(t.planned_route_geojson)
        if not route:
            continue
        for p in GpsPoint.query.filter_by(transport_id=t.transport_id).all():
            dmin = min_distance_to_polyline_m(p.lat, p.lng, route)
            if dmin > buffer_m:
                incidents.append(
                    {
                        "type": "route_deviation",
                        "ref_id": t.transport_id,
                        "detail": f"deviation {int(dmin)} m",
                    }
                )
                break
    # overdue collected
    overdue = []
    for w in WastePackage.query.all():
        e = (
            StatusEvent.query.filter_by(ref_type="waste", ref_id=w.waste_id)
            .order_by(StatusEvent.at.desc())
            .first()
        )
        if (
            e
            and e.status == "Collected"
            and (datetime.utcnow() - (w.collected_time or datetime.utcnow()))
            > overdue_threshold()
        ):
            overdue.append(w.waste_id)
    for wid in overdue:
        incidents.append(
            {
                "type": "overdue_collected",
                "ref_id": wid,
                "detail": ">24h without On Truck",
            }
        )

    # Latest table (last 20)
    latest = (
        StatusEvent.query.filter_by(ref_type="waste")
        .order_by(StatusEvent.at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "dashboard.html",
        dt_from=dt_from,
        dt_to=dt_to,
        total=total,
        by_type=by_type,
        percent_completed=percent_completed,
        ts=ts,
        latest=latest,
        incidents=incidents,
    )


@bp.route("/status/scan", methods=["GET", "POST"])
@login_required
@require_roles("staff", "transport")
def status_scan():
    msg = None
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        action = request.form.get("action")  # optional target status
        allow_skip = bool(action)
        # Detect type by existence
        w = WastePackage.query.get(code)
        t = Transport.query.get(code) if not w else None
        try:
            if w:
                # Staff restriction: must match hospital+dept
                if current_user.role == "staff" and not (
                    current_user.hospital_id == w.hospital_id
                    and current_user.dept_id == w.dept_id
                ):
                    return (
                        render_template(
                            "status_scan.html",
                            message="Permission denied: different dept/hospital",
                        ),
                        403,
                    )
                new_status = advance_waste(
                    w.waste_id, current_user.id, allow_skip=allow_skip, to_status=action
                )
                db.session.commit()
                msg = f"Waste {w.waste_id} → {new_status}"
            elif t:
                # Transport restriction
                if (
                    current_user.role == "transport"
                    and current_user.transport_code
                    and current_user.transport_code != (t.transport_by or "")
                ):
                    return (
                        render_template(
                            "status_scan.html",
                            message="Permission denied: not your transport",
                        ),
                        403,
                    )
                new_status = advance_transport(
                    t.transport_id,
                    current_user.id,
                    allow_skip=allow_skip,
                    to_status=action,
                )
                db.session.commit()
                msg = f"Transport {t.transport_id} → {new_status} (batch updated)"
            else:
                msg = "ไม่พบ waste_id หรือ transport_id นี้"
        except FlowError as e:
            # Log invalid update incident
            db.session.add(
                Incident(
                    type="invalid_update", ref_id=code, detail=str(e), severity="red"
                )
            )
            db.session.commit()
            return render_template("status_scan.html", message=f"Invalid: {e}"), 400

    return render_template("status_scan.html", message=msg)


@bp.route("/export/excel")
@login_required
@require_roles("manager", "staff")
def export_excel():
    from_str = request.args.get("from")
    to_str = request.args.get("to")
    now = datetime.utcnow()
    dt_from = (
        datetime.fromisoformat(from_str) if from_str else (now - timedelta(days=30))
    )
    dt_to = datetime.fromisoformat(to_str) if to_str else now

    rows = []
    for w in WastePackage.query.all():
        rows.append(
            {
                "waste_id": w.waste_id,
                "waste_type": w.waste_type,
                "weight_kg": float(w.weight_kg),
                "hospital_id": w.hospital_id,
                "dept_id": w.dept_id,
                "collected_time": w.collected_time,
            }
        )
    df = pd.DataFrame(rows)

    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="wastes")
    out.seek(0)
    return send_file(
        out,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="medwaste_export.xlsx",
    )


@bp.route("/export/pdf")
@login_required
@require_roles("manager", "staff")
def export_pdf():
    buf = BytesIO()
    c = canvas.Canvas(buf)
    c.setFont("Helvetica", 12)
    c.drawString(50, 800, "MedWaste Report")
    y = 770
    items = WastePackage.query.limit(30).all()
    for w in items:
        c.drawString(
            50,
            y,
            f"{w.waste_id} | {w.waste_type} | {float(w.weight_kg)} kg | {w.hospital_id}/{w.dept_id}",
        )
        y -= 18
        if y < 60:
            c.showPage()
            y = 800
    c.save()
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="medwaste_report.pdf",
    )
