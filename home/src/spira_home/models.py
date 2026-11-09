from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> float:
    return datetime.now(timezone.utc).timestamp()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class StateCondition(Strict):
    target: str
    property: str
    equals: Any = None
    minimum: float | None = None
    maximum: float | None = None
    stable_seconds: float = Field(default=0, ge=0, le=259200)

    def matches(self, values):
        if self.property not in values:
            return False
        value = values[self.property]
        if self.minimum is not None or self.maximum is not None:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            return (self.minimum is None or value >= self.minimum) and (self.maximum is None or value <= self.maximum)
        return value == self.equals


class Trigger(Strict):
    type: Literal['immediate', 'at', 'raw_event.v1', 'first_guest_arrival.v1',
                  'package_brought_in.v1', 'event_end.v1', 'departed.v1', 'returned.v1',
                  'door_cycle.v1', 'state.v1'] = 'immediate'
    at: float | None = None
    event_type: str | None = None
    source: str | None = None
    payload_equals: dict[str, Any] = Field(default_factory=dict)
    bell_source: str | None = None
    contact_source: str | None = None
    camera_source: str | None = None
    window_seconds: float = Field(default=60, gt=0, le=300)
    quiet_seconds: float = Field(default=1800, gt=0, le=86400)
    fallback_at: float | None = None
    condition: StateCondition | None = None

    @model_validator(mode='after')
    def complete(self):
        if self.type in ('at', 'event_end.v1', 'departed.v1', 'returned.v1') and self.at is None:
            raise ValueError('timed rule requires at')
        if self.type == 'raw_event.v1' and not (self.event_type and self.source):
            raise ValueError('raw event requires source and event_type')
        if self.type == 'first_guest_arrival.v1' and not (self.bell_source and self.contact_source):
            raise ValueError('arrival rule requires explicit bell/contact sources')
        if self.type == 'package_brought_in.v1' and not (self.camera_source and self.contact_source):
            raise ValueError('package rule requires explicit camera/contact sources')
        if self.type in ('event_end.v1', 'departed.v1', 'returned.v1', 'door_cycle.v1') and not self.contact_source:
            raise ValueError('local rule requires contact source')
        if self.type == 'state.v1' and not self.condition:
            raise ValueError('state rule requires condition')
        return self


class End(Strict):
    trigger: Trigger | None = None
    alternatives: list[Trigger] = Field(default_factory=list)
    after_seconds: float | None = Field(default=None, gt=0, le=1209600)
    completion: StateCondition | None = None

    @model_validator(mode='after')
    def complete(self):
        if not (self.trigger or self.alternatives or self.after_seconds or self.completion):
            raise ValueError('end condition required')
        return self


class Retry(Strict):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_seconds: float = Field(default=1, gt=0, le=60)


class Precheck(Strict):
    kind: Literal['not_in_use', 'not_already_done', 'quiet_hours', 'room']
    target: str | None = None
    property: str | None = None
    value: Any = None
    timezone: str = 'Asia/Seoul'
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)


class Task(Strict):
    schema_version: Literal[1] = 1
    episode_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    plan_version: int = Field(ge=1)
    task_id: str = Field(min_length=1, max_length=128)
    line_id: str | None = None
    logical_command: str | None = None
    target: str = Field(min_length=1, max_length=128)
    family: Literal['SET_STATE', 'CONFIGURE_FOR_NEXT_USE', 'RUN_JOB', 'MAINTAIN_CONDITION', 'PRESENT_INFORMATION', 'STOP', 'ABSTAIN'] = 'SET_STATE'
    desired: dict[str, Any] = Field(default_factory=dict, max_length=32)
    trigger: Trigger = Field(default_factory=Trigger)
    valid_from: float = 0
    valid_until: float
    preconditions: dict[str, Any] = Field(default_factory=dict)
    prechecks: list[Precheck] = Field(default_factory=list)
    temporary: bool = False
    restore: bool = False
    policy: Literal['ALLOW', 'PROHIBITED'] = 'ALLOW'
    verify_seconds: float = Field(default=1, ge=0, le=30)
    end: End | None = None
    retry: Retry = Field(default_factory=Retry)

    @model_validator(mode='after')
    def complete(self):
        if self.valid_until <= self.valid_from:
            raise ValueError('valid_until must follow valid_from')
        if self.restore and not self.temporary:
            raise ValueError('restore requires temporary effect')
        if self.family != 'ABSTAIN' and not self.desired:
            raise ValueError('desired effect required')
        return self

    @property
    def key(self):
        import json
        return json.dumps([self.plan_id, self.plan_version, self.task_id], separators=(',', ':'))


class Observation(Strict):
    target: str
    values: dict[str, Any]
    revisions: dict[str, str] = Field(default_factory=dict)
    observed_at: float = Field(default_factory=now)
    provenance: str = 'UNKNOWN'
    substrate: str
    available: bool = True


class Capability(Strict):
    target: str
    writable: list[str]
    families: list[str] = Field(default_factory=lambda: ['SET_STATE'])
    available: bool = True
    conditional_write: bool = False
    substrate: str
    device_type: str = 'generic'
    room: str | None = None
    value_schema: dict[str, Any] = Field(default_factory=dict)
    events: list[str] = Field(default_factory=list)
    commands: list[dict[str, Any]] = Field(default_factory=list)
    assumption: str | None = None
    noisy: bool = False


class Receipt(Strict):
    status: Literal['ACCEPTED', 'REJECTED', 'UNAVAILABLE', 'UNSUPPORTED', 'CONFLICT', 'PROHIBITED', 'UNKNOWN']
    operation_id: str
    detail: str | None = None
    job_id: str | None = None


class Event(Strict):
    event_id: str
    type: str
    source: str
    at: float = Field(default_factory=now)
    provenance: str = 'UNKNOWN'
    substrate: str
    payload: dict[str, Any] = Field(default_factory=dict)
    observation: Observation | None = None
