# Domain Watcher

This is a small script that checks domain RDAP records and sends Telegram messages when domains look available.

Requirements
- Python 3.8 or newer
- `python-dotenv` to load environment variables
- `telegram` and `rdap` Python packages

Quick setup
1. Create a `.env` file in the project root with these values:

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=-1001234567890
```

2. (Optional) Create and activate a virtual environment, then install requirements:

```bash
python3 -m venv venv
source venv/bin/activate
pip install python-dotenv telegram rdap
```

Run
```bash
python3 main.py
```

Cron example (run every minute):

```cron
* * * * * cd /root/Projects/domain-watcher-py && /usr/bin/python3 main.py >> watcher.log 2>&1
```

Domains file
- Put a `domains.txt` file next to `main.py`.
- One domain per line. Blank lines and lines starting with `#` are ignored.

Example `domains.txt`:

```txt
# domains to watch
example.com
mytestdomain.org
```