"""Wall time in normal operation; explicitly isolated virtual time in testbeds."""
import time


class Clock:
    virtual = False

    def now(self):
        return time.time()


class VirtualClock(Clock):
    virtual = True

    def __init__(self, value):
        self.value = float(value)

    def now(self):
        return self.value

    def advance(self, value):
        if value < self.value:
            raise ValueError('virtual time cannot move backwards')
        self.value = float(value)
