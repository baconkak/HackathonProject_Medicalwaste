from app import create_app
from models import db, Role, User, Hospital, Department, WastePackage, Transport, WasteOnTransport, StatusEvent
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
import json, os

app = create_app()
with app.app_context():
    db.drop_all(); db.create_all()

    # Roles
    r_mgr = Role(name='manager'); r_staff = Role(name='staff'); r_trans = Role(name='transport')
    db.session.add_all([r_mgr, r_staff, r_trans]); db.session.flush()

    # Hospitals & Depts
    h1 = Hospital(hospital_id='H001', name='Bangkok General', address='Bangkok', lat=13.7563, lng=100.5018)
    h2 = Hospital(hospital_id='H002', name='Chiang Mai Care', address='Chiang Mai', lat=18.7883, lng=98.9853)
    db.session.add_all([h1,h2]); db.session.flush()

    d11 = Department(dept_id='D001', hospital_id='H001', name='ER')
    d12 = Department(dept_id='D002', hospital_id='H001', name='ICU')
    d21 = Department(dept_id='D101', hospital_id='H002', name='ER')
    db.session.add_all([d11,d12,d21])

    # Users
    u1 = User(username='manager1', password_hash=generate_password_hash('password'), role_id=r_mgr.role_id)
    u2 = User(username='staff1', password_hash=generate_password_hash('password'), role_id=r_staff.role_id, hospital_id='H001', dept_id='D001')
    u3 = User(username='transport1', password_hash=generate_password_hash('password'), role_id=r_trans.role_id, transport_code='TRUCK001')
    db.session.add_all([u1,u2,u3])

    # Transport with route
    route = {
        "type": "LineString",
        "coordinates": [
            [100.493, 13.756],
            [100.50, 13.758],
            [100.505, 13.760],
            [100.51, 13.762],
            [100.515, 13.764],
        ],
    }
    t1 = Transport(
        transport_id="T001",
        transport_by="TRUCK001",
        vehicle_plate="9กก1234",
        planned_route_geojson=json.dumps(route),
    )
    db.session.add(t1)

    # Sample waste + mapping to transport
    now = datetime.utcnow()
    w1 = WastePackage(
        waste_id="W0001",
        waste_type="infectious",
        weight_kg=5.0,
        hospital_id="H001",
        dept_id="D001",
        collected_time=now - timedelta(hours=26),
    )
    w2 = WastePackage(
        waste_id="W0002",
        waste_type="sharps",
        weight_kg=2.4,
        hospital_id="H001",
        dept_id="D002",
        collected_time=now - timedelta(hours=1),
    )
    db.session.add_all([w1, w2])
    db.session.flush()

    db.session.add_all(
        [
            WasteOnTransport(transport_id="T001", waste_id="W0001"),
            WasteOnTransport(transport_id="T001", waste_id="W0002"),
        ]
    )

    # Seed statuses
    db.session.add(
        StatusEvent(
            ref_type="waste",
            ref_id="W0001",
            status="Collected",
            at=now - timedelta(hours=26),
        )
    )
    db.session.add(
        StatusEvent(
            ref_type="waste",
            ref_id="W0002",
            status="Collected",
            at=now - timedelta(hours=1),
        )
    )
    db.session.add(
        StatusEvent(
            ref_type="transport",
            ref_id="T001",
            status="In Transit",
            at=now - timedelta(minutes=30),
        )
    )

    db.session.commit()

    print("Seeded database medwaste.db")
