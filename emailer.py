# Copyright (c) 2026 Myles Willett. All rights reserved.
# Proprietary and confidential. No reproduction, distribution, or use
# without express written permission.

import smtplib
import sys
import json
import schedule
import time
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def clean(text):
    text = text.replace('\xa0', ' ')
    text = text.replace('\u200b', '')
    text = text.replace('\u2019', "'")
    text = text.replace('\u2018', "'")
    text = text.replace('\u201c', '"')
    text = text.replace('\u201d', '"')
    text = text.replace('\u2013', '-')
    text = text.replace('\u2014', '-')
    return text.encode('ascii', errors='replace').decode('ascii')

def send_email(config):
    try:
        sender     = clean(config['sender'])
        recipients = [clean(r) for r in config['recipients']]
        subject    = clean(config['subject'])
        body       = clean(config['body'])
        password   = config['password'].replace(' ', '')

        msg = MIMEMultipart()
        msg['From']    = sender
        msg['To']      = ', '.join(recipients)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())

        print('Email sent successfully!')

    except Exception as e:
        print(f'Error: {str(e)}', file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    config_file = sys.argv[1]
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    mode = config.get('mode', 'now')

    if mode == 'now':
        send_email(config)

    elif mode == 'once':
        # Send exactly once at the specified date and time then exit
        once_date = config.get('onceDate')
        once_time = config.get('onceTime')
        send_dt = datetime.strptime(f'{once_date} {once_time}', '%Y-%m-%d %H:%M')
        print(f'Waiting to send once at {send_dt}...')
        while True:
            if datetime.now() >= send_dt:
                send_email(config)
                break
            time.sleep(15)

    elif mode == 'recurring':
        day      = config.get('schedDay', 'friday')
        time_str = config.get('schedTime', '09:30')

        if day == 'daily':
            schedule.every().day.at(time_str).do(send_email, config)
        elif day == 'monday':
            schedule.every().monday.at(time_str).do(send_email, config)
        elif day == 'tuesday':
            schedule.every().tuesday.at(time_str).do(send_email, config)
        elif day == 'wednesday':
            schedule.every().wednesday.at(time_str).do(send_email, config)
        elif day == 'thursday':
            schedule.every().thursday.at(time_str).do(send_email, config)
        elif day == 'friday':
            schedule.every().friday.at(time_str).do(send_email, config)
        elif day == 'saturday':
            schedule.every().saturday.at(time_str).do(send_email, config)
        elif day == 'sunday':
            schedule.every().sunday.at(time_str).do(send_email, config)

        print(f'Recurring schedule set for {day} at {time_str}')
        while True:
            schedule.run_pending()
            time.sleep(30)