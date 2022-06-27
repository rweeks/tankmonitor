from collections import deque, namedtuple

default_max_log_records = 1440
default_alert_rate_threshold = -2.0
TankLogRecord = namedtuple("TankLogRecord", "timestamp value")
TankAlert = namedtuple("TankAlert", "timestamp value delta")


def find_delta(record, prev_rec):
    """Returns the interval in seconds and the rate of change per minute between the two
    log records. Returns None, None if the interval is zero (rate of change is infinite)"""
    if prev_rec is None:
        return None, None
    interval = record.timestamp - prev_rec.timestamp
    if interval == 0:
        return None, None
    return interval, 60.0 * (record.value - prev_rec.value) / interval


class TankLogger:
    def __init__(self, log_interval, max_log_records=default_max_log_records,
                 alert_rate_threshold=default_alert_rate_threshold,
                 comparator=lambda d, t : d < t):
        self.log_interval = log_interval
        self.next_capture = 0
        self.alert_rate_threshold = alert_rate_threshold
        self.records = deque(maxlen=max_log_records)

    def offer(self, tank_log_record):
        """May add the given tank record to the buffer, if it hasn't already added a record to the
        buffer for the current log interval"""
        if tank_log_record.timestamp > self.next_capture:
            self.records.append(tank_log_record)
            self.next_capture = tank_log_record.timestamp + self.log_interval
            prev_rec = self.records[-1] if self.records else None
            if prev_rec:
                interval, delta = find_delta(tank_log_record, prev_rec)
                if delta is not None and self.alert_rate_threshold is not None and self.comparator(delta, self.alert_rate_threshold):
                    return TankAlert(tank_log_record.timestamp,
                                     tank_log_record.value,
                                     delta)

    @property
    def deltas(self):
        dlog = []
        prev_rec = None
        for record in self.records:
            if prev_rec is None:
                prev_rec = record
                continue
            interval, delta = find_delta(record, prev_rec)
            if not interval:
                continue
            dlog.append(TankLogRecord(
                timestamp=prev_rec.timestamp + 0.5*interval,
                # Actually change in value per minute
                value=delta
            ))
            prev_rec = record
        return dlog