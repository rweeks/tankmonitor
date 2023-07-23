from collections import deque
from dataclasses import dataclass
from typing import Optional, List, Union

default_max_log_records: int = 1440
default_alert_rate_threshold: float = -2.0


@dataclass
class TankLogRecord:
    """
    The TankLogRecord class is a dataclass that stores a point on a time series. This
    class is used to represent various qualities of the water pumping system. For example,
    it may be used to describe the rate of change, purity, or the litres of water.
    """
    timestamp: float
    value: float


@dataclass
class TankAlert:
    """
    The TankAlert class is a dataclass representing a single data point that causes an alert
    """
    timestamp: float
    value: float
    delta: Union[None, float]


def find_delta(record: TankLogRecord, prev_rec: TankLogRecord) -> Union[tuple[None, None], tuple[float, float]]:
    """
    The find_delta() function returns a tuple with two pieces of data.

    Information stored in the tuple:
        Index 0: Interval in seconds.
        Index 1: Rate of change per minute between the two log records

    What happens in the interval is 0?
        An interval of zero means that the records were taken at the same time.
        The function will return the tuple (None, None) since the there is no change
        in time and therefore the rate of change is undefined.

    The function will always return either (interval, rate of change) or (None, None)
    """
    if prev_rec is None:
        return None, None
    interval = record.timestamp - prev_rec.timestamp
    if interval == 0:
        return None, None
    return interval, 60.0 * (record.value - prev_rec.value) / interval


class TankLogger:
    def __init__(
            self,
            log_interval: int,  # seconds
            max_log_records: int = default_max_log_records,
            alert_rate_threshold: Union[None, float] = default_alert_rate_threshold,
            comparator=lambda d, t: d < t):

        self.log_interval: float = log_interval
        self.next_capture: int = 0
        self.alert_rate_threshold = alert_rate_threshold
        self.records = deque(maxlen=max_log_records)
        self.comparator = comparator

    def offer(self, tank_log_record: TankLogRecord) -> Optional[TankAlert]:
        """
        The offer() method may add the given TankLogRecord() to the buffer
        only if the buffer does not contain a TankLogRecord() taken at the same time.

        Additionally, the function checks whether the rate of change is
        lower than the alert_rate_threshold. If the comparison is true,
        then the function returns a TankAlert().

        What is the buffer used for?
            The buffer is used to store information for the graph. Every TankLogRecord()
            has a value and a timestamp, so the graphing function will use the information
            to plot a point (representing the TankLogRecord()) on a graph.
        """
        if tank_log_record.timestamp > self.next_capture:
            self.records.append(tank_log_record)
            self.next_capture = tank_log_record.timestamp + self.log_interval
            prev_rec = self.records[-1] if self.records else None
            if prev_rec:
                interval, delta = find_delta(tank_log_record, prev_rec)
                if delta is not None and self.alert_rate_threshold is not None and self.comparator(delta,
                                                                                                   self.alert_rate_threshold):
                    return TankAlert(tank_log_record.timestamp,
                                     tank_log_record.value,
                                     delta)

    @property
    def deltas(self) -> List[TankLogRecord]:
        """
        The deltas() function returns a list containing multiple instances
         of the TankLogRecords() dataclass.

        """
        dlog: List = []
        prev_rec: Optional[TankLogRecord] = None
        for record in self.records:
            if prev_rec is None:
                prev_rec = record
                continue
            interval, delta = find_delta(record, prev_rec)
            if not interval:
                continue
            dlog.append(TankLogRecord(
                timestamp=prev_rec.timestamp + 0.5 * interval,
                # Actually change in value per minute
                value=delta
            ))
            prev_rec = record
        return dlog
