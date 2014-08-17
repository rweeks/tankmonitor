from collections import deque, namedtuple

default_max_log_records = 1000
TankLogRecord = namedtuple("TankLogRecord", "timestamp depth")

class TankLogger:
    def __init__(self, log_interval, max_log_records=default_max_log_records):
        self.log_interval = log_interval
        self.next_capture = 0
        self.records = deque(maxlen=max_log_records)

    def offer(self, tank_log_record):
        if tank_log_record.timestamp > self.next_capture:
            self.records.append(tank_log_record)
            self.next_capture = tank_log_record.timestamp + self.log_interval

