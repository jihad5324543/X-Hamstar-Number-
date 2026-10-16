# Telegram Voice Clone Bot — consent-based

এই project Telegram voice/audio → transcription → target-speaker voice synthesis → Telegram voice-message pipeline তৈরি করে।

## Important

এই bot-এর Target Voice ব্যবহারের আগে user-কে explicit permission confirmation দিতে হয়:

> I confirm that I have permission from the speaker to use this voice sample for voice conversion.

শুধু নিজের voice অথবা যার স্পষ্ট permission আছে তার voice ব্যবহার করুন।

এটি 100% exact identity clone guarantee করে না।

## Stack

- Python 3.11/3.12 recommended
- aiogram 3
- faster-whisper
- Coqui XTTS-v2
- FFmpeg
- Telegram Bot API

## 1. System requirements

Ubuntu/Debian example:

```bash
sudo apt update
sudo apt install -y ffmpeg python3.11 python3.11-venv
```

A CUDA/NVIDIA machine is strongly recommended for practical inference speed.

## 2. Create environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configure bot

Copy:

```bash
cp .env.example .env
```

Then put your BotFather token into `.env`:

```env
BOT_TOKEN=123456:ABC...
```

## 4. Start

```bash
python main.py
```

The first run may download model files and can take a while.

## 5. Usage

1. `/start`
2. Press `🎯 Target Voice`
3. Press `✅ I have permission`
4. Send target voice/audio OR direct audio URL
5. Press `🎙 Main Voice`
6. Send the voice containing the speech you want converted
7. Press `🔄 Convert`
8. The bot sends a fresh Telegram voice message and an OGG/Opus download.

The output is sent with `sendVoice`, so Telegram displays it as a voice message rather than a normal audio player.

## Language

XTTS-v2 supports a fixed language set. The code accepts:

```text
en es fr de it pt pl tr ru nl cs ar zh-cn ja hu ko
```

Use:

```text
/language en
```

The default is English.

Important: Whisper can detect many languages, but that does NOT mean XTTS-v2 can synthesize every detected language. Bengali, for example, is not in the XTTS-v2 language list used by this project.

## Direct links

The URL input expects a direct downloadable audio URL. A normal Telegram post URL, Google Drive sharing page, YouTube page, etc. is not necessarily a direct audio file.

## Production recommendations

For production:

- Redis/PostgreSQL for session state
- Celery/RQ for GPU jobs
- Nginx
- Docker
- per-user rate limits
- maximum audio duration
- storage cleanup
- stronger SSRF/effective egress filtering for URL downloads
- authentication/admin panel
- logging without storing unnecessary voice data
- explicit consent records if this is used by other people

## Model/license

Check the current Coqui/XTTS model license before commercial deployment.
