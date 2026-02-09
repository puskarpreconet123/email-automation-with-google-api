from flask import Flask, render_template, request, redirect
import os
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
import json

from dotenv import load_dotenv
load_dotenv()

# steps  for sendin via google api . protocol - http, step-1

import base64
from email.message import EmailMessage
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
 
 #---------------------------------------------------

app = Flask(__name__)

# convert utc to local time and apply to jinja

@app.template_filter('to_local')
def to_local_filter(dt):
    if not dt:
        return "N/A"
    
    # If dt has no timezone info, attach UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_tz = ZoneInfo("Asia/Kolkata")
    return dt.astimezone(local_tz).strftime("%Y-%m-%d %I:%M:%S %p")

client = MongoClient(os.getenv("MONGO_URI"))
db = client["emailAutomation"]
users_collection = db["users"]
groups_collection = db["groups"]

# steps  for sending via google api . protocol - http, step-2
 
def send_email_api(to_email, subject, template, data):
    try:
        # Load the token.json from env variable
        token_str = os.getenv("GMAIL_TOKEN_JSON")
        if not token_str:
            print("Error in token_str: GMAIL_TOKEN_JSON environment variable is not set!")
            return False
        tokeninfo=json.loads(token_str)
        creds = Credentials.from_authorized_user_info(tokeninfo)
        service = build('gmail', 'v1', credentials=creds)

        # Render your HTML template
        html_body = render_template(template, **data)

        # Build the message
        message = EmailMessage()
        message.set_content("HTML email content.")
        message.add_alternative(html_body, subtype='html')
        message['To'] = to_email
        message['From'] = "me" # Google automatically uses your email
        message['Subject'] = subject

        # Encode for API
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        # Send
        service.users().messages().send(userId="me", body={'raw': raw_message}).execute()
        print("Email sent")
        return True
    except Exception as e:
        print(f"Error in send_email_api function: {e}")
        return False
#-------------------------------------------------------------------------------------

def send_email(user):
    try:
        success = send_email_api(
            user["email"],
            user["subject"],
            "email.html",
            {
                "name": user["name"],
                "message": user["message"],
                "sender": "Backend Team"
            }
        )

        if not success:
            return False
        
        users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$inc": {"noOfEmailsSend": 1},
            "$set": {"lastSend": datetime.now(timezone.utc)}
        }
        )

        return True
    except Exception as e:
        print(f"Error in send_email function: {e}")
        return False

@app.route("/", methods=["GET", "POST"])
def dashboard():
    if request.method == "POST":
        name = request.form["name"]
        toEmail = request.form["email"]
        subject = request.form["subject"]
        message = request.form["message"]
        group = request.form["group"]

        user_exist = users_collection.find_one({"email": toEmail}) is not None
        if user_exist:
            return render_template("showerror.html", error_message="Email already exists")

        users_collection.insert_one({
            "name": name,
            "email": toEmail,
            "subject": subject,
            "message": message,
            "group": group,
            "noOfEmailsSend": 0,
            "dateCreated": datetime.now(timezone.utc),
            "lastSend": None
        })

        return redirect("/")

    all_users = list(users_collection.find())
    all_group = list(groups_collection.find())

    total_users = users_collection.count_documents({})
    total_emails_send = sum(u.get("noOfEmailsSend", 0) for u in all_users)

    return render_template(
        "dashboard.html",
        allUser=all_users,
        active="home",
        users=total_users,
        emails=total_emails_send,
        allGroup=all_group,
    )

@app.route("/emailForm")
def emailForm():
  all_group = list(groups_collection.find())
  return  render_template("emailAutomationForm.html", active="emailForm", allGroup = all_group)

@app.route('/emailForm-edit/<id>', methods=['GET', 'POST'])
def update_user(id):
    if request.method == "POST":
        users_collection.update_one(
            {"_id": ObjectId(id)},
            {"$set": {
                "name": request.form["name"],
                "email": request.form["email"],
                "subject": request.form["subject"],
                "message": request.form["message"],
                "group": request.form["group"]
            }}
        )
        return redirect("/")

    user = users_collection.find_one({"_id": ObjectId(id)})
    all_group = list(groups_collection.find())
    return render_template("emailAutomationForm.html", user=user, allGroup=all_group)

@app.route("/create-group", methods=["POST"])
def create_group():
    group_name = request.form.get("group_name")
    
    if groups_collection.find_one({"groupName": group_name.lower()}) is not None:
        return render_template("showerror.html", error_message="Group already exists" )
    
    rules = []
    i = 1
    while True:
        max_emails = request.form.get(f"rule{i}_maxEmails")
        if not max_emails:
            print("max emails not found")
            break

        rule = {
            "maxEmails": int(max_emails),
            "wait": {
                "value": int(request.form.get(f"rule{i}_wait_value")),
                "unit": request.form.get(f"rule{i}_wait_unit")
            }
        }
        rules.append(rule)
        i += 1

    group_doc = {
        "groupName": group_name.lower(),
        "rules": rules,
        "createdAt": datetime.now(timezone.utc)
    }

    groups_collection.insert_one(group_doc)

    return redirect("/")

@app.route('/details/<id>')
def user_details(id):
    user = users_collection.find_one({"_id": ObjectId(id)})
    return render_template("userCard.html", user=user)

@app.route('/delete/<id>')
def delete_user(id):
    users_collection.delete_one({"_id": ObjectId(id)})
    return redirect("/")

@app.route("/start")
def start_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        return render_template('showerror.html', error_message="Scheduler already running")

    create_scheduler()
    return redirect("/")

@app.route("/stop")
def stop_scheduler():
    global scheduler
    if not scheduler or not scheduler.running:
        return render_template('showerror.html', error_message="Scheduler already stopped")

    scheduler.shutdown()
    scheduler = None
    return redirect("/")
 
@app.route("/status")
def scheduler_status():
    return {"running": scheduler.running if scheduler else False}

def get_timedelta(wait):
    value = wait["value"]
    unit = wait["unit"]

    if unit == "seconds":
        return timedelta(seconds=value)
    if unit == "minutes":
        return timedelta(minutes=value)
    if unit == "hours":
        return timedelta(hours=value)
    if unit == "days":
        return timedelta(days=value)
    return print("Error to fetch timedelta")

def get_group_rule(group_name):
    return groups_collection.find_one({"groupName": group_name.lower()})
  
def job():
    with app.app_context():
        all_users = list(users_collection.find())
        now = datetime.now(timezone.utc)

        for user in all_users:
            group_data = get_group_rule(user.get("group"))
            if not group_data:
                continue

            # first email
            if user.get("lastSend") is None:
                send_email(user)
                continue

            last_send = user["lastSend"]
            if last_send.tzinfo is None:
                last_send = last_send.replace(tzinfo=timezone.utc)

            diff = now - last_send
            sent_count = user.get("noOfEmailsSend", 0)

            rules = group_data.get("rules", [])

            for rule in rules:
                max_emails = rule.get("maxEmails", 0)
                wait_time = get_timedelta(rule.get("wait"))

                if sent_count < max_emails and diff >= wait_time:
                    send_email(user)
                    break

scheduler = None

def create_scheduler():
    global scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(job, 'interval', seconds=5, id="email_scheduler")
    scheduler.start()

if __name__ == "__main__":
    create_scheduler()
    app.run()