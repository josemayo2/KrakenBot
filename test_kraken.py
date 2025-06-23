from dotenv import load_dotenv
import os

load_dotenv()

print("API KEY:", os.getenv("KRAKEN_API_KEY"))
print("API SECRET:", os.getenv("KRAKEN_API_SECRET"))
