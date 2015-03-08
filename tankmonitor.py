from threading import Lock, Thread
from tornado.web import Application, RequestHandler
from tornado.httpserver import HTTPServer
from tornado.template import Template
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.gen import coroutine
from tornado.concurrent import run_on_executor
from sockjs.tornado import SockJSRouter, SockJSConnection
import logging
from tanklogger import TankLogger, TankLogRecord, TankAlert
from functools import partial
from datetime import datetime
from time import time
from serial import Serial
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor

import smtplib

import settings as appconfig
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

BTN_IN = 2   # wiringpi pin ID
BTN_OUT = 3  # wiringpi pin ID
VALVE_GPIO = 6   # wiringpi pin ID

thread_pool = ThreadPoolExecutor(2)

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
    '10': 'tensec_logger',
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
                             'values': list(records)})
            elif fmt == 'tsv':
                self.set_header('Content-Type', 'text/plain')
                if deltas:
                    self.write('"Timestamp"\t"Rate of Change (%s/min)"\n' % appconfig.LOG_UNIT)
                else:
                    self.write('"Timestamp"\t"%s"\n' % appconfig.LOG_UNIT)
                self.write_tsv(records)
                self.finish()

    def write_tsv(self, records):
        for record in records:
            timestamp = datetime.fromtimestamp(record.timestamp).strftime('%Y-%m-%d %H:%M:%S')
            self.write(str(timestamp))
            self.write('\t')
            self.write(str(record.depth))
            self.write('\n')


class ValveHandler(RequestHandler):
    """Callers can use the GET method to get the status of the creek intake valve and use the
       POST method to toggle the status of the creek intake valve.
       In both cases the response is a json dict like so:
       {
          "valve": 0,
          "transition_time": "2015-03-18T12:00:12"
       }
       Indicating the current status of the valve: 0 means that the IO pin is low (the valve is
       normally-open, so the valve will be open). 1 means that the IO pin is high and the valve is
       closed. transition_time is the time of the most recent state change, in the server's time
       zone, or null if the transition time is not known."""

    _valve_state = False
    _transition_time = None

    def get(self, *args, **kwargs):
        self.finish(ValveHandler.get_state())

    def post(self, *args, **kwargs):
        ValveHandler._valve_state = not ValveHandler._valve_state
        ValveHandler._transition_time = datetime.now().isoformat()[:19]
        wiringpi.digitalWrite(VALVE_GPIO, int(ValveHandler._valve_state))
        self.finish(ValveHandler.get_state())

    @staticmethod
    def get_state():
        return {
            'valve': ValveHandler._valve_state,
            'transition_time': ValveHandler._transition_time
        }



class TankMonitor(Application):
    def __init__(self, handlers=None, **settings):
        super(TankMonitor, self).__init__(handlers, **settings)
        rate_threshold = appconfig.ALERT_RATE_THRESHOLD
        self.level_threshold = appconfig.ALERT_LEVEL_THRESHOLD
        self.tensec_logger = TankLogger(10, alert_rate_threshold=rate_threshold)
        self.minute_logger = TankLogger(60, alert_rate_threshold=rate_threshold)
        self.hour_logger = TankLogger(3600, alert_rate_threshold=rate_threshold)
        self.latest_raw_val = None
        self.display_expiry = 0

    def log_tank_depth(self, tank_depth):
        """This method can be called from outside the app's IOLoop. It's the
        only method that can be called like that"""
        log.debug("Logging depth: " + str(tank_depth))
        IOLoop.current().add_callback(partial(self._offer_log_record, time(),
                                              tank_depth))

    @coroutine
    def _offer_log_record(self, timestamp, depth):
        log_record = TankLogRecord(timestamp=timestamp, depth=depth)
        if depth < self.level_threshold:
            yield AlertMailer.offer(TankAlert(timestamp=timestamp, depth=depth, delta=None))
        for logger in self.tensec_logger, self.minute_logger, self.hour_logger:
            alert = logger.offer(log_record)
            if alert:
                yield AlertMailer.offer(alert)
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
        to convert the output of the sensor to whatever units are specified in the settings file.
        """
        log.info("Calibrating Maxbotix interface with m=%2.4f, b=%2.4f" % (m, b))
        self.calibrate_m = float(m)
        self.calibrate_b = float(b)

    def convert(self, val):
        converted = self.calibrate_m * float(val) + self.calibrate_b
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Raw value %2.4f converted to %2.4f" % (float(val), converted))
        return converted

    def shutdown(self):
        self.stop_reading = True

    def set_serial_port(self, **kwargs):
        with self.port_lock:
            self.serial_port = Serial(**kwargs)


class AlertMailer(object):

    last_alert = None
    alert_mail = Template(open('templates/tanklevel.txt', 'rb').read())

    @staticmethod
    def send_message(alert_text, tank_alert):
        msg = MIMEText(alert_text)
        msg[
            'Subject'] = "[TWUC Alert] Tank Level Warning" if not tank_alert.delta else "[TWUC Alert] Tank Delta Warning"
        msg['From'] = appconfig.EMAIL['sending_address']
        msg['To'] = ', '.join(appconfig.EMAIL['distribution'])
        conn = None
        try:
            conn = smtplib.SMTP(
                "%s:%d" % (appconfig.EMAIL['smtp_server'], appconfig.EMAIL['smtp_port']))
            if appconfig.EMAIL['smtp_tls']:
                conn.starttls()
            conn.login(appconfig.EMAIL['sending_address'], appconfig.EMAIL['sending_password'])
            conn.sendmail(appconfig.EMAIL['sending_address'], appconfig.EMAIL['distribution'],
                          msg.as_string())
        finally:
            if conn:
                conn.quit()

    @staticmethod
    @coroutine
    def offer(tank_alert):
        offer_time = time()
        if AlertMailer.last_alert is None or \
                (offer_time - AlertMailer.last_alert) > appconfig.EMAIL['period']:
            alert_text = AlertMailer.alert_mail.generate(alert=tank_alert)
            log.warn("Sending e-mail alert due to " + str(tank_alert))
            log.warn(alert_text)
            AlertMailer.last_alert = offer_time
            yield thread_pool.submit(lambda: AlertMailer.send_message(alert_text, tank_alert))

if __name__ == "__main__":
    event_router = SockJSRouter(EventConnection, '/event')
    handlers = [
        (r'/', MainPageHandler),
        (r'/logger/(.*)', LogDownloadHandler),  # arg is log interval
        (r'/valve', ValveHandler)
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
    wiringpi.pinMode(VALVE_GPIO, 1)
    wiringpi.digitalWrite(VALVE_GPIO, 0)
    wiringpi.pinMode(BTN_IN, 0)

    app = TankMonitor(handlers, **tornado_settings)
    maxbotix = MaxbotixHandler(tank_monitor=app, port='/dev/ttyAMA0', timeout=10)
    maxbotix.calibrate(appconfig.MAXBOTICS['calibrate_m'],
                       appconfig.MAXBOTICS['calibrate_b'])
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
