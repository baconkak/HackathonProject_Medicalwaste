import csv
from io import StringIO
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
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
    WASTE_TYPES,
    DISPOSAL_METHODS,
    DISPOSAL_MAPPING,
)
bp = Blueprint("upload", __name__)

DATE_FMTS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]

ALLOWED_HEADERS = {
    "waste_id",
    "waste_type",
    "weight_kg",
    "hospital_id",
    "department",
    "collected_time",
    "transport_id",
    "transport_by",
    "transport_start",
    "transport_end",
    "disposal_name",
    "disposal_method",
    "disposal_time",
}


def parse_dt(val):
    if not val:
        return None
    for f in DATE_FMTS:
        try:
            return datetime.strptime(val.strip(), f)
        except Exception:
            pass
    raise ValueError(f"Invalid datetime format: {val}")


def norm_waste_type(v):
    return (v or "").strip().lower()


def validate_and_collect(rows):
    errors = []
    seen_in_file = set()
    hospitals = {h.hospital_id for h in Hospital.query.all()}
    # map (hospital_id, dept_name) -> dept_id
    dept_map = {
        (d.hospital_id, d.name.lower()): d.dept_id for d in Department.query.all()
    }

    # Existing wastes for duplicate check
    existing = {
        w.waste_id
        for w in WastePackage.query.with_entities(WastePackage.waste_id).all()
    }

    collected = []
    for i, r in enumerate(rows, start=2):  # header = line 1
        line = i
        w_id = (r.get("waste_id") or "").strip()
        w_type = norm_waste_type(r.get("waste_type"))
        weight = r.get("weight_kg")
        hosp = (r.get("hospital_id") or "").strip()
        dept_name = (r.get("department") or "").strip()
        collected_time = r.get("collected_time")
        tr_id = (r.get("transport_id") or "").strip() or None
        tr_by = (r.get("transport_by") or "").strip() or None
        tr_start = r.get("transport_start")
        tr_end = r.get("transport_end")
        disp_name = (r.get("disposal_name") or "").strip() or None
        disp_method = (r.get("disposal_method") or "").strip() or None
        disp_time = r.get("disposal_time")

        # Header presence check is done outside
        if not w_id:
            errors.append(f"แถว {line}: missing waste_id")
        if w_id in seen_in_file:
            errors.append(f"แถว {line}: waste_id ซ้ำในไฟล์เดียวกัน")
        if w_id in existing:
            errors.append(f"แถว {line}: waste_id ซ้ำกับข้อมูลเดิม")
        seen_in_file.add(w_id)

        if w_type not in WASTE_TYPES:
            errors.append(f"แถว {line}: waste_type '{r.get('waste_type')}' ไม่อยู่ใน ENUM")

        try:
            wval = float(weight)
            if wval <= 0:
                errors.append(f"แถว {line}: weight_kg ≤ 0")
        except Exception:
            errors.append(f"แถว {line}: weight_kg ไม่ใช่ตัวเลขถูกต้อง")

        if hosp not in hospitals:
            errors.append(f"แถว {line}: hospital_id '{hosp}' ไม่พบ")

        dept_id = dept_map.get((hosp, dept_name.lower()))
        if not dept_id:
            errors.append(
                f"แถว {line}: department '{dept_name}' ไม่ตรงกับ hospital_id '{hosp}'"
            )

        # Datetimes
        try:
            ct = parse_dt(collected_time) if collected_time else None
        except ValueError:
            errors.append(f"แถว {line}: collected_time ฟอร์แมตไม่ถูกต้อง")
            ct = None
        try:
            tstart = parse_dt(tr_start) if tr_start else None
        except ValueError:
            errors.append(f"แถว {line}: transport_start ฟอร์แมตไม่ถูกต้อง")
            tstart = None
        try:
            tend = parse_dt(tr_end) if tr_end else None
        except ValueError:
            errors.append(f"แถว {line}: transport_end ฟอร์แมตไม่ถูกต้อง")
            tend = None
        try:
            dtime = parse_dt(disp_time) if disp_time else None
        except ValueError:
            errors.append(f"แถว {line}: disposal_time ฟอร์แมตไม่ถูกต้อง")
            dtime = None

        # Disposal mapping rule
        if disp_method:
            # Normalise to title case choice
            normalized = None
            for dm in DISPOSAL_METHODS:
                if dm.lower() == disp_method.lower():
                    normalized = dm
                    break
            if not normalized:
                errors.append(
                    f"แถว {line}: disposal_method '{disp_method}' ไม่อยู่ใน ENUM"
                )
            else:
                required = DISPOSAL_MAPPING.get(w_type)
                if required and normalized != required:
                    errors.append(
                        f"แถว {line}: disposal_method '{normalized}' ไม่เข้ากับ waste_type '{w_type}' (ควรเป็น {required})"
                    )

        collected.append((r, dept_id))

    return errors, collected


@bp.route("/upload/csv", methods=["GET", "POST"])
@login_required
def upload_csv():
    if request.method == "GET":
        departments = Department.query.filter_by(hospital_id=current_user.hospital_id).all()
        return render_template("upload.html", departments=departments, waste_types=WASTE_TYPES)

    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "errors": ["ไม่พบไฟล์"]}), 400

    text = file.stream.read().decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    headers = (
        set([h.strip() for h in reader.fieldnames]) if reader.fieldnames else set()
    )
    missing = ALLOWED_HEADERS.intersection(ALLOWED_HEADERS) - headers  #require at least allowed subset present
    # We accept subset but require the core ones
    core = {'waste_id','waste_type','weight_kg','hospital_id','department'}
    if not core.issubset(headers):
        return jsonify({"ok": False, "errors": [f"header ขาด: {', '.join(sorted(core - headers))}"]}), 400

    rows = list(reader)
    errors, collected = validate_and_collect(rows)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Insert
    created = 0
    for r, dept_id in collected:
        w = WastePackage(
            waste_id=r['waste_id'].strip(),
            waste_type=r['waste_type'].strip().lower(),
            weight_kg=float(r['weight_kg']),
            hospital_id=r['hospital_id'].strip(),
            dept_id=dept_id,
            collected_time=parse_dt(r.get('collected_time')) if r.get('collected_time') else None,
        )
        db.session.add(w)
        created += 1

        # Transport (optional upsert)
        tr_id = (r.get('transport_id') or '').strip()
        if tr_id:
            tr = Transport.query.get(tr_id)
            if not tr:
                tr = Transport(transport_id=tr_id, transport_by=(r.get('transport_by') or '').strip())
                db.session.add(tr)
            # Join mapping
            db.session.add(WasteOnTransport(transport_id=tr_id, waste_id=w.waste_id))

        # Disposal (optional)
        disp_method = (r.get('disposal_method') or '').strip()
        disp_time = parse_dt(r.get('disposal_time')) if r.get('disposal_time') else None
        disp_name = (r.get('disposal_name') or '').strip() or None
        if disp_method or disp_time or disp_name:
            # Normalize method to declared enum (title case)
            normalized = None
            for dm in ("Autoclave","Incineration","Chemical Treatment","Decay Storage"):
                if disp_method and dm.lower() == disp_method.lower():
                    normalized = dm
                    break
            d = Disposal(
                waste_id=w.waste_id,
                disposal_name=disp_name,
                disposal_method=normalized,
                disposal_time=disp_time,
            )
            db.session.add(d)

        # Initial status if collected_time present
        if w.collected_time:
            db.session.add(
                StatusEvent(ref_type="waste", ref_id=w.waste_id, status="Collected")
            )

    try:
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return jsonify({"ok": False, "errors": ["DB error: " + str(e)]}), 400

    return jsonify({"ok": True, "message": f"อัปโหลดข้อมูลสำเร็จ ({created} records)"})


@bp.route("/waste/add", methods=["POST"])
@login_required
def add_waste():
    weight = request.form.get("weight")
    dept_id = request.form.get("department_id")
    waste_type = request.form.get("waste_type")

    if not weight or not dept_id or not waste_type:
        flash("Weight, department, and waste type are required.", "error")
        return redirect(url_for("upload.upload_csv"))

    if waste_type not in WASTE_TYPES:
        flash("Invalid waste type.", "error")
        return redirect(url_for("upload.upload_csv"))

    try:
        weight_kg = float(weight)
    except ValueError:
        flash("Invalid weight.", "error")
        return redirect(url_for("upload.upload_csv"))

    # Generate a unique waste_id
    waste_id = f"W-{current_user.hospital_id}-{int(datetime.utcnow().timestamp())}"

    new_waste = WastePackage(
        waste_id=waste_id,
        waste_type=waste_type,
        weight_kg=weight_kg,
        hospital_id=current_user.hospital_id,
        dept_id=dept_id,
        collected_time=datetime.utcnow(),
    )
    db.session.add(new_waste)

    # Add a status event
    status_event = StatusEvent(
        ref_type="waste",
        ref_id=waste_id,
        status="Collected",
        by_user=current_user.id
    )
    db.session.add(status_event)

    try:
        db.session.commit()
        flash(f"Waste package {waste_id} added successfully.", "success")
    except IntegrityError as e:
        db.session.rollback()
        flash(f"Error adding waste package: {e}", "error")

    return redirect(url_for("upload.upload_csv"))
