from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import Enum, UniqueConstraint

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

# Mapping (waste_type -> required disposal method)
DISPOSAL_MAPPING = {
    "infectious": "Autoclave",
    "sharps": "Incineration",
    "pathological": "Incineration",
    "chemical": "Chemical Treatment",
    "pharmaceutical": "Incineration",
    "genotoxic": "Incineration",
    "radioactive": "Decay Storage",
    # "general": (no forced method for demo)
}

class Hospital(db.Model):
    __tablename__ = 'hospitals'
    hospital_id = db.Column(db.String(20), primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    departments = db.relationship('Department', backref='hospital', lazy=True)

class Department(db.Model):
    __tablename__ = 'departments'
    dept_id = db.Column(db.String(20), primary_key=True)
    hospital_id = db.Column(db.String(20), db.ForeignKey('hospitals.hospital_id'), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    __table_args__ = (UniqueConstraint('hospital_id', 'name', name='uq_dept_per_hospital'),)

class Role(db.Model):
    __tablename__ = 'roles'
    role_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)  # manager, staff, transport

class User(db.Model):
    __tablename__ = 'users'
    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey('roles.role_id'), nullable=False)
    hospital_id = db.Column(db.String(20), db.ForeignKey("hospitals.hospital_id"))
    dept_id = db.Column(db.String(20), db.ForeignKey("departments.dept_id"))
    transport_code = db.Column(db.String(50))  # e.g., TRUCK001
    role = db.relationship("Role")


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
    status_events = db.relationship(
        "StatusEvent",
        backref="waste",
        lazy=True,
        primaryjoin="and_(StatusEvent.ref_id==WastePackage.waste_id, "
        "StatusEvent.ref_type=='waste')",
    )


class Transport(db.Model):
    __tablename__ = "transports"
    transport_id = db.Column(db.String(40), primary_key=True)
    transport_by = db.Column(db.String(80))  # match user's transport_code for RBAC
    vehicle_plate = db.Column(db.String(20))
    planned_route_geojson = db.Column(db.Text)  # LineString
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    wastes = db.relationship("WasteOnTransport", backref="transport", lazy=True)


class WasteOnTransport(db.Model):
    __tablename__ = "waste_on_transport"
    id = db.Column(db.Integer, primary_key=True)
    transport_id = db.Column(
        db.String(40), db.ForeignKey("transports.transport_id"), nullable=False
    )
    waste_id = db.Column(
        db.String(40), db.ForeignKey("waste_packages.waste_id"), nullable=False
    )


class Disposal(db.Model):
    __tablename__ = "disposals"
    id = db.Column(db.Integer, primary_key=True)
    waste_id = db.Column(
        db.String(40), db.ForeignKey("waste_packages.waste_id"), nullable=False
    )
    disposal_name = db.Column(db.String(120))
    disposal_method = db.Column(Enum(*DISPOSAL_METHODS, name='disposal_method_enum'))
    disposal_time = db.Column(db.DateTime)

class StatusEvent(db.Model):
    __tablename__ = 'status_events'
    id = db.Column(db.Integer, primary_key=True)
    ref_type = db.Column(Enum('waste','transport', name='ref_type_enum'), nullable=False)
    ref_id = db.Column(db.String(40), nullable=False)
    status = db.Column(Enum('Collected','On Truck','In Transit','Arrived Disposal Site','In Disposal','Completed', name='status_enum'), nullable=False)
    at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    by_user = db.Column(db.Integer, db.ForeignKey('users.user_id'))
    note = db.Column(db.String(255))

class GpsPoint(db.Model):
    __tablename__ = 'gps_points'
    id = db.Column(db.Integer, primary_key=True)
    transport_id = db.Column(db.String(40), db.ForeignKey('transports.transport_id'), nullable=False)
    at = db.Column(db.DateTime, default=datetime.utcnow)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    speed = db.Column(db.Float)

class Incident(db.Model):
    __tablename__ = 'incidents'
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(Enum('route_deviation','overdue_collected','invalid_update', name='incident_type_enum'), nullable=False)
    ref_id = db.Column(db.String(40), nullable=False)
    detail = db.Column(db.String(255))
    at = db.Column(db.DateTime, default=datetime.utcnow)
    severity = db.Column(db.String(10))  # red/orange/green
