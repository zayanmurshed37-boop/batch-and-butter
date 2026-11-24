# Batch & Butter

A local bakery order planner built for MGOC50 Lecture 9. Review Instagram order examples, organize daily baking tasks in a clickable weekly calendar, and manage capacity and helper bookings.

## Start the app

Requires Python 3.9 or later. No packages or installs are needed.

1. Download or clone this repository, then open a terminal in its folder.
2. Run `python3 app.py` (on Windows, use `python app.py`).
3. Open http://127.0.0.1:8000 in your browser. Keep the terminal open while using the app.

Press Control+C in the terminal to stop. If you change the code, restart the app and refresh your browser.

## What you can do

- View confirmed baking tasks on a weekly calendar and click each task for its details.
- Accept or reject new orders, changes, and cancellations.
- Check daily baking capacity and book a helper.
- Enter requests manually and export orders as CSV.
- Advance through the classroom week to reveal each day's sample messages.

## Bakery rules

- Cupcakes: $40 per dozen, requiring 30 minutes of oven time per dozen.
- Cakes: $120 each, requiring 2 hours each.
- Daily capacity: 8 hours. One helper adds 4 hours for $100 per day.
- Orders, changes, cancellations, and helper bookings require at least 2 calendar days' notice.
- Each order contains one product. Baking happens on pickup day; pickup is at 5 PM.
- Decisions use fixed rules, with no AI or live Instagram integration.

## Files and saved records

`app.py` serves the entire app using only Python's standard library. `data/` contains newly generated fictional demo orders and seven days of fictional messages for November 23–29, 2026.

The first run creates `bakery.db` beside `app.py`. It stores orders, requests, helper bookings, the practice date, and activity. This file is ignored by Git so saved records stay on your computer. A fresh download starts with the fictional demo examples.

GitHub stores the source code; the app runs locally and is not a GitHub Pages site.

All customer names in this repository are explicit fictional demo labels. Original Instagram files and the local saved database are excluded.
