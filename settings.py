MAXBOTICS = dict(
    calibrate_m=-0.037453,
    calibrate_b=107.1161
)

LOG_UNIT = "% full"

EMAIL = dict(
    period=3600,   # Minimum time in seconds between alert emails.
    smtp_server="smtp.gmail.com",
    smtp_port=465,
    smtp_tls=True,
    sending_address="<your address here>@gmail.com",
    sending_password="<e-mail account password>",
    distribution=['fred@example.com', 'jim@example.com']
)

CREDENTIALS = dict(
    username="admin",
    password="admin"
)
