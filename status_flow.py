from datetime import datetime
from models import db, StatusEvent, WasteOnTransport, WastePackage

WASTE_FLOW = [
    "Collected",
    "On Truck",
    "In Transit",
    "Arrived Disposal Site",
    "In Disposal",
    "Completed",
]
TRANSPORT_FLOW = [
    "In Transit",
    "Arrived Disposal Site",
    "Completed",
]  # Planned shown implicitly
class FlowError(Exception):
    pass

def _next(flow, current):
    if current is None:
        return flow[0]
    try:
        i = flow.index(current)
        return flow[i+1]
    except (ValueError, IndexError):
        raise FlowError(f"Invalid transition from {current}")


def latest_status(ref_type, ref_id):
    e = (StatusEvent.query
         .filter_by(ref_type=ref_type, ref_id=ref_id)
         .order_by(StatusEvent.at.desc()).first())
    return e.status if e else None


def advance_waste(waste_id, user_id, allow_skip=False, to_status=None):
    cur = latest_status('waste', waste_id)
    target = to_status if (allow_skip and to_status) else _next(WASTE_FLOW, cur)
    # Validate monotonic forward-only unless allow_skip True
    if cur and target:
        ci = WASTE_FLOW.index(cur)
        ti = WASTE_FLOW.index(target)
        if ti <= ci:
            raise FlowError("Cannot go backwards or repeat same status")
        if (ti - ci) > 1 and not allow_skip:
            raise FlowError("Cannot skip statuses")
    ev = StatusEvent(ref_type='waste', ref_id=waste_id, status=target, by_user=user_id, at=datetime.utcnow())
    db.session.add(ev)
    db.session.flush()
    return target


def advance_transport(transport_id, user_id, allow_skip=False, to_status=None):
    cur = latest_status('transport', transport_id)
    target = to_status if (allow_skip and to_status) else _next(TRANSPORT_FLOW, cur)
    if cur and target:
        ci = TRANSPORT_FLOW.index(cur)
        ti = TRANSPORT_FLOW.index(target)
        if ti <= ci:
            raise FlowError("Cannot go backwards or repeat same status")
        if (ti - ci) > 1 and not allow_skip:
            raise FlowError("Cannot skip statuses")
    ev = StatusEvent(ref_type='transport', ref_id=transport_id, status=target, by_user=user_id, at=datetime.utcnow())
    db.session.add(ev)
    db.session.flush()
    # If batch: when transport starts moving to In Transit etc., cascade to all wastes in that transport
    if target in ("On Truck","In Transit","Arrived Disposal Site","In Disposal","Completed"):
        for wot in WasteOnTransport.query.filter_by(transport_id=transport_id).all():
            # Advance waste correspondingly (best-effort, ignore errors if already ahead)
            try:
                advance_waste(wot.waste_id, user_id)
            except Exception:
                pass
    return target
