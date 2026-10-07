<div align="center">

# 📇 TNTU Schedule Bot

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/aiogram-3.x-24A1DE?logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![SQLite](https://img.shields.io/badge/SQLite-Enabled-90D4F4?logo=sqlite&logoColor=white)](https://sqlite.org/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Checks](https://github.com/Andromedov/TNTU-Telegram-Schedule/actions/workflows/checks.yml/badge.svg)](https://github.com/Andromedov/TNTU-Telegram-Schedule/actions/workflows/checks.yml)
[![License: Apache License v2.0](https://img.shields.io/badge/License-Apache_2.0-orange?logo=apache&logoColor=white.svg)](https://www.apache.org/licenses/LICENSE-2.0)

**A Telegram bot for tracking Ternopil National Technical University (TNTU) class schedules.**<br>
Provides real-time access to schedules, sends reminders, and allows customizable notifications.

</div>

---

## ✨ Features

- 📅 **View Schedule** - Display the classes at any time.
- 👥 **Subgroups** - In Settings, choose the entire group or subgroup 1/2. Common classes are always included; the selection also applies to reminders, sharing, and calendar export. Another group's schedule shows all its subgroups. Changing your main group resets this choice.
- 🌙 **Evening Schedule Delivery** - Automatically send tomorrow's schedule every evening at 20:00.
- ⏰ **10-Minute Reminders** - Get notified 10 minutes before each class starts.
- 🌅 **Smart Reminders** - Configure the first class separately, choose lesson types and quiet hours, receive a morning digest, snooze for five minutes, or mute notifications until tomorrow.
- 🔔 **Detailed Schedule Changes** - See which class, time, type, building, room, note, or ATutor link changed.
- ⚙️ **Customizable Settings** - Toggle notifications and pause alerts as needed.
- 🌐 **Ukrainian and English UI** - Choose the interface language in bot settings.
- 🔄 **Group Management** - Easily switch between different study groups.
- 📄 **PDF Support** - Direct links to official PDF schedules when available.
- 📤 **Schedule Sharing** - Generate compact localized day or week messages ready to forward in Telegram.
- 🏫 **Campus Guide** - Find building addresses, room-code explanations, landmarks, and a shared campus map.
- 📊 **Admin Dashboard** - Monitor user activity, notification preferences, scheduler jobs, uptime, and database size.

## 🛠️ Tech Stack & Data Sources
- **Framework:** [aiogram 3.x](https://docs.aiogram.dev/) (Asynchronous Telegram Bot API)
- **Database:** aiosqlite (Local `users.sqlite3` for preferences and selected groups)
- **Scheduling:** APScheduler (For evening deliveries and pre-class reminders)
- **Time zone:** Schedule dates and reminders use `Europe/Kyiv`, independently of the server's local time zone.
- **Data Source:** Web scraping the official [TNTU Website](https://tntu.edu.ua/) using `beautifulsoup4` and `aiohttp`.

## 🚀 Installation & Setup

You can either just use bot that already running [@tntu_schedule_bot](https://t.me/tntu_schedule_bot), or run this bot locally or via Docker. In case of selfhosted option, you will need a Telegram Bot Token from [@BotFather](https://t.me/BotFather).

### 1. Configuration (`.env`)
First, clone the repository and set up your environment variables:

```bash
git clone https://github.com/Andromedov/TNTU-Telegram-Schedule.git
cd TNTU-Telegram-Schedule
cp .env.example .env
```

Edit `.env` with your Bot Token:

```env
BOT_TOKEN=your_telegram_bot_token_here
```

### 2. Running with Docker (recommended)

The easiest way to run this bot is via Docker Compose:
```bash
docker compose pull
docker compose up -d
```

### 3. Running Locally

If you prefer to run it without Docker, ensure you have Python 3.12+ installed.

```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the bot
python src/main.py
```

## 📁 Project Structure

```text
TNTU-Telegram-Schedule/
├── src/
│   ├── main.py               # Application entry point and lifecycle
│   ├── config.py             # Environment-backed configuration
│   ├── bot/                  # Telegram routes, handlers, keyboards, and calendar UI
│   ├── schedule/             # TNTU access, parsing, formatting, sharing, and ICS
│   ├── jobs/                 # Scheduled notifications and group promotion
│   ├── infrastructure/       # SQLite and shared HTTP client
│   ├── campus/               # Building directory and location helpers
│   └── i18n/                 # Localization loader and message catalog
├── data/                    # Automatically generated (DB & caches)
├── .env.example             # Environment variables template
├── docker-compose.yml       
└── Dockerfile
```

Feature modules use imports rooted at `src`, so local runs, tests, and Docker resolve the same code paths without an additional package wrapper.

## 📝 Localization
The bot supports Ukrainian (`uk`) and English (`en`). Users can switch language in **Settings → Language**, and the preference is stored in SQLite. New users start with English when Telegram reports an English locale; all other or unknown locales fall back to Ukrainian.

Translations, button labels, notifications, calendar names, and command descriptions are stored under language keys in `src/i18n/messages.json`. Missing English keys automatically fall back to Ukrainian.

## ✅ Quality Checks

The `Checks` workflow can be started manually and runs automatically for pull requests and pushes to `main` or any `dev/*` branch. It performs linting, formatting checks, byte-compilation, JSON validation, unit tests, dependency auditing, and a Docker build. Version tags such as `v1.4.0` run the same checks before the `Release` workflow publishes the image to GitHub Container Registry and invokes the separate `Deployment` workflow. Production pulls the public image by its immutable SHA-256 digest and never builds application code on the VPS.

Run the same Python checks locally with:

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m ruff check src tests
python -m ruff format --check src tests
python -m compileall -q src tests
python -m unittest discover -s tests -v
python -m pip_audit -r requirements.txt
```

## 📜 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

---

**Note:** This bot requires internet access to fetch schedule data from the official TNTU website. Schedule availability and formatting strictly depend on the official website's structure.
**Note 2:** All of code is created by Google Gemini & [Me](https://github.com/Andromedov). This bot is designed solely to help you easily find the schedule.
