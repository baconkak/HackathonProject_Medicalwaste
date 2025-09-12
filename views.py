from flask import Blueprint, render_template, request, jsonify, send_file, redirect, url_for, flash
from flask_login import login_required, current_user
from io import BytesIO
from datetime import datetime, timedelta
import pandas as pd
from reportlab.pdfgen import canvas
from sqlalchemy import func
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import os
import logging

def get_time_category(hour):
    if 6 <= hour < 12:
        return "Morning"
    elif 12 <= hour < 18:
        return "Afternoon"
    elif 18 <= hour < 24:
        return "Evening"
    else:
        return "Night"

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
from status_flow import (
    latest_status,
    advance_waste,
    advance_transport,
    FlowError,
    WASTE_FLOW,
)
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
    result_rows = []

    if not q:
        return render_template("search.html", q=q, results=[], WASTE_FLOW=WASTE_FLOW)

    # We need to find all relevant waste packages first.
    waste_ids_to_query = set()

    if stype in ("all", "waste"):
        ws = WastePackage.query.filter(WastePackage.waste_id.like(f"%{q}%")).all()
        for w in ws:
            waste_ids_to_query.add(w.waste_id)

    if stype in ("all", "hospital"):
        # Find hospitals, then find their wastes
        hs = Hospital.query.filter(
            (Hospital.hospital_id.like(f"%{q}%")) | (Hospital.name.like(f"%{q}%"))
        ).all()
        h_ids = {h.hospital_id for h in hs}
        wastes_in_hospitals = WastePackage.query.filter(
            WastePackage.hospital_id.in_(list(h_ids))
        ).all()
        for w in wastes_in_hospitals:
            waste_ids_to_query.add(w.waste_id)

    if stype in ("all", "transport"):
        # Find transports, then find their wastes
        ts = Transport.query.filter(Transport.transport_id.like(f"%{q}%")).all()
        t_ids = {t.transport_id for t in ts}
        wastes_on_transports = WasteOnTransport.query.filter(
            WasteOnTransport.transport_id.in_(list(t_ids))
        ).all()
        for wot in wastes_on_transports:
            waste_ids_to_query.add(wot.waste_id)

    # Now we have a set of all relevant waste IDs. Let's build the rows.
    if waste_ids_to_query:
        wastes = (
            WastePackage.query.filter(WastePackage.waste_id.in_(list(waste_ids_to_query)))
            .order_by(WastePackage.collected_time.desc())
            .all()
        )
        for w in wastes:
            hospital = Hospital.query.get(w.hospital_id)
            current_status = latest_status("waste", w.waste_id)
            transport = None

            # Only show transport if status is In Transit or Arrived Disposal Site
            try:
                if (
                    current_status
                    and WASTE_FLOW.index(current_status)
                    >= WASTE_FLOW.index("In Transit")
                    and WASTE_FLOW.index(current_status)
                    < WASTE_FLOW.index("In Disposal")
                ):
                    transport = (
                        db.session.query(Transport)
                        .join(
                            WasteOnTransport,
                            WasteOnTransport.transport_id == Transport.transport_id,
                        )
                        .filter(WasteOnTransport.waste_id == w.waste_id)
                        .first()
                    )
            except ValueError:  # Status not in WASTE_FLOW
                pass

            result_rows.append(
                {
                    "waste": {
                        "id": w.waste_id,
                        "type": w.waste_type,
                        "weight": float(w.weight_kg),
                        "status": current_status,
                    },
                    "hospital": {
                        "id": hospital.hospital_id, "name": hospital.name
                    }
                    if hospital
                    else None,
                    "transport": {
                        "id": transport.transport_id,
                        "by": transport.transport_by,
                        "plate": transport.vehicle_plate,
                        "status": latest_status("transport", transport.transport_id),
                    }
                    if transport
                    else None,
                }
            )

    return render_template("search.html", q=q, results=result_rows, WASTE_FLOW=WASTE_FLOW)


@bp.route("/waste/<waste_id>")
@login_required
def waste_detail(waste_id):
    w = WastePackage.query.get_or_404(waste_id)
    events = (
        StatusEvent.query.filter_by(ref_type="waste", ref_id=waste_id)
        .order_by(StatusEvent.at.desc())
        .all()
    )

    # Conditionally load transport and disposal info
    trans, disp = None, None
    current_status = latest_status("waste", waste_id)
    try:
        if (
            current_status
            and WASTE_FLOW.index(current_status) >= WASTE_FLOW.index("In Transit")
            and WASTE_FLOW.index(current_status) < WASTE_FLOW.index("In Disposal")
        ):
            trans = (
                db.session.query(Transport)
                .join(
                    WasteOnTransport,
                    WasteOnTransport.transport_id == Transport.transport_id,
                )
                .filter(WasteOnTransport.waste_id == waste_id)
                .first()
            )
        # Disposal info is separate, shown when waste is at or beyond disposal site
        if current_status and WASTE_FLOW.index(current_status) >= WASTE_FLOW.index(
            "Arrived Disposal Site"
        ):
            disp = Disposal.query.filter_by(waste_id=waste_id).first()

    except ValueError:  # Status not in WASTE_FLOW
        pass

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
        WastePackage.collected_time.isnot(None),
        WastePackage.collected_time >= dt_from,
        WastePackage.collected_time <= dt_to
    ).all()

    # KPIs
    total = len(wastes)
    total_weight = sum([w.weight_kg for w in wastes])
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
    
    # Sort by_type by value
    sorted_by_type = sorted(by_type.items(), key=lambda item: item[1], reverse=True)
    by_type_labels = [item[0] for item in sorted_by_type]
    by_type_data = [item[1] for item in sorted_by_type]


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
    if not df.empty:
        df["day"] = pd.to_datetime(df["day"])
        ts = (
            df.groupby(df["day"].dt.date)["count"]
            .sum()
            .reset_index()
            .rename(columns={"day": "day"})
        )
        ts["day"] = ts["day"].astype(str)
        ts = ts.to_dict(orient="records")
    else:
        ts = []

    # Department waste
    by_dept_labels = []
    by_dept_data = []
    try:
        by_dept = (
            db.session.query(
                Department.name, func.sum(WastePackage.weight_kg).label("total_weight")
            )
            .join(WastePackage, WastePackage.dept_id == Department.dept_id)
            .filter(
                WastePackage.collected_time >= dt_from,
                WastePackage.collected_time <= dt_to,
            )
            .group_by(Department.name)
            .order_by("total_weight")
            .all()
        )
        by_dept_labels = [d[0] for d in by_dept]
        by_dept_data = [float(d[1]) for d in by_dept]
        logging.info(f"by_dept_labels: {by_dept_labels}")
        logging.info(f"by_dept_data: {by_dept_data}")
    except Exception as e:
        logging.error(f"Error querying department waste: {e}")

    # Heatmap generation
    heatmap_image_path = None
    cumulative_waste_image_path = None
    if wastes:
        df_heatmap = pd.DataFrame([
            {
                "collected_time": w.collected_time,
                "weight_kg": float(w.weight_kg)
            }
            for w in wastes if w.collected_time is not None
        ])

        if not df_heatmap.empty:
            df_heatmap['hour'] = df_heatmap['collected_time'].dt.hour
            df_heatmap['day_of_week'] = df_heatmap['collected_time'].dt.day_name()
            df_heatmap['time_category'] = df_heatmap['hour'].apply(get_time_category)

            day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

            # Cumulative Waste Plot
            df_cumulative = df_heatmap.sort_values('collected_time')
            df_cumulative['cumulative_weight'] = df_cumulative['weight_kg'].cumsum()

            plt.figure(figsize=(10, 6))
            plt.plot(df_cumulative['collected_time'], df_cumulative['cumulative_weight'])
            plt.xlabel('Date')
            plt.ylabel('Cumulative Waste (kg)')
            plt.title('Cumulative Waste Over Time')
            plt.grid(True)
            plt.tight_layout()

            static_folder = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "static"
            )
            cumulative_waste_image_path_file = os.path.join(
                static_folder, "cumulative_waste.png"
            )
            plt.savefig(cumulative_waste_image_path_file)
            plt.close()
            cumulative_waste_image_path = url_for('static', filename='cumulative_waste.png')

            time_category_order = ['Night', 'Morning', 'Afternoon', 'Evening'] # Order for Y-axis

            # Create a pivot table for the heatmap
            heatmap_data_pivot = df_heatmap.pivot_table(
                index='time_category',
                columns='day_of_week',
                values='weight_kg',
                aggfunc='sum'
            ).reindex(index=time_category_order, columns=day_order).fillna(0)

            plt.figure(figsize=(10, 6))
            sns.heatmap(heatmap_data_pivot, cmap='viridis', annot=True, fmt=".1f", linewidths=.5)
            plt.title('Waste Quantity (kg) per Day and Time Category')
            plt.xlabel('Day of Week')
            plt.ylabel('Time Category')
            plt.tight_layout()

            # Define the path to save the heatmap image
            static_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
            if not os.path.exists(static_folder):
                os.makedirs(static_folder)

            heatmap_filename = 'waste_heatmap.png'
            heatmap_full_path = os.path.join(static_folder, heatmap_filename)

            plt.savefig(heatmap_full_path)
            plt.close() # Close the plot to free up memory
            heatmap_image_path = url_for('static', filename=heatmap_filename)

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
                "detail": ">24h without In Transit",
            }
        )

    # Latest table (last 20)
    latest = (
        StatusEvent.query.filter_by(ref_type="waste")
        .order_by(StatusEvent.at.desc())
        .limit(20)
        .all()
    )

    hospital_name = None
    if current_user.hospital_id:
        hospital = Hospital.query.get(current_user.hospital_id)
        if hospital:
            hospital_name = hospital.name

    return render_template(
    "dashboard.html",
    dt_from=dt_from,
    dt_to=dt_to,
    total=total,
    total_weight=total_weight,
    by_type_labels=by_type_labels,
    by_type_data=by_type_data,
    percent_completed=percent_completed,
    ts=ts,
    latest=latest,
    incidents=incidents,
    hospital_name=hospital_name,
    heatmap_image_path=heatmap_image_path,
    cumulative_waste_image_path=cumulative_waste_image_path,
    by_dept_labels=by_dept_labels,
    by_dept_data=by_dept_data,
)


@bp.route("/status/scan", methods=["GET", "POST"])
@login_required
@require_roles("staff", "transport")
def status_scan():
    msg = None
    if request.method == "POST":
        # This is a multi-stage form. Stage 1: scan code. Stage 2: assign transport.
        if 'transport_id' in request.form:
            waste_id = request.form.get('waste_id')
            transport_id = request.form.get('transport_id')
            # Associate waste with transport
            db.session.add(WasteOnTransport(transport_id=transport_id, waste_id=waste_id))
            # Advance status
            new_status = advance_waste(waste_id, current_user.id, to_status="In Transit")
            db.session.commit()
            flash(f"Waste {waste_id} assigned to transport {transport_id} and is now {new_status}", "success")
            return redirect(url_for('views.waste_detail', waste_id=waste_id))

        code = (request.form.get("code") or "").strip()
        action = request.form.get("action")  # optional target status
        allow_skip = bool(action)
        # Detect type by existence
        w = WastePackage.query.get(code)
        t = Transport.query.get(code) if not w else None
        try:
            if w:
                # Staff restriction: must match hospital
                if (
                    current_user.role == "staff"
                    and current_user.hospital_id != w.hospital_id
                ):
                    return (
                        render_template(
                            "status_scan.html",
                            message="Permission denied: different hospital",
                        ),
                        403,
                    )
                
                # If status is Collected, show transport selection
                current_status = latest_status("waste", w.waste_id)
                if current_status == "Collected":
                    transports = Transport.query.all()
                    return render_template("status_scan.html", waste_to_assign=w, transports=transports)

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

    # GET request: show list of waste for the user's hospital
    wastes_in_hospital = []
    if current_user.hospital_id:
        all_wastes = WastePackage.query.filter_by(hospital_id=current_user.hospital_id).all()
        for w in all_wastes:
            s = latest_status("waste", w.waste_id)
            if s != "Completed":
                wastes_in_hospital.append({"waste": w, "status": s})

    return render_template("status_scan.html", message=msg, wastes=wastes_in_hospital)


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


@bp.route("/help")
@login_required
def help():
    return render_template("help.html")


@bp.route("/status/bulk_update", methods=["POST"])
@login_required
@require_roles("manager", "staff", "transport")
def bulk_update_status():
    data = request.get_json()
    waste_ids = data.get("waste_ids", [])
    action = data.get("action")
    target_status = data.get("target_status")

    if not waste_ids:
        return jsonify({"message": "No waste packages selected.", "status": "error"}), 400

    updated_count = 0
    failed_updates = {}

    for waste_id in waste_ids:
        try:
            if action == "advance":
                advance_waste(waste_id, current_user.id)
            elif action == "set_status":
                if not target_status:
                    failed_updates[waste_id] = "Target status not provided."
                    continue
                advance_waste(waste_id, current_user.id, to_status=target_status)
            updated_count += 1
        except FlowError as e:
            failed_updates[waste_id] = str(e)
        except Exception as e:
            failed_updates[waste_id] = f"An unexpected error occurred: {str(e)}"

    db.session.commit()

    if updated_count == len(waste_ids):
        return jsonify({"message": f"Successfully updated {updated_count} waste package(s).", "status": "success"})
    elif updated_count > 0:
        return jsonify({"message": f"Updated {updated_count} waste package(s). Failed to update {len(failed_updates)} waste package(s).", "failed": failed_updates, "status": "partial_success"})
    else:
        return jsonify({"message": f"Failed to update any waste package. Errors: {failed_updates}", "failed": failed_updates, "status": "error"}), 500
