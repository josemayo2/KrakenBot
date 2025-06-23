from datetime import datetime
import pytz

tz = pytz.timezone('Europe/Madrid')
print(datetime.now(tz).strftime("%A, %d/%m/%Y"))
print("weekday():", datetime.now(tz).weekday())
