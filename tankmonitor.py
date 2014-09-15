from threading import Lock, Thread
from tornado.web import Application, RequestHandler
from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop, PeriodicCallback
from sockjs.tornado import SockJSRouter, SockJSConnection
import logging
from tanklogger import TankLogger, TankLogRecord
from functools import partial
from datetime import datetime
from time import time
from serial import Serial
import settings
from PIL import Image, ImageDraw, ImageFont
import pcd8544.lcd as lcd
import netifaces as ni
import wiringpi2 as wiringpi

logging.basicConfig()
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
listen_port = 4242
disp_contrast_on = 0xB0
disp_contrast_off = 0x80
disp_font = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf", 34)
disp_font_sm = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf", 9)

BTN_IN = 2   # wiringpi pin IDs
BTN_OUT = 3  # wiringpi pin IDs


class EventConnection(SockJSConnection):
    event_listeners = set()
    def on_open(self, request):
        self.event_listeners.add(self)

    def on_close(self):
        self.event_listeners.remove(self)

    @classmethod
    def notify_all(cls, msg_dict):
        import json
        for event_listener in EventConnection.event_listeners:
            event_listener.send(json.dumps(msg_dict))


class MainPageHandler(RequestHandler):
    def get(self, *args, **kwargs):
        self.render('main.html')

logger_map = {
    '60': 'minute_logger',
    '3600': 'hour_logger'
}


class LogDownloadHandler(RequestHandler):
    def get(self, logger_interval):
        fmt = self.get_argument('format', 'nvd3')  # or tsv
        deltas = self.get_argument('deltas', False)
        logger = getattr(self.application, logger_map[logger_interval], None)
        if logger:
            records = logger.deltas if deltas else logger.records
            if fmt == 'nvd3':
                self.finish({'key': 'Tank Level',
                             'values': list(logger.records)})
            elif fmt == 'tsv':
                self.set_header('Content-Type', 'text/plain')
                if deltas:
                    self.write('"Timestamp"\t"Rate of Change (%s/min)"\n' % settings.LOG_UNIT)
                else:
                    self.write('"Timestamp"\t"%s"\n' % settings.LOG_UNIT)
                self.write_tsv(records)
                self.finish()

    def write_tsv(self, records):
        for record in records:
            timestamp = datetime.fromtimestamp(record.timestamp).strftime('%Y-%m-%d %H:%M:%S')
            self.write(str(timestamp))
            self.write('\t')
            self.write(str(record.depth))
            self.write('\n')


class TankMonitor(Application):
    def __init__(self, handlers=None, **settings):
        super(TankMonitor, self).__init__(handlers, **settings)
        self.minute_logger = TankLogger(60)
        self.hour_logger = TankLogger(3600)
        self.latest_raw_val = None
        self.display_expiry = 0

    def log_tank_depth(self, tank_depth):
        """This method can be called from outside the app's IOLoop. It's the
        only method that can be called like that"""
        log.debug("Logging depth: " + str(tank_depth))
        IOLoop.current().add_callback(partial(self._offer_log_record, time(),
                                              tank_depth))

    def _offer_log_record(self, timestamp, depth):
        log_record = TankLogRecord(timestamp=timestamp, depth=depth)
        for logger in self.minute_logger, self.hour_logger:
            alert = logger.offer(log_record)
            if alert:
                # TODO: e-mail alert
                AlertMailer.offer(alert)
        EventConnection.notify_all({
            'event': 'log_value',
            'timestamp': timestamp,
            'value': depth
        })

    def update_display(self):
        ip_addr = ni.ifaddresses('eth0')[ni.AF_INET][0]['addr']
        now = time()
        if now < self.display_expiry:
            im = Image.new('1', (84, 48))
            draw = ImageDraw.Draw(im)
            draw.text((0, 5), self.latest_raw_val, font=disp_font, fill=1)
            draw.text((0, 0), ip_addr, font=disp_font_sm, fill=1)
            draw.text((5, 36), "mm to surface", font=disp_font_sm, fill=1)
            lcd.show_image(im)
            # clean up
            del draw
            del im
            lcd.set_contrast(disp_contrast_on)
        else:
            lcd.set_contrast(disp_contrast_off)
            lcd.cls()

    def poll_display_button(self):
        btn_down = wiringpi.digitalRead(BTN_IN)
        if btn_down:
            self.display_expiry = time() + 60

    def _set_latest_raw_val(self, val):
        self.latest_raw_val = val

    def set_latest_raw_val(self, val):
        """This method can be called from any thread."""
        IOLoop.instance().add_callback(self._set_latest_raw_val, val)


class MaxbotixHandler():
    def __init__(self, tank_monitor, **kwargs):
        """kwargs will be passed through to the serial port constructor"""
        self.port_lock = Lock()
        self.serial_port = None
        self.set_serial_port(**kwargs)
        self.stop_reading = False
        self.tank_monitor = tank_monitor
        self.calibrate_m = 1
        self.calibrate_b = 0

    def read(self):
        log.info("Starting MaxbotixHandler read")
        val = None
        while not self.stop_reading:
            try:
                with self.port_lock:
                    val = self.serial_port.read()
                    if val == 'R':
                        val = self.serial_port.read(4)
                        self.tank_monitor.set_latest_raw_val(val)
                        self.tank_monitor.log_tank_depth(self.convert(val))
            except:
                print "Unable to convert value '" + str(val) + "'"
                import traceback
                traceback.print_exc()

    def calibrate(self, m, b):
        """ Defines the parameters for a linear equation y=mx+b, which is used
        to convert the output of the sensor to a tank depth.
        """
        log.info("Calibrating Maxbotix interface with m=%2.4f, b=%2.4f" % (m, b))
        self.calibrate_m = float(m)
        self.calibrate_b = float(b)

    def convert(self, val):
        return long(self.calibrate_m * float(val) + self.calibrate_b)

    def shutdown(self):
        self.stop_reading = True

    def set_serial_port(self, **kwargs):
        with self.port_lock:
            self.serial_port = Serial(**kwargs)


class AlertMailer(object):

    last_alert = None

    @staticmethod
    def offer(tank_alert):
        log.warn("Sending e-mail alert due to " + str(tank_alert))
        if AlertMailer.last_alert is None or \
                (time() - AlertMailer.last_alert) > settings.EMAIL['period']:
            pass

if __name__ == "__main__":
    event_router = SockJSRouter(EventConnection, '/event')
    handlers = [
        (r'/', MainPageHandler),
        (r'/logger/(.*)', LogDownloadHandler)  # arg is log interval
    ]
    handlers += event_router.urls
    tornado_settings = {
        'static_path': 'static',
        'template_path': 'templates',
        'debug': True
    }
    lcd.init()
    lcd.gotoxy(0, 0)
    lcd.set_contrast(disp_contrast_on)
    lcd.cls()
    lcd.text("LCD Init")
    wiringpi.pinMode(BTN_OUT, 1)
    wiringpi.digitalWrite(BTN_OUT, 1)
    wiringpi.pinMode(BTN_IN, 0)

    app = TankMonitor(handlers, **tornado_settings)
    maxbotix = MaxbotixHandler(tank_monitor=app, port='/dev/ttyAMA0', timeout=10,
                               **settings.MAXBOTICS)
    ioloop = IOLoop.instance()
    disp_print_cb = PeriodicCallback(app.update_display, callback_time=500, io_loop=ioloop)
    disp_print_cb.start()
    button_poll_cb = PeriodicCallback(app.poll_display_button, callback_time=100, io_loop=ioloop)
    button_poll_cb.start()
    http_server = HTTPServer(app)
    http_server.listen(listen_port)
    log.info("Listening on port " + str(listen_port))
    maxbotix_thread = Thread(target=maxbotix.read)
    maxbotix_thread.daemon = True
    maxbotix_thread.start()
    ioloop.start()
