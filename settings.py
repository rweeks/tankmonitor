MAXBOTICS = {

    """
    What is the Maxbotix?
        Generally, the Maxbotix machine is an ultra-sonic range sensor. The laser shoots out
        from the top of the tank and when/if it hits the water inside the tank, it bounces back
        into the receiver and calculates the distance in centimetres that the laser traveled.
        
    Where is the code to calculate the distance that the laser traveled?
        The firmware of the Maxbotix machine calculates the distance and this Python program
        interprets the data given by the range sensor. THE DISTANCE-FINDING CODE IS NOT HERE.
    
    What do "calibrate_m" and "calibrate_b" do?
        If we wanted to, we could use the data provided by Maxbotix machine to draw a graph.
        In the case where we were measuring volume vs. distance, the slope would be negative,
        since the volume of the water would be inversely related to the distance measured via the sensor.
        
    """
    
    
    "calibrate_m": -0.037453,
    "calibrate_b": 107.1161
}

LOG_UNIT = "% full"

LOG_UNITS = {
    'depth': 'litres',
    'density': 'density',
    'water_temp': 'degrees',
    'distance': 'mm'
}

EMAIL = {
    "period": 3600,  # Minimum time in seconds between alert emails.
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 465,
    "smtp_tls": True,
    "sending_address": "<your address here>@gmail.com",
    "sending_password": "<e-mail account password>",
    "distribution": ['fred@example.com', 'jim@example.com']
}

CREDENTIALS = {
    "username": "admin",
    "password": "admin"
}

ALERT_THRESHOLDS = {
    'depth': 10000.0,  # measured in Litres
    'density': 1.005  # measured in g/cm3
}

ALERT_RATE_THRESHOLDS = {
    'depth': -200.0,  # measured in Litres/second
    'density': 0.02  # measured in g/cm3
}
