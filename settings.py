MAXBOTICS = dict(
    calibrate_m=1.0/(2860-190),
    calibrate_b=-190.0/(2860-190)
)

LOG_UNIT = "litres"

EMAIL = dict(
    period=3600,   # Minimum time in seconds between alert emails.
    smtp_server="smtp.gmail.com",
    smtp_port=465,
    smtp_tls=True,
    sending_address="<your address here>@gmail.com",
    sending_password="<e-mail account password>",
    distribution=['fred@example.com', 'jim@example.com']
)

