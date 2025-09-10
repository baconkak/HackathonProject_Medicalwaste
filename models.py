from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Enum, UniqueConstraint, ForeignKeyConstraint
from datetime import datetime

db = SQLAlchemy()

WASTE_TYPES = (
    "infectious",
    "sharps",
    "pathological",
    "chemical",
    "pharmaceutical",
    "genotoxic",
    "radioactive",
    "general",
)
DISPOSAL_METHODS = ("Autoclave", "Incineration", "Chemical Treatment", "Decay Storage")

DISPOSAL_MAPPING = {
    "infectious": "Autoclave",
    "sharps": "Incineration",
    "pathological": "Incineration",
    "chemical": "Chemical Treatment",
    "pharmaceutical": "Incineration",
    "genotoxic": "Incineration",
    "radioactive": "Decay Storage",
}


class Hospital(db.Model):
    __tablename__ = "hospitals"
    hospital_id = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    departments = db.relationship("Department", backref="hospital", lazy=True)


class Department(db.Model):
    __tablename__ = "departments"
    dept_id = db.Column(db.String(20), primary_key=True)
    hospital_id = db.Column(
        db.String(20), db.ForeignKey("hospitals.hospital_id"), nullable=False
    )
    name = db.Column(db.String(120), nullable=False)
    __table_args__ = (
        UniqueConstraint("hospital_id", "name", name="uq_dept_per_hospital"),
    )


class Role(db.Model):
    __tablename__ = "roles"
    role_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)


class User(db.Model):
    __tablename__ = "users"
    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.role_id"), nullable=False)
    hospital_id = db.Column(db.String(20), db.ForeignKey("hospitals.hospital_id"))
    dept_id = db.Column(db.String(20), db.ForeignKey("departments.dept_id"))
    transport_code = db.Column(db.String(50))
    role = db.relationship("Role", backref=db.backref("users", lazy=True))


class WastePackage(db.Model):
    __tablename__ = "waste_packages"
    waste_id = db.Column(db.String(40), primary_key=True)
    waste_type = db.Column(Enum(*WASTE_TYPES, name="waste_type_enum"), nullable=False)
    weight_kg = db.Column(db.Numeric(6, 2), nullable=False)
    hospital_id = db.Column(
        db.String(20), db.ForeignKey("hospitals.hospital_id"), nullable=False
    )
    dept_id = db.Column(
        db.String(20), db.ForeignKey("departments.dept_id"), nullable=False
    )
    collected_time = db.Column(db.DateTime)
    tracking_code = db.Column(db.String(40), unique=True)

    # แก้ไขความสัมพันธ์ให้ชัดเจนขึ้นสำหรับ StatusEvent
    status_events = db.relationship(
        "StatusEvent",
        primaryjoin="and_(StatusEvent.ref_id==WastePackage.waste_id, StatusEvent.ref_type=='waste')",
        backref="waste_ref",  # เปลี่ยน backref เพื่อป้องกันชื่อซ้ำ
        lazy=True,
    )


class Transport(db.Model):
    __tablename__ = "transports"
    transport_id = db.Column(db.String(40), primary_key=True)
    transport_by = db.Column(db.String(80))
    vehicle_plate = db.Column(db.String(20))
    planned_route_geojson = db.Column(db.Text)
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)

    # เพิ่มความสัมพันธ์ไปยัง WasteOnTransport
    waste_on_transport = db.relationship(
        "WasteOnTransport", backref="transport_ref", lazy=True
    )

    # เพิ่มความสัมพันธ์ไปยัง StatusEvent
    status_events = db.relationship(
        "StatusEvent",
        primaryjoin="and_(StatusEvent.ref_id==Transport.transport_id, StatusEvent.ref_type=='transport')",
        backref="transport_ref",
        lazy=True,
    )


class WasteOnTransport(db.Model):
    __tablename__ = "waste_on_transport"
    id = db.Column(db.Integer, primary_key=True)
    transport_id = db.Column(
        db.String(40), db.ForeignKey("transports.transport_id"), nullable=False
    )
    waste_id = db.Column(
        db.String(40), db.ForeignKey("waste_packages.waste_id"), nullable=False
    )

    # เพิ่ม backref เพื่อให้เข้าถึงข้อมูล WastePackage ได้จาก WasteOnTransport
    waste_package = db.relationship(
        "WastePackage", backref=db.backref("transport_links", lazy=True)
    )

    # เพิ่ม UniqueConstraint เพื่อป้องกันข้อมูลซ้ำ
    __table_args__ = (
        UniqueConstraint("transport_id", "waste_id", name="uq_waste_on_transport"),
    )


class Disposal(db.Model):
    __tablename__ = "disposals"
    id = db.Column(db.Integer, primary_key=True)
    waste_id = db.Column(
        db.String(40), db.ForeignKey("waste_packages.waste_id"), nullable=False
    )
    disposal_name = db.Column(db.String(120))
    disposal_method = db.Column(Enum(*DISPOSAL_METHODS, name="disposal_method_enum"))
    disposal_time = db.Column(db.DateTime)

    # เพิ่มความสัมพันธ์ backref เพื่อเข้าถึงข้อมูล Disposal จาก WastePackage
    waste_package = db.relationship(
        "WastePackage", backref=db.backref("disposal", uselist=False)
    )


class StatusEvent(db.Model):
    __tablename__ = "status_events"
    id = db.Column(db.Integer, primary_key=True)
    ref_type = db.Column(
        Enum("waste", "transport", name="ref_type_enum"), nullable=False
    )
    ref_id = db.Column(db.String(40), nullable=False)
    status = db.Column(
        Enum(
            "Collected",
            "In Transit",
            "Arrived Disposal Site",
            "In Disposal",
            "Completed",
            name="status_enum",
        ),
        nullable=False,
    )
    at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    by_user = db.Column(db.Integer, db.ForeignKey("users.user_id"))
    note = db.Column(db.String(255))

    # แก้ไขความสัมพันธ์ไปหา WastePackage และ Transport
    # เนื่องจากเป็น Polymorphic relationship จึงต้องสร้าง ForeignKeyConstraint
    # เพื่อให้ SQLAlchemy เข้าใจว่า ref_id อ้างอิงถึงคอลัมน์ใด
    __table_args__ = (
        ForeignKeyConstraint(
            ["ref_id"], ["waste_packages.waste_id"], name="fk_status_event_waste"
        ),
        ForeignKeyConstraint(
            ["ref_id"], ["transports.transport_id"], name="fk_status_event_transport"
        ),
    )


class GpsPoint(db.Model):
    __tablename__ = "gps_points"
    id = db.Column(db.Integer, primary_key=True)
    transport_id = db.Column(
        db.String(40), db.ForeignKey("transports.transport_id"), nullable=False
    )
    at = db.Column(db.DateTime, default=datetime.utcnow)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    speed = db.Column(db.Float)

    transport = db.relationship(
        "Transport", backref=db.backref("gps_points", lazy=True)
    )


class Incident(db.Model):
    __tablename__ = "incidents"
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(
        Enum(
            "route_deviation",
            "overdue_collected",
            "invalid_update",
            name="incident_type_enum",
        ),
        nullable=False,
    )
    ref_id = db.Column(db.String(40), nullable=False)
    detail = db.Column(db.String(255))
    at = db.Column(db.DateTime, default=datetime.utcnow)
    severity = db.Column(db.String(10))
    by_user = db.Column(db.Integer, db.ForeignKey("users.user_id"))

    user_ref = db.relationship("User", backref=db.backref("incidents", lazy=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["ref_id"], ["waste_packages.waste_id"], name="fk_incident_waste"
        ),
        ForeignKeyConstraint(
            ["ref_id"], ["transports.transport_id"], name="fk_incident_transport"
        ),
    )
