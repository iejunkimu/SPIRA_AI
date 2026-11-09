"""Small shared versioned local fact rules, not purchase-intent detectors."""
from spira_home.models import Trigger


def evaluate(rule: Trigger, events, timestamp, lower):
    facts = [e for e in events if lower <= e.at <= timestamp]
    if rule.type == 'immediate':
        return True, []
    if rule.type == 'at':
        return timestamp >= rule.at, []
    if rule.type == 'raw_event.v1':
        matches = [e for e in facts if e.source == rule.source and e.type == rule.event_type and all(e.payload.get(k)==v for k,v in rule.payload_equals.items())]
        return bool(matches), [e.event_id for e in matches[-1:]]
    if rule.type == 'first_guest_arrival.v1':
        bells = [e for e in facts if e.source == rule.bell_source and e.type == 'doorbell.pressed']
        opened = [e for e in facts if e.source == rule.contact_source and e.type in ('contact.opened', 'doorlock.opened')]
        for b in bells:
            for c in opened:
                if 0 <= c.at-b.at <= rule.window_seconds:
                    return True, [b.event_id, c.event_id]
    if rule.type == 'package_brought_in.v1':
        removed = [e for e in facts if e.source == rule.camera_source and e.type == 'camera.package_removed'
                   and e.payload.get('removed_count', 0) > 0]
        opened = [e for e in facts if e.source == rule.contact_source and e.type in ('contact.opened', 'doorlock.opened')]
        for r in removed:
            for c in opened:
                if abs(c.at-r.at) <= rule.window_seconds:
                    return True, [r.event_id, c.event_id]
    if rule.type == 'event_end.v1':
        if rule.fallback_at is not None and timestamp >= rule.fallback_at:
            return True, []
        door = [e for e in facts if e.source == rule.contact_source and e.at >= rule.at
                and e.type in ('contact.opened', 'doorlock.opened', 'contact.closed', 'doorlock.locked', 'doorlock.closed', 'doorbell.pressed')]
        closed = [e for e in door if e.type in ('contact.closed', 'doorlock.locked', 'doorlock.closed')]
        if closed and max(e.at for e in door) == closed[-1].at and timestamp-closed[-1].at >= rule.quiet_seconds:
            return True, [closed[-1].event_id]
    if rule.type in ('departed.v1', 'returned.v1'):
        locked = [e for e in facts if e.source == rule.contact_source and e.type == 'doorlock.locked' and e.at >= rule.at]
        if locked and rule.type == 'departed.v1':
            return True, [locked[0].event_id]
        if locked:
            opened = [e for e in facts if e.source == rule.contact_source and e.type in ('doorlock.opened', 'contact.opened') and e.at > locked[0].at]
            if opened:
                return True, [locked[0].event_id, opened[0].event_id]
    if rule.type == 'door_cycle.v1':
        opened = [e for e in facts if e.source == rule.contact_source and e.type.endswith(('.door_opened', '.opened'))]
        closed = [e for e in facts if e.source == rule.contact_source and e.type.endswith(('.door_closed', '.closed'))]
        for a in opened:
            for b in closed:
                if b.at >= a.at:
                    return True, [a.event_id, b.event_id]
    return False, []


def next_rule_time(rule, events, timestamp, lower):
    candidates = []
    if rule.at is not None and rule.at > timestamp:
        candidates.append(rule.at)
    if rule.fallback_at is not None and rule.fallback_at > timestamp:
        candidates.append(rule.fallback_at)
    if rule.type == 'event_end.v1':
        closed = [e for e in events if e.source == rule.contact_source and e.at >= max(lower, rule.at)
                  and e.type in ('contact.closed', 'doorlock.locked', 'doorlock.closed')]
        if closed and closed[-1].at+rule.quiet_seconds > timestamp:
            candidates.append(closed[-1].at+rule.quiet_seconds)
    return min(candidates) if candidates else None
