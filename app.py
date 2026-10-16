import os
import sqlite3
from datetime import datetime, date
from flask import Flask, request, render_template, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "mealplan_secret"
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mealplan.db")
CATEGORIES = ["Food", "Transport", "Stationery", "Other"]


# ---------- Database ----------
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if missing; add last_end_day to an older setup table."""
    db = get_db()
    db.execute("""CREATE TABLE IF NOT EXISTS setup(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        total REAL, days INTEGER, start_date TEXT,
        daily_allowance REAL, last_end_day TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT, category TEXT, amount REAL, date_time TEXT)""")
    cols = [r["name"] for r in db.execute("PRAGMA table_info(setup)")]
    if "last_end_day" not in cols:
        db.execute("ALTER TABLE setup ADD COLUMN last_end_day TEXT")
    db.commit()
    db.close()


# ---------- Helpers ----------
@app.template_filter("naira")
def naira(value):
    return f"₦{(value or 0):,.2f}"


def get_setup():
    db = get_db()
    row = db.execute("SELECT * FROM setup ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    return row


def spent_today():
    db = get_db()
    val = db.execute("SELECT SUM(amount) FROM expenses WHERE substr(date_time,1,10)=?",
                     (date.today().isoformat(),)).fetchone()[0]
    db.close()
    return val or 0


def total_spent():
    db = get_db()
    val = db.execute("SELECT SUM(amount) FROM expenses").fetchone()[0]
    db.close()
    return val or 0


def get_balance():
    setup = get_setup()
    if setup is None:
        return 0
    return setup["total"] - total_spent()


def days_remaining(setup):
    start = date.fromisoformat(setup["start_date"])
    return max(0, setup["days"] - (date.today() - start).days)


# ---------- Routes ----------
@app.route("/")
def home():
    return redirect(url_for("dashboard") if get_setup() else url_for("setup"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "POST":
        try:
            total = float(request.form["total"])
            days = int(request.form["days"])
        except ValueError:
            flash("Enter valid numbers.", "danger")
            return redirect(url_for("setup"))
        if total <= 0 or days <= 0:
            flash("Credits and days must be more than 0.", "danger")
            return redirect(url_for("setup"))
        start_date = request.form.get("start_date") or date.today().isoformat()
        db = get_db()
        db.execute("INSERT INTO setup(total, days, start_date, daily_allowance) VALUES (?,?,?,?)",
                   (total, days, start_date, total / days))
        db.commit()
        db.close()
        return redirect(url_for("dashboard"))
    return render_template("setup.html")


@app.route("/dashboard")
def dashboard():
    s = get_setup()
    if s is None:
        return redirect(url_for("setup"))
    allowance = s["daily_allowance"]
    spent = spent_today()
    left_today = allowance - spent
    percent = min(100, round(spent / allowance * 100)) if allowance else 0

    if left_today <= 0:
        warning, level = "Budget exhausted for today!", "danger"
    elif left_today <= allowance * 0.1:
        warning, level = "Low budget warning: 10% or less left today.", "warn"
    else:
        warning, level = None, None

    db = get_db()
    recent = db.execute("SELECT * FROM expenses ORDER BY date_time DESC LIMIT 5").fetchall()
    db.close()
    return render_template("dashboard.html", balance=get_balance(), total=s["total"],
                           allowance=allowance, spent_today=spent, left_today=left_today,
                           days_left=days_remaining(s), percent=percent,
                           warning=warning, level=level, recent=recent)


@app.route("/add_expense", methods=["GET", "POST"])
def add_expense():
    if get_setup() is None:
        return redirect(url_for("setup"))
    if request.method == "POST":
        try:
            amount = float(request.form["amount"])
        except ValueError:
            flash("Enter a valid amount.", "danger")
            return redirect(url_for("add_expense"))
        desc = request.form["description"].strip()
        if amount <= 0 or not desc:
            flash("Enter a description and an amount above 0.", "danger")
            return redirect(url_for("add_expense"))
        if amount > get_balance():
            flash("Expense exceeds your available balance!", "danger")
            return redirect(url_for("add_expense"))
        db = get_db()
        db.execute("INSERT INTO expenses(description, category, amount, date_time) VALUES (?,?,?,?)",
                   (desc, request.form["category"], amount, datetime.now().isoformat()))
        db.commit()
        db.close()
        flash("Expense added.", "success")
        return redirect(url_for("dashboard"))
    return render_template("add_expense.html", categories=CATEGORIES)


@app.route("/end_day", methods=["POST"])
def end_day():
    s = get_setup()
    if s is None:
        return redirect(url_for("setup"))
    today = date.today().isoformat()
    if s["last_end_day"] == today:
        flash("You already ended today.", "info")
        return redirect(url_for("dashboard"))
    start = date.fromisoformat(s["start_date"])
    days_after_today = s["days"] - (date.today() - start).days - 1
    if days_after_today <= 0:
        flash("Your budget period is over.", "info")
        return redirect(url_for("dashboard"))
    new_allowance = get_balance() / days_after_today
    db = get_db()
    db.execute("UPDATE setup SET daily_allowance=?, last_end_day=? WHERE id=?",
               (new_allowance, today, s["id"]))
    db.commit()
    db.close()
    flash("Day ended. Leftover spread over the remaining days.", "success")
    return redirect(url_for("dashboard"))


@app.route("/history")
def history():
    db = get_db()
    expenses = db.execute("SELECT * FROM expenses ORDER BY date_time DESC").fetchall()
    db.close()
    return render_template("history.html", expenses=expenses)


@app.route("/edit_expense/<int:expense_id>", methods=["GET", "POST"])
def edit_expense(expense_id):
    db = get_db()
    expense = db.execute("SELECT * FROM expenses WHERE id=?", (expense_id,)).fetchone()
    if expense is None:
        db.close()
        return redirect(url_for("history"))
    if request.method == "POST":
        try:
            amount = float(request.form["amount"])
        except ValueError:
            flash("Enter a valid amount.", "danger")
            db.close()
            return redirect(url_for("edit_expense", expense_id=expense_id))
        if amount <= 0 or amount > get_balance() + expense["amount"]:
            flash("Invalid amount.", "danger")
            db.close()
            return redirect(url_for("edit_expense", expense_id=expense_id))
        db.execute("UPDATE expenses SET description=?, category=?, amount=? WHERE id=?",
                   (request.form["description"].strip(), request.form["category"], amount, expense_id))
        db.commit()
        db.close()
        flash("Expense updated.", "success")
        return redirect(url_for("history"))
    db.close()
    return render_template("edit_expense.html", expense=expense, categories=CATEGORIES)


@app.route("/delete_expense/<int:expense_id>", methods=["POST"])
def delete_expense(expense_id):
    db = get_db()
    db.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
    db.commit()
    db.close()
    flash("Expense deleted.", "success")
    return redirect(url_for("history"))


@app.route("/summary")
def summary():
    s = get_setup()
    if s is None:
        return redirect(url_for("setup"))
    return render_template("summary.html", total=s["total"],
                           total_spent=total_spent(), balance=get_balance())

init_db()

if __name__ == "__main__":
    app.run(debug=True)