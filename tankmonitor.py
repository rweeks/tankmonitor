import os
import sys
from threading import Lock, Thread
from tornado.web import Application, RequestHandler, HTTPError, StaticFileHandler
from tornado.httpserver import HTTPServer
from tornado.template import Template
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.gen import coroutine
from sockjs.tornado import SockJSRouter, SockJSConnection
import logging
from logging.handlers import TimedRotatingFileHandler
import json
import binascii
from tanklogger import TankLogger, TankLogRecord, TankAlert
from functools import partial
from datetime import datetime, timedelta
from time import time, sleep
from serial import Serial
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor
import struct
import smtplib
import base64
import settings as appconfig
from PIL import Image, ImageDraw, ImageFont
import pcd8544.lcd as lcd
import netifaces as ni
import wiringpi2 as wiringpi

log_level_reset_at = None

logging.basicConfig(filename="syslog/tankmonitor.log",
                    format='%(asctime)s %(levelname)-8s %(message)s',
                    level=logging.INFO,
                    datefmt='%Y-%m-%d %H:%M:%S')

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
logging.getLogger("tornado.access").addHandler(logging.NullHandler())
logging.getLogger("tornado.access").propagate = False

debugHandler = TimedRotatingFileHandler('tankmonitor-log', backupCount=24)
debugHandler.setLevel(logging.DEBUG)
debugHandler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
log.addHandler(debugHandler)

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
        failed_listeners = set()
        for event_listener in EventConnection.event_listeners:
            try:
                event_listener.send(json.dumps(msg_dict))
            except:
                log.debug('Removing listener ' + event_listener)
                failed_listeners.append(event_listener)
        EventConnection.event_listeners = EventConnection.event_listeners.difference(failed_listeners)


class MainPageHandler(RequestHandler):
    def get(self, *args, **kwargs):
        self.render('main.html')

CATEGORY_LABELS = {
    'depth': 'Volume',
    'density': 'Water Quality',
    'water_temp': 'Water Temperature',
    'distance': 'Raw Maxbotix Reading'
}

class LogDownloadHandler(RequestHandler):
    def get(self, category, logger_interval):
        fmt = self.get_argument('format', 'nvd3')  # or tsv
        deltas = self.get_argument('deltas', False)
        loggers = getattr(self.application, 'loggers', None)
        loggers = filter(lambda l: l.log_interval == int(logger_interval), loggers[category])
        if not loggers:
            raise Exception("No logger matching " + logger_interval)
        logger = loggers[0]
        records = logger.deltas if deltas else logger.records
        log.debug("Returning %d records for %s" % (len(records), category))
        if fmt == 'nvd3':
            self.finish({'key': CATEGORY_LABELS[category],
                         'values': list(records)})
        elif fmt == 'tsv':
            self.set_header('Content-Type', 'text/plain')
            log_unit = appconfig.LOG_UNITS[category]
            if deltas:
                self.write('"Timestamp"\t"Rate of Change (%s/min)"\n' % log_unit)
            else:
                self.write('"Timestamp"\t"%s"\n' % log_unit)
            self.write_tsv(records)
            self.finish()

    def write_tsv(self, records):
        for record in records:
            timestamp = datetime.fromtimestamp(record.timestamp).strftime('%Y-%m-%d %H:%M:%S')
            self.write(str(timestamp))
            self.write('\t')
            self.write(str(record.value))
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
        auth_header = self.request.headers.get('Authorization')
        if auth_header is None or not auth_header.startswith('Basic '):
            self.set_status(401, reason="Valve control requires authentication")
            self.set_header('WWW-Authenticate', 'Basic realm=Restricted')
            self.finish()
            return
        else:
            auth_decoded = base64.decodestring(auth_header[6:])
            hdr_auth = dict()
            hdr_auth['username'], hdr_auth['password'] = auth_decoded.split(':', 2)
            if hdr_auth != appconfig.CREDENTIALS:
                raise HTTPError(403, reason="Valve control credentials invalid")
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
        self.loggers = {
            'depth': [
                TankLogger(10, alert_rate_threshold=appconfig.ALERT_RATE_THRESHOLDS['depth']),
                TankLogger(60, alert_rate_threshold=appconfig.ALERT_RATE_THRESHOLDS['depth']),
                TankLogger(3600, alert_rate_threshold=appconfig.ALERT_RATE_THRESHOLDS['depth'])
            ],
            'density': [
                TankLogger(10, alert_rate_threshold=appconfig.ALERT_RATE_THRESHOLDS['density'],
                           comparator=lambda d, t: d > t),
                TankLogger(60, alert_rate_threshold=appconfig.ALERT_RATE_THRESHOLDS['density'],
                           comparator=lambda d, t: d > t),
                TankLogger(3600, alert_rate_threshold=appconfig.ALERT_RATE_THRESHOLDS['density'],
                           comparator=lambda d, t: d > t),
            ],
            'water_temp': [
                TankLogger(10, alert_rate_threshold=None),
                TankLogger(60, alert_rate_threshold=None),
                TankLogger(3600, alert_rate_threshold=None)
            ],
            'distance': [
                TankLogger(10, alert_rate_threshold=None),
                TankLogger(60, alert_rate_threshold=None),
                TankLogger(3600, alert_rate_threshold=None)
            ]
        }
        self.latest_raw_val = None
        self.display_expiry = 0

    def log_tank_depth(self, tank_depth):
        """The log* methods can be called from outside the app's IOLoop. They're the
        only methods that can be called like that"""
        log.debug("Logging depth: " + str(tank_depth))
        IOLoop.current().add_callback(partial(self._offer_log_record, 'depth', time(),
                                              tank_depth))

    def log_density(self, density):
        log.debug("Logging density: " + str(density))
        IOLoop.current().add_callback(partial(self._offer_log_record, 'density', time(),
                                              density))

    def log_water_temp(self, water_temp):
        log.debug("Logging water temp: " + str(water_temp))
        IOLoop.current().add_callback(partial(self._offer_log_record, 'water_temp', time(),
                                              water_temp))

    def log_distance(self, distance):
        # log.debug("Logging distance:" + str(distance))
        IOLoop.current().add_callback(partial(self._offer_log_record, 'distance', time(),
                                              distance))

    @coroutine
    def _offer_log_record(self, category, timestamp, value):
        log_record = TankLogRecord(timestamp=timestamp, value=value)
        if category == 'depth' and value < appconfig.ALERT_THRESHOLDS['depth']:
            yield AlertMailer.offer('depth', TankAlert(timestamp=timestamp, value=value, delta=None))
        elif category == 'density' and value > appconfig.ALERT_THRESHOLDS['density']:
            yield AlertMailer.offer('density', TankAlert(timestamp=timestamp, value=value, delta=None))
        for logger in self.loggers[category]:
            alert = logger.offer(log_record)
            if alert:
                yield AlertMailer.offer(alert)
        EventConnection.notify_all({
            'event': 'log_value',
            'unit': appconfig.LOG_UNITS[category],
            'timestamp': timestamp,
            'category': category,
            'value': value
        })

    def update_display(self):
        ip_addr = ni.ifaddresses('eth0')[ni.AF_INET][0]['addr']
        now = time()
        if now < self.display_expiry:
            im = Image.new('1', (84, 48))
            draw = ImageDraw.Draw(im)
            if self.latest_raw_val is not None:
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

    def log_level_reset(self):
        global log_level_reset_at
        if log_level_reset_at is not None and log_level_reset_at < datetime.now():
            log.info("Resetting logging level to INFO")
            log.setLevel(logging.INFO)
            log_level_reset_at = None

SERIAL_LOCK = Lock()

class MaxbotixHandler:
    def __init__(self, tank_monitor, **kwargs):
        """kwargs will be passed through to the serial port constructor"""
        self.serial_port = None
        self.set_serial_port(**kwargs)
        self.stop_reading = False
        self.tank_monitor = tank_monitor
        self.calibrate_m = 1
        self.calibrate_b = 0

    def read(self):
        log.info("Starting MaxbotixHandler read")
        val = None
        read_count = 0
        while not self.stop_reading:
            try:
                with SERIAL_LOCK:
                    val = self.serial_port.read()
                    if val == 'R':
                        val = self.serial_port.read(4)
                        read_count += 1
                        if read_count % 5 == 0:  # cheesy kludge to avoid tons of logging
                            self.tank_monitor.set_latest_raw_val(val)
                            self.tank_monitor.log_tank_depth(self.convert(val))
                        self.tank_monitor.log_distance(int(val))
            except:
                print "Unable to convert value '" + str(val) + "'"
                import traceback
                traceback.print_exc()
            finally:
                sleep(0.1)

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
        with SERIAL_LOCK:
            self.serial_port = Serial(**kwargs)


class DensitrakHandler:

    def __init__(self, tank_monitor, device_name):
        self.device_name = device_name
        self.stop_reading = False
        self.serial_port = Serial(device_name, baudrate=115200, timeout=10)
        self.tank_monitor = tank_monitor

    def read(self):
        self.serial_port.open()
        log.info("Starting Densitrak read")
        while not self.stop_reading:
            try:
                self.tank_monitor.log_density(
                    self.send_command(b'\x01\x31\x41\x34\x36\x30\x0D\x00'))
                self.tank_monitor.log_water_temp((5.0/9) * (
                    self.send_command(b'\x01\x31\x41\x34\x31\x30\x0D\x00') - 32.0))
            except:
                log.debug("Failure reading densitrak", exc_info=sys.exc_info())
            finally:
                sleep(2)

    def send_command(self, command):
        with SERIAL_LOCK:
            self.serial_port.flush()
            self.serial_port.write(command)
            self.serial_port.flush()
            response = self.serial_port.read(17)
            self.serial_port.flush()
            # TODO: error checking etc.
            encoded_value = response[8:-1]
            decoded_value = struct.unpack('>f', binascii.unhexlify(encoded_value))[0]
            return decoded_value

    def shutdown(self):
        self.stop_reading = True

class SyslogStatusHandler(RequestHandler):

    def get(self, *args, **kwargs):
        self.finish(self.get_status())

    def post(self):
        global log_level_reset_at
        log.setLevel(logging.DEBUG)
        log.debug("Log level temporarily set to DEBUG")
        log_level_reset_at = datetime.now() + timedelta(minutes=30)
        self.finish(self.get_status())

    def get_status(self):
        return {
            'level': log.getEffectiveLevel(),
            'level_reset_at': None if log_level_reset_at is None else log_level_reset_at.strftime("%b %d %Y %H:%M:%S"),
            'syslogs': filter(lambda x: x.startswith('tankmonitor.log'), os.listdir('syslog'))
        }

class SyslogFileHandler(StaticFileHandler):

    def get_content_type(self):
        return "text/plain"


class AlertMailer(object):

    last_alert = None
    generic_alert_mail = Template(open('templates/generic_alert.txt', 'rb').read())

    alert_config_by_category = {
        'density': {
            'alert_measurement_name': 'Water quality',
            'alert_value_format': '%2.4f',
            'log_unit': 'g/cm^3',
        },

        'depth': {
            'alert_measurement_name': 'Tank level',
            'alert_value_format': '%2.2f',
            'log_unit': appconfig.LOG_UNITS['depth'],
        }
    }

    @staticmethod
    def send_message(alert_text, tank_alert):
        msg = MIMEText(alert_text)
        msg[
            'Subject'] = "[TWUC Alert] Tank Warning" if not tank_alert.delta else "[TWUC Alert] Tank Delta Warning"
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
    def offer(category, tank_alert):
        offer_time = time()
        if AlertMailer.last_alert is None or \
                (offer_time - AlertMailer.last_alert) > appconfig.EMAIL['period']:
            alert_config = AlertMailer.alert_config_by_category[category].copy()
            alert_config['alert'] = tank_alert
            alert_config['alert_threshold'] = appconfig.ALERT_RATE_THRESHOLDS[category] if tank_alert.delta else appconfig.ALERT_THRESHOLDS[category]
            alert_text = AlertMailer.generic_alert_mail.generate(**alert_config)
            log.warn("Sending e-mail alert due to %s %s" % (category, str(tank_alert)))
            log.warn(alert_text)
            AlertMailer.last_alert = offer_time
            yield thread_pool.submit(lambda: AlertMailer.send_message(alert_text, tank_alert))


if __name__ == "__main__":
    event_router = SockJSRouter(EventConnection, '/event')
    handlers = [
        (r'/', MainPageHandler),
        (r'/logger/(.*)/(.*)', LogDownloadHandler),  # args are category, log interval
        (r'/valve', ValveHandler),
        (r'/syslog', SyslogStatusHandler),
        (r'/syslog/(.*)', SyslogFileHandler, {'path': 'syslog/'})
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
    ioloop = IOLoop.instance()
    disp_print_cb = PeriodicCallback(app.update_display, callback_time=500, io_loop=ioloop)
    disp_print_cb.start()
    button_poll_cb = PeriodicCallback(app.poll_display_button, callback_time=100, io_loop=ioloop)
    button_poll_cb.start()
    log_level_cb = PeriodicCallback(app.log_level_reset, callback_time=10*1000, io_loop=ioloop)
    log_level_cb.start()

    http_server = HTTPServer(app)
    http_server.listen(listen_port)
    log.info("Listening on port " + str(listen_port))
    try:
        maxbotix = MaxbotixHandler(tank_monitor=app, port='/dev/ttyAMA0', timeout=10)
        maxbotix.calibrate(appconfig.MAXBOTICS['calibrate_m'],
                           appconfig.MAXBOTICS['calibrate_b'])
        maxbotix_thread = Thread(target=maxbotix.read)
        maxbotix_thread.daemon = True
        maxbotix_thread.start()
    except Exception as e:
        log.error("Unable to setup MaxbotixHandler", exc_info=e)
    try:
        densitrak = DensitrakHandler(app, '/dev/ttyUSB0')
        densitrak_thread = Thread(target=densitrak.read)
        densitrak_thread.daemon = True
        densitrak_thread.start()
    except Exception as e:
        log.error("Unable to setup DensitrakHandler", exc_info=e)
    ioloop.start()
